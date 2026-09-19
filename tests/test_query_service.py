from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
from fastapi import HTTPException

import app.database as database
from app.attendance_service import check_in, check_out
from app.query_service import (
    get_attendance_history,
    get_missing_checkouts,
    get_weekly_hours,
)


IST = ZoneInfo("Asia/Kolkata")


@pytest.fixture(autouse=True)
def isolated_database(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(database, "DATA_DIR", tmp_path)
    monkeypatch.setattr(database, "DB_PATH", tmp_path / "test.db")
    database.initialise_database()


def test_weekly_hours_and_missing_checkout_report() -> None:
    check_in(1, "OFFICE", datetime(2026, 9, 14, 9, 0, tzinfo=IST))
    check_out(1, 30, datetime(2026, 9, 14, 17, 30, tzinfo=IST))
    check_in(1, "WFH", datetime(2026, 9, 15, 9, 30, tzinfo=IST))

    summary = get_weekly_hours(1, date(2026, 9, 14))
    assert summary["total_worked_minutes"] == 480
    assert summary["total_worked_hours"] == 8.0
    assert summary["completed_days"] == 1
    assert summary["open_sessions"] == 1

    history = get_attendance_history(1, date(2026, 9, 14), date(2026, 9, 20))
    assert len(history) == 2

    missing = get_missing_checkouts(4, date(2026, 9, 15))
    assert len(missing) == 1
    assert missing[0]["employee_id"] == 1


def test_regular_employee_cannot_view_missing_checkout_report() -> None:
    with pytest.raises(HTTPException) as error:
        get_missing_checkouts(1, date(2026, 9, 15))
    assert error.value.status_code == 403
