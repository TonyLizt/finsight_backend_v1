from pydantic import BaseModel, Field


class UpdateStatusRequest(BaseModel):
    status: str = Field(pattern="^(active|disabled)$")
    reason: str | None = None


class UpdateRoleRequest(BaseModel):
    role: str = Field(pattern="^(user|admin)$")
    reason: str | None = None


class UpdateUsernameRequest(BaseModel):
    username: str = Field(min_length=2, max_length=100)
    reason: str | None = None


SHA256_HEX_PATTERN = r"^[0-9a-fA-F]{64}$"


class ResetPasswordRequest(BaseModel):
    new_password_sha256: str = Field(min_length=64, max_length=64, pattern=SHA256_HEX_PATTERN)
    confirm_password_sha256: str = Field(min_length=64, max_length=64, pattern=SHA256_HEX_PATTERN)
    force_logout: bool = True
    reason: str | None = None

class DeleteUserRequest(BaseModel):
    reason: str | None = None
