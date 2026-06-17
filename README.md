# Argentum AI — Backend API

> **From Notes to Mastery.**

FastAPI backend for the Argentum AI adaptive learning and exam preparation platform.

---

## Tech Stack

| Layer | Technology |
|---|---|
| Framework | FastAPI 0.111 (Python 3.11) |
| Database | PostgreSQL 16 + SQLAlchemy 2.0 (async) |
| Migrations | Alembic |
| Background Tasks | Celery + Redis |
| AI — Quiz Engine | OpenAI GPT-4o-mini (generation + validation) |
| AI — Advanced Tutor | OpenAI GPT-4o (explanations, exam prediction) |
| AI — Embeddings | OpenAI text-embedding-3-small |
| OCR | Tesseract OCR + PyMuPDF |
| File Parsing | python-docx, python-pptx, PyMuPDF |
| Duplicate Detection | FAISS vector similarity |
| File Storage | AWS S3 |
| Auth | JWT (email) + Firebase (Google) |
| Rate Limiting | slowapi |
| Containerisation | Docker + Docker Compose |

---

## Architecture Overview

```
┌─────────────────────────────────────────────────────┐
│                  Flutter Mobile App                  │
└──────────────────────┬──────────────────────────────┘
                       │ HTTPS
┌──────────────────────▼──────────────────────────────┐
│              FastAPI  /api/v1                        │
│  /auth  /files  /tests  /questions                  │
│  /recovery  /tutor  /dashboard  /users              │
└──────┬──────────────────────────┬───────────────────┘
       │                          │
┌──────▼──────┐          ┌────────▼────────┐
│ PostgreSQL  │          │  Celery Worker   │
│  (primary)  │          │  (file pipeline) │
└─────────────┘          └────────┬─────────┘
                                  │
┌─────────────────────────────────▼──────────────────┐
│              File Processing Pipeline               │
│  Upload → OCR/Parse → Structure → Generate →       │
│  Validate (3-pass) → Deduplicate → Save            │
└────────────────────────────────────────────────────┘
```

---

## AI Pipeline — 3-Pass Question Generation

Every question goes through three AI passes before being saved:

```
Pass 1: Generate
  └── GPT-4o-mini generates question + 4 options + explanation

Pass 2: Validate
  └── GPT-4o-mini validates:
        - answer correctness
        - ambiguity check
        - explanation quality
        - grounding in source material
        - distractor realism
        → Rejected if validation_score < 0.6

Pass 3: Classify
  └── GPT-4o-mini classifies difficulty: easy / medium / hard

Post-pipeline: Duplicate Detection
  └── OpenAI embeddings + FAISS cosine similarity
        → Rejected if similarity > 90% to existing question
```

---

## Analytics & Learning Graph

Every student interaction generates a structured event:

```json
{
  "event": "QUESTION_ANSWERED",
  "user_id": "u91",
  "question_id": "q44",
  "topic": "Recursion",
  "is_correct": false,
  "response_time_seconds": 74,
  "difficulty": "medium",
  "confidence": "high",
  "payload": {
    "selected_answer": "B",
    "correct_answer": "A",
    "misconception": "B"
  }
}
```

These events feed the **Learning Graph** — a per-user, per-topic intelligence record tracking:
- Accuracy over time (learning velocity)
- Misconception patterns (most-selected wrong answers)
- Confidence gaps (high confidence + wrong = guessing)
- Fatigue signals (accuracy drop in second half of test)
- Recovery progression (improvement across recovery sessions)

---

## Quick Start

### 1. Clone and configure

```bash
git clone <repo>
cd argentum-backend
cp .env.example .env
# Fill in OPENAI_API_KEY, DATABASE_URL, AWS keys, etc.
```

### 2. Run with Docker Compose

```bash
docker-compose up --build
```

This starts:
- FastAPI API on `http://localhost:8000`
- PostgreSQL on port `5432`
- Redis on port `6379`
- Celery worker for background file processing

### 3. Run migrations

```bash
docker-compose exec api alembic upgrade head
```

### 4. View API docs

Open `http://localhost:8000/docs`

---

## Manual Setup (without Docker)

```bash
# Create virtual environment
python -m venv venv
source venv/bin/activate  # or venv\Scripts\activate on Windows

# Install dependencies
pip install -r requirements.txt

# Install system dependencies (Ubuntu/Debian)
sudo apt-get install tesseract-ocr tesseract-ocr-eng poppler-utils

# Run PostgreSQL and Redis locally, then:
alembic upgrade head

# Start API
uvicorn app.main:app --reload --port 8000

# Start Celery worker (separate terminal)
celery -A app.core.celery_app worker --loglevel=info -Q file_processing
```

---

## API Endpoints

### Authentication
| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | `/api/v1/auth/register` | Register with email + password |
| POST | `/api/v1/auth/login` | Login with email + password |
| POST | `/api/v1/auth/firebase` | Authenticate via Firebase (Google) |
| POST | `/api/v1/auth/refresh` | Refresh access token |
| POST | `/api/v1/auth/forgot-password` | Send password reset email |
| POST | `/api/v1/auth/reset-password` | Reset password with token |
| GET  | `/api/v1/auth/me` | Get current user |

### File Upload
| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | `/api/v1/files/upload` | Upload PDF/DOCX/PPTX/image |
| GET  | `/api/v1/files/{id}/status` | Poll processing status |
| GET  | `/api/v1/files/` | List all uploaded files |
| DELETE | `/api/v1/files/{id}` | Delete file + its questions |

