from __future__ import annotations

import logging
import os
from datetime import date, datetime
from zoneinfo import ZoneInfo

from apscheduler.schedulers.background import BackgroundScheduler
from fastapi import HTTPException, status

from app.database import employee_is_admin, get_connection
from app.notification_service import notify


IST = ZoneInfo("Asia/Kolkata")

# Weekday schedule in Asia/Kolkata. The admin "run now" endpoint lets the demo
# trigger any of these without waiting for the clock.
SCHEDULE = {
    "PENDING_DIGEST": {"hour": 9, "minute": 30},
    "CHECK_IN": {"hour": 10, "minute": 0},
    "CHECK_OUT": {"hour": 18, "minute": 30},
}

logger = logging.getLogger(__name__)


def _today(today: date | None) -> str:
    return (today or datetime.now(IST).date()).isoformat()


def send_check_in_reminders(today: date | None = None) -> list[str]:
    """Nudge active staff who have neither checked in nor taken approved leave."""
    work_date = _today(today)
    with get_connection() as connection:
        rows = connection.execute(
            """
            SELECT e.id, e.name
            FROM employees AS e
            WHERE e.is_active = 1
              AND NOT EXISTS (
                  SELECT 1 FROM attendance AS a
                  WHERE a.employee_id = e.id AND a.work_date = ?
              )
              AND NOT EXISTS (
                  SELECT 1 FROM leaves AS l
                  WHERE l.employee_id = e.id
                    AND l.status = 'APPROVED'
                    AND ? BETWEEN l.start_date AND l.end_date
              )
            ORDER BY e.id
            """,
            (work_date, work_date),
        ).fetchall()

    notified = []
    for row in rows:
        created = notify(
            row["id"],
            "CHECK_IN_REMINDER",
            "You haven't checked in yet today. Mark your attendance when you start work.",
            dedupe_key=f"CHECK_IN_REMINDER:{row['id']}:{work_date}",
        )
        if created:
            notified.append(row["name"])
    return notified


def send_check_out_reminders(today: date | None = None) -> list[str]:
    """Nudge staff who checked in today but have not checked out."""
    work_date = _today(today)
    with get_connection() as connection:
        rows = connection.execute(
            """
            SELECT e.id, e.name, a.check_in
            FROM attendance AS a
            JOIN employees AS e ON e.id = a.employee_id
            WHERE a.work_date = ? AND a.check_out IS NULL AND e.is_active = 1
            ORDER BY e.id
            """,
            (work_date,),
        ).fetchall()

    notified = []
    for row in rows:
        started = datetime.fromisoformat(row["check_in"]).strftime("%H:%M")
        created = notify(
            row["id"],
            "CHECK_OUT_REMINDER",
            f"You checked in at {started} and haven't checked out. "
            "Check out when you finish so your hours are recorded.",
            dedupe_key=f"CHECK_OUT_REMINDER:{row['id']}:{work_date}",
        )
        if created:
            notified.append(row["name"])
    return notified


def send_pending_leave_digest(today: date | None = None) -> list[str]:
    """Tell each admin how many leave requests are waiting for a decision."""
    work_date = _today(today)
    with get_connection() as connection:
        pending = connection.execute(
            "SELECT COUNT(*) AS count FROM leaves WHERE status = 'PENDING'"
        ).fetchone()["count"]
        admins = connection.execute(
            "SELECT id, name FROM employees WHERE is_admin = 1 AND is_active = 1"
        ).fetchall()

    if pending == 0:
        return []

    noun = "request is" if pending == 1 else "requests are"
    notified = []
    for admin in admins:
        created = notify(
            admin["id"],
            "PENDING_LEAVE_DIGEST",
            f"{pending} leave {noun} waiting for your decision.",
            dedupe_key=f"PENDING_LEAVE_DIGEST:{admin['id']}:{work_date}",
        )
        if created:
            notified.append(admin["name"])
    return notified


JOBS = {
    "CHECK_IN": send_check_in_reminders,
    "CHECK_OUT": send_check_out_reminders,
    "PENDING_DIGEST": send_pending_leave_digest,
}


def run_reminder_now(kind: str, manager_id: int) -> list[str]:
    """Let an admin fire a reminder job on demand, e.g. during a demo."""
    if not employee_is_admin(manager_id):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only an active manager or HR administrator can send reminders.",
        )
    return JOBS[kind]()


def reminders_enabled() -> bool:
    return os.getenv("REMINDERS_ENABLED", "true").strip().lower() in {"1", "true", "yes"}


def start_scheduler() -> BackgroundScheduler | None:
    """Start the weekday reminder jobs, unless REMINDERS_ENABLED is off."""
    if not reminders_enabled():
        return None

    scheduler = BackgroundScheduler(timezone=IST)
    for kind, when in SCHEDULE.items():
        scheduler.add_job(
            JOBS[kind],
            "cron",
            day_of_week="mon-fri",
            id=kind,
            misfire_grace_time=15 * 60,
            **when,
        )
    scheduler.start()
    logger.info("Reminder scheduler started: %s", SCHEDULE)
    return scheduler
