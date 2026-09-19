from __future__ import annotations

from datetime import date, datetime
from zoneinfo import ZoneInfo

from fastapi import HTTPException, status

from app.database import employee_exists, employee_is_admin, get_connection
from app.notification_service import notify, notify_admins


IST = ZoneInfo("Asia/Kolkata")


def _get_leave(leave_id: int):
    with get_connection() as connection:
        return connection.execute(
            """
            SELECT
                l.id,
                l.employee_id,
                e.name AS employee_name,
                l.start_date,
                l.end_date,
                l.leave_type,
                l.reason,
                l.status,
                l.manager_id,
                l.decision_comment,
                l.decided_at,
                l.created_at,
                l.updated_at
            FROM leaves AS l
            JOIN employees AS e ON e.id = l.employee_id
            WHERE l.id = ?
            """,
            (leave_id,),
        ).fetchone()


def get_active_leave_for_date(employee_id: int, work_date: str):
    """Return an approved leave covering the supplied ISO date, if one exists."""
    with get_connection() as connection:
        return connection.execute(
            """
            SELECT id, start_date, end_date, leave_type, reason
            FROM leaves
            WHERE employee_id = ?
              AND status = 'APPROVED'
              AND ? BETWEEN start_date AND end_date
            LIMIT 1
            """,
            (employee_id, work_date),
        ).fetchone()


def create_leave(
    employee_id: int,
    start_date: date,
    end_date: date,
    leave_type: str,
    reason: str,
) -> dict:
    if not employee_exists(employee_id):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Active employee not found.",
        )
    if end_date < start_date:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Leave end date cannot be earlier than the start date.",
        )

    start_value = start_date.isoformat()
    end_value = end_date.isoformat()

    with get_connection() as connection:
        attendance_conflict = connection.execute(
            """
            SELECT work_date
            FROM attendance
            WHERE employee_id = ? AND work_date BETWEEN ? AND ?
            ORDER BY work_date
            LIMIT 1
            """,
            (employee_id, start_value, end_value),
        ).fetchone()
        if attendance_conflict:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=(
                    "Leave cannot be created because attendance already exists "
                    f"for {attendance_conflict['work_date']}."
                ),
            )

        overlapping_leave = connection.execute(
            """
            SELECT id
            FROM leaves
            WHERE employee_id = ?
              AND status IN ('PENDING', 'APPROVED')
              AND NOT (end_date < ? OR start_date > ?)
            LIMIT 1
            """,
            (employee_id, start_value, end_value),
        ).fetchone()
        if overlapping_leave:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="A pending or approved leave already overlaps the requested dates.",
            )

        timestamp = datetime.now(IST).isoformat(timespec="seconds")
        cursor = connection.execute(
            """
            INSERT INTO leaves (
                employee_id, start_date, end_date, leave_type, reason,
                status, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, 'PENDING', ?, ?)
            """,
            (
                employee_id,
                start_value,
                end_value,
                leave_type,
                reason.strip(),
                timestamp,
                timestamp,
            ),
        )
        leave_id = cursor.lastrowid

    leave = dict(_get_leave(leave_id))
    notify_admins(
        "LEAVE_REQUESTED",
        f"{leave['employee_name']} requested {leave_type.lower()} leave "
        f"from {start_value} to {end_value}.",
        dedupe_key=f"LEAVE_REQUESTED:{leave_id}",
    )
    return leave


def list_leaves(employee_id: int) -> list[dict]:
    if not employee_exists(employee_id):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Active employee not found.",
        )

    with get_connection() as connection:
        rows = connection.execute(
            """
            SELECT
                l.id,
                l.employee_id,
                e.name AS employee_name,
                l.start_date,
                l.end_date,
                l.leave_type,
                l.reason,
                l.status,
                l.manager_id,
                l.decision_comment,
                l.decided_at,
                l.created_at,
                l.updated_at
            FROM leaves AS l
            JOIN employees AS e ON e.id = l.employee_id
            WHERE l.employee_id = ?
            ORDER BY l.start_date DESC, l.id DESC
            """,
            (employee_id,),
        ).fetchall()
    return [dict(row) for row in rows]


def cancel_leave(leave_id: int) -> dict:
    existing = _get_leave(leave_id)
    if existing is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Leave record not found.",
        )
    if existing["status"] in {"CANCELLED", "REJECTED"}:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="This leave can no longer be cancelled.",
        )

    timestamp = datetime.now(IST).isoformat(timespec="seconds")
    with get_connection() as connection:
        connection.execute(
            """
            UPDATE leaves
            SET status = 'CANCELLED', updated_at = ?
            WHERE id = ?
            """,
            (timestamp, leave_id),
        )

    leave = dict(_get_leave(leave_id))
    notify_admins(
        "LEAVE_CANCELLED",
        f"{leave['employee_name']} cancelled their {existing['status'].lower()} "
        f"leave from {leave['start_date']} to {leave['end_date']}.",
        dedupe_key=f"LEAVE_CANCELLED:{leave_id}",
    )
    return leave


def list_pending_leaves(manager_id: int) -> list[dict]:
    if not employee_is_admin(manager_id):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only an active manager or HR administrator can view this queue.",
        )

    with get_connection() as connection:
        rows = connection.execute(
            """
            SELECT
                l.id,
                l.employee_id,
                e.name AS employee_name,
                l.start_date,
                l.end_date,
                l.leave_type,
                l.reason,
                l.status,
                l.manager_id,
                l.decision_comment,
                l.decided_at,
                l.created_at,
                l.updated_at
            FROM leaves AS l
            JOIN employees AS e ON e.id = l.employee_id
            WHERE l.status = 'PENDING'
            ORDER BY l.created_at ASC
            """
        ).fetchall()
    return [dict(row) for row in rows]


def decide_leave(
    leave_id: int,
    manager_id: int,
    decision: str,
    comment: str = "",
) -> dict:
    if not employee_is_admin(manager_id):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only an active manager or HR administrator can decide leave.",
        )

    existing = _get_leave(leave_id)
    if existing is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Leave record not found.",
        )
    if existing["status"] != "PENDING":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Only pending leave requests can be approved or rejected.",
        )

    if decision == "APPROVED":
        with get_connection() as connection:
            conflict = connection.execute(
                """
                SELECT work_date
                FROM attendance
                WHERE employee_id = ? AND work_date BETWEEN ? AND ?
                ORDER BY work_date
                LIMIT 1
                """,
                (
                    existing["employee_id"],
                    existing["start_date"],
                    existing["end_date"],
                ),
            ).fetchone()
        if conflict:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=(
                    "Leave cannot be approved because attendance already exists "
                    f"for {conflict['work_date']}."
                ),
            )

    timestamp = datetime.now(IST).isoformat(timespec="seconds")
    with get_connection() as connection:
        connection.execute(
            """
            UPDATE leaves
            SET status = ?, manager_id = ?, decision_comment = ?,
                decided_at = ?, updated_at = ?
            WHERE id = ?
            """,
            (
                decision,
                manager_id,
                comment.strip() or None,
                timestamp,
                timestamp,
                leave_id,
            ),
        )

    leave = dict(_get_leave(leave_id))
    note = f" Comment: {leave['decision_comment']}" if leave["decision_comment"] else ""
    notify(
        leave["employee_id"],
        f"LEAVE_{decision}",
        f"Your leave from {leave['start_date']} to {leave['end_date']} "
        f"was {decision.lower()}.{note}",
        dedupe_key=f"LEAVE_DECISION:{leave_id}",
    )
    return leave
