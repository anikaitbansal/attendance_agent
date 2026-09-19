from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
from fastapi import HTTPException

import app.database as database
from app.attendance_service import check_in, check_out


IST = ZoneInfo("Asia/Kolkata")


@pytest.fixture(autouse=True)
def isolated_database(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(database, "DATA_DIR", tmp_path)
    monkeypatch.setattr(database, "DB_PATH", tmp_path / "test.db")
    database.initialise_database()


def test_check_in_and_checkout_calculate_net_hours() -> None:
    check_in(
        employee_id=1,
        work_mode="OFFICE",
        at=datetime(2026, 9, 19, 9, 30, tzinfo=IST),
    )
    completed = check_out(
        employee_id=1,
        break_minutes=30,
        at=datetime(2026, 9, 19, 18, 0, tzinfo=IST),
    )

    assert completed["worked_minutes"] == 480
    assert completed["worked_hours"] == 8.0
    assert completed["state"] == "COMPLETED"


def test_duplicate_check_in_is_blocked() -> None:
    event_time = datetime(2026, 9, 19, 9, 30, tzinfo=IST)
    check_in(employee_id=1, work_mode="WFH", at=event_time)

    with pytest.raises(HTTPException) as error:
        check_in(employee_id=1, work_mode="WFH", at=event_time)

    assert error.value.status_code == 409
