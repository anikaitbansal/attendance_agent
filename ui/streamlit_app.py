"""Streamlit demo front end for the AI Attendance Agent.

Run the API first, then in a second terminal:

    streamlit run ui/streamlit_app.py
"""

from __future__ import annotations

import base64
import os
from datetime import datetime
from functools import lru_cache
from pathlib import Path
from zoneinfo import ZoneInfo

import requests
import streamlit as st
from dotenv import load_dotenv


load_dotenv()

IST = ZoneInfo("Asia/Kolkata")
API_BASE = os.getenv("API_BASE_URL", "http://127.0.0.1:8000")
TIMEOUT = 60
ASSETS = Path(__file__).parent / "assets"

st.set_page_config(
    page_title="KarmaVerse · Attendance Agent",
    page_icon=str(ASSETS / "karmaverse_icon.png"),
    layout="wide",
)


@lru_cache(maxsize=None)
def data_uri(name: str) -> str:
    encoded = base64.b64encode((ASSETS / name).read_bytes()).decode()
    return f"data:image/png;base64,{encoded}"


def render_mascot() -> None:
    """Pin the KarmaVerse mascot to the bottom-right corner, gently bobbing.

    It sits above the chat input and ignores clicks so it never blocks the UI.
    """
    st.markdown(
        f"""
        <style>
        .kv-mascot {{
            position: fixed;
            right: 28px;
            bottom: 104px;
            width: 120px;
            z-index: 1000;
            pointer-events: none;
            filter: drop-shadow(0 8px 12px rgba(0, 0, 0, 0.18));
            animation: kv-float 3.2s ease-in-out infinite;
        }}
        @keyframes kv-float {{
            0%, 100% {{ transform: translateY(0) rotate(-2deg); }}
            50% {{ transform: translateY(-12px) rotate(2deg); }}
        }}
        @media (max-width: 640px) {{
            .kv-mascot {{ width: 72px; right: 12px; }}
        }}
        @media (prefers-reduced-motion: reduce) {{
            .kv-mascot {{ animation: none; }}
        }}
        </style>
        <img class="kv-mascot" src="{data_uri("mascot.png")}" alt="KarmaVerse mascot">
        """,
        unsafe_allow_html=True,
    )


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

render_mascot()

with st.sidebar:
    st.image(str(ASSETS / "karmaverse_logo.png"), width="stretch")
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

labels = ["Chat", "Attendance", "Leave"]
if employee["is_admin"]:
    labels.append("Admin")

tabs = st.tabs(labels)
chat_tab, attendance_tab, leave_tab = tabs[0], tabs[1], tabs[2]
admin_tab = tabs[3] if employee["is_admin"] else None


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
                show(ok, payload, "Leave requested — waiting for a manager decision.")

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
                width="stretch",
            )

            cancellable = [
                leave["id"]
                for leave in leaves
                if leave["status"] in {"PENDING", "APPROVED"}
            ]
            if cancellable:
                leave_id = st.selectbox("Cancel leave id", cancellable)
                if st.button("Cancel leave"):
                    ok, payload = api("DELETE", f"/leaves/{leave_id}")
                    show(ok, payload, f"Leave {leave_id} cancelled.")
                    st.rerun()


if admin_tab is not None:
    with admin_tab:
        # Survives the rerun that refreshes the queue after a decision.
        if flash := st.session_state.pop("admin_flash", None):
            st.success(flash)

        st.subheader("Pending leave queue")
        ok, pending = api(
            "GET", "/admin/leaves/pending", params={"manager_id": employee_id}
        )

        if not ok:
            st.error(pending)
        elif not pending:
            st.info("Nothing is waiting for a decision.")
        else:
            st.dataframe(
                [
                    {
                        "id": leave["id"],
                        "who": leave["employee_name"],
                        "from": leave["start_date"],
                        "to": leave["end_date"],
                        "type": leave["leave_type"],
                        "reason": leave["reason"],
                    }
                    for leave in pending
                ],
                hide_index=True,
                width="stretch",
            )

            st.divider()
            st.subheader("Decide")

            by_id = {leave["id"]: leave for leave in pending}
            leave_id = st.selectbox(
                "Leave request",
                list(by_id),
                format_func=lambda i: (
                    f"#{i} · {by_id[i]['employee_name']} · "
                    f"{by_id[i]['start_date']} to {by_id[i]['end_date']}"
                ),
            )
            st.caption(f"{by_id[leave_id]['leave_type']} · {by_id[leave_id]['reason']}")
            comment = st.text_input("Comment (optional)", max_chars=300)

            def decide(decision: str) -> None:
                ok, payload = api(
                    "PATCH",
                    f"/admin/leaves/{leave_id}/decision",
                    json={
                        "manager_id": employee_id,
                        "decision": decision,
                        "comment": comment,
                    },
                )
                if ok:
                    st.session_state["admin_flash"] = (
                        f"Leave {leave_id} {decision.lower()}."
                    )
                    st.rerun()
                st.error(payload)

            approve_col, reject_col = st.columns(2)
            with approve_col:
                if st.button("Approve", type="primary", width="stretch"):
                    decide("APPROVED")
            with reject_col:
                if st.button("Reject", width="stretch"):
                    decide("REJECTED")
