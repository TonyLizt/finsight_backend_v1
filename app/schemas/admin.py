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


class ResetPasswordRequest(BaseModel):
    new_password: str = Field(min_length=6)
    confirm_password: str = Field(min_length=6)
    force_logout: bool = True
    reason: str | None = None


class DeleteUserRequest(BaseModel):
    reason: str | None = None
