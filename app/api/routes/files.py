from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from pathlib import Path
from typing import List
from app.core.dependencies import get_db, get_current_user
from app.models.user import User, UploadedFile, FileType, FileStatus
from app.schemas.schemas import FileUploadResponse, FileStatusResponse
from app.services.files.storage import storage_service, get_content_type
from app.services.ai.analytics import analytics, EventType
from app.core.config import settings
import structlog

logger = structlog.get_logger()
router = APIRouter(prefix="/files", tags=["File Upload"])

ALLOWED_EXTENSIONS = {".pdf", ".docx", ".pptx", ".ppt", ".jpg", ".jpeg", ".png", ".webp"}
MAX_BYTES = settings.UPLOAD_SIZE_LIMIT_MB * 1024 * 1024

EXTENSION_TO_FILE_TYPE = {
    ".pdf": FileType.PDF,
    ".docx": FileType.DOCX,
    ".pptx": FileType.PPTX,
    ".ppt": FileType.PPTX,
    ".jpg": FileType.IMAGE,
    ".jpeg": FileType.IMAGE,
    ".png": FileType.IMAGE,
    ".webp": FileType.IMAGE,
}


@router.post("/upload", response_model=FileUploadResponse, status_code=status.HTTP_202_ACCEPTED)
async def upload_file(
    file: UploadFile = File(...),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Upload an academic file for processing.
    File is stored to S3, then processing is queued as a background task.
    Returns immediately with file record — poll /files/{id}/status for progress.
    """
    # Validate extension
    ext = Path(file.filename or "").suffix.lower()
    if ext not in ALLOWED_EXTENSIONS:
        raise HTTPException(
            status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            detail=f"File type '{ext}' not supported. Allowed: {', '.join(ALLOWED_EXTENSIONS)}",
        )

    # Read and validate size
    file_bytes = await file.read()
    if len(file_bytes) > MAX_BYTES:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail=f"File too large. Maximum size is {settings.UPLOAD_SIZE_LIMIT_MB}MB",
        )
    if len(file_bytes) < 100:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="File appears to be empty or corrupt",
        )

    # Upload to S3
    try:
        file_url = await storage_service.upload_file(
            file_bytes=file_bytes,
            filename=file.filename,
            user_id=current_user.id,
            content_type=get_content_type(file.filename),
        )
    except RuntimeError as e:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(e))

    # Create DB record
    file_record = UploadedFile(
        user_id=current_user.id,
        original_filename=file.filename,
        file_type=EXTENSION_TO_FILE_TYPE[ext],
        file_url=file_url,
        file_size_bytes=len(file_bytes),
        status=FileStatus.UPLOADED,
    )
    db.add(file_record)
    await db.flush()

    # Track upload event
    await analytics.track(
        db=db,
        event_type=EventType.FILE_UPLOADED,
        user_id=current_user.id,
        file_id=file_record.id,
        payload={
            "filename": file.filename,
            "file_type": ext,
            "file_size_bytes": len(file_bytes),
        },
    )

    # Queue background processing task
    try:
        from app.tasks.file_tasks import process_uploaded_file
        process_uploaded_file.delay(file_record.id, current_user.id)
    except Exception as e:
        logger.warning("Could not queue processing task (Celery may be offline)", error=str(e))

    logger.info("File uploaded", file_id=file_record.id, user_id=current_user.id)
    return FileUploadResponse.model_validate(file_record)


@router.get("/{file_id}/status", response_model=FileStatusResponse)
async def get_file_status(
    file_id: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Poll for file processing status. Frontend should poll every 3 seconds until status = 'ready'."""
    result = await db.execute(
        select(UploadedFile).where(
            UploadedFile.id == file_id,
            UploadedFile.user_id == current_user.id,
        )
    )
    file_record = result.scalar_one_or_none()
    if not file_record:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="File not found")

    return FileStatusResponse.model_validate(file_record)


@router.get("/", response_model=List[FileUploadResponse])
async def list_files(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """List all uploaded files for the current user."""
    result = await db.execute(
        select(UploadedFile)
        .where(UploadedFile.user_id == current_user.id)
        .order_by(UploadedFile.created_at.desc())
    )
    files = result.scalars().all()
    return [FileUploadResponse.model_validate(f) for f in files]


@router.delete("/{file_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_file(
    file_id: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Delete an uploaded file and its associated questions."""
    result = await db.execute(
        select(UploadedFile).where(
            UploadedFile.id == file_id,
            UploadedFile.user_id == current_user.id,
        )
    )
    file_record = result.scalar_one_or_none()
    if not file_record:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="File not found")

    await storage_service.delete_file(file_record.file_url)
    await db.delete(file_record)
    logger.info("File deleted", file_id=file_id, user_id=current_user.id)
