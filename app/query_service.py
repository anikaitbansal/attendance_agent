from __future__ import annotations

from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from fastapi import HTTPException, status

from app.database import employee_exists, employee_is_admin, get_connection


IST = ZoneInfo("Asia/Kolkata")


def _serialize_attendance(row) -> dict:
    record = dict(row)
    worked_minutes = record["worked_minutes"]
    record["worked_hours"] = (
        round(worked_minutes / 60, 2) if worked_minutes is not None else None
    )
    record["state"] = "COMPLETED" if record["check_out"] else "CHECKED_IN"
    return record


def _attendance_query() -> str:
    return """
        SELECT
            a.id,
            a.employee_id,
            e.name AS employee_name,
            a.work_date,
            a.work_mode,
            a.check_in,
            a.check_out,
            a.break_minutes,
            a.worked_minutes
        FROM attendance AS a
        JOIN employees AS e ON e.id = a.employee_id
    """


def get_attendance_history(
    employee_id: int,
    start_date: date | None = None,
    end_date: date | None = None,
) -> list[dict]:
    if not employee_exists(employee_id):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Active employee not found.",
        )

    effective_end = end_date or datetime.now(IST).date()
    effective_start = start_date or (effective_end - timedelta(days=29))
    if effective_end < effective_start:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="End date cannot be earlier than start date.",
        )

    with get_connection() as connection:
        rows = connection.execute(
            _attendance_query()
            + """
              WHERE a.employee_id = ? AND a.work_date BETWEEN ? AND ?
              ORDER BY a.work_date DESC, a.check_in DESC
            """,
            (employee_id, effective_start.isoformat(), effective_end.isoformat()),
        ).fetchall()
    return [_serialize_attendance(row) for row in rows]


def get_weekly_hours(employee_id: int, week_start: date | None = None) -> dict:
    if not employee_exists(employee_id):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Active employee not found.",
        )

    today = datetime.now(IST).date()
    effective_start = week_start or (today - timedelta(days=today.weekday()))
    effective_end = effective_start + timedelta(days=6)
    records = get_attendance_history(employee_id, effective_start, effective_end)

    total_minutes = sum(record["worked_minutes"] or 0 for record in records)
    completed_days = sum(record["state"] == "COMPLETED" for record in records)
    open_sessions = sum(record["state"] == "CHECKED_IN" for record in records)

    with get_connection() as connection:
        employee = connection.execute(
            "SELECT name FROM employees WHERE id = ?",
            (employee_id,),
        ).fetchone()

    return {
        "employee_id": employee_id,
        "employee_name": employee["name"],
        "week_start": effective_start.isoformat(),
        "week_end": effective_end.isoformat(),
        "total_worked_minutes": total_minutes,
        "total_worked_hours": round(total_minutes / 60, 2),
        "completed_days": completed_days,
        "open_sessions": open_sessions,
        "records": records,
    }


def get_missing_checkouts(
    manager_id: int,
    work_date: date | None = None,
) -> list[dict]:
    if not employee_is_admin(manager_id):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only an active manager or HR administrator can view this report.",
        )

    effective_date = work_date or datetime.now(IST).date()
    with get_connection() as connection:
        rows = connection.execute(
            _attendance_query()
            + """
              WHERE a.work_date = ? AND a.check_out IS NULL
              ORDER BY a.check_in ASC
            """,
            (effective_date.isoformat(),),
        ).fetchall()
    return [_serialize_attendance(row) for row in rows]
