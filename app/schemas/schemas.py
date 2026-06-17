from pydantic import BaseModel, EmailStr, field_validator, model_validator
from typing import Optional, List, Dict, Any
from datetime import datetime
from app.models.user import (
    AuthProvider, FileType, FileStatus, Difficulty,
    TestMode, TestStatus, QuestionFeedback, ConfidenceLevel
)


# ─── Auth Schemas ────────────────────────────────────────────────────────────────

class UserRegisterRequest(BaseModel):
    name: str
    email: EmailStr
    password: str
    university: Optional[str] = None

    @field_validator("password")
    @classmethod
    def password_strength(cls, v: str) -> str:
        if len(v) < 8:
            raise ValueError("Password must be at least 8 characters")
        return v

    @field_validator("name")
    @classmethod
    def name_not_empty(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("Name cannot be empty")
        return v.strip()


class UserLoginRequest(BaseModel):
    email: EmailStr
    password: str


class FirebaseAuthRequest(BaseModel):
    firebase_token: str
    name: Optional[str] = None
    university: Optional[str] = None


class TokenResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    user: "UserResponse"


class RefreshTokenRequest(BaseModel):
    refresh_token: str


class ForgotPasswordRequest(BaseModel):
    email: EmailStr


class ResetPasswordRequest(BaseModel):
    token: str
    new_password: str

    @field_validator("new_password")
    @classmethod
    def password_strength(cls, v: str) -> str:
        if len(v) < 8:
            raise ValueError("Password must be at least 8 characters")
        return v


# ─── User Schemas ────────────────────────────────────────────────────────────────

class UserResponse(BaseModel):
    id: str
    name: str
    email: str
    university: Optional[str]
    auth_provider: AuthProvider
    is_active: bool
    is_verified: bool
    streak_count: int
    total_questions_answered: int
    created_at: datetime

    class Config:
        from_attributes = True


class UserUpdateRequest(BaseModel):
    name: Optional[str] = None
    university: Optional[str] = None


# ─── File Upload Schemas ─────────────────────────────────────────────────────────

class FileUploadResponse(BaseModel):
    id: str
    original_filename: str
    file_type: FileType
    status: FileStatus
    topic_name: Optional[str]
    questions_generated: int
    created_at: datetime

    class Config:
        from_attributes = True


class FileStatusResponse(BaseModel):
    id: str
    status: FileStatus
    topic_name: Optional[str]
    questions_generated: int
    processing_error: Optional[str]
    structured_content: Optional[Dict[str, Any]]

    class Config:
        from_attributes = True


# ─── Question Schemas ────────────────────────────────────────────────────────────

class QuestionResponse(BaseModel):
    id: str
    topic: str
    subtopic: Optional[str]
    difficulty: Difficulty
    question_text: str
    option_a: str
    option_b: str
    option_c: str
    option_d: str
    correct_answer: Optional[str] = None  # Hidden during active test
    explanation: Optional[str] = None     # Hidden during active test
    source_reference: Optional[str]
    times_answered: int
    times_correct: int

    class Config:
        from_attributes = True


class QuestionWithAnswerResponse(QuestionResponse):
    correct_answer: str
    explanation: str


class QuestionFeedbackRequest(BaseModel):
    feedback_type: QuestionFeedback
    comment: Optional[str] = None


# ─── Test Session Schemas ────────────────────────────────────────────────────────

class StartTestRequest(BaseModel):
    mode: TestMode
    topic_filter: Optional[str] = None
    question_count: int = 10
    time_limit_seconds: Optional[int] = None
    difficulty: Optional[Difficulty] = None
    file_id: Optional[str] = None

    @field_validator("question_count")
    @classmethod
    def valid_count(cls, v: int) -> int:
        if v < 1 or v > 50:
            raise ValueError("Question count must be between 1 and 50")
        return v


class SubmitAnswerRequest(BaseModel):
    question_id: str
    selected_answer: str
    time_taken_seconds: float
    confidence: Optional[ConfidenceLevel] = None

    @field_validator("selected_answer")
    @classmethod
    def valid_answer(cls, v: str) -> str:
        if v.upper() not in ["A", "B", "C", "D"]:
            raise ValueError("Answer must be A, B, C, or D")
        return v.upper()


class AnswerResultResponse(BaseModel):
    is_correct: bool
    correct_answer: str
    explanation: str
    time_taken_seconds: float
    question_id: str


class TestSessionResponse(BaseModel):
    id: str
    mode: TestMode
    status: TestStatus
    topic_filter: Optional[str]
    total_questions: int
    answered_questions: int
    correct_answers: int
    score_percentage: float
    duration_seconds: int
    time_limit_seconds: Optional[int]
    weak_topics_detected: Optional[List[str]]
    is_recovery_session: bool
    started_at: datetime
    completed_at: Optional[datetime]

    class Config:
        from_attributes = True


class TestResultResponse(BaseModel):
    session: TestSessionResponse
    responses: List["ResponseDetailResponse"]
    weak_topics: List["WeaknessResponse"]
    recovery_available: bool
    performance_summary: Dict[str, Any]


class ResponseDetailResponse(BaseModel):
    question_id: str
    question_text: str
    selected_answer: Optional[str]
    correct_answer: str
    is_correct: bool
    explanation: str
    time_taken_seconds: float
    confidence: Optional[ConfidenceLevel]
    topic: str
    difficulty: Difficulty

    class Config:
        from_attributes = True


# ─── Weakness & Mastery Schemas ──────────────────────────────────────────────────

class WeaknessResponse(BaseModel):
    id: str
    topic: str
    subtopic: Optional[str]
    accuracy: float
    total_attempts: int
    confidence_gap: float
    last_updated: datetime

    class Config:
        from_attributes = True


class MasteryResponse(BaseModel):
    id: str
    topic: str
    consecutive_sessions_above_threshold: int
    is_mastered: bool
    mastered_at: Optional[datetime]
    accuracy_history: Optional[List[float]]

    class Config:
        from_attributes = True


# ─── Dashboard Schemas ───────────────────────────────────────────────────────────

class DashboardResponse(BaseModel):
    user: UserResponse
    overall_accuracy: float
    total_questions_answered: int
    streak_count: int
    weakest_topics: List[WeaknessResponse]
    strongest_topics: List[WeaknessResponse]
    mastered_topics: List[MasteryResponse]
    recent_sessions: List[TestSessionResponse]
    recovery_sessions_available: int
    improvement_trend: List[Dict[str, Any]]


# ─── AI Tutor Schemas ────────────────────────────────────────────────────────────

class TutorMessageRequest(BaseModel):
    message: str
    topic_context: Optional[str] = None
    file_id: Optional[str] = None
    session_id: Optional[str] = None
    conversation_history: Optional[List[Dict[str, str]]] = []

    @field_validator("message")
    @classmethod
    def message_not_empty(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("Message cannot be empty")
        if len(v) > 2000:
            raise ValueError("Message too long (max 2000 chars)")
        return v.strip()


class TutorMessageResponse(BaseModel):
    reply: str
    topic_referenced: Optional[str]
    suggested_questions: Optional[List[str]]
    model_used: str


# ─── Recovery Schemas ────────────────────────────────────────────────────────────

class RecoverySessionRequest(BaseModel):
    topic: str
    question_count: int = 10

    @field_validator("question_count")
    @classmethod
    def valid_count(cls, v: int) -> int:
        if v < 5 or v > 20:
            raise ValueError("Recovery session must have 5–20 questions")
        return v


# ─── Pagination ──────────────────────────────────────────────────────────────────

class PaginatedResponse(BaseModel):
    items: List[Any]
    total: int
    page: int
    page_size: int
    total_pages: int
