from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator


PROJECT_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = PROJECT_ROOT / "data"
DB_PATH = DATA_DIR / "attendance.db"


@contextmanager
def get_connection() -> Iterator[sqlite3.Connection]:
    """Open a SQLite connection that returns rows like dictionaries."""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(DB_PATH)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    try:
        yield connection
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def _migrate_leave_workflow_if_needed(connection: sqlite3.Connection) -> None:
    """Upgrade the earlier auto-approved leave table without losing demo data."""
    table = connection.execute(
        "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'leaves'"
    ).fetchone()
    if table is None or "PENDING" in table["sql"]:
        return

    connection.executescript(
        """
        DROP INDEX IF EXISTS idx_leaves_employee_dates;
        ALTER TABLE leaves RENAME TO leaves_legacy;

        CREATE TABLE leaves (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            employee_id INTEGER NOT NULL,
            start_date TEXT NOT NULL,
            end_date TEXT NOT NULL,
            leave_type TEXT NOT NULL CHECK (
                leave_type IN ('CASUAL', 'SICK', 'OTHER')
            ),
            reason TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'PENDING' CHECK (
                status IN ('PENDING', 'APPROVED', 'REJECTED', 'CANCELLED')
            ),
            manager_id INTEGER,
            decision_comment TEXT,
            decided_at TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            FOREIGN KEY (employee_id) REFERENCES employees(id),
            FOREIGN KEY (manager_id) REFERENCES employees(id),
            CHECK (end_date >= start_date)
        );

        INSERT INTO leaves (
            id, employee_id, start_date, end_date, leave_type, reason,
            status, created_at, updated_at
        )
        SELECT
            id, employee_id, start_date, end_date, leave_type, reason,
            status, created_at, updated_at
        FROM leaves_legacy;

        DROP TABLE leaves_legacy;

        CREATE INDEX idx_leaves_employee_dates
        ON leaves (employee_id, start_date, end_date);
        """
    )


def initialise_database() -> None:
    """Create the initial schema and seed six fictional employees."""
    with get_connection() as connection:
        connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS employees (
                id INTEGER PRIMARY KEY,
                name TEXT NOT NULL,
                email TEXT NOT NULL UNIQUE,
                department TEXT NOT NULL,
                is_admin INTEGER NOT NULL DEFAULT 0 CHECK (is_admin IN (0, 1)),
                is_active INTEGER NOT NULL DEFAULT 1 CHECK (is_active IN (0, 1))
            );

            CREATE TABLE IF NOT EXISTS attendance (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                employee_id INTEGER NOT NULL,
                work_date TEXT NOT NULL,
                work_mode TEXT NOT NULL CHECK (work_mode IN ('OFFICE', 'WFH')),
                check_in TEXT NOT NULL,
                check_out TEXT,
                break_minutes INTEGER NOT NULL DEFAULT 0 CHECK (break_minutes >= 0),
                worked_minutes INTEGER,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                FOREIGN KEY (employee_id) REFERENCES employees(id),
                UNIQUE (employee_id, work_date)
            );

            CREATE TABLE IF NOT EXISTS leaves (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                employee_id INTEGER NOT NULL,
                start_date TEXT NOT NULL,
                end_date TEXT NOT NULL,
                leave_type TEXT NOT NULL CHECK (
                    leave_type IN ('CASUAL', 'SICK', 'OTHER')
                ),
                reason TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'PENDING' CHECK (
                    status IN ('PENDING', 'APPROVED', 'REJECTED', 'CANCELLED')
                ),
                manager_id INTEGER,
                decision_comment TEXT,
                decided_at TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                FOREIGN KEY (employee_id) REFERENCES employees(id),
                FOREIGN KEY (manager_id) REFERENCES employees(id),
                CHECK (end_date >= start_date)
            );

            CREATE INDEX IF NOT EXISTS idx_attendance_employee_date
            ON attendance (employee_id, work_date);

            CREATE INDEX IF NOT EXISTS idx_leaves_employee_dates
            ON leaves (employee_id, start_date, end_date);

            CREATE TABLE IF NOT EXISTS notifications (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                employee_id INTEGER NOT NULL,
                kind TEXT NOT NULL,
                message TEXT NOT NULL,
                -- Stops a reminder job that runs twice from notifying twice.
                dedupe_key TEXT UNIQUE,
                is_read INTEGER NOT NULL DEFAULT 0 CHECK (is_read IN (0, 1)),
                sent_to_chat INTEGER NOT NULL DEFAULT 0 CHECK (sent_to_chat IN (0, 1)),
                created_at TEXT NOT NULL,
                FOREIGN KEY (employee_id) REFERENCES employees(id)
            );

            CREATE INDEX IF NOT EXISTS idx_notifications_employee
            ON notifications (employee_id, is_read, created_at);
            """
        )

        _migrate_leave_workflow_if_needed(connection)

        employee_count = connection.execute(
            "SELECT COUNT(*) AS count FROM employees"
        ).fetchone()["count"]

        if employee_count == 0:
            connection.executemany(
                """
                INSERT INTO employees (id, name, email, department, is_admin)
                VALUES (?, ?, ?, ?, ?)
                """,
                [
                    (1, "Aarav Mehta", "aarav@example.com", "Engineering", 0),
                    (2, "Diya Sharma", "diya@example.com", "Operations", 0),
                    (3, "Kabir Verma", "kabir@example.com", "Sales", 0),
                    (4, "Meera Iyer", "meera@example.com", "HR", 1),
                    (5, "Rohan Gupta", "rohan@example.com", "Finance", 0),
                    (6, "Sara Khan", "sara@example.com", "Product", 0),
                ],
            )


def list_employees() -> list[dict]:
    with get_connection() as connection:
        rows = connection.execute(
            """
            SELECT id, name, email, department, is_admin, is_active
            FROM employees
            ORDER BY id
            """
        ).fetchall()
    return [dict(row) for row in rows]


def employee_exists(employee_id: int) -> bool:
    with get_connection() as connection:
        row = connection.execute(
            "SELECT 1 FROM employees WHERE id = ? AND is_active = 1",
            (employee_id,),
        ).fetchone()
    return row is not None


def employee_is_admin(employee_id: int) -> bool:
    with get_connection() as connection:
        row = connection.execute(
            """
            SELECT is_admin
            FROM employees
            WHERE id = ? AND is_active = 1
            """,
            (employee_id,),
        ).fetchone()
    return bool(row and row["is_admin"])
