from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class Employee(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    email: str
    department: str
    is_admin: bool
    is_active: bool


class HealthResponse(BaseModel):
    status: str
    service: str
    agent_ready: bool = False


class CheckInRequest(BaseModel):
    employee_id: int = Field(gt=0)
    work_mode: Literal["OFFICE", "WFH"] = "OFFICE"
    at: datetime | None = None


class CheckOutRequest(BaseModel):
    employee_id: int = Field(gt=0)
    break_minutes: int = Field(default=0, ge=0, le=240)
    at: datetime | None = None


class AttendanceRecord(BaseModel):
    id: int
    employee_id: int
    employee_name: str
    work_date: str
    work_mode: Literal["OFFICE", "WFH"]
    check_in: datetime
    check_out: datetime | None
    break_minutes: int
    worked_minutes: int | None
    worked_hours: float | None
    state: Literal["CHECKED_IN", "COMPLETED"]


class LeaveRequest(BaseModel):
    employee_id: int = Field(gt=0)
    start_date: date
    end_date: date
    leave_type: Literal["CASUAL", "SICK", "OTHER"] = "CASUAL"
    reason: str = Field(min_length=3, max_length=300)


class ChatTurn(BaseModel):
    role: Literal["user", "assistant"]
    content: str


class AgentChatRequest(BaseModel):
    employee_id: int = Field(gt=0)
    message: str = Field(min_length=1, max_length=1000)
    history: list[ChatTurn] = Field(default_factory=list)


class AgentChatResponse(BaseModel):
    employee_id: int
    reply: str


class LeaveRecord(BaseModel):
    id: int
    employee_id: int
    employee_name: str
    start_date: date
    end_date: date
    leave_type: Literal["CASUAL", "SICK", "OTHER"]
    reason: str
    status: Literal["PENDING", "APPROVED", "REJECTED", "CANCELLED"]
    manager_id: int | None
    decision_comment: str | None
    decided_at: datetime | None
    created_at: datetime
    updated_at: datetime


class LeaveDecisionRequest(BaseModel):
    manager_id: int = Field(gt=0)
    decision: Literal["APPROVED", "REJECTED"]
    comment: str = Field(default="", max_length=300)
