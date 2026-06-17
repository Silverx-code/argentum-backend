from fastapi import APIRouter, Depends, HTTPException, status, BackgroundTasks
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from datetime import timedelta
from app.core.dependencies import get_db, get_current_user
from app.core.security import (
    hash_password, verify_password,
    create_access_token, create_refresh_token, decode_token
)
from app.models.user import User, AuthProvider
from app.schemas.schemas import (
    UserRegisterRequest, UserLoginRequest, TokenResponse,
    FirebaseAuthRequest, RefreshTokenRequest,
    ForgotPasswordRequest, ResetPasswordRequest, UserResponse
)
from app.core.config import settings
import structlog

logger = structlog.get_logger()
router = APIRouter(prefix="/auth", tags=["Authentication"])


@router.post("/register", response_model=TokenResponse, status_code=status.HTTP_201_CREATED)
async def register(
    request: UserRegisterRequest,
    db: AsyncSession = Depends(get_db),
):
    """Register a new user with email and password."""
    # Check for existing email
    result = await db.execute(select(User).where(User.email == request.email))
    if result.scalar_one_or_none():
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="An account with this email already exists",
        )

    user = User(
        name=request.name,
        email=request.email,
        hashed_password=hash_password(request.password),
        university=request.university,
        auth_provider=AuthProvider.EMAIL,
        is_verified=False,
    )
    db.add(user)
    await db.flush()

    access_token = create_access_token({"sub": user.id, "email": user.email})
    refresh_token = create_refresh_token({"sub": user.id})

    logger.info("User registered", user_id=user.id, email=user.email)

    return TokenResponse(
        access_token=access_token,
        refresh_token=refresh_token,
        user=UserResponse.model_validate(user),
    )


@router.post("/login", response_model=TokenResponse)
async def login(
    request: UserLoginRequest,
    db: AsyncSession = Depends(get_db),
):
    """Login with email and password."""
    result = await db.execute(select(User).where(User.email == request.email))
    user = result.scalar_one_or_none()

    if not user or not user.hashed_password:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid email or password",
        )

    if not verify_password(request.password, user.hashed_password):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid email or password",
        )

    if not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Account is deactivated",
        )

    access_token = create_access_token({"sub": user.id, "email": user.email})
    refresh_token = create_refresh_token({"sub": user.id})

    logger.info("User logged in", user_id=user.id)

    return TokenResponse(
        access_token=access_token,
        refresh_token=refresh_token,
        user=UserResponse.model_validate(user),
    )


@router.post("/firebase", response_model=TokenResponse)
async def firebase_auth(
    request: FirebaseAuthRequest,
    db: AsyncSession = Depends(get_db),
):
    """
    Authenticate via Firebase token (Google Sign-In, etc).
    Verifies the Firebase ID token and creates/finds the user.
    """
    try:
        import firebase_admin
        from firebase_admin import auth as firebase_auth_module

        decoded = firebase_auth_module.verify_id_token(request.firebase_token)
        firebase_uid = decoded["uid"]
        email = decoded.get("email", "")
        name = request.name or decoded.get("name", email.split("@")[0])

    except Exception as e:
        logger.error("Firebase token verification failed", error=str(e))
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid Firebase token",
        )

    # Find or create user
    result = await db.execute(select(User).where(User.firebase_uid == firebase_uid))
    user = result.scalar_one_or_none()

    if not user:
        # Check if email exists from a different provider
        email_result = await db.execute(select(User).where(User.email == email))
        user = email_result.scalar_one_or_none()

        if user:
            # Link Firebase UID to existing account
            user.firebase_uid = firebase_uid
            user.auth_provider = AuthProvider.GOOGLE
        else:
            user = User(
                name=name,
                email=email,
                firebase_uid=firebase_uid,
                auth_provider=AuthProvider.GOOGLE,
                university=request.university,
                is_verified=True,
            )
            db.add(user)
            await db.flush()

    access_token = create_access_token({"sub": user.id, "email": user.email})
    refresh_token = create_refresh_token({"sub": user.id})

    return TokenResponse(
        access_token=access_token,
        refresh_token=refresh_token,
        user=UserResponse.model_validate(user),
    )


@router.post("/refresh", response_model=TokenResponse)
async def refresh_token(
    request: RefreshTokenRequest,
    db: AsyncSession = Depends(get_db),
):
    """Exchange a refresh token for a new access token."""
    payload = decode_token(request.refresh_token)

    if payload.get("type") != "refresh":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid token type",
        )

    user_id = payload.get("sub")
    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()

    if not user or not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="User not found or inactive",
        )

    access_token = create_access_token({"sub": user.id, "email": user.email})
    new_refresh_token = create_refresh_token({"sub": user.id})

    return TokenResponse(
        access_token=access_token,
        refresh_token=new_refresh_token,
        user=UserResponse.model_validate(user),
    )


@router.post("/forgot-password", status_code=status.HTTP_202_ACCEPTED)
async def forgot_password(
    request: ForgotPasswordRequest,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
):
    """Send a password reset email (always returns 202 to prevent email enumeration)."""
    result = await db.execute(select(User).where(User.email == request.email))
    user = result.scalar_one_or_none()

    if user and user.auth_provider == AuthProvider.EMAIL:
        reset_token = create_access_token(
            {"sub": user.id, "purpose": "password_reset"},
            expires_delta=timedelta(hours=1),
        )
        background_tasks.add_task(_send_reset_email, user.email, reset_token)

    return {"message": "If that email exists, a reset link has been sent"}


@router.post("/reset-password", status_code=status.HTTP_200_OK)
async def reset_password(
    request: ResetPasswordRequest,
    db: AsyncSession = Depends(get_db),
):
    """Reset password using a valid reset token."""
    payload = decode_token(request.token)

    if payload.get("purpose") != "password_reset":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid reset token",
        )

    user_id = payload.get("sub")
    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()

    if not user:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid token")

    user.hashed_password = hash_password(request.new_password)
    return {"message": "Password reset successfully"}


@router.get("/me", response_model=UserResponse)
async def get_me(current_user: User = Depends(get_current_user)):
    """Get the currently authenticated user's profile."""
    return UserResponse.model_validate(current_user)


async def _send_reset_email(email: str, token: str):
    """Background task to send password reset email."""
    # Integrate with your email provider (SendGrid, SES, etc.)
    logger.info("Password reset email would be sent", email=email)
