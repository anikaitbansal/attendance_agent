from __future__ import annotations

import logging
import os
from concurrent.futures import Future, ThreadPoolExecutor, wait
from datetime import datetime
from zoneinfo import ZoneInfo

import requests
import urllib3.util.connection
from fastapi import HTTPException, status

from app.database import employee_exists, get_connection


IST = ZoneInfo("Asia/Kolkata")
CHAT_TIMEOUT_SECONDS = 5

logger = logging.getLogger(__name__)

# Many home and office networks resolve Google to IPv6 addresses they cannot
# route. urllib3 then waits out a timeout on every IPv6 address before trying
# IPv4, which took ~45 s per message on the dev machine. In the API process
# only this webhook uses urllib3 (Groq uses httpx), so IPv4-only is contained.
urllib3.util.connection.HAS_IPV6 = False

# Chat delivery happens off the request thread so a slow or unreachable
# webhook never delays a check-in, a leave action or a reminder run.
_chat_pool = ThreadPoolExecutor(max_workers=2, thread_name_prefix="google-chat")
_pending: set[Future] = set()


def _post_to_google_chat(employee_name: str, message: str) -> bool:
    """Mirror a notification into a Google Chat space when a webhook is set.

    Delivery is best effort: a slow or broken webhook must never fail the
    attendance or leave action that triggered the notification.
    """
    webhook_url = os.getenv("GOOGLE_CHAT_WEBHOOK_URL", "").strip()
    if not webhook_url:
        return False

    try:
        response = requests.post(
            webhook_url,
            json={"text": f"*{employee_name}* — {message}"},
            timeout=CHAT_TIMEOUT_SECONDS,
        )
        response.raise_for_status()
    except requests.RequestException as error:
        logger.warning("Google Chat delivery failed: %s", error)
        return False
    return True


def notify(
    employee_id: int,
    kind: str,
    message: str,
    dedupe_key: str | None = None,
) -> dict | None:
    """Store a notification for one employee and mirror it to Google Chat.

    Returns the new notification, or None when dedupe_key was already used.
    """
    timestamp = datetime.now(IST).isoformat(timespec="seconds")
    with get_connection() as connection:
        cursor = connection.execute(
            """
            INSERT OR IGNORE INTO notifications (
                employee_id, kind, message, dedupe_key, created_at
            ) VALUES (?, ?, ?, ?, ?)
            """,
            (employee_id, kind, message, dedupe_key, timestamp),
        )
        if cursor.rowcount == 0:
            return None
        notification_id = cursor.lastrowid
        employee_name = connection.execute(
            "SELECT name FROM employees WHERE id = ?", (employee_id,)
        ).fetchone()["name"]

    if os.getenv("GOOGLE_CHAT_WEBHOOK_URL", "").strip():
        future = _chat_pool.submit(
            _deliver_to_chat, notification_id, employee_name, message
        )
        _pending.add(future)
        future.add_done_callback(_pending.discard)

    return _get_notification(notification_id)


def _deliver_to_chat(notification_id: int, employee_name: str, message: str) -> None:
    if _post_to_google_chat(employee_name, message):
        with get_connection() as connection:
            connection.execute(
                "UPDATE notifications SET sent_to_chat = 1 WHERE id = ?",
                (notification_id,),
            )


def wait_for_chat_deliveries(timeout: float = 30) -> None:
    """Block until queued Google Chat posts finish. Used by tests and scripts."""
    wait(list(_pending), timeout=timeout)


def notify_admins(kind: str, message: str, dedupe_key: str | None = None) -> None:
    """Send the same notification to every active manager or HR admin."""
    with get_connection() as connection:
        admins = connection.execute(
            "SELECT id FROM employees WHERE is_admin = 1 AND is_active = 1"
        ).fetchall()
    for admin in admins:
        key = f"{dedupe_key}:{admin['id']}" if dedupe_key else None
        notify(admin["id"], kind, message, key)


def _get_notification(notification_id: int) -> dict:
    with get_connection() as connection:
        row = connection.execute(
            """
            SELECT id, employee_id, kind, message, is_read, sent_to_chat, created_at
            FROM notifications
            WHERE id = ?
            """,
            (notification_id,),
        ).fetchone()
    return dict(row)


def list_notifications(employee_id: int, unread_only: bool = False) -> list[dict]:
    if not employee_exists(employee_id):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Active employee not found.",
        )

    with get_connection() as connection:
        rows = connection.execute(
            """
            SELECT id, employee_id, kind, message, is_read, sent_to_chat, created_at
            FROM notifications
            WHERE employee_id = ? AND (? = 0 OR is_read = 0)
            ORDER BY created_at DESC, id DESC
            LIMIT 50
            """,
            (employee_id, int(unread_only)),
        ).fetchall()
    return [dict(row) for row in rows]


def mark_all_read(employee_id: int) -> int:
    if not employee_exists(employee_id):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Active employee not found.",
        )

    with get_connection() as connection:
        cursor = connection.execute(
            "UPDATE notifications SET is_read = 1 WHERE employee_id = ? AND is_read = 0",
            (employee_id,),
        )
    return cursor.rowcount
