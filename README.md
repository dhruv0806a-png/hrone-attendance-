# Employee Attendance & Analytics API

FastAPI + MongoDB backend implementing the HROne hiring assignment contract
(`openapi.yaml`) and data model (`DATA_MODEL.md`).

## Requirements

- Python 3.11+
- MongoDB 6.0+ (local service or Atlas M0)

## Setup

1. Create and activate a virtual environment:
   python -m venv venv

Windows:
venv\Scripts\activate

macOS/Linux:
source venv/bin/activate

text

2. Install dependencies:
   pip install -r requirements.txt

text

3. Create a `.env` file at the repository root (real environment variables
   take precedence over this file):
   MONGO_URI=mongodb://localhost:27017
   MONGO_DB=sdt_attendance

text

4. (Optional) Load the nine sample documents:
   python sample_seed.py

text

## Run

uvicorn app.main:app --port 8000

text

The app creates all required indexes at startup (idempotent). Open
`http://localhost:8000/docs` for the interactive OpenAPI UI.

## Endpoints

- `GET  /health`
- `POST /employees`, `GET /employees`
- `POST /attendance/punch-in`, `POST /attendance/punch-out`
- `GET  /attendance`
- `PATCH /attendance/{emp_code}/{date}`
- `GET  /analytics/employees/{emp_code}/monthly`
- `GET  /analytics/departments/summary`
- `GET  /analytics/leaderboard/late`
- `GET  /analytics/departments/{department}/trend`
- `GET  /admin/explain/{endpoint}`

The three "hard" analytics endpoints are implemented as single MongoDB
aggregation pipelines (`$lookup`, `$setWindowFields`, `$map`/`$sum`), not
as Python loops over fetched documents.

## Conventions

- Instants in the API are **epoch milliseconds** (integers). MongoDB stores
  them as BSON datetimes; conversion happens at the boundary.
- Calendar dates are `YYYY-MM-DD` strings (IST). Times of day are `HH:MM`
  strings (IST).
- `emp_code` is the business key. MongoDB's `_id` never appears in a
  request or response. Attendance records are addressed by
  `(emp_code, date)`.
- Derived fields (`work_hours`, `late_minutes`, `overtime_minutes`,
  `half_day`) are computed by the API and never accepted from a client.

## Documentation

- `REVIEW.md` — Part A: bugs found in the starter and how they were fixed.
- `DECISIONS.md` — Part C: design decisions and answers to the five questions.

## Dev helpers

Files prefixed with `_` at the repo root are local development helpers, not
required by the assignment:

- `_epoch_helper.py` — quick epoch-millis calculator.
- `_test_overtime.py` — unit tests for `compute_overtime`.
- `_test_race.py` — concurrency smoke test for punch-in.

These are not part of the submission contract; the required submission is
`app/main.py`, `requirements.txt`, `README.md`, `REVIEW.md`, `DECISIONS.md`.
