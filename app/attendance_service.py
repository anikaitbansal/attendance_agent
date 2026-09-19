from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from fastapi import HTTPException, status

from app.database import employee_exists, get_connection
from app.leave_service import get_active_leave_for_date


IST = ZoneInfo("Asia/Kolkata")


def _normalise_time(value: datetime | None) -> datetime:
    """Return an Asia/Kolkata-aware timestamp for real or simulated events."""
    if value is None:
        return datetime.now(IST)
    if value.tzinfo is None:
        return value.replace(tzinfo=IST)
    return value.astimezone(IST)


def _serialize_record(row) -> dict:
    record = dict(row)
    worked_minutes = record["worked_minutes"]
    record["worked_hours"] = (
        round(worked_minutes / 60, 2) if worked_minutes is not None else None
    )
    record["state"] = "COMPLETED" if record["check_out"] else "CHECKED_IN"
    return record


def _get_record(employee_id: int, work_date: str):
    with get_connection() as connection:
        return connection.execute(
            """
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
            WHERE a.employee_id = ? AND a.work_date = ?
            """,
            (employee_id, work_date),
        ).fetchone()


def check_in(employee_id: int, work_mode: str, at: datetime | None = None) -> dict:
    event_time = _normalise_time(at)
    work_date = event_time.date().isoformat()

    if not employee_exists(employee_id):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Active employee not found.",
        )

    active_leave = get_active_leave_for_date(employee_id, work_date)
    if active_leave:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                "Check-in is blocked because the employee is on approved "
                f"{active_leave['leave_type'].lower()} leave."
            ),
        )

    existing = _get_record(employee_id, work_date)
    if existing:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Attendance has already been marked for this employee today.",
        )

    timestamp = event_time.isoformat(timespec="seconds")
    with get_connection() as connection:
        connection.execute(
            """
            INSERT INTO attendance (
                employee_id, work_date, work_mode, check_in, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            (employee_id, work_date, work_mode, timestamp, timestamp, timestamp),
        )

    return _serialize_record(_get_record(employee_id, work_date))


def check_out(
    employee_id: int,
    break_minutes: int = 0,
    at: datetime | None = None,
) -> dict:
    event_time = _normalise_time(at)
    work_date = event_time.date().isoformat()
    existing = _get_record(employee_id, work_date)

    if existing is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No check-in was found for this employee today.",
        )
    if existing["check_out"] is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="This employee has already checked out today.",
        )

    check_in_time = datetime.fromisoformat(existing["check_in"])
    elapsed_minutes = int((event_time - check_in_time).total_seconds() // 60)
    if elapsed_minutes < 0:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Checkout time cannot be earlier than check-in time.",
        )
    if break_minutes > elapsed_minutes:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Break duration cannot exceed the total elapsed time.",
        )

    worked_minutes = elapsed_minutes - break_minutes
    checkout_timestamp = event_time.isoformat(timespec="seconds")
    with get_connection() as connection:
        connection.execute(
            """
            UPDATE attendance
            SET check_out = ?, break_minutes = ?, worked_minutes = ?, updated_at = ?
            WHERE employee_id = ? AND work_date = ?
            """,
            (
                checkout_timestamp,
                break_minutes,
                worked_minutes,
                checkout_timestamp,
                employee_id,
                work_date,
            ),
        )

    return _serialize_record(_get_record(employee_id, work_date))


def get_today(employee_id: int) -> dict:
    if not employee_exists(employee_id):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Active employee not found.",
        )

    work_date = datetime.now(IST).date().isoformat()
    record = _get_record(employee_id, work_date)
    if record is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No attendance record exists for this employee today.",
        )
    return _serialize_record(record)
