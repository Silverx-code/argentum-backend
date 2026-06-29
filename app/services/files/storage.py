import boto3
import uuid
from pathlib import Path
from typing import Optional
from botocore.exceptions import ClientError
import structlog
from app.core.config import settings

logger = structlog.get_logger()


class S3StorageService:
    def __init__(self):
        self.s3 = boto3.client(
            "s3",
            aws_access_key_id=settings.AWS_ACCESS_KEY_ID,
            aws_secret_access_key=settings.AWS_SECRET_ACCESS_KEY,
            region_name=settings.AWS_REGION,
        )
        self.bucket = settings.S3_BUCKET_NAME

    async def upload_file(
        self,
        file_bytes: bytes,
        filename: str,
        user_id: str,
        content_type: str,
    ) -> str:
        """Upload file to S3 and return the URL."""
        ext = Path(filename).suffix.lower()
        object_key = f"uploads/{user_id}/{uuid.uuid4()}{ext}"

        try:
            self.s3.put_object(
                Bucket=self.bucket,
                Key=object_key,
                Body=file_bytes,
                ContentType=content_type,
                ServerSideEncryption="AES256",
            )
            url = f"https://{self.bucket}.s3.{settings.AWS_REGION}.amazonaws.com/{object_key}"
            logger.info("File uploaded to S3", key=object_key, user_id=user_id)
            return url
        except ClientError as e:
            logger.error("S3 upload failed", error=str(e))
            raise RuntimeError(f"File upload failed: {e}")

    async def delete_file(self, file_url: str) -> bool:
        """Delete file from S3 by its URL."""
        try:
            key = file_url.split(f"{self.bucket}.s3.{settings.AWS_REGION}.amazonaws.com/")[1]
            self.s3.delete_object(Bucket=self.bucket, Key=key)
            logger.info("File deleted from S3", key=key)
            return True
        except (ClientError, IndexError) as e:
            logger.error("S3 delete failed", error=str(e))
            return False

    async def get_presigned_url(self, file_url: str, expiry: int = 3600) -> str:
        """Generate a presigned URL for temporary file access."""
        try:
            key = file_url.split(f"{self.bucket}.s3.{settings.AWS_REGION}.amazonaws.com/")[1]
            url = self.s3.generate_presigned_url(
                "get_object",
                Params={"Bucket": self.bucket, "Key": key},
                ExpiresIn=expiry,
            )
            return url
        except ClientError as e:
            logger.error("Presigned URL generation failed", error=str(e))
            raise RuntimeError(f"Could not generate presigned URL: {e}")


class LocalStorageService:
    """Filesystem-backed storage used when S3 credentials are absent.

    Files are written under ``settings.LOCAL_STORAGE_DIR`` and referenced by a
    ``local://<user_id>/<uuid><ext>`` marker URL. Because the API and Celery
    worker share the same bind-mounted volume (``.:/app`` in docker-compose),
    the worker can read back what the API wrote.
    """

    def __init__(self):
        self.root = Path(settings.LOCAL_STORAGE_DIR)

    async def upload_file(
        self,
        file_bytes: bytes,
        filename: str,
        user_id: str,
        content_type: str,
    ) -> str:
        ext = Path(filename).suffix.lower()
        rel_key = f"{user_id}/{uuid.uuid4()}{ext}"
        dest = self.root / rel_key
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(file_bytes)
        logger.info("File written to local storage", path=str(dest), user_id=user_id)
        return f"local://{rel_key}"

    async def delete_file(self, file_url: str) -> bool:
        try:
            rel_key = file_url.replace("local://", "", 1)
            path = self.root / rel_key
            if path.exists():
                path.unlink()
            logger.info("File deleted from local storage", key=rel_key)
            return True
        except OSError as e:
            logger.error("Local delete failed", error=str(e))
            return False

    async def get_presigned_url(self, file_url: str, expiry: int = 3600) -> str:
        # No signing needed for local storage — the marker is the reference.
        return file_url


CONTENT_TYPE_MAP = {
    ".pdf": "application/pdf",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    ".ppt": "application/vnd.ms-powerpoint",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
    ".webp": "image/webp",
}


def get_content_type(filename: str) -> str:
    ext = Path(filename).suffix.lower()
    return CONTENT_TYPE_MAP.get(ext, "application/octet-stream")


storage_service = (
    LocalStorageService() if settings.use_local_storage else S3StorageService()
)
