from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
import requests
from fastapi import HTTPException
from fastapi.testclient import TestClient

import app.database as database
import app.notification_service as notification_service
from app.attendance_service import check_in
from app.leave_service import cancel_leave, create_leave, decide_leave
from app.main import app
from app.notification_service import (
    list_notifications,
    mark_all_read,
    wait_for_chat_deliveries,
)
from app.reminder_service import (
    run_reminder_now,
    send_check_in_reminders,
    send_check_out_reminders,
    send_pending_leave_digest,
)


IST = ZoneInfo("Asia/Kolkata")
ADMIN_ID = 4


@pytest.fixture(autouse=True)
def isolated_database(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(database, "DATA_DIR", tmp_path)
    monkeypatch.setattr(database, "DB_PATH", tmp_path / "test.db")
    monkeypatch.setenv("REMINDERS_ENABLED", "false")
    monkeypatch.delenv("GOOGLE_CHAT_WEBHOOK_URL", raising=False)
    database.initialise_database()


def kinds(employee_id: int) -> list[str]:
    return [n["kind"] for n in list_notifications(employee_id)]


def future_day(days: int = 30):
    return datetime.now(IST).date() + timedelta(days=days)


def test_leave_lifecycle_notifies_admin_and_employee() -> None:
    day = future_day()
    leave = create_leave(1, day, day, "CASUAL", "Family function")
    assert kinds(ADMIN_ID) == ["LEAVE_REQUESTED"]
    assert "Aarav Mehta requested casual leave" in list_notifications(ADMIN_ID)[0]["message"]

    decide_leave(leave["id"], ADMIN_ID, "APPROVED", "Enjoy")
    [decision] = list_notifications(1)
    assert decision["kind"] == "LEAVE_APPROVED"
    assert "was approved. Comment: Enjoy" in decision["message"]

    cancel_leave(leave["id"])
    assert kinds(ADMIN_ID) == ["LEAVE_CANCELLED", "LEAVE_REQUESTED"]


def test_check_in_reminder_skips_checked_in_and_on_leave_staff() -> None:
    today = datetime.now(IST).date()
    check_in(1, "OFFICE")
    leave = create_leave(2, today, today, "SICK", "Fever")
    decide_leave(leave["id"], ADMIN_ID, "APPROVED")

    notified = send_check_in_reminders()
    assert "Aarav Mehta" not in notified
    assert "Diya Sharma" not in notified
    assert notified == ["Kabir Verma", "Meera Iyer", "Rohan Gupta", "Sara Khan"]
    assert "CHECK_IN_REMINDER" in kinds(3)

    # Running the job again the same day must not double-notify anyone.
    assert send_check_in_reminders() == []


def test_check_out_reminder_only_for_open_attendance() -> None:
    check_in(1, "WFH")
    assert send_check_out_reminders() == ["Aarav Mehta"]
    assert "haven't checked out" in list_notifications(1)[0]["message"]


def test_pending_digest_counts_waiting_requests() -> None:
    assert send_pending_leave_digest() == []

    create_leave(1, future_day(30), future_day(30), "CASUAL", "Trip one")
    create_leave(3, future_day(40), future_day(41), "OTHER", "Trip two")
    assert send_pending_leave_digest() == ["Meera Iyer"]
    digest = next(
        n for n in list_notifications(ADMIN_ID) if n["kind"] == "PENDING_LEAVE_DIGEST"
    )
    assert digest["message"] == "2 leave requests are waiting for your decision."


def test_only_admins_can_trigger_reminders() -> None:
    with pytest.raises(HTTPException) as error:
        run_reminder_now("CHECK_IN", manager_id=1)
    assert error.value.status_code == 403


def test_mark_all_read() -> None:
    send_check_in_reminders()
    assert len(list_notifications(3, unread_only=True)) == 1
    assert mark_all_read(3) == 1
    assert list_notifications(3, unread_only=True) == []


def test_google_chat_webhook_is_best_effort(monkeypatch: pytest.MonkeyPatch) -> None:
    sent = []

    class Ok:
        def raise_for_status(self) -> None:
            pass

    def fake_post(url, json, timeout):
        sent.append((url, json["text"]))
        return Ok()

    monkeypatch.setenv("GOOGLE_CHAT_WEBHOOK_URL", "https://chat.example.com/hook")
    monkeypatch.setattr(notification_service.requests, "post", fake_post)
    check_in(1, "OFFICE")
    send_check_out_reminders()
    wait_for_chat_deliveries()
    assert sent[0][0] == "https://chat.example.com/hook"
    assert sent[0][1].startswith("*Aarav Mehta* — You checked in at")
    assert list_notifications(1)[0]["sent_to_chat"] == 1

    # A broken webhook must not break the leave action that triggered it.
    def broken_post(*_, **__):
        raise requests.ConnectionError("chat is down")

    monkeypatch.setattr(notification_service.requests, "post", broken_post)
    day = future_day()
    leave = create_leave(3, day, day, "CASUAL", "Wedding")
    wait_for_chat_deliveries()
    assert leave["status"] == "PENDING"
    assert list_notifications(ADMIN_ID)[0]["sent_to_chat"] == 0


def test_notification_and_reminder_endpoints() -> None:
    with TestClient(app) as client:
        forbidden = client.post(
            "/admin/reminders/run", json={"manager_id": 1, "kind": "CHECK_IN"}
        )
        run = client.post(
            "/admin/reminders/run", json={"manager_id": ADMIN_ID, "kind": "CHECK_IN"}
        )
        inbox = client.get("/notifications/2", params={"unread_only": True})
        read = client.post("/notifications/2/read")

    assert forbidden.status_code == 403
    assert run.status_code == 200
    assert len(run.json()["notified"]) == 6
    assert inbox.json()[0]["kind"] == "CHECK_IN_REMINDER"
    assert read.json() == {"employee_id": 2, "updated": 1}


def test_slow_webhook_does_not_delay_the_action(monkeypatch: pytest.MonkeyPatch) -> None:
    import time

    class Ok:
        def raise_for_status(self) -> None:
            pass

    def slow_post(*_, **__):
        time.sleep(3)
        return Ok()

    monkeypatch.setenv("GOOGLE_CHAT_WEBHOOK_URL", "https://chat.example.com/hook")
    monkeypatch.setattr(notification_service.requests, "post", slow_post)

    started = time.monotonic()
    day = future_day()
    create_leave(5, day, day, "SICK", "Dentist appointment")
    assert time.monotonic() - started < 1

    wait_for_chat_deliveries()
    assert list_notifications(ADMIN_ID)[0]["sent_to_chat"] == 1
