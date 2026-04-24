"""
V-Watch Backend - Violation Pydantic Schemas
"""

from pydantic import BaseModel, Field
from typing import Optional, Dict, Any, List
from datetime import datetime
from ..models.violation import ViolationType, ViolationStatus


class ViolationCreate(BaseModel):
    evidence_id: str
    vehicle_id: str
    plate_number: str
    vehicle_type: Optional[str] = None
    violation_type: ViolationType
    speed_recorded: Optional[float] = None
    speed_limit: Optional[float] = None
    location: str
    camera_id: str
    violation_time: datetime
    confidence: float = 0.0
    frame_sha256: Optional[str] = None
    plate_sha256: Optional[str] = None
    video_sha256: Optional[str] = None
    metadata_sha256: Optional[str] = None
    extra_data: Optional[Dict[str, Any]] = None

    # Aliases for edge AI metadata format
    class Config:
        populate_by_name = True


class ViolationReview(BaseModel):
    status: ViolationStatus
    remarks: Optional[str] = None
    fine_amount: Optional[float] = None


class ViolationResponse(BaseModel):
    id: int
    evidence_id: str
    vehicle_id: str
    plate_number: str
    vehicle_type: Optional[str]
    violation_type: ViolationType
    status: ViolationStatus
    speed_recorded: Optional[float]
    speed_limit: Optional[float]
    location: str
    camera_id: str
    violation_time: datetime
    created_at: datetime
    frame_image_url: Optional[str]
    plate_image_url: Optional[str]
    video_clip_url: Optional[str]
    frame_sha256: Optional[str]
    plate_sha256: Optional[str]
    confidence: float
    reviewer_remarks: Optional[str]
    reviewed_at: Optional[datetime]
    fine_amount: Optional[float]
    fine_paid: bool

    class Config:
        from_attributes = True


class ViolationListResponse(BaseModel):
    items: List[ViolationResponse]
    total: int
    page: int
    page_size: int
    total_pages: int


class ViolationStats(BaseModel):
    total_violations: int
    pending_count: int
    approved_count: int
    rejected_count: int
    paid_count: int
    today_count: int
    total_fines_collected: float
    violations_by_type: Dict[str, int]
    violations_by_day: List[Dict[str, Any]]


class VehicleCreate(BaseModel):
    plate_number: str
    vehicle_type: Optional[str] = None
    make: Optional[str] = None
    model: Optional[str] = None
    color: Optional[str] = None
    year: Optional[int] = None
    owner_name: Optional[str] = None
    owner_email: Optional[str] = None
    owner_phone: Optional[str] = None


class VehicleResponse(BaseModel):
    id: int
    plate_number: str
    vehicle_type: Optional[str]
    make: Optional[str]
    model: Optional[str]
    owner_name: Optional[str]
    owner_phone: Optional[str]
    is_stolen: bool
    is_blacklisted: bool
    created_at: datetime

    class Config:
        from_attributes = True


class SystemConfigUpdate(BaseModel):
    key: str
    value: str
    description: Optional[str] = None
