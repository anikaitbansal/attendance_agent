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

## Step 4 Query and reporting endpoints

- `GET /attendance/{employee_id}/history` returns a date-filtered history.
- `GET /reports/{employee_id}/weekly-hours` calculates weekly totals.
- `GET /admin/attendance/missing-checkout` lists open sessions for managers.

These deterministic functions will be exposed as tools to the LangGraph agent.

## Step 5 LangGraph and Groq agent

`POST /agent/chat` interprets natural-language requests and routes them through
a LangGraph workflow to deterministic attendance and leave tools. Groq is used
for intent extraction when `GROQ_API_KEY` is configured. A local fallback keeps
the main demo flows available if the key or network is unavailable.
