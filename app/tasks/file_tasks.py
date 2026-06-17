import asyncio
from celery import shared_task
from sqlalchemy.orm import Session
from sqlalchemy import create_engine
from app.core.config import settings
from app.models.user import UploadedFile, FileStatus, Question
from app.services.files.extractor import file_extractor
from app.services.ai.structurer import content_structurer
from app.services.ai.quiz_generator import quiz_engine
from app.services.ai.duplicate_detector import duplicate_detector
import httpx
import structlog

logger = structlog.get_logger()

# Synchronous engine for Celery worker
sync_engine = create_engine(settings.SYNC_DATABASE_URL)


def get_sync_db():
    from sqlalchemy.orm import sessionmaker
    SessionLocal = sessionmaker(bind=sync_engine)
    return SessionLocal()


@shared_task(
    bind=True,
    max_retries=3,
    default_retry_delay=30,
    name="app.tasks.file_tasks.process_uploaded_file",
)
def process_uploaded_file(self, file_id: str, user_id: str):
    """
    Full processing pipeline for an uploaded file:
    1. Download file bytes from S3
    2. Extract text (OCR / parser)
    3. Structure content via AI
    4. Generate + validate questions (3-pass pipeline)
    5. Deduplicate
    6. Save questions to DB
    7. Update file status
    """
    db = get_sync_db()

    try:
        # 1. Fetch file record
        file_record = db.query(UploadedFile).filter(UploadedFile.id == file_id).first()
        if not file_record:
            logger.error("File record not found", file_id=file_id)
            return

        # Mark as processing
        file_record.status = FileStatus.PROCESSING
        db.commit()

        logger.info("Processing started", file_id=file_id, filename=file_record.original_filename)

        # 2. Download file bytes from S3
        file_bytes = _download_file(file_record.file_url)

        # 3. Extract text
        extracted_text = asyncio.run(
            file_extractor.extract(file_bytes, file_record.original_filename)
        )
        if not extracted_text or len(extracted_text.strip()) < 100:
            raise ValueError("Insufficient text extracted from file")

        file_record.extracted_text = extracted_text
        db.commit()

        # 4. Structure content
        structured = asyncio.run(
            content_structurer.structure(extracted_text, file_record.original_filename)
        )
        file_record.structured_content = structured
        file_record.topic_name = structured.get("topic", "Unknown Topic")
        db.commit()

        # 5. Generate questions
        chunks = content_structurer.extract_chunks(extracted_text)
        if not chunks:
            raise ValueError("No processable text chunks found")

        raw_questions = asyncio.run(
            quiz_engine.generate_batch(
                chunks=chunks,
                topic=structured.get("topic", "Unknown"),
                structured_content=structured,
                target_count=20,
            )
        )

        # 6. Deduplicate
        unique_questions = asyncio.run(
            duplicate_detector.add_batch(raw_questions, user_id)
        )

        # 7. Save to DB
        saved_count = 0
        for q_data in unique_questions:
            question = Question(
                file_id=file_id,
                user_id=user_id,
                topic=q_data.get("topic", structured.get("topic", "Unknown")),
                subtopic=q_data.get("subtopic"),
                difficulty=q_data.get("difficulty", "medium"),
                question_text=q_data["question"],
                option_a=q_data["option_a"],
                option_b=q_data["option_b"],
                option_c=q_data["option_c"],
                option_d=q_data["option_d"],
                correct_answer=q_data["correct_answer"],
                explanation=q_data["explanation"],
                source_reference=q_data.get("source_reference"),
                is_validated=q_data.get("is_validated", False),
                validation_score=q_data.get("validation_score"),
            )
            db.add(question)
            saved_count += 1

        # 8. Update file record
        file_record.questions_generated = saved_count
        file_record.status = FileStatus.READY
        db.commit()

        logger.info(
            "File processing complete",
            file_id=file_id,
            questions=saved_count,
            topic=structured.get("topic"),
        )
        return {"file_id": file_id, "questions_generated": saved_count}

    except Exception as exc:
        logger.error("File processing failed", file_id=file_id, error=str(exc))
        try:
            file_record = db.query(UploadedFile).filter(UploadedFile.id == file_id).first()
            if file_record:
                file_record.status = FileStatus.FAILED
                file_record.processing_error = str(exc)[:500]
                db.commit()
        except Exception:
            pass

        raise self.retry(exc=exc)
    finally:
        db.close()


def _download_file(file_url: str) -> bytes:
    """Download file bytes from S3 URL."""
    response = httpx.get(file_url, timeout=60.0)
    response.raise_for_status()
    return response.content
