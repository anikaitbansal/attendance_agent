from __future__ import annotations

import os
import re
from datetime import date, datetime, timedelta
from typing import Literal, TypedDict
from zoneinfo import ZoneInfo

from dotenv import load_dotenv
from fastapi import HTTPException
from langchain_groq import ChatGroq
from langgraph.graph import END, StateGraph
from pydantic import BaseModel, Field

from app.attendance_service import get_today
from app.leave_service import create_leave, list_leaves
from app.query_service import (
    get_attendance_history,
    get_missing_checkouts,
    get_weekly_hours,
)


load_dotenv()
IST = ZoneInfo("Asia/Kolkata")

IntentName = Literal[
    "weekly_hours",
    "today_status",
    "attendance_history",
    "leave_history",
    "create_leave",
    "missing_checkout",
    "unknown",
]


class IntentDecision(BaseModel):
    intent: IntentName
    start_date: date | None = None
    end_date: date | None = None
    week_start: date | None = None
    work_date: date | None = None
    leave_type: Literal["CASUAL", "SICK", "OTHER"] = "CASUAL"
    reason: str = Field(default="Personal leave", max_length=300)


class AgentState(TypedDict, total=False):
    employee_id: int
    manager_id: int | None
    message: str
    decision: dict
    reply: str
    tool_used: str | None
    interpretation_source: Literal["groq", "fallback"]


def _extract_iso_dates(message: str) -> list[date]:
    values: list[date] = []
    for raw in re.findall(r"\b\d{4}-\d{2}-\d{2}\b", message):
        try:
            values.append(date.fromisoformat(raw))
        except ValueError:
            continue
    return values


def _fallback_decision(message: str) -> IntentDecision:
    """Keep the demo usable when Groq is unavailable or unconfigured."""
    lowered = message.lower()
    dates = _extract_iso_dates(message)
    today = datetime.now(IST).date()

    if "tomorrow" in lowered and not dates:
        dates = [today + timedelta(days=1)]

    if any(term in lowered for term in ("missing checkout", "not checked out")):
        return IntentDecision(
            intent="missing_checkout",
            work_date=dates[0] if dates else today,
        )
    if "week" in lowered and any(term in lowered for term in ("hour", "worked")):
        return IntentDecision(intent="weekly_hours", week_start=dates[0] if dates else None)
    if "attendance history" in lowered or "attendance record" in lowered:
        return IntentDecision(
            intent="attendance_history",
            start_date=dates[0] if dates else None,
            end_date=dates[1] if len(dates) > 1 else None,
        )
    if "leave history" in lowered or "leave status" in lowered:
        return IntentDecision(intent="leave_history")
    if "leave" in lowered and any(
        term in lowered for term in ("apply", "request", "taking", "on leave")
    ):
        leave_type = "SICK" if "sick" in lowered else "CASUAL"
        return IntentDecision(
            intent="create_leave",
            start_date=dates[0] if dates else None,
            end_date=dates[1] if len(dates) > 1 else (dates[0] if dates else None),
            leave_type=leave_type,
            reason=message.strip(),
        )
    if any(
        term in lowered
        for term in ("today", "checked in", "check-in status", "attendance status")
    ):
        return IntentDecision(intent="today_status")
    return IntentDecision(intent="unknown")


