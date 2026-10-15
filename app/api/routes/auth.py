"""Auth endpoints: register, login, refresh (JWT + rotation)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Depends, status
from sqlalchemy.orm import Session

from app.core.errors import ApiError
from app.core.security import (
    TokenError,
    create_access_token,
    create_refresh_token,
    decode_token,
    hash_password,
    verify_password,
)
from app.db.session import get_db
from app.models.entities import RefreshToken, User
from app.schemas.auth import (
    LoginRequest,
    RefreshRequest,
    RegisterRequest,
    TokenResponse,
    UserResponse,
)

router = APIRouter(prefix="/auth", tags=["auth"])


def _issue_pair(user: User, db: Session) -> TokenResponse:
    access = create_access_token(user_id=str(user.id), role=user.role)
    refresh, jti = create_refresh_token(user_id=str(user.id))
    db.add(
        RefreshToken(
            user_id=user.id,
            jti=jti,
            expires_at=datetime.now(UTC) + timedelta(days=7),
        )
    )
    db.commit()
    return TokenResponse(access_token=access, refresh_token=refresh)


@router.post("/register", response_model=UserResponse, status_code=status.HTTP_201_CREATED)
def register(payload: RegisterRequest, db: Session = Depends(get_db)) -> User:
    """Register a new ``user`` account (public signup never grants admin)."""
    email = payload.email.strip().lower()
    if "@" not in email:
        raise ApiError(
            status.HTTP_422_UNPROCESSABLE_ENTITY, "validation_error", "Invalid email address"
        )
    if db.query(User).filter(User.email == email).first() is not None:
        raise ApiError(status.HTTP_409_CONFLICT, "already_exists", "Email is already registered")
    try:
        password_hash = hash_password(payload.password)
    except ValueError as exc:
        raise ApiError(status.HTTP_422_UNPROCESSABLE_ENTITY, "validation_error", str(exc)) from exc
    user = User(email=email, password_hash=password_hash, role="user")
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


@router.post("/login", response_model=TokenResponse)
def login(payload: LoginRequest, db: Session = Depends(get_db)) -> TokenResponse:
    """Authenticate with email + password and receive a token pair."""
    email = payload.email.strip().lower()
    user = db.query(User).filter(User.email == email).first()
    if user is None or not verify_password(payload.password, user.password_hash):
        raise ApiError(
            status.HTTP_401_UNAUTHORIZED, "invalid_credentials", "Invalid email or password"
        )
    if not user.is_active:
        raise ApiError(status.HTTP_403_FORBIDDEN, "inactive", "User account is inactive")
    return _issue_pair(user, db)


def _is_expired(moment: datetime) -> bool:
    """Compare DB datetimes safely (SQLite returns naive datetimes)."""
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=UTC)
    return moment < datetime.now(UTC)


@router.post("/refresh", response_model=TokenResponse)
def refresh(payload: RefreshRequest, db: Session = Depends(get_db)) -> TokenResponse:
    """Rotate a refresh token: the presented token is revoked, a new pair issued."""
    try:
        decoded = decode_token(payload.refresh_token, expected_type="refresh")
    except TokenError as exc:
        raise ApiError(status.HTTP_401_UNAUTHORIZED, "unauthorized", str(exc)) from exc
    jti = str(decoded.get("jti", ""))
    record = db.query(RefreshToken).filter(RefreshToken.jti == jti).first()
    if record is None or record.revoked:
        raise ApiError(
            status.HTTP_401_UNAUTHORIZED, "unauthorized", "Refresh token is revoked or unknown"
        )
    if _is_expired(record.expires_at):
        raise ApiError(status.HTTP_401_UNAUTHORIZED, "unauthorized", "Refresh token has expired")
    record.revoked = True  # rotation: single use
    user = db.get(User, record.user_id)
    if user is None or not user.is_active:
        db.commit()
        raise ApiError(status.HTTP_401_UNAUTHORIZED, "unauthorized", "User not found or inactive")
    db.commit()
    return _issue_pair(user, db)
