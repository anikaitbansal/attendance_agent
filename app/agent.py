from __future__ import annotations

import os
from datetime import date, datetime
from functools import lru_cache
from typing import Any, Callable
from zoneinfo import ZoneInfo

from dotenv import load_dotenv
from fastapi import HTTPException
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from langchain_core.tools import tool
from langgraph.prebuilt import create_react_agent

from app.attendance_service import check_in, check_out, get_today
from app.database import list_employees
from app.leave_service import cancel_leave, create_leave, list_leaves


load_dotenv()

IST = ZoneInfo("Asia/Kolkata")
DEFAULT_MODEL = "llama-3.3-70b-versatile"

SYSTEM_PROMPT = """You are the AI Attendance Agent for a small company.

You help one employee at a time manage their own attendance and leave by
calling the tools available to you.

Context for this conversation:
- The employee you are helping has employee_id {employee_id}.
- Today's date in Asia/Kolkata is {today}.
- The current time in Asia/Kolkata is {now}.

Rules:
- Always pass {employee_id} as the employee_id argument unless the user is
  clearly asking about somebody else by name; in that case call
  lookup_employees first to resolve the name to an id.
- Resolve relative dates ("tomorrow", "next Monday") against today's date
  yourself, and pass tools an ISO date in YYYY-MM-DD form.
- Never invent attendance or leave data. If a tool reports an error, explain
  what went wrong in one short sentence and suggest the next step.
- Before cancelling a leave, make sure you know its leave id; call
  show_my_leaves if you need to find it.
- Keep replies to a couple of short sentences. This is a chat window, not a
  report. Mention worked hours rather than raw minutes when both are present.
"""


def _call(action: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
    """Run a service call and turn API errors into text the model can act on."""
    try:
        return action(*args, **kwargs)
    except HTTPException as error:
        return f"ERROR: {error.detail}"


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
) -> str:
    """Answer one chat turn on behalf of an employee."""
    if not agent_is_configured():
        raise HTTPException(
            status_code=503,
            detail=(
                "The agent needs a GROQ_API_KEY. Copy .env.example to .env and "
                "add a free key from https://console.groq.com/keys."
            ),
        )

    now = datetime.now(IST)
    prompt = SYSTEM_PROMPT.format(
        employee_id=employee_id,
        today=now.date().isoformat(),
        now=now.strftime("%H:%M"),
    )

    agent = build_agent(os.getenv("GROQ_MODEL", DEFAULT_MODEL))
    messages = [SystemMessage(content=prompt), *_to_messages(history)]
    messages.append(HumanMessage(content=message))

    try:
        result = agent.invoke({"messages": messages})
    except Exception as error:  # noqa: BLE001 - surfaced to the chat window
        raise HTTPException(
            status_code=502,
            detail=f"The language model call failed: {error}",
        ) from error

    return result["messages"][-1].content
