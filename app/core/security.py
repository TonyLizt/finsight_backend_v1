"""密码摘要、bcrypt 哈希与 JWT Token 工具。

登录安全模型 v2：

前端不直接发送明文 password。
前端发送：

    password_sha256 = sha256("FINSIGHT_CLIENT_PASSWORD_V1:" + raw_password)

后端不保存 password_sha256 明文。
后端保存：

    users.password_hash = bcrypt(password_sha256)

登录时：

    bcrypt.verify(password_sha256, users.password_hash)

注意：
- password_sha256 本质上仍然是 password-equivalent secret。
- 企业生产环境仍必须使用 HTTPS。
- 不能只把 sha256 直接存入数据库。
"""

from __future__ import annotations

import hashlib
import re
from datetime import datetime, timedelta, timezone
from typing import Any

from jose import jwt
from passlib.context import CryptContext

from app.core.config import settings

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

CLIENT_PASSWORD_SHA256_PREFIX = "FINSIGHT_CLIENT_PASSWORD_V1:"
SHA256_HEX_RE = re.compile(r"^[0-9a-fA-F]{64}$")


def normalize_password_sha256(password_sha256: str) -> str:
    return str(password_sha256 or "").strip().lower()


def is_valid_password_digest_format(password_sha256: str) -> bool:
    value = normalize_password_sha256(password_sha256)
    return bool(SHA256_HEX_RE.fullmatch(value))


def get_password_hash(password_sha256: str) -> str:
    """Hash the client-side SHA-256 password digest with bcrypt."""
    digest = normalize_password_sha256(password_sha256)

    if not is_valid_password_digest_format(digest):
        raise ValueError("password_sha256 must be 64 hex characters.")

    return pwd_context.hash(digest)


def verify_password(password_sha256: str, hashed_password: str) -> bool:
    """Verify client-side SHA-256 password digest against bcrypt hash."""
    digest = normalize_password_sha256(password_sha256)

    if not is_valid_password_digest_format(digest):
        return False

    try:
        return pwd_context.verify(digest, hashed_password)
    except Exception:
        return False


def build_client_password_sha256_for_seed(raw_password: str) -> str:
    """Only for backend seed/demo scripts.

    This mirrors the frontend hashing rule so seed accounts can still be used.
    Do not use this in API login/register handlers.
    """
    raw = str(raw_password or "")
    material = f"{CLIENT_PASSWORD_SHA256_PREFIX}{raw}".encode("utf-8")
    return hashlib.sha256(material).hexdigest()


def create_access_token(subject: str | int, extra: dict[str, Any] | None = None) -> str:
    expire = datetime.now(timezone.utc) + timedelta(minutes=settings.access_token_expire_minutes)
    payload: dict[str, Any] = {
        "sub": str(subject),
        "exp": expire,
    }

    if extra:
        payload.update(extra)

    return jwt.encode(payload, settings.secret_key, algorithm=settings.algorithm)


def decode_token(token: str) -> dict[str, Any]:
    return jwt.decode(token, settings.secret_key, algorithms=[settings.algorithm])


def is_valid_password_format(password_sha256: str) -> bool:
    """Compatibility name used by user_service.

    In v2 this validates the client-side SHA-256 digest format,
    not the raw password length.
    """
    return is_valid_password_digest_format(password_sha256)