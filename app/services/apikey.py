"""API Key service: hash-only storage, scopes, unified auth principal."""

from __future__ import annotations

import hashlib
import hmac
import secrets
from datetime import UTC, datetime, timedelta
from uuid import UUID

from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.errors import ApiError
from app.models.entities import ApiKey, User
from app.schemas.apikey import (
    ApiKeyCreate,
    ApiKeyCreateResponse,
    ApiKeyListResponse,
    ApiKeyResponse,
)
from app.services.audit import AuditAction, AuditService


class ApiKeyService:
    """Service for managing API keys (hash-only storage)."""

    @staticmethod
    def _hash_key(key: str) -> str:
        """SHA256 hash of the full API key."""
        return hashlib.sha256(key.encode()).hexdigest()

    @staticmethod
    def _generate_prefix() -> str:
        """Generate 8-char hex prefix for API key.

        Uses only hex characters [0-9a-f] so the prefix never contains
        '_' or '-', keeping ``cf_{prefix}_{secret}`` unambiguously
        parseable with split('_', 2).
        """
        return secrets.token_hex(4)  # always exactly 8 hex chars

    @staticmethod
    def _generate_secret() -> str:
        """Generate 32+ byte URL-safe secret for API key."""
        return secrets.token_urlsafe(32)

    @staticmethod
    def _verify_key(key: str, key_hash: str) -> bool:
        """Constant-time verification of API key against stored hash."""
        return hmac.compare_digest(ApiKeyService._hash_key(key), key_hash)

    @staticmethod
    def create_key(
        db: Session,
        user: User,
        payload: ApiKeyCreate,
        request_ip: str | None = None,
    ) -> ApiKeyCreateResponse:
        """Create a new API key. Returns the raw key ONCE."""
        # Validate scopes
        valid_scopes = {"read", "run", "write"}
        for scope in payload.scopes:
            if scope not in valid_scopes:
                raise ApiError(
                    422, "validation_error", f"Invalid scope: {scope}", {"valid_scopes": list(valid_scopes)}
                )

        # Generate key components
        prefix = ApiKeyService._generate_prefix()
        secret = ApiKeyService._generate_secret()
        raw_key = f"cf_{prefix}_{secret}"
        key_hash = ApiKeyService._hash_key(raw_key)

        # Determine expiry
        expires_at = payload.expires_at
        if expires_at is None and settings.API_KEY_DEFAULT_EXPIRY_DAYS > 0:
            expires_at = datetime.now(UTC) + timedelta(days=settings.API_KEY_DEFAULT_EXPIRY_DAYS)

        # Store in database
        api_key = ApiKey(
            user_id=user.id,
            name=payload.name,
            prefix=prefix,
            key_hash=key_hash,
            scopes=payload.scopes,
            expires_at=expires_at,
        )
        db.add(api_key)
        db.commit()
        db.refresh(api_key)

        # Audit
        AuditService.record(
            db,
            actor=user.email,
            action=AuditAction.APIKEY_CREATE,
            target_type="apikey",
            target_id=api_key.id,
            ip=request_ip,
            metadata={"name": payload.name, "scopes": payload.scopes},
        )

        return ApiKeyCreateResponse(
            id=api_key.id,
            name=api_key.name,
            prefix=api_key.prefix,
            key=raw_key,  # ONLY returned here
            scopes=api_key.scopes,
            expires_at=api_key.expires_at,
            created_at=api_key.created_at,
        )

    @staticmethod
    def list_keys(db: Session, user: User) -> ApiKeyListResponse:
        """List API keys for a user (no raw keys)."""
        query = db.query(ApiKey).filter(ApiKey.user_id == user.id)
        keys = query.order_by(ApiKey.created_at.desc()).all()
        return ApiKeyListResponse(
            items=[
                ApiKeyResponse(
                    id=k.id,
                    name=k.name,
                    prefix=k.prefix,
                    scopes=k.scopes,
                    expires_at=k.expires_at,
                    last_used_at=k.last_used_at,
                    revoked_at=k.revoked_at,
                    created_at=k.created_at,
                )
                for k in keys
            ],
            total=len(keys),
            page=1,
            page_size=len(keys),
        )

    @staticmethod
    def revoke_key(
        db: Session,
        user: User,
        key_id: UUID,
        request_ip: str | None = None,
    ) -> None:
        """Revoke an API key."""
        api_key = db.query(ApiKey).filter(ApiKey.id == key_id, ApiKey.user_id == user.id).first()
        if api_key is None:
            raise ApiError(404, "not_found", "API key not found")
        if api_key.revoked_at is not None:
            raise ApiError(409, "already_revoked", "API key is already revoked")

        api_key.revoked_at = datetime.now(UTC)
        db.commit()

        AuditService.record(
            db,
            actor=user.email,
            action=AuditAction.APIKEY_REVOKE,
            target_type="apikey",
            target_id=api_key.id,
            ip=request_ip,
            metadata={"name": api_key.name},
        )

    @staticmethod
    def authenticate_key(db: Session, raw_key: str) -> tuple[User, list[str]] | None:
        """Authenticate an API key. Returns (user, scopes) or None.

        The key format is ``cf_{prefix}_{secret}`` where prefix is 8 hex chars
        (no underscores/hyphens) generated by ``_generate_prefix()``.  We extract
        the prefix as the fixed slice ``raw_key[3:11]`` rather than relying on
        split("_", …) so that any legacy keys whose prefix happened to contain
        '_' are still resolved correctly via full-hash fallback.
        """
        if not raw_key.startswith("cf_"):
            return None

        # Extract prefix: fixed 8-char slice after "cf_"
        # Format: "cf_" (3) + prefix (8) + "_" (1) + secret (rest)
        # Minimum length: 3 + 8 + 1 + 1 = 13
        if len(raw_key) < 13 or raw_key[11] != "_":
            # Not a fixed-format key — fall back to split-based extraction
            parts = raw_key.split("_", 2)
            if len(parts) != 3:
                return None
            _, prefix, _ = parts
        else:
            prefix = raw_key[3:11]

        # Query all matching prefixes to handle prefix collisions
        candidate_keys = db.query(ApiKey).filter(ApiKey.prefix == prefix).all()
        if not candidate_keys:
            return None

        matched_key = None
        for k in candidate_keys:
            if ApiKeyService._verify_key(raw_key, k.key_hash):
                matched_key = k
                break

        if matched_key is None:
            return None

        api_key = matched_key

        if api_key.revoked_at is not None:
            return None

        now = datetime.now(UTC)
        if api_key.expires_at is not None:
            expires_at = api_key.expires_at
            if expires_at.tzinfo is None:
                expires_at = expires_at.replace(tzinfo=UTC)
            if expires_at < now:
                return None

        user = db.get(User, api_key.user_id)
        if user is None or not user.is_active:
            return None

        # Update last_used_at throttled to avoid write-heavy DB churn on every request
        last_used = api_key.last_used_at
        if last_used is not None and last_used.tzinfo is None:
            last_used = last_used.replace(tzinfo=UTC)
        if last_used is None or (now - last_used).total_seconds() > 60:
            api_key.last_used_at = now
            db.commit()

        return user, api_key.scopes


class Principal:
    """Unified authentication principal (JWT or API key)."""

    def __init__(
        self,
        user: User,
        scopes: list[str],
        auth_method: str,  # "jwt" or "apikey"
    ):
        self.user = user
        self.scopes = scopes
        self.auth_method = auth_method

    def has_scope(self, scope: str) -> bool:
        """Check if principal has a specific scope."""
        return scope in self.scopes

    def require_scope(self, scope: str) -> None:
        """Require a specific scope (raises 403 if not present)."""
        if not self.has_scope(scope):
            from fastapi import status

            from app.core.errors import ApiError
            raise ApiError(
                status.HTTP_403_FORBIDDEN,
                "insufficient_scope",
                f"Required scope: {scope}",
                {"required": scope, "granted": self.scopes},
            )
