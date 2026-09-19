from fastapi.testclient import TestClient

from app.main import app


def test_health_and_seeded_employees() -> None:
    with TestClient(app) as client:
        health = client.get("/health")
        employees = client.get("/employees")

    assert health.status_code == 200
    assert health.json()["status"] == "ok"
    assert employees.status_code == 200
    assert len(employees.json()) == 6
    assert all("example.com" in employee["email"] for employee in employees.json())
