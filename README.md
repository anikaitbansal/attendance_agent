# AI Attendance Agent

Zero-cost hackathon MVP for attendance, work-hours and leave management.

## Step 1 Run the foundation

Windows PowerShell:

```powershell
cd attendance-agent
py -m venv .venv
.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
python -m uvicorn app.main:app --reload
```

Open `http://127.0.0.1:8000/docs`. Test `GET /health` and `GET /employees`.

Run tests in a second terminal:

```powershell
cd attendance-agent
.venv\Scripts\Activate.ps1
python -m pytest -q
```

Only fictional employees and `example.com` email addresses are used.

## Step 2 Attendance endpoints

- `POST /attendance/check-in`
- `POST /attendance/check-out`
- `GET /attendance/{employee_id}/today`

The optional `at` field lets the team simulate a full workday during the demo.
If it is omitted, the API uses the current time in Asia/Kolkata.

## Step 3 Leave endpoints

- `POST /leaves` creates a pending leave request.
- `GET /leaves/{employee_id}` returns leave history.
- `DELETE /leaves/{leave_id}` cancels a leave without deleting its audit record.
- `GET /admin/leaves/pending?manager_id=4` shows the manager queue.
- `PATCH /admin/leaves/{leave_id}/decision` approves or rejects pending leave.

Only an active admin can decide leave. In production, the manager identity will
come from authentication rather than the request. Approved leave blocks check-in,
attendance blocks conflicting approval, and overlapping requests are rejected.

## Step 4 Chat agent

`POST /agent/chat` accepts `{employee_id, message, history}` and returns
`{reply, tools_used, interpretation_source}`. Both agents call the same service
functions the REST endpoints use, so every business rule above still applies.

- **With `GROQ_API_KEY`** (`interpretation_source: "groq"`): a LangGraph ReAct
  agent in `app/agent.py` that keeps the conversation and can check in and
  out, request and cancel leave, and report weekly hours, attendance history
  and (for admins) missing check-outs.
- **Without a key** (`interpretation_source: "fallback"`): the keyword router
  in `app/agent_service.py` handles one-line requests such as weekly hours,
  today's status, leave history and `apply for leave on YYYY-MM-DD`.

`tools_used` lists the tools called, which the UI shows under each reply.

Copy `.env.example` to `.env` and add a free key from
<https://console.groq.com/keys>:

```
GROQ_API_KEY=gsk_...
```

`GET /health` reports `agent_ready` (whether a Groq key is set) so the UI can
explain when chat is running in basic mode.

## Step 5 Streamlit demo UI

```powershell
.venv\Scripts\Activate.ps1
streamlit run ui/streamlit_app.py
```

Open `http://localhost:8501` with the API already running. Pick an employee in
the sidebar, then use the Chat, Attendance and Leave tabs. The Chat tab works
in basic mode without a Groq key. Point the UI at a different API with
`API_BASE_URL`.

Admins (the seeded HR user is Meera Iyer, #4) also get an Admin tab to approve
or reject pending leave and to send reminders on demand.

## Step 6 Reminders and notifications

Every notification is stored in the database and shown in the sidebar inbox.
If `GOOGLE_CHAT_WEBHOOK_URL` is set in `.env`, it is also posted to that Google
Chat space; a failing webhook never blocks the action that caused it.

Event notifications come from the service layer, so they fire for REST calls
and for chat agent actions alike:

- leave requested or cancelled → every admin
- leave approved or rejected → the employee

Scheduled reminders run on weekdays in Asia/Kolkata time:

| Time  | Reminder | Who |
|-------|----------|-----|
| 09:30 | Approvals digest | admins, when requests are pending |
| 10:00 | Check-in | staff with no attendance and no approved leave today |
| 18:30 | Check-out | staff who checked in but have not checked out |

Each reminder goes out at most once per person per day. Set
`REMINDERS_ENABLED=false` to turn the scheduler off.

- `GET /notifications/{employee_id}?unread_only=false`
- `POST /notifications/{employee_id}/read`
- `POST /admin/reminders/run` with `{manager_id, kind}` where `kind` is
  `CHECK_IN`, `CHECK_OUT` or `PENDING_DIGEST` — the demo shortcut