def _groq_decision(message: str) -> IntentDecision:
    api_key = os.getenv("GROQ_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError("GROQ_API_KEY is not configured")

    today = datetime.now(IST).date().isoformat()
    model = os.getenv("GROQ_MODEL", "llama-3.1-8b-instant")
    llm = ChatGroq(model=model, temperature=0, api_key=api_key)
    structured_llm = llm.with_structured_output(IntentDecision)
    return structured_llm.invoke(
        f"""
You route requests for an employee attendance and leave agent.
Today's date in India is {today}.

Choose exactly one intent:
- weekly_hours: hours worked during a week
- today_status: whether the employee is checked in today
- attendance_history: attendance records over dates
- leave_history: past and current leave requests
- create_leave: submit a leave request
- missing_checkout: manager report for employees who did not check out
- unknown: anything outside these capabilities

Resolve words such as today and tomorrow into ISO dates. For create_leave,
extract start_date, end_date, leave_type and a short reason. A one-day leave has
the same start_date and end_date. Do not invent a date if none was provided.

User message: {message}
""".strip()
    )


def classify_request(state: AgentState) -> AgentState:
    try:
        decision = _groq_decision(state["message"])
        source: Literal["groq", "fallback"] = "groq"
    except Exception:
        decision = _fallback_decision(state["message"])
        source = "fallback"

    return {
        "decision": decision.model_dump(mode="json"),
        "interpretation_source": source,
    }


def _format_today(record: dict) -> str:
    if record["state"] == "COMPLETED":
        return (
            f"{record['employee_name']} completed today's attendance with "
            f"{record['worked_hours']} worked hours."
        )
    return (
        f"{record['employee_name']} is currently checked in from "
        f"{record['check_in']}."
    )


def execute_tool(state: AgentState) -> AgentState:
    decision = IntentDecision.model_validate(state["decision"])
    employee_id = state["employee_id"]
    manager_id = state.get("manager_id")
    tool_used: str | None = decision.intent

    try:
        if decision.intent == "weekly_hours":
            result = get_weekly_hours(employee_id, decision.week_start)
            reply = (
                f"{result['employee_name']} worked {result['total_worked_hours']} "
                f"hours from {result['week_start']} to {result['week_end']}. "
                f"Completed days: {result['completed_days']}; open sessions: "
                f"{result['open_sessions']}."
            )
        elif decision.intent == "today_status":
            reply = _format_today(get_today(employee_id))
        elif decision.intent == "attendance_history":
            records = get_attendance_history(
                employee_id,
                decision.start_date,
                decision.end_date,
            )
            if not records:
                reply = "No attendance records were found for the requested period."
            else:
                completed = sum(record["state"] == "COMPLETED" for record in records)
                reply = (
                    f"I found {len(records)} attendance record(s): {completed} "
                    "completed and "
                    f"{len(records) - completed} still open."
                )
        elif decision.intent == "leave_history":
            leaves = list_leaves(employee_id)
            if not leaves:
                reply = "No leave requests were found for this employee."
            else:
                latest = leaves[0]
                reply = (
                    f"The latest leave request is {latest['status']} for "
                    f"{latest['start_date']} to {latest['end_date']}. "
                    f"Total leave records: {len(leaves)}."
                )
        elif decision.intent == "create_leave":
            if decision.start_date is None or decision.end_date is None:
                reply = (
                    "Please provide the leave date. For example: "
                    "Apply for casual leave on 2026-09-23."
                )
                tool_used = None
            else:
                leave = create_leave(
                    employee_id=employee_id,
                    start_date=decision.start_date,
                    end_date=decision.end_date,
                    leave_type=decision.leave_type,
                    reason=decision.reason,
                )
                reply = (
                    f"Leave request {leave['id']} was submitted for "
                    f"{leave['start_date']} to {leave['end_date']}. "
                    "Its status is PENDING manager approval."
                )
        elif decision.intent == "missing_checkout":
            if manager_id is None:
                reply = "A manager identity is required for the missing-checkout report."
                tool_used = None
            else:
                records = get_missing_checkouts(manager_id, decision.work_date)
                if not records:
                    reply = "No missing checkouts were found for the requested date."
                else:
                    names = ", ".join(record["employee_name"] for record in records)
                    reply = f"Missing checkout: {names}."
        else:
            tool_used = None
            reply = (
                "I can help with weekly hours, today's attendance, attendance "
                "history, leave requests, leave status and missing checkouts."
            )
    except HTTPException as exc:
        reply = str(exc.detail)

    return {"reply": reply, "tool_used": tool_used}


def build_agent():
    workflow = StateGraph(AgentState)
    workflow.add_node("classify_request", classify_request)
    workflow.add_node("execute_tool", execute_tool)
    workflow.set_entry_point("classify_request")
    workflow.add_edge("classify_request", "execute_tool")
    workflow.add_edge("execute_tool", END)
    return workflow.compile()


attendance_agent = build_agent()


def run_agent(employee_id: int, message: str, manager_id: int | None = None) -> dict:
    result = attendance_agent.invoke(
        {
            "employee_id": employee_id,
            "manager_id": manager_id,
            "message": message,
        }
    )
    return {
        "reply": result["reply"],
        "intent": result["decision"]["intent"],
        "tool_used": result.get("tool_used"),
        "interpretation_source": result["interpretation_source"],
    }
