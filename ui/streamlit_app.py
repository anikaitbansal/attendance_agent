"""Streamlit demo front end for the AI Attendance Agent.

Run the API first, then in a second terminal:

    streamlit run ui/streamlit_app.py
"""

from __future__ import annotations

import os
from datetime import datetime
from zoneinfo import ZoneInfo

import requests
import streamlit as st
from dotenv import load_dotenv


load_dotenv()

IST = ZoneInfo("Asia/Kolkata")
API_BASE = os.getenv("API_BASE_URL", "http://127.0.0.1:8000")
TIMEOUT = 60

st.set_page_config(page_title="AI Attendance Agent", page_icon="🕘", layout="wide")


def api(method: str, path: str, **kwargs) -> tuple[bool, object]:
    """Call the API and return (ok, payload-or-error-message)."""
    try:
        response = requests.request(
            method, f"{API_BASE}{path}", timeout=TIMEOUT, **kwargs
        )
    except requests.RequestException:
        return False, f"Cannot reach the API at {API_BASE}. Is uvicorn running?"

    if response.ok:
        return True, response.json()

    try:
        return False, response.json().get("detail", response.text)
    except ValueError:
        return False, response.text


def show(ok: bool, payload: object, success: str) -> None:
    if ok:
        st.success(success)
        st.json(payload)
    else:
        st.error(payload)


ok, health = api("GET", "/health")
if not ok:
    st.error(health)
    st.stop()

ok, employees = api("GET", "/employees")
if not ok:
    st.error(employees)
    st.stop()

by_label = {f"{e['name']} (#{e['id']})": e for e in employees}

with st.sidebar:
    st.header("Who are you?")
    label = st.selectbox("Employee", list(by_label))
    employee = by_label[label]
    employee_id = employee["id"]
    st.caption(f"{employee['department']} · {employee['email']}")
    st.divider()
    st.caption(f"API: {API_BASE}")
    st.caption(f"Now (IST): {datetime.now(IST):%Y-%m-%d %H:%M}")
    if health.get("agent_ready"):
        st.caption("Agent: ready")
    else:
        st.caption("Agent: no GROQ_API_KEY set")

st.title("AI Attendance Agent")

chat_tab, attendance_tab, leave_tab = st.tabs(["Chat", "Attendance", "Leave"])


with chat_tab:
    if not health.get("agent_ready"):
        st.warning(
            "Add a free Groq key to `.env` as `GROQ_API_KEY` and restart the API "
            "to enable chat. The Attendance and Leave tabs work without it."
        )

    history = st.session_state.setdefault("history", {})
    messages = history.setdefault(employee_id, [])

    for turn in messages:
        with st.chat_message(turn["role"]):
            st.markdown(turn["content"])

    placeholder = "Try: check me in from home, or book leave next Friday"
    if prompt := st.chat_input(placeholder, disabled=not health.get("agent_ready")):
        messages.append({"role": "user", "content": prompt})
        with st.chat_message("user"):
            st.markdown(prompt)

        with st.chat_message("assistant"), st.spinner("Thinking..."):
            ok, payload = api(
                "POST",
                "/agent/chat",
                json={
                    "employee_id": employee_id,
                    "message": prompt,
                    # Send prior turns only; this turn is the message field.
                    "history": messages[:-1],
                },
            )
            reply = payload["reply"] if ok else f"Sorry — {payload}"
            st.markdown(reply)

        messages.append({"role": "assistant", "content": reply})

    if messages and st.button("Clear conversation"):
        history[employee_id] = []
        st.rerun()


with attendance_tab:
    left, right = st.columns(2)

    with left:
        st.subheader("Check in")
        work_mode = st.radio("Work mode", ["OFFICE", "WFH"], horizontal=True)
        if st.button("Check in", type="primary"):
            ok, payload = api(
                "POST",
                "/attendance/check-in",
                json={"employee_id": employee_id, "work_mode": work_mode},
            )
            show(ok, payload, f"Checked in ({work_mode}).")

        st.subheader("Check out")
        break_minutes = st.number_input("Break minutes", 0, 240, 0, step=15)
        if st.button("Check out"):
            ok, payload = api(
                "POST",
                "/attendance/check-out",
                json={"employee_id": employee_id, "break_minutes": break_minutes},
            )
            show(ok, payload, "Checked out.")

    with right:
        st.subheader("Today")
        ok, payload = api("GET", f"/attendance/{employee_id}/today")
        if not ok:
            st.info(payload)
        else:
            st.metric("State", payload["state"].replace("_", " ").title())
            hours = payload["worked_hours"]
            st.metric("Worked hours", "—" if hours is None else f"{hours:.2f}")
            st.json(payload)


with leave_tab:
    left, right = st.columns(2)

    with left:
        st.subheader("Request leave")
        today = datetime.now(IST).date()
        start_date = st.date_input("Start date", today)
        end_date = st.date_input("End date", today)
        leave_type = st.selectbox("Type", ["CASUAL", "SICK", "OTHER"])
        reason = st.text_area("Reason", placeholder="At least 3 characters")

        if st.button("Request leave", type="primary"):
            if len(reason.strip()) < 3:
                st.error("Please give a reason of at least 3 characters.")
            else:
                ok, payload = api(
                    "POST",
                    "/leaves",
                    json={
                        "employee_id": employee_id,
                        "start_date": start_date.isoformat(),
                        "end_date": end_date.isoformat(),
                        "leave_type": leave_type,
                        "reason": reason.strip(),
                    },
                )
                show(ok, payload, "Leave approved.")

    with right:
        st.subheader("Leave history")
        ok, leaves = api("GET", f"/leaves/{employee_id}")
        if not ok:
            st.error(leaves)
        elif not leaves:
            st.info("No leave records yet.")
        else:
            st.dataframe(
                [
                    {
                        "id": leave["id"],
                        "from": leave["start_date"],
                        "to": leave["end_date"],
                        "type": leave["leave_type"],
                        "status": leave["status"],
                        "reason": leave["reason"],
                    }
                    for leave in leaves
                ],
                hide_index=True,
                use_container_width=True,
            )

            approved = [
                leave["id"] for leave in leaves if leave["status"] == "APPROVED"
            ]
            if approved:
                leave_id = st.selectbox("Cancel leave id", approved)
                if st.button("Cancel leave"):
                    ok, payload = api("DELETE", f"/leaves/{leave_id}")
                    show(ok, payload, f"Leave {leave_id} cancelled.")
                    st.rerun()
