from typing import Optional
from datetime import datetime
from uuid import UUID
from pydantic import BaseModel, EmailStr


class SignupRequest(BaseModel):
    name: str
    email: str
    password: str
    org_name: Optional[str] = None  # If not provided, creates org from user's name


class LoginRequest(BaseModel):
    email: str
    password: str


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"


class UserResponse(BaseModel):
    id: UUID
    org_id: UUID
    email: str
    name: str
    role: str
    auth_provider: str
    created_at: datetime

    class Config:
        from_attributes = True
