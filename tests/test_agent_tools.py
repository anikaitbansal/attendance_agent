from pathlib import Path

import pytest

import app.database as database
from app.agent import (
    agent_is_configured,
    cancel_my_leave,
    mark_check_in,
    request_leave,
    show_my_leaves,
    show_today,
)


@pytest.fixture(autouse=True)
def isolated_database(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(database, "DATA_DIR", tmp_path)
    monkeypatch.setattr(database, "DB_PATH", tmp_path / "test.db")
    database.initialise_database()


def test_check_in_tool_round_trips_through_show_today() -> None:
    record = mark_check_in.invoke({"employee_id": 1, "work_mode": "wfh"})
    assert record["work_mode"] == "WFH"
    assert record["state"] == "CHECKED_IN"

    assert show_today.invoke({"employee_id": 1})["id"] == record["id"]


def test_tools_return_readable_errors_instead_of_raising() -> None:
    mark_check_in.invoke({"employee_id": 2})
    duplicate = mark_check_in.invoke({"employee_id": 2})
    assert isinstance(duplicate, str)
    assert duplicate.startswith("ERROR: ")

    assert show_today.invoke({"employee_id": 99}).startswith("ERROR: ")


def test_bad_tool_arguments_are_rejected_before_the_service_runs() -> None:
    assert mark_check_in.invoke(
        {"employee_id": 3, "work_mode": "REMOTE"}
    ).startswith("ERROR: ")

    assert request_leave.invoke(
        {
            "employee_id": 3,
            "start_date": "next friday",
            "end_date": "2026-09-25",
            "reason": "Wedding",
        }
    ).startswith("ERROR: ")


def test_leave_tools_create_list_and_cancel() -> None:
    leave = request_leave.invoke(
        {
            "employee_id": 4,
            "start_date": "2026-09-24",
            "end_date": "2026-09-25",
            "reason": "Family wedding",
            "leave_type": "casual",
        }
    )
    assert leave["status"] == "APPROVED"
    assert leave["leave_type"] == "CASUAL"

    assert [row["id"] for row in show_my_leaves.invoke({"employee_id": 4})] == [
        leave["id"]
    ]

    assert cancel_my_leave.invoke({"leave_id": leave["id"]})["status"] == "CANCELLED"
    assert cancel_my_leave.invoke({"leave_id": leave["id"]}).startswith("ERROR: ")


def test_agent_reports_when_no_key_is_configured(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    assert agent_is_configured() is False
