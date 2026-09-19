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

KIND_LABELS = {
    "CHECK_IN_REMINDER": "Check-in reminder",
    "CHECK_OUT_REMINDER": "Check-out reminder",
    "PENDING_LEAVE_DIGEST": "Approvals digest",
    "LEAVE_REQUESTED": "Leave request",
    "LEAVE_CANCELLED": "Leave cancelled",
    "LEAVE_APPROVED": "Leave approved",
    "LEAVE_REJECTED": "Leave rejected",
}

REMINDER_BUTTONS = [
    ("CHECK_IN", "Check-in reminders"),
    ("CHECK_OUT", "Check-out reminders"),
    ("PENDING_DIGEST", "Approvals digest"),
]

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


WORK_MODES = {"OFFICE": "🏢 Office", "WFH": "🏠 Work from home"}
LEAVE_STATUSES = {
    "PENDING": "⏳ Pending",
    "APPROVED": "✅ Approved",
    "REJECTED": "❌ Rejected",
    "CANCELLED": "🚫 Cancelled",
}


def clock(timestamp: str | None) -> str:
    """'2026-09-19T09:30:00+05:30' -> '09:30'."""
    return datetime.fromisoformat(timestamp).strftime("%H:%M") if timestamp else "—"


def nice_date(value: str) -> str:
    """'2026-09-25' -> '25 Sep 2026'."""
    return datetime.fromisoformat(value).strftime("%d %b %Y")


def leave_dates(leave: dict) -> str:
    start, end = leave["start_date"], leave["end_date"]
    if start == end:
        return nice_date(start)
    return f"{nice_date(start)} → {nice_date(end)}"


def show_flash(key: str) -> None:
    """Show a message saved before st.rerun(), which would otherwise wipe it."""
    if message := st.session_state.pop(key, None):
        st.success(message)


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

    ok, inbox = api("GET", f"/notifications/{employee_id}")
    if ok:
        unread = [n for n in inbox if not n["is_read"]]
        title = f"🔔 Notifications ({len(unread)} new)" if unread else "🔔 Notifications"
        with st.expander(title, expanded=bool(unread)):
            if not inbox:
                st.caption("Nothing yet.")
            for note in inbox[:8]:
                marker = "**New** · " if not note["is_read"] else ""
                st.markdown(f"{marker}{note['message']}")
                when = f"{note['created_at'][:10]} {note['created_at'][11:16]}"
                st.caption(f"{KIND_LABELS.get(note['kind'], note['kind'])} · {when}")
            if unread and st.button("Mark all read"):
                api("POST", f"/notifications/{employee_id}/read")
                st.rerun()

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
        st.info(
            "Basic mode: without a Groq key the chat understands simple requests "
            "such as *hours this week*, *am I checked in*, *my leave history* or "
            "*apply for leave on 2026-10-02*. Add `GROQ_API_KEY` to `.env` and "
            "restart the API for full conversations."
        )

    history = st.session_state.setdefault("history", {})
    messages = history.setdefault(employee_id, [])

    def show_tools(tools: list[str]) -> None:
        if tools:
            st.caption("🔧 " + " → ".join(tools))

    for turn in messages:
        with st.chat_message(turn["role"]):
            st.markdown(turn["content"])
            show_tools(turn.get("tools", []))

    placeholder = "Try: check me in from home, or how many hours this week?"
    if prompt := st.chat_input(placeholder):
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
                    "history": [
                        {"role": t["role"], "content": t["content"]}
                        for t in messages[:-1]
                    ],
                },
            )
            reply = payload["reply"] if ok else f"Sorry — {payload}"
            tools = payload["tools_used"] if ok else []
            st.markdown(reply)
            show_tools(tools)

        messages.append({"role": "assistant", "content": reply, "tools": tools})

    if messages and st.button("Clear conversation"):
        history[employee_id] = []
        st.rerun()


