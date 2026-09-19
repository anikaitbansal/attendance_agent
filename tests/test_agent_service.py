from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

import app.database as database
from app.agent_service import run_agent
from app.attendance_service import check_in, check_out


IST = ZoneInfo("Asia/Kolkata")


@pytest.fixture(autouse=True)
def isolated_database(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(database, "DATA_DIR", tmp_path)
    monkeypatch.setattr(database, "DB_PATH", tmp_path / "test.db")
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    database.initialise_database()


def test_agent_routes_weekly_hours_without_groq() -> None:
    check_in(1, "OFFICE", datetime(2026, 9, 14, 9, 0, tzinfo=IST))
    check_out(1, 30, datetime(2026, 9, 14, 17, 30, tzinfo=IST))

    result = run_agent(1, "How many hours did I work this week?")

    assert result["intent"] == "weekly_hours"
    assert result["tool_used"] == "weekly_hours"
    assert result["interpretation_source"] == "fallback"
    assert "8.0 hours" in result["reply"]


def test_agent_creates_pending_leave_without_groq() -> None:
    result = run_agent(2, "Apply for casual leave on 2026-09-23")

    assert result["intent"] == "create_leave"
    assert result["tool_used"] == "create_leave"
    assert "PENDING" in result["reply"]
