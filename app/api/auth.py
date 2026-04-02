"""Auth endpoints: signup, login, me, invite.

Signup creates a new org + user (first user is admin).
Invite allows existing org admin to add team members.
Login returns JWT. Me returns current user profile.

Designed for future OAuth: when adding Google/GitHub SSO,
add a POST /api/auth/oauth/{provider} endpoint that:
1. Verifies the OAuth token with the provider
2. Finds or creates the user
3. Returns our JWT (same format)
"""

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_db
from app.models.org import Org
from app.models.user import User
from app.schemas.auth import SignupRequest, LoginRequest, TokenResponse, UserResponse
from app.utils.auth import hash_password, verify_password, create_access_token
from app.deps import get_current_user, CurrentUser

router = APIRouter()


@router.post("/auth/signup", response_model=TokenResponse, status_code=201)
def signup(body: SignupRequest, db: Session = Depends(get_db)):
    # Check if email already exists
    existing = db.execute(select(User).where(User.email == body.email)).scalar_one_or_none()
    if existing:
        raise HTTPException(status_code=409, detail="Email already registered")

    # Create org
    org_name = body.org_name or f"{body.name}'s Org"
    org = Org(name=org_name)
    db.add(org)
    db.flush()  # Get org.id before creating user

    # Create user (first user in org is admin)
    user = User(
        org_id=org.id,
        email=body.email,
        name=body.name,
        password_hash=hash_password(body.password),
        role="admin",
        auth_provider="local",
    )
    db.add(user)
    db.commit()
    db.refresh(user)

    token = create_access_token(user.id, org.id, user.role)
    return TokenResponse(access_token=token)


@router.post("/auth/login", response_model=TokenResponse)
def login(body: LoginRequest, db: Session = Depends(get_db)):
    user = db.execute(select(User).where(User.email == body.email)).scalar_one_or_none()
    if not user or not verify_password(body.password, user.password_hash):
        raise HTTPException(status_code=401, detail="Invalid email or password")

    token = create_access_token(user.id, user.org_id, user.role)
    return TokenResponse(access_token=token)


@router.get("/auth/me", response_model=UserResponse)
def me(current_user: CurrentUser = Depends(get_current_user), db: Session = Depends(get_db)):
    user = db.get(User, current_user.id)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    return user


@router.post("/auth/invite", status_code=201)
def invite_member(
    email: str,
    name: str,
    role: str = "developer",
    current_user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Invite a team member to the current user's org.

    Only org admins can invite. Invited user gets a temporary password
    they should change on first login (or use OAuth when available).
    """
    if current_user.role != "admin":
        raise HTTPException(status_code=403, detail="Only admins can invite members")

    # Check email not taken
    existing = db.execute(select(User).where(User.email == email)).scalar_one_or_none()
    if existing:
        raise HTTPException(status_code=409, detail="Email already registered")

    if role not in ("admin", "po", "tech_lead", "qa", "developer"):
        raise HTTPException(status_code=400, detail=f"Invalid role: {role}")

    # Create user with a temporary password (they'll reset or use OAuth)
    import secrets
    temp_password = secrets.token_urlsafe(12)

    user = User(
        org_id=current_user.org_id,
        email=email,
        name=name,
        password_hash=hash_password(temp_password),
        role=role,
        auth_provider="local",
    )
    db.add(user)
    db.commit()
    db.refresh(user)

    return {
        "message": f"Invited {email} as {role}",
        "user_id": str(user.id),
        "temp_password": temp_password,  # In production: send via email, not in response
    }