with attendance_tab:
    left, right = st.columns(2)

    with left:
        st.subheader("Check in")
        work_mode = st.radio(
            "Work mode",
            list(WORK_MODES),
            format_func=WORK_MODES.get,
            horizontal=True,
        )
        if st.button("Check in", type="primary"):
            ok, payload = api(
                "POST",
                "/attendance/check-in",
                json={"employee_id": employee_id, "work_mode": work_mode},
            )
            if ok:
                st.success(
                    f"Checked in at {clock(payload['check_in'])} · "
                    f"{WORK_MODES[payload['work_mode']]}"
                )
            else:
                st.error(payload)

        st.subheader("Check out")
        break_minutes = st.number_input("Break minutes", 0, 240, 0, step=15)
        if st.button("Check out"):
            ok, payload = api(
                "POST",
                "/attendance/check-out",
                json={"employee_id": employee_id, "break_minutes": break_minutes},
            )
            if ok:
                taken = payload["break_minutes"]
                st.success(
                    f"Checked out at {clock(payload['check_out'])} · "
                    f"{payload['worked_hours']:.2f} h worked"
                    + (f" after a {taken} min break" if taken else "")
                )
            else:
                st.error(payload)

    with right:
        st.subheader("Today")
        ok, payload = api("GET", f"/attendance/{employee_id}/today")
        if not ok:
            st.info(payload)
        else:
            done = payload["state"] == "COMPLETED"
            hours = payload["worked_hours"]
            status_col, hours_col = st.columns(2)
            status_col.metric("Status", "Checked out" if done else "Checked in")
            hours_col.metric(
                "Worked", f"{hours:.2f} h" if hours is not None else "In progress"
            )

            in_col, out_col, break_col = st.columns(3)
            in_col.metric("Check-in", clock(payload["check_in"]))
            out_col.metric("Check-out", clock(payload["check_out"]))
            break_col.metric("Break", f"{payload['break_minutes']} min")
            st.caption(
                f"{WORK_MODES[payload['work_mode']]} · {nice_date(payload['work_date'])}"
            )


with leave_tab:
    show_flash("leave_flash")
    left, right = st.columns(2)

    with left:
        st.subheader("Request leave")
        today = datetime.now(IST).date()
        start_date = st.date_input("Start date", today)
        end_date = st.date_input("End date", today)
        leave_type = st.selectbox(
            "Type", ["CASUAL", "SICK", "OTHER"], format_func=str.title
        )
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
                if ok:
                    st.success(
                        f"{payload['leave_type'].title()} leave requested for "
                        f"{leave_dates(payload)}. Waiting for a manager decision."
                    )
                else:
                    st.error(payload)

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
                        "Request": f"#{leave['id']}",
                        "Dates": leave_dates(leave),
                        "Type": leave["leave_type"].title(),
                        "Status": LEAVE_STATUSES[leave["status"]],
                        "Reason": leave["reason"],
                        "Manager note": leave["decision_comment"] or "",
                    }
                    for leave in leaves
                ],
                hide_index=True,
                width="stretch",
            )

            by_id = {
                leave["id"]: leave
                for leave in leaves
                if leave["status"] in {"PENDING", "APPROVED"}
            }
            if by_id:
                leave_id = st.selectbox(
                    "Cancel a leave",
                    list(by_id),
                    format_func=lambda i: (
                        f"#{i} · {by_id[i]['leave_type'].title()} · "
                        f"{leave_dates(by_id[i])}"
                    ),
                )
                if st.button("Cancel leave"):
                    ok, payload = api("DELETE", f"/leaves/{leave_id}")
                    if ok:
                        st.session_state["leave_flash"] = (
                            f"Leave #{leave_id} for {leave_dates(payload)} cancelled."
                        )
                        st.rerun()
                    st.error(payload)


if admin_tab is not None:
    with admin_tab:
        show_flash("admin_flash")

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
                        "Request": f"#{leave['id']}",
                        "Employee": leave["employee_name"],
                        "Dates": leave_dates(leave),
                        "Type": leave["leave_type"].title(),
                        "Reason": leave["reason"],
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
                    f"#{i} · {by_id[i]['employee_name']} · {leave_dates(by_id[i])}"
                ),
            )
            chosen = by_id[leave_id]
            st.caption(f"{chosen['leave_type'].title()} leave · {chosen['reason']}")
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

        st.divider()
        st.subheader("Reminders")
        st.caption(
            "Sent automatically on weekdays (IST): approvals digest at 09:30, "
            "check-in at 10:00, check-out at 18:30. Send one now:"
        )
        for column, (kind, text) in zip(st.columns(3), REMINDER_BUTTONS):
            with column:
                if st.button(text, key=f"remind_{kind}", width="stretch"):
                    ok, payload = api(
                        "POST",
                        "/admin/reminders/run",
                        json={"manager_id": employee_id, "kind": kind},
                    )
                    if ok:
                        names = payload["notified"]
                        st.session_state["admin_flash"] = (
                            f"{text} sent to {', '.join(names)}."
                            if names
                            else f"{text}: nobody needs one right now "
                            "(or they were already reminded today)."
                        )
                        st.rerun()
                    st.error(payload)
