from pydantic import BaseModel, Field

SHA256_HEX_PATTERN = r"^[0-9a-fA-F]{64}$"


class RegisterRequest(BaseModel):
    username: str = Field(min_length=2, max_length=100)
    password_sha256: str = Field(min_length=64, max_length=64, pattern=SHA256_HEX_PATTERN)
    confirm_password_sha256: str = Field(min_length=64, max_length=64, pattern=SHA256_HEX_PATTERN)


class LoginRequest(BaseModel):
    username: str = Field(min_length=1, max_length=100)
    password_sha256: str = Field(min_length=64, max_length=64, pattern=SHA256_HEX_PATTERN)