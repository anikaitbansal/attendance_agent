"""The chat endpoint must answer with or without a Groq key.

A schema change once made every POST /agent/chat return 500 while every
service-level test still passed, so these tests go through the HTTP route.
"""

from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
from fastapi.testclient import TestClient

import app.database as database
import app.main as main
from app.attendance_service import check_in, check_out


IST = ZoneInfo("Asia/Kolkata")


@pytest.fixture(autouse=True)
def isolated_database(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(database, "DATA_DIR", tmp_path)
    monkeypatch.setattr(database, "DB_PATH", tmp_path / "test.db")
    monkeypatch.setenv("REMINDERS_ENABLED", "false")
    database.initialise_database()


def test_chat_falls_back_to_keyword_agent_without_groq(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    today = datetime.now(IST)
    check_in(1, "OFFICE", today.replace(hour=9, minute=0))
    check_out(1, 0, today.replace(hour=13, minute=0))

    with TestClient(main.app) as client:
        response = client.post(
            "/agent/chat",
            json={
                "employee_id": 1,
                "message": "How many hours did I work this week?",
                "history": [{"role": "user", "content": "hi"}],
            },
        )

    assert response.status_code == 200
    body = response.json()
    assert body["interpretation_source"] == "fallback"
    assert body["tools_used"] == ["weekly_hours"]
    assert "4.0 hours" in body["reply"]


def test_chat_uses_react_agent_when_groq_is_configured(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("GROQ_API_KEY", "test-key")
    seen = {}

    def fake_run_agent(message, employee_id, history):
        seen.update(message=message, employee_id=employee_id, history=history)
        return {"reply": "Checked you in from home.", "tools_used": ["mark_check_in"]}

    monkeypatch.setattr(main, "run_agent", fake_run_agent)

    with TestClient(main.app) as client:
        response = client.post(
            "/agent/chat",
            json={
                "employee_id": 2,
                "message": "check me in from home",
                "history": [{"role": "assistant", "content": "Hello!"}],
            },
        )

    assert response.status_code == 200
    assert response.json() == {
        "employee_id": 2,
        "reply": "Checked you in from home.",
        "tools_used": ["mark_check_in"],
        "interpretation_source": "groq",
    }
    assert seen["history"] == [{"role": "assistant", "content": "Hello!"}]
