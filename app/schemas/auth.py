from pydantic import BaseModel, Field


class RegisterRequest(BaseModel):
    username: str = Field(min_length=2, max_length=100)
    password: str = Field(min_length=6)
    confirm_password: str = Field(min_length=6)


class LoginRequest(BaseModel):
    username: str
    password: str