### Test Sessions
| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | `/api/v1/tests/start` | Start a new test session |
| GET  | `/api/v1/tests/{id}/questions` | Get session questions |
| POST | `/api/v1/tests/{id}/answer` | Submit an answer |
| POST | `/api/v1/tests/{id}/complete` | Complete session + get analysis |
| POST | `/api/v1/tests/{id}/abandon` | Abandon session |
| GET  | `/api/v1/tests/history` | Test session history |

### Questions
| Method | Endpoint | Description |
|--------|----------|-------------|
| GET  | `/api/v1/questions/` | List questions with filters |
| GET  | `/api/v1/questions/topics` | Get all topics with counts |
| GET  | `/api/v1/questions/{id}` | Get question with answer |
| POST | `/api/v1/questions/{id}/feedback` | Rate question quality |
| POST | `/api/v1/questions/{id}/explanation-rating` | Rate explanation |

### Recovery & Mastery
| Method | Endpoint | Description |
|--------|----------|-------------|
| GET  | `/api/v1/recovery/weaknesses` | Get weak topics |
| GET  | `/api/v1/recovery/mastery` | Get mastery progression |
| POST | `/api/v1/recovery/start` | Start recovery session |
| GET  | `/api/v1/recovery/suggested` | Get recovery suggestions |

### AI Tutor
| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | `/api/v1/tutor/chat` | Chat with AI tutor |
| POST | `/api/v1/tutor/explain-question` | Premium GPT-4o explanation |

### Dashboard & Analytics
| Method | Endpoint | Description |
|--------|----------|-------------|
| GET  | `/api/v1/dashboard/` | Full dashboard summary |
| GET  | `/api/v1/dashboard/learning-graph` | Full Learning Graph data |
| GET  | `/api/v1/dashboard/events-summary` | Analytics events summary |
| GET  | `/api/v1/dashboard/topic-breakdown` | Per-topic accuracy breakdown |
| GET  | `/api/v1/dashboard/weekly-progress` | Weekly study progress |

### User Profile
| Method | Endpoint | Description |
|--------|----------|-------------|
| GET  | `/api/v1/users/me` | Get profile |
| PATCH | `/api/v1/users/me` | Update profile |
| GET  | `/api/v1/users/me/stats` | All-time statistics |
| DELETE | `/api/v1/users/me` | Delete account (GDPR) |

---

## Test Modes

| Mode | Timer | Hints | Description |
|------|-------|-------|-------------|
| `practice` | None | Yes | Instant explanations, retries allowed |
| `timed` | 60s/question | No | Exam simulation |
| `speed_drill` | 10s/question | No | Rapid-fire questions |
| `recovery` | 75s/question | No | Weak-topic focused, easy-first |

---

## Database Schema

```
users                    uploaded_files
  id                       id
  name                     user_id → users
  email                    original_filename
  hashed_password          file_type
  university               status
  streak_count             extracted_text
  total_questions_answered structured_content (JSON)
                           questions_generated

questions                test_sessions
  id                       id
  file_id                  user_id → users
  user_id                  mode
  topic                    status
  difficulty               score_percentage
  question_text            duration_seconds
  option_a/b/c/d           weak_topics_detected
  correct_answer           is_recovery_session
  explanation
  is_validated             question_responses
  validation_score           session_id → test_sessions
                             question_id → questions
user_weaknesses            selected_answer
  user_id → users          is_correct
  topic                    time_taken_seconds
  accuracy                 confidence
  confidence_gap

topic_mastery            analytics_events
  user_id → users          event_type
  topic                    user_id
  consecutive_sessions     payload (JSON)
  is_mastered              topic, is_correct, response_time

learning_graph
  user_id
  topic
  accuracy_over_time (JSON)
  learning_velocity
  misconception_pattern
  confidence_gap
  fatigue_drop_detected
```

---

## Environment Variables

See `.env.example` for all required variables.

Key ones:
- `OPENAI_API_KEY` — Required for quiz generation + tutor
- `DATABASE_URL` — PostgreSQL async URL
- `SECRET_KEY` — JWT signing key (min 32 chars, keep secret)
- `FIREBASE_CREDENTIALS_PATH` — For Google auth
- `AWS_*` — For S3 file storage

---

## Data Privacy

Argentum AI is designed with student privacy in mind:

- Passwords are bcrypt-hashed, never stored in plain text
- File contents are stored encrypted on S3 (AES-256)
- Analytics events use anonymisable user IDs, not PII
- AI tutor messages store only length + truncated preview (not full text)
- Students can delete all their data via `DELETE /api/v1/users/me`
- No personal data is used to train external AI models

---

## Production Checklist

- [ ] Set `APP_ENV=production` (disables API docs)
- [ ] Use a strong, random `SECRET_KEY` (32+ chars)
- [ ] Set up SSL/TLS termination (nginx or cloud LB)
- [ ] Configure S3 bucket with proper IAM policies
- [ ] Set up Firebase project and download service account JSON
- [ ] Configure `ALLOWED_ORIGINS` to your Flutter app domain
- [ ] Set up database backups
- [ ] Configure Celery worker autoscaling
- [ ] Add monitoring (Sentry, PostHog, or Datadog)
- [ ] Run `alembic upgrade head` on first deploy
#   a r g e n t u m - b a c k e n d  
 