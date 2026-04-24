"""
V-Watch Backend - User Management API Routes
"""

from fastapi import APIRouter, Depends, HTTPException, status, Query, Request
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func, update, delete
from typing import Optional, List

from ..core.database import get_db
from ..core.security import PasswordHasher
from ..core.dependencies import get_current_user, require_admin
from ..models.user import User, UserRole
from ..models.violation import AuditLog
from ..schemas.user import UserCreate, UserUpdate, UserResponse

router = APIRouter(prefix="/users", tags=["User Management"])
hasher = PasswordHasher()


@router.post("", response_model=UserResponse, status_code=201)
async def create_user(
    user_data: UserCreate,
    request: Request,
    admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """Create a new user account (Admin only)."""
    # Check email uniqueness
    existing_email = await db.execute(select(User).where(User.email == user_data.email))
    if existing_email.scalar_one_or_none():
        raise HTTPException(status_code=409, detail="Email already registered")

    existing_username = await db.execute(select(User).where(User.username == user_data.username))
    if existing_username.scalar_one_or_none():
        raise HTTPException(status_code=409, detail="Username already taken")

    user = User(
        email=user_data.email,
        username=user_data.username,
        full_name=user_data.full_name,
        hashed_password=hasher.hash(user_data.password),
        role=user_data.role,
        badge_number=user_data.badge_number,
        phone=user_data.phone,
    )
    db.add(user)
    await db.commit()
    await db.refresh(user)

    audit = AuditLog(
        user_id=admin.id,
        action="CREATE_USER",
        resource_type="user",
        resource_id=str(user.id),
        details={"email": user.email, "role": user.role.value},
        ip_address=request.client.host if request.client else None,
    )
    db.add(audit)
    await db.commit()

    return user


@router.get("", response_model=List[UserResponse])
async def list_users(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    role: Optional[UserRole] = None,
    is_active: Optional[bool] = None,
    search: Optional[str] = None,
    admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """List all users (Admin only)."""
    query = select(User)
    if role:
        query = query.where(User.role == role)
    if is_active is not None:
        query = query.where(User.is_active == is_active)
    if search:
        from sqlalchemy import or_
        query = query.where(
            or_(
                User.full_name.ilike(f"%{search}%"),
                User.email.ilike(f"%{search}%"),
                User.username.ilike(f"%{search}%"),
            )
        )
    offset = (page - 1) * page_size
    query = query.offset(offset).limit(page_size).order_by(User.created_at.desc())
    result = await db.execute(query)
    return result.scalars().all()


@router.get("/{user_id}", response_model=UserResponse)
async def get_user(
    user_id: int,
    admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """Get user by ID (Admin only)."""
    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    return user


@router.patch("/{user_id}", response_model=UserResponse)
async def update_user(
    user_id: int,
    user_data: UserUpdate,
    request: Request,
    admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """Update user details (Admin only)."""
    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    update_data = user_data.model_dump(exclude_unset=True)
    for key, value in update_data.items():
        setattr(user, key, value)

    audit = AuditLog(
        user_id=admin.id,
        action="UPDATE_USER",
        resource_type="user",
        resource_id=str(user_id),
        details=update_data,
        ip_address=request.client.host if request.client else None,
    )
    db.add(audit)
    await db.commit()
    await db.refresh(user)
    return user


@router.delete("/{user_id}")
async def deactivate_user(
    user_id: int,
    request: Request,
    admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """Deactivate user account (Admin only). Soft delete."""
    if user_id == admin.id:
        raise HTTPException(status_code=400, detail="Cannot deactivate your own account")

    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    user.is_active = False
    audit = AuditLog(
        user_id=admin.id,
        action="DEACTIVATE_USER",
        resource_type="user",
        resource_id=str(user_id),
        ip_address=request.client.host if request.client else None,
    )
    db.add(audit)
    await db.commit()
    return {"message": f"User {user_id} deactivated successfully"}
