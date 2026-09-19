from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
from fastapi import HTTPException

import app.database as database
from app.attendance_service import check_in
from app.leave_service import cancel_leave, create_leave, list_leaves


IST = ZoneInfo("Asia/Kolkata")


@pytest.fixture(autouse=True)
def isolated_database(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(database, "DATA_DIR", tmp_path)
    monkeypatch.setattr(database, "DB_PATH", tmp_path / "test.db")
    database.initialise_database()


def test_leave_blocks_check_in_until_cancelled() -> None:
    leave = create_leave(
        employee_id=1,
        start_date=date(2026, 9, 20),
        end_date=date(2026, 9, 20),
        leave_type="CASUAL",
        reason="Family commitment",
    )

    with pytest.raises(HTTPException) as error:
        check_in(
            employee_id=1,
            work_mode="OFFICE",
            at=datetime(2026, 9, 20, 9, 30, tzinfo=IST),
        )
    assert error.value.status_code == 409

    cancelled = cancel_leave(leave["id"])
    assert cancelled["status"] == "CANCELLED"

    attendance = check_in(
        employee_id=1,
        work_mode="OFFICE",
        at=datetime(2026, 9, 20, 9, 30, tzinfo=IST),
    )
    assert attendance["state"] == "CHECKED_IN"


def test_attendance_blocks_leave_for_same_date() -> None:
    check_in(
        employee_id=2,
        work_mode="WFH",
        at=datetime(2026, 9, 21, 9, 0, tzinfo=IST),
    )

    with pytest.raises(HTTPException) as error:
        create_leave(
            employee_id=2,
            start_date=date(2026, 9, 21),
            end_date=date(2026, 9, 21),
            leave_type="SICK",
            reason="Not feeling well",
        )
    assert error.value.status_code == 409


def test_leave_history_contains_cancelled_records() -> None:
    leave = create_leave(
        employee_id=3,
        start_date=date(2026, 9, 22),
        end_date=date(2026, 9, 23),
        leave_type="OTHER",
        reason="Personal work",
    )
    cancel_leave(leave["id"])

    history = list_leaves(3)
    assert len(history) == 1
    assert history[0]["status"] == "CANCELLED"
