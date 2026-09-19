from __future__ import annotations

import os
from datetime import date, datetime, timedelta
from functools import lru_cache
from typing import Any, Callable
from zoneinfo import ZoneInfo

from dotenv import load_dotenv
from fastapi import HTTPException
from groq import RateLimitError
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from langchain_core.runnables import RunnableConfig
from langchain_core.tools import tool
from langgraph.prebuilt import create_react_agent

from app.attendance_service import check_in, check_out, get_today
from app.database import list_employees
from app.leave_service import cancel_leave, create_leave, list_leaves
from app.query_service import (
    get_attendance_history,
    get_missing_checkouts,
    get_weekly_hours,
)


load_dotenv()

IST = ZoneInfo("Asia/Kolkata")
DEFAULT_MODEL = "openai/gpt-oss-20b"

SYSTEM_PROMPT = """You are the AI Attendance Agent for a small company.

You help one employee at a time manage their own attendance and leave by
calling the tools available to you.

Context for this conversation:
- The employee you are helping has employee_id {employee_id}.
- Today is {weekday}, {today} (Asia/Kolkata). The time is {now}.
- The next 14 days are: {calendar}.

Rules:
- Always pass {employee_id} as the employee_id argument unless the user is
  clearly asking about somebody else by name; in that case call
  lookup_employees first to resolve the name to an id.
- Resolve relative dates ("tomorrow", "next Friday") by reading them off the
  list of the next 14 days above, and pass tools an ISO date in YYYY-MM-DD form.
- Never invent attendance or leave data. If a tool reports an error, explain
  what went wrong in one short sentence and suggest the next step.
- Before cancelling a leave, make sure you know its leave id; call
  show_my_leaves if you need to find it.
- For questions about hours worked or past attendance, use show_weekly_hours
  or show_attendance_history rather than guessing from today's record.
- Only suggest next steps the tools can actually do. There is one attendance
  record per day: after checking out, an employee cannot check in again that day.
- Keep replies to a couple of short sentences. This is a chat window, not a
  report. Mention worked hours rather than raw minutes when both are present.
"""


def _parse_date(value: str | None) -> date | None:
    """Parse an optional ISO date, raising ValueError on bad input."""
    return date.fromisoformat(value) if value else None


