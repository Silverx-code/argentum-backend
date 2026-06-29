FROM python:3.11-slim

# System dependencies for OCR, PDF parsing, and image processing
RUN apt-get update && apt-get install -y \
    tesseract-ocr \
    tesseract-ocr-eng \
    poppler-utils \
    libmagic1 \
    libgl1 \
    libglib2.0-0 \
    gcc \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy app
COPY . .

# Create directories for persistent data.
# On Render, /app/data is mapped to a persistent disk volume so
# uploaded files and ChromaDB embeddings survive container restarts.
RUN mkdir -p /app/data/uploads \
    && mkdir -p /app/data/chroma_store

# Non-root user for security
RUN adduser --disabled-password --gecos "" appuser \
    && chown -R appuser:appuser /app
USER appuser

EXPOSE 8000

# workers=1 keeps ChromaDB in-process consistent.
# Increase to 2-4 only after migrating ChromaDB to a hosted service.
CMD ["uvicorn", "app.main:app", \
     "--host", "0.0.0.0", \
     "--port", "8000", \
     "--workers", "1"]
