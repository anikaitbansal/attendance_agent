from contextlib import asynccontextmanager
from datetime import date

from fastapi import FastAPI

from app.agent import agent_is_configured, run_agent
from app.agent_service import run_agent as run_fallback_agent
from app.attendance_service import check_in, check_out, get_today
from app.database import initialise_database, list_employees
from app.leave_service import (
    cancel_leave,
    create_leave,
    decide_leave,
    list_leaves,
    list_pending_leaves,
)
from app.query_service import (
    get_attendance_history,
    get_missing_checkouts,
    get_weekly_hours,
)
from app.notification_service import list_notifications, mark_all_read
from app.reminder_service import run_reminder_now, start_scheduler
from app.schemas import (
    AgentChatRequest,
    AgentChatResponse,
    AttendanceRecord,
    CheckInRequest,
    CheckOutRequest,
    Employee,
    HealthResponse,
    LeaveRecord,
    LeaveDecisionRequest,
    LeaveRequest,
    WeeklyHoursSummary,
    MarkReadResponse,
    NotificationRecord,
    ReminderRunRequest,
    ReminderRunResponse,
)


@asynccontextmanager
async def lifespan(_: FastAPI):
    initialise_database()
    scheduler = start_scheduler()
    yield
    if scheduler is not None:
        scheduler.shutdown(wait=False)


app = FastAPI(
    title="AI Attendance Agent API",
    version="0.1.0",
    description="Zero-cost hackathon MVP using fictional employee data.",
    lifespan=lifespan,
)


@app.get("/health", response_model=HealthResponse)
def health_check() -> HealthResponse:
    return HealthResponse(
        status="ok",
        service="attendance-agent",
        agent_ready=agent_is_configured(),
    )


@app.get("/employees", response_model=list[Employee])
def get_employees() -> list[dict]:
    return list_employees()


@app.post("/attendance/check-in", response_model=AttendanceRecord, status_code=201)
def mark_check_in(request: CheckInRequest) -> dict:
    return check_in(request.employee_id, request.work_mode, request.at)


@app.post("/attendance/check-out", response_model=AttendanceRecord)
def mark_check_out(request: CheckOutRequest) -> dict:
    return check_out(request.employee_id, request.break_minutes, request.at)


@app.get("/attendance/{employee_id}/today", response_model=AttendanceRecord)
def get_employee_attendance_today(employee_id: int) -> dict:
    return get_today(employee_id)


@app.post("/leaves", response_model=LeaveRecord, status_code=201)
def request_leave(request: LeaveRequest) -> dict:
    return create_leave(
        employee_id=request.employee_id,
        start_date=request.start_date,
        end_date=request.end_date,
        leave_type=request.leave_type,
        reason=request.reason,
    )


@app.get("/leaves/{employee_id}", response_model=list[LeaveRecord])
def get_employee_leaves(employee_id: int) -> list[dict]:
    return list_leaves(employee_id)


@app.delete("/leaves/{leave_id}", response_model=LeaveRecord)
def delete_leave(leave_id: int) -> dict:
    return cancel_leave(leave_id)


@app.get("/admin/leaves/pending", response_model=list[LeaveRecord])
def get_pending_leaves(manager_id: int) -> list[dict]:
    return list_pending_leaves(manager_id)


@app.patch("/admin/leaves/{leave_id}/decision", response_model=LeaveRecord)
def make_leave_decision(leave_id: int, request: LeaveDecisionRequest) -> dict:
    return decide_leave(
        leave_id=leave_id,
        manager_id=request.manager_id,
        decision=request.decision,
        comment=request.comment,
    )


@app.post("/agent/chat", response_model=AgentChatResponse)
def chat_with_agent(request: AgentChatRequest) -> AgentChatResponse:
    if not agent_is_configured():
        # Without a Groq key, the keyword router in agent_service still handles
        # simple requests so the demo chat never goes dark.
        result = run_fallback_agent(
            request.employee_id,
            request.message,
            manager_id=request.employee_id,
        )
        return AgentChatResponse(
            employee_id=request.employee_id,
            reply=result["reply"],
            tools_used=[result["tool_used"]] if result["tool_used"] else [],
            interpretation_source="fallback",
        )

    result = run_agent(
        message=request.message,
        employee_id=request.employee_id,
        history=[turn.model_dump() for turn in request.history],
    )
    return AgentChatResponse(
        employee_id=request.employee_id,
        reply=result["reply"],
        tools_used=result["tools_used"],
        interpretation_source="groq",
    )


@app.get("/notifications/{employee_id}", response_model=list[NotificationRecord])
def get_notifications(employee_id: int, unread_only: bool = False) -> list[dict]:
    return list_notifications(employee_id, unread_only)


@app.post("/notifications/{employee_id}/read", response_model=MarkReadResponse)
def read_notifications(employee_id: int) -> MarkReadResponse:
    return MarkReadResponse(employee_id=employee_id, updated=mark_all_read(employee_id))


@app.post("/admin/reminders/run", response_model=ReminderRunResponse)
def trigger_reminders(request: ReminderRunRequest) -> ReminderRunResponse:
    notified = run_reminder_now(request.kind, request.manager_id)
    return ReminderRunResponse(kind=request.kind, notified=notified)