def _call(action: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
    """Run a service call and turn API errors into text the model can act on."""
    try:
        result = action(*args, **kwargs)
    except HTTPException as error:
        return f"ERROR: {error.detail}"
    # Groq rejects a tool message whose content is an empty list.
    return result if result != [] else "No records found."


@tool
def mark_check_in(employee_id: int, work_mode: str = "OFFICE") -> Any:
    """Check an employee in for today. work_mode is either OFFICE or WFH."""
    normalised = work_mode.strip().upper()
    if normalised not in {"OFFICE", "WFH"}:
        return "ERROR: work_mode must be either OFFICE or WFH."
    return _call(check_in, employee_id, normalised)


@tool
def mark_check_out(employee_id: int, break_minutes: int = 0) -> Any:
    """Check an employee out for today, subtracting any break minutes taken."""
    return _call(check_out, employee_id, break_minutes)


@tool
def show_today(employee_id: int) -> Any:
    """Show today's attendance record, including worked hours if checked out."""
    return _call(get_today, employee_id)


@tool
def request_leave(
    employee_id: int,
    start_date: str,
    end_date: str,
    reason: str,
    leave_type: str = "CASUAL",
) -> Any:
    """Request leave between two ISO dates. leave_type is CASUAL, SICK or OTHER.

    The leave is created as PENDING and needs a manager decision before it counts.
    """
    normalised = leave_type.strip().upper()
    if normalised not in {"CASUAL", "SICK", "OTHER"}:
        return "ERROR: leave_type must be CASUAL, SICK or OTHER."
    try:
        start = date.fromisoformat(start_date)
        end = date.fromisoformat(end_date)
    except ValueError:
        return "ERROR: dates must be in YYYY-MM-DD form."
    return _call(create_leave, employee_id, start, end, normalised, reason)


@tool
def show_my_leaves(employee_id: int) -> Any:
    """List every leave record for an employee, including cancelled ones."""
    return _call(list_leaves, employee_id)


@tool
def cancel_my_leave(leave_id: int) -> Any:
    """Cancel a pending or approved leave by its leave id."""
    return _call(cancel_leave, leave_id)


@tool
def show_weekly_hours(employee_id: int, week_start: str | None = None) -> Any:
    """Total hours worked in one Monday-to-Sunday week.

    week_start is that Monday as an ISO date; omit it for the current week.
    """
    try:
        start = _parse_date(week_start)
    except ValueError:
        return "ERROR: week_start must be in YYYY-MM-DD form."
    return _call(get_weekly_hours, employee_id, start)


@tool
def show_attendance_history(
    employee_id: int,
    start_date: str | None = None,
    end_date: str | None = None,
) -> Any:
    """List attendance records between two ISO dates. Defaults to the last 30 days."""
    try:
        start, end = _parse_date(start_date), _parse_date(end_date)
    except ValueError:
        return "ERROR: dates must be in YYYY-MM-DD form."
    return _call(get_attendance_history, employee_id, start, end)


@tool
def show_missing_checkouts(config: RunnableConfig, work_date: str | None = None) -> Any:
    """Managers only: staff who checked in on a date but never checked out.

    work_date is an ISO date; omit it for today.
    """
    # Permission comes from the signed-in employee, never from a model-chosen
    # id, so a prompt cannot talk the agent into running a manager report.
    manager_id = config["configurable"]["employee_id"]
    try:
        day = _parse_date(work_date)
    except ValueError:
        return "ERROR: work_date must be in YYYY-MM-DD form."
    return _call(get_missing_checkouts, manager_id, day)


@tool
def lookup_employees() -> Any:
    """List all employees with their ids, useful for resolving a name to an id."""
    return list_employees()


TOOLS = [
    mark_check_in,
    mark_check_out,
    show_today,
    request_leave,
    show_my_leaves,
    cancel_my_leave,
    show_weekly_hours,
    show_attendance_history,
    show_missing_checkouts,
    lookup_employees,
]


def agent_is_configured() -> bool:
    """Report whether a Groq key is present, so callers can degrade gracefully."""
    return bool(os.getenv("GROQ_API_KEY"))


@lru_cache(maxsize=1)
def build_agent(model_name: str):
    """Build the ReAct agent once and reuse it across requests."""
    from langchain_groq import ChatGroq

    model = ChatGroq(model=model_name, temperature=0)
    return create_react_agent(model, TOOLS)


def _to_messages(history: list[dict] | None) -> list:
    messages: list = []
    for turn in history or []:
        content = turn.get("content", "")
        if not content:
            continue
        if turn.get("role") == "assistant":
            messages.append(AIMessage(content=content))
        else:
            messages.append(HumanMessage(content=content))
    return messages


def run_agent(
    message: str,
    employee_id: int,
    history: list[dict] | None = None,
) -> dict:
    """Answer one chat turn on behalf of an employee.

    Returns the reply and the names of the tools called, in order.
    """
    if not agent_is_configured():
        raise HTTPException(
            status_code=503,
            detail=(
                "The agent needs a GROQ_API_KEY. Copy .env.example to .env and "
                "add a free key from https://console.groq.com/keys."
            ),
        )

    now = datetime.now(IST)
    today = now.date()
    # Small models get weekday arithmetic wrong, so hand them a calendar.
    calendar = ", ".join(
        f"{day:%a} {day.isoformat()}"
        for day in (today + timedelta(days=offset) for offset in range(1, 15))
    )
    prompt = SYSTEM_PROMPT.format(
        employee_id=employee_id,
        weekday=f"{today:%A}",
        today=today.isoformat(),
        now=now.strftime("%H:%M"),
        calendar=calendar,
    )

    agent = build_agent(os.getenv("GROQ_MODEL", DEFAULT_MODEL))
    messages = [SystemMessage(content=prompt), *_to_messages(history)]
    messages.append(HumanMessage(content=message))

    try:
        result = agent.invoke(
            {"messages": messages},
            config={"configurable": {"employee_id": employee_id}},
        )
    except RateLimitError as error:
        raise HTTPException(
            status_code=429,
            detail=(
                "The Groq free tier only allows a few requests per minute. "
                "Wait about 30 seconds and try again."
            ),
        ) from error
    except Exception as error:  # noqa: BLE001 - surfaced to the chat window
        raise HTTPException(
            status_code=502,
            detail=f"The language model call failed: {error}",
        ) from error

    new_messages = result["messages"][len(messages):]
    tools_used = [
        call["name"]
        for turn in new_messages
        if isinstance(turn, AIMessage)
        for call in turn.tool_calls
    ]
    return {"reply": result["messages"][-1].content, "tools_used": tools_used}
