from typing import List, Optional
from datetime import datetime
from pydantic import BaseModel, EmailStr, Field, field_validator

OUR_DATETIME_FORMAT = "%Y-%m-%dT%H:%M:%S"


class BaseRequest(BaseModel):
    """Base schema with common fields for authentication and validation"""
    timestamp: str
    salt: str
    hash: str

    @field_validator('timestamp')
    def validate_timestamp_format(cls, v, info):
        try:
            # Validate format but return original string
            datetime.strptime(v, OUR_DATETIME_FORMAT)
            return v
        except ValueError:
            raise ValueError("Invalid timestamp format, must be " + OUR_DATETIME_FORMAT)

    def parsed_timestamp(self) -> datetime:
        """Return the timestamp as a datetime object when needed"""
        return datetime.strptime(self.timestamp, OUR_DATETIME_FORMAT)


class UserCreate(BaseRequest):
    """Schema for user creation requests"""
    name: str = Field(..., min_length=3, max_length=100)
    email: EmailStr


class ApiKeyRotation(BaseRequest):
    """Schema for API key rotation requests"""
    user_id: int = Field(..., gt=0)
    key: str
    key_salt: str


class PaymentMethodCheck(BaseRequest):
    """Schema for payment method validation requests"""
    user_id: int = Field(..., gt=0)
    partner_id: int = Field(..., gt=0)
    key: str
    key_salt: str


class BillLineItem(BaseModel):
    """Schema for individual line items in a bill"""
    sku: str
    quantity: float = Field(..., gt=0)
    price_unit: float = Field(..., gt=0)

    @field_validator('sku')
    def validate_sku(cls, v, info):
        # You can add specific SKU validation rules here
        if not v or len(v) < 3:
            raise ValueError("SKU must be at least 3 characters")
        return v


class BillCreate(BaseRequest):
    """Schema for bill creation requests"""
    lines_data: List[BillLineItem]
    key: str
    key_salt: str
    session_start: str
    session_end: str
    user_id: int = Field(..., gt=0)
    partner_id: int = Field(..., gt=0)

    @field_validator('session_start', 'session_end')
    def validate_session_timestamps(cls, v, info):
        try:
            datetime.strptime(v, OUR_DATETIME_FORMAT)
            return v
        except ValueError:
            raise ValueError("Invalid timestamp format, must be " + OUR_DATETIME_FORMAT)

    @field_validator('session_end')
    def validate_session_end_after_start(cls, v, info):
        session_start = info.data.get('session_start')
        if session_start:
            start = datetime.strptime(session_start, OUR_DATETIME_FORMAT)
            end = datetime.strptime(v, OUR_DATETIME_FORMAT)
            if end <= start:
                raise ValueError("Session end must be after session start")
        return v

    def parsed_session_start(self) -> datetime:
        """Return session_start as datetime object"""
        return datetime.strptime(self.session_start, OUR_DATETIME_FORMAT)

    def parsed_session_end(self) -> datetime:
        """Return session_end as datetime object"""
        return datetime.strptime(self.session_end, OUR_DATETIME_FORMAT)


class PortalLogin(BaseRequest):
    """Schema for portal auto-login requests"""
    key: str
    key_salt: str
