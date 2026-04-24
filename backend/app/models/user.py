"""
V-Watch Backend - User Database Model
"""

from sqlalchemy import Column, Integer, String, Boolean, DateTime, Enum as SAEnum, Text
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
from enum import Enum
from ..core.database import Base


class UserRole(str, Enum):
    ADMIN = "admin"
    TRAFFIC_POLICE = "traffic_police"
    VIEWER = "viewer"


class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, index=True)
    email = Column(String(255), unique=True, nullable=False, index=True)
    username = Column(String(100), unique=True, nullable=False, index=True)
    full_name = Column(String(255), nullable=False)
    hashed_password = Column(String(255), nullable=False)
    role = Column(SAEnum(UserRole), default=UserRole.TRAFFIC_POLICE, nullable=False)
    badge_number = Column(String(50), unique=True, nullable=True)
    phone = Column(String(20), nullable=True)
    is_active = Column(Boolean, default=True, nullable=False)
    is_superuser = Column(Boolean, default=False, nullable=False)
    last_login = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())

    # Relationships
    approved_violations = relationship(
        "Violation", back_populates="reviewer", foreign_keys="Violation.reviewer_id"
    )
    audit_logs = relationship("AuditLog", back_populates="user")
