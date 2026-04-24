"""
V-Watch Backend - Violation Database Model
"""

from sqlalchemy import (
    Column, Integer, String, Float, DateTime, Enum as SAEnum,
    Text, Boolean, ForeignKey, JSON
)
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
from enum import Enum
from ..core.database import Base


class ViolationType(str, Enum):
    SPEEDING = "SPEEDING"
    RED_LIGHT = "RED_LIGHT"
    WRONG_DIRECTION = "WRONG_DIRECTION"
    LANE_VIOLATION = "LANE_VIOLATION"
    NO_HELMET = "NO_HELMET"
    NO_SEATBELT = "NO_SEATBELT"
    PARKING = "PARKING"


class ViolationStatus(str, Enum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    APPEALED = "appealed"
    PAID = "paid"


class Violation(Base):
    __tablename__ = "violations"

    id = Column(Integer, primary_key=True, index=True)
    evidence_id = Column(String(36), unique=True, nullable=False, index=True)

    # Vehicle info
    vehicle_id = Column(String(50), index=True)
    plate_number = Column(String(20), nullable=False, index=True)
    vehicle_type = Column(String(50), nullable=True)

    # Violation details
    violation_type = Column(SAEnum(ViolationType), nullable=False, index=True)
    status = Column(SAEnum(ViolationStatus), default=ViolationStatus.PENDING, index=True)
    speed_recorded = Column(Float, nullable=True)  # km/h
    speed_limit = Column(Float, nullable=True)     # km/h
    location = Column(String(500), nullable=False)
    camera_id = Column(String(50), nullable=False)

    # Timestamps
    violation_time = Column(DateTime(timezone=True), nullable=False, index=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())

    # Evidence files
    frame_image_url = Column(String(1000), nullable=True)
    plate_image_url = Column(String(1000), nullable=True)
    video_clip_url = Column(String(1000), nullable=True)

    # Cryptographic integrity
    frame_sha256 = Column(String(64), nullable=True)
    plate_sha256 = Column(String(64), nullable=True)
    video_sha256 = Column(String(64), nullable=True)
    metadata_sha256 = Column(String(64), nullable=True)

    # AI confidence score
    confidence = Column(Float, default=0.0)

    # Review workflow
    reviewer_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    reviewer_remarks = Column(Text, nullable=True)
    reviewed_at = Column(DateTime(timezone=True), nullable=True)

    # Fine
    fine_amount = Column(Float, nullable=True)
    fine_paid = Column(Boolean, default=False)
    fine_paid_at = Column(DateTime(timezone=True), nullable=True)

    # Extra metadata JSON
    extra_data = Column(JSON, nullable=True)

    # Relationships
    reviewer = relationship("User", back_populates="approved_violations", foreign_keys=[reviewer_id])
    vehicle = relationship("Vehicle", back_populates="violations", primaryjoin="Violation.plate_number == Vehicle.plate_number", foreign_keys="Violation.plate_number")


class Vehicle(Base):
    __tablename__ = "vehicles"

    id = Column(Integer, primary_key=True, index=True)
    plate_number = Column(String(20), unique=True, nullable=False, index=True)
    vehicle_type = Column(String(50), nullable=True)
    make = Column(String(100), nullable=True)
    model = Column(String(100), nullable=True)
    color = Column(String(50), nullable=True)
    year = Column(Integer, nullable=True)

    # Owner info
    owner_name = Column(String(255), nullable=True)
    owner_email = Column(String(255), nullable=True)
    owner_phone = Column(String(20), nullable=True)
    owner_address = Column(Text, nullable=True)

    # Registration
    registration_number = Column(String(100), nullable=True)
    registration_expiry = Column(DateTime(timezone=True), nullable=True)

    is_stolen = Column(Boolean, default=False)
    is_blacklisted = Column(Boolean, default=False)

    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())

    # Relationships
    violations = relationship(
        "Violation",
        back_populates="vehicle",
        primaryjoin="Vehicle.plate_number == foreign(Violation.plate_number)",
    )


class AuditLog(Base):
    __tablename__ = "audit_logs"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    action = Column(String(100), nullable=False)
    resource_type = Column(String(50), nullable=True)
    resource_id = Column(String(50), nullable=True)
    details = Column(JSON, nullable=True)
    ip_address = Column(String(45), nullable=True)
    user_agent = Column(String(500), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), index=True)

    user = relationship("User", back_populates="audit_logs")


class SystemConfig(Base):
    __tablename__ = "system_config"

    id = Column(Integer, primary_key=True, index=True)
    key = Column(String(100), unique=True, nullable=False, index=True)
    value = Column(Text, nullable=True)
    description = Column(Text, nullable=True)
    updated_by = Column(Integer, ForeignKey("users.id"), nullable=True)
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())
