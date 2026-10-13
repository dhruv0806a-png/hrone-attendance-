"""
Employee Attendance & Analytics API - STARTER

Run:  uvicorn app.main:app --port 8000
Env:  MONGO_URI, MONGO_DB (a local .env is loaded for convenience)

This file was written quickly by a colleague who has left the company. The happy path
works, but nobody has reviewed it. Read PROBLEM_STATEMENT.docx for what is expected of you,
openapi.yaml for the contract and DATA_MODEL.md for what is stored in MongoDB.
"""
import os
from datetime import datetime, timedelta, timezone
from typing import Optional

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Query, Request
from pymongo.errors import DuplicateKeyError
from pydantic import BaseModel,Field,StrictInt
from pymongo import MongoClient
from decimal import Decimal, ROUND_HALF_UP
from contextlib import asynccontextmanager
from typing import Literal
from pydantic import StrictInt 
from calendar import monthrange
from datetime import date as _date

load_dotenv()

client = MongoClient(
    os.getenv("MONGO_URI", "mongodb://localhost:27017"),
    serverSelectionTimeoutMS=2000,
)
db = client[os.getenv("MONGO_DB", "attendance_db")]

IST = timezone(timedelta(hours=5, minutes=30))


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Create all indexes at startup (idempotent).
    db.employees.create_index([("emp_code", 1)], unique=True, name="uniq_emp_code")
    db.employees.create_index([("department", 1)], name="idx_emp_department")

    db.attendance_logs.create_index(
        [("emp_code", 1), ("date", 1)], unique=True, name="uniq_emp_date"
    )
    db.attendance_logs.create_index(
        [("emp_code", 1), ("date", -1)], name="idx_emp_date_desc"
    )
    db.attendance_logs.create_index(
        [("date", 1), ("status", 1)], name="idx_date_status"
    )
    yield


app = FastAPI(
    title="Employee Attendance & Analytics API",
    version="2.0.0",
    lifespan=lifespan,
)


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #
def compute_late_minutes(punch_in: datetime, shift_start: str) -> int:
    ist = punch_in.astimezone(IST)
    h, m = map(int, shift_start.split(":"))
    shift_start_ist = ist.replace(hour=h, minute=m, second=0, microsecond=0)
    minutes = int((punch_in - shift_start_ist).total_seconds() // 60)
    return minutes if minutes > 10 else 0


def compute_work_hours(punch_in: datetime, punch_out: datetime) -> float:
    hours = (punch_out - punch_in).total_seconds() / 3600
    return float(Decimal(str(hours)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


def compute_overtime(punch_out: datetime, shift_end: str, date_str: str, shift_start: str) -> int:
   
    punch_out_ist = punch_out.astimezone(IST) if punch_out.tzinfo else punch_out.replace(tzinfo=timezone.utc).astimezone(IST)
    
    h, m = map(int, shift_end.split(":"))
   
    shift_date_ist = datetime.fromisoformat(date_str).replace(tzinfo=IST)
    end_ist = shift_date_ist.replace(hour=h, minute=m, second=0, microsecond=0)
    
   
    sh, sm = map(int, shift_start.split(":"))
    if (h, m) <= (sh, sm):
        end_ist = end_ist + timedelta(days=1)
    
    delta_seconds = (punch_out_ist - end_ist).total_seconds()
    minutes = int(delta_seconds // 60)
    return minutes if minutes >= 30 else 0

def _to_epoch_ms(dt: datetime) -> int:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return int(dt.timestamp() * 1000)


def _serialize_employee(doc: dict) -> dict:
    out = dict(doc)
    out.pop("_id", None)
    if isinstance(out.get("created_at"), datetime):
        out["created_at"] = _to_epoch_ms(out["created_at"])
    return out

def _serialize_attendance(doc: dict) -> dict:
    out = dict(doc)
    out.pop("_id", None)
    for f in ("punch_in", "punch_out"):
        if isinstance(out.get(f), datetime):
            out[f] = _to_epoch_ms(out[f])
    hist = out.get("history") or []
    new_hist = []
    for entry in hist:
        e = dict(entry)
        if isinstance(e.get("at"), datetime):
            e["at"] = _to_epoch_ms(e["at"])
        changes = e.get("changes") or {}
        new_changes = {}
        for k, v in changes.items():
            nv = dict(v)
            if k in ("punch_in", "punch_out"):
                for side in ("from", "to"):
                    if isinstance(nv.get(side), datetime):
                        nv[side] = _to_epoch_ms(nv[side])
            new_changes[k] = nv
        e["changes"] = new_changes
        new_hist.append(e)
    out["history"] = new_hist
    return out
def _recompute_derived(record: dict, emp: dict) -> dict:
    """Given a full record (with punch_in, punch_out, status) and an employee,
    return the derived fields {late_minutes, work_hours, overtime_minutes, half_day}."""
    status = record.get("status")
    punch_in = record.get("punch_in")
    punch_out = record.get("punch_out")

    if status in ("ABSENT", "LEAVE"):
        return {"late_minutes": 0, "work_hours": None, "overtime_minutes": 0, "half_day": False}

    late_minutes = 0
    if punch_in is not None:
        late_minutes = compute_late_minutes(punch_in, emp["shift_start"])

    if punch_in is None or punch_out is None:
        return {"late_minutes": late_minutes, "work_hours": None, "overtime_minutes": 0, "half_day": False}

    work_hours = compute_work_hours(punch_in, punch_out)
    overtime_minutes = compute_overtime(punch_out, emp["shift_end"], record["date"], emp["shift_start"])
    half_day = work_hours < 4.50
    return {
        "late_minutes": late_minutes,
        "work_hours": work_hours,
        "overtime_minutes": overtime_minutes,
        "half_day": half_day,
    }
_HISTORY_FIELDS = ("status", "punch_in", "punch_out", "work_hours", "late_minutes", "overtime_minutes", "half_day")


def _diff(old: dict, new: dict) -> dict:
    """Return {field: {from, to}} for fields that actually changed."""
    out = {}
    for f in _HISTORY_FIELDS:
        o = old.get(f)
        n = new.get(f)
        if o != n:
            out[f] = {"from": o, "to": n}
    return out
def _working_days(year: int, month: int, joined_on: Optional[str]) -> int:
    """Count Mon-Fri days in the given month.
    If joined_on is provided and falls within the month, count from joined_on."""
    first = _date(year, month, 1)
    last = _date(year, month, monthrange(year, month)[1])

    start = first
    if joined_on:
        j = _date.fromisoformat(joined_on)
        if j > first:
            start = j
        # If joined_on > last day of month, there are no working days.
        if j > last:
            return 0

    count = 0
    cur = start
    while cur <= last:
        if cur.weekday() < 5:  # Mon=0, Fri=4
            count += 1
        cur = cur + timedelta(days=1)
    return count
def _is_weekday(date_str: str) -> bool:
    return _date.fromisoformat(date_str).weekday() < 5
def _parse_date(s: str) -> _date:
    try:
        return _date.fromisoformat(s)
    except ValueError:
        raise HTTPException(422, f"invalid date: {s}")
# --------------------------------------------------------------------------- #
# Models
# --------------------------------------------------------------------------- #
class EmployeeIn(BaseModel):
    emp_code: str
    name: str
    email: str
    department: str
    shift_start: str = Field(default="09:30", pattern=r"^\d{2}:\d{2}$")
    shift_end: str = Field(default="18:30", pattern=r"^\d{2}:\d{2}$")
    joined_on: str = Field(pattern=r"^\d{4}-\d{2}-\d{2}$")


class PunchInIn(BaseModel):
    emp_code: str
    punched_at: Optional[StrictInt] = None
    status: Literal["PRESENT", "WFH", "ON_DUTY"] = "PRESENT"

class PunchOutIn(BaseModel):
    emp_code: str
    punched_at: Optional[StrictInt] = None

class RegularizeIn(BaseModel):
    status: Optional[Literal["PRESENT", "WFH", "ON_DUTY", "ABSENT", "LEAVE"]] = None
    punch_in: Optional[StrictInt] = None
    punch_out: Optional[StrictInt] = None
    reason: str = Field(min_length=5, max_length=200)
    regularized_by: str = Field(min_length=1, max_length=50)
# --------------------------------------------------------------------------- #
# Endpoints provided
# --------------------------------------------------------------------------- #
@app.get("/health")
def health():
    try:
        db.command("ping")
    except Exception:
        raise HTTPException(503, "database unavailable")
    return {"status": "ok"}


@app.post("/employees", status_code=201)
def create_employee(body: EmployeeIn):
    doc = body.model_dump()
    doc["created_at"] = datetime.now(timezone.utc).replace(microsecond=0)
    try:
        db.employees.insert_one(doc)
    except DuplicateKeyError:
        raise HTTPException(409, "emp_code already exists")
    return _serialize_employee(doc)


@app.get("/employees")
def list_employees(
    department: Optional[str] = None,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
):
    q = {}
    if department:
        q["department"] = department
    skip = (page - 1) * page_size
    total = db.employees.count_documents(q)
    items = [
        _serialize_employee(d)
        for d in db.employees.find(q)
                            .sort("emp_code", 1)
                            .skip(skip)
                            .limit(page_size)
    ]
    return {"items": items, "total": total, "page": page, "page_size": page_size}


@app.post("/attendance/punch-in", status_code=201)
def punch_in(body: PunchInIn):
    emp = db.employees.find_one({"emp_code": body.emp_code})
    if emp is None:
        raise HTTPException(404, "employee not found")

    if body.punched_at is None:
        ts = datetime.now(timezone.utc).replace(microsecond=0)
    else:
        ts = datetime.fromtimestamp(body.punched_at / 1000, tz=timezone.utc).replace(microsecond=0)

    d = ts.astimezone(IST).date().isoformat()

    doc = {
        "emp_code": body.emp_code,
        "date": d,
        "status": body.status,
        "punch_in": ts,
        "punch_out": None,
        "work_hours": None,
        "late_minutes": compute_late_minutes(ts, emp["shift_start"]),
        "overtime_minutes": 0,
        "half_day": False,
        "history": [],
    }

    try:
        db.attendance_logs.insert_one(doc)
    except DuplicateKeyError:
        raise HTTPException(409, "already punched in for this date")

    doc.pop("_id", None)
    return _serialize_attendance(doc)


@app.get("/attendance")
def list_attendance(
    emp_code: Optional[str] = None,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    status: Optional[str] = None,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
):
    q = {}
    if emp_code:
        q["emp_code"] = emp_code
    if date_from or date_to:
        q["date"] = {}
        if date_from:
            q["date"]["$gte"] = date_from
        if date_to:
            q["date"]["$lte"] = date_to
    if status:
        q["status"] = status

    skip = (page - 1) * page_size
    total = db.attendance_logs.count_documents(q)
    items = [
        _serialize_attendance(d)
        for d in db.attendance_logs.find(q, {"_id": 0})
                          .sort([("date", -1), ("emp_code", 1)])
                          .skip(skip)
                          .limit(page_size)
    ]
    return {"items": items, "total": total, "page": page, "page_size": page_size}

@app.post("/attendance/punch-out")
def punch_out(body: PunchOutIn):
    # 1) Employee must exist.
    emp = db.employees.find_one({"emp_code": body.emp_code})
    if emp is None:
        raise HTTPException(404, "employee not found")

    # 2) Resolve punched_at (default now).
    if body.punched_at is None:
        ts = datetime.now(timezone.utc).replace(microsecond=0)
    else:
        ts = datetime.fromtimestamp(body.punched_at / 1000, tz=timezone.utc).replace(microsecond=0)

    # 3) Find the most recent open record for this employee whose punch_in <= ts.
    record = db.attendance_logs.find_one(
        {
            "emp_code": body.emp_code,
            "punch_out": None,
            "punch_in": {"$ne": None, "$lte": ts},
        },
        sort=[("punch_in", -1)],
    )
    if record is None:
        raise HTTPException(404, "no open punch-in found")

    # 4) Validate the 24-hour window.
    punch_in = record["punch_in"]
    if punch_in.tzinfo is None:
        punch_in = punch_in.replace(tzinfo=timezone.utc)
    delta_seconds = (ts - punch_in).total_seconds()
    if delta_seconds <= 0:
        raise HTTPException(422, "punched_at must be after punch_in")
    if delta_seconds > 24 * 3600:
        raise HTTPException(422, "punched_at must be within 24 hours of punch_in")

    # 5) Compute derived fields.
    work_hours = compute_work_hours(punch_in, ts)
    overtime_minutes = compute_overtime(ts, emp["shift_end"], record["date"], emp["shift_start"])
    half_day = work_hours < 4.50

    # 6) Race-safe update: filter on punch_out == None so two concurrent calls can't both succeed.
    result = db.attendance_logs.find_one_and_update(
        {"_id": record["_id"], "punch_out": None},
        {
            "$set": {
                "punch_out": ts,
                "work_hours": work_hours,
                "overtime_minutes": overtime_minutes,
                "half_day": half_day,
            }
        },
        return_document=True,  # after
    )
    if result is None:
        # Another concurrent punch-out beat us to it.
        raise HTTPException(409, "already punched out")

    return _serialize_attendance(result)
@app.patch("/attendance/{emp_code}/{date}")
def regularize(emp_code: str, date: str, body: RegularizeIn):
    # 1) Employee must exist.
    emp = db.employees.find_one({"emp_code": emp_code})
    if emp is None:
        raise HTTPException(404, "employee not found")

    # 2) Record must exist.
    existing = db.attendance_logs.find_one({"emp_code": emp_code, "date": date})
    if existing is None:
        raise HTTPException(404, "no record for that date")

    # 3) Build the "candidate" new state by overlaying the request on the existing doc.
    new = dict(existing)

    if body.status is not None:
        new["status"] = body.status

    # Handle status rules first.
    status = new["status"]

    if status in ("ABSENT", "LEAVE"):
        # Any punch times supplied with this status -> 422
        if body.punch_in is not None or body.punch_out is not None:
            raise HTTPException(422, f"cannot set punch times with status {status}")
        new["punch_in"] = None
        new["punch_out"] = None
    else:
        # Presence status.
        # punch_in can be supplied or already stored.
        if body.punch_in is not None:
            new["punch_in"] = datetime.fromtimestamp(body.punch_in / 1000, tz=timezone.utc).replace(microsecond=0)
        if body.punch_out is not None:
            new["punch_out"] = datetime.fromtimestamp(body.punch_out / 1000, tz=timezone.utc).replace(microsecond=0)
        # If presence status but no punch_in (neither supplied nor stored):
        if new.get("punch_in") is None:
            raise HTTPException(422, "presence status requires punch_in")

        # punch_in must be on the record's date (R1).
        ist_punch_in = new["punch_in"].astimezone(IST)
        if ist_punch_in.date().isoformat() != date:
            raise HTTPException(422, "punch_in must fall on the record's attendance date")

        # punch_out, if present, must be after punch_in and within 24h.
        if new.get("punch_out") is not None:
            delta = (new["punch_out"] - new["punch_in"]).total_seconds()
            if delta <= 0:
                raise HTTPException(422, "punch_out must be after punch_in")
            if delta > 24 * 3600:
                raise HTTPException(422, "punch_out must be within 24 hours of punch_in")

    # 4) Recompute derived fields.
    derived = _recompute_derived(new, emp)
    new.update(derived)

    # 5) Build the diff (before vs after) using a version of `existing` that
    #    has any missing optional fields normalized (history: [], etc.).
    existing_normalized = dict(existing)
    existing_normalized.setdefault("history", [])
    changes = _diff(existing_normalized, new)

    # If nothing changed at all -> 422.
    if not changes:
        raise HTTPException(422, "no changes applied")

    # 6) Build the history entry.
    entry = {
        "at": datetime.now(timezone.utc).replace(microsecond=0),
        "by": body.regularized_by,
        "reason": body.reason,
        "changes": changes,
    }

    # 7) Atomic update: set the new field values and push the history entry.
    set_fields = {
        "status": new["status"],
        "punch_in": new.get("punch_in"),
        "punch_out": new.get("punch_out"),
        "work_hours": new.get("work_hours"),
        "late_minutes": new.get("late_minutes"),
        "overtime_minutes": new.get("overtime_minutes"),
        "half_day": new.get("half_day"),
    }

    result = db.attendance_logs.find_one_and_update(
        {"_id": existing["_id"], "punch_in": existing.get("punch_in"), "punch_out": existing.get("punch_out")},
        {
            "$set": set_fields,
            "$push": {"history": entry},
        },
        return_document=True,
    )

    if result is None:
        # Someone else modified the record between our read and our write.
        raise HTTPException(409, "concurrent modification")

    return _serialize_attendance(result)
@app.get("/analytics/employees/{emp_code}/monthly")
def employee_monthly(
    emp_code: str,
    month: str = Query(..., pattern=r"^\d{4}-(0[1-9]|1[0-2])$"),
):
    # 1) Employee must exist.
    emp = db.employees.find_one({"emp_code": emp_code})
    if emp is None:
        raise HTTPException(404, "employee not found")

    year, mon = int(month[:4]), int(month[5:])
    month_prefix = f"{month}-"  # e.g. "2026-07-"

    # 2) Aggregate attendance_logs for this employee and month.
    pipeline = [
        {"$match": {
            "emp_code": emp_code,
            "date": {"$gte": f"{month_prefix}01", "$lte": f"{month_prefix}31"},
        }},
        {"$group": {
            "_id": None,
            # Count leave records.
            "leave_days": {"$sum": {"$cond": [{"$eq": ["$status", "LEAVE"]}, 1, 0]}},
            # Late count = records with late_minutes > 0.
            "late_count": {"$sum": {"$cond": [{"$gt": [{"$ifNull": ["$late_minutes", 0]}, 0]}, 1, 0]}},
            # Sums.
            "total_late_minutes": {"$sum": {"$ifNull": ["$late_minutes", 0]}},
            "total_overtime_minutes": {"$sum": {"$ifNull": ["$overtime_minutes", 0]}},
            # Collect records we need for present_days (post-processing in Python).
            "presence_records": {"$push": {
                "$cond": [
                    {"$in": ["$status", ["PRESENT", "WFH", "ON_DUTY"]]},
                    {"date": "$date", "half_day": {"$ifNull": ["$half_day", False]}},
                    None,
                ]
            }},
        }},
    ]

    agg = list(db.attendance_logs.aggregate(pipeline))
    if agg:
        row = agg[0]
        presence_records = [r for r in row["presence_records"] if r is not None]
        leave_days = int(row["leave_days"])
        late_count = int(row["late_count"])
        total_late_minutes = int(row["total_late_minutes"])
        total_overtime_minutes = int(row["total_overtime_minutes"])
    else:
        presence_records = []
        leave_days = 0
        late_count = 0
        total_late_minutes = 0
        total_overtime_minutes = 0

    # 3) Compute present_days: only Mon-Fri, half-day = 0.5.
    present_days = 0.0
    for r in presence_records:
        if _is_weekday(r["date"]):
            present_days += 0.5 if r.get("half_day") else 1.0

    # 4) working_days per R7 (from joined_on if mid-month).
    joined_on = emp.get("joined_on")
    working_days = _working_days(year, mon, joined_on)

    # 5) attendance_pct.
    if working_days == 0:
        attendance_pct = None
    else:
        attendance_pct = float(
            Decimal(present_days * 100 / working_days).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        )

    return {
        "emp_code": emp_code,
        "month": month,
        "working_days": working_days,
        "present_days": present_days,
        "leave_days": leave_days,
        "late_count": late_count,
        "total_late_minutes": total_late_minutes,
        "total_overtime_minutes": total_overtime_minutes,
        "attendance_pct": attendance_pct,
    }
@app.get("/analytics/leaderboard/late")
def late_leaderboard(
    month: str = Query(..., pattern=r"^\d{4}-(0[1-9]|1[0-2])$"),
    limit: int = Query(10, ge=1, le=50),
    department: Optional[str] = None,
):
    month_prefix = f"{month}-"

    # Build the match stage for the pipeline.
    match = {
        "date": {"$gte": f"{month_prefix}01", "$lte": f"{month_prefix}31"},
        "late_minutes": {"$gt": 0},
    }

    # If department is given, restrict to employees in that department.
    # We can do this with $lookup at the start of the pipeline.
    pipeline = [
        # Stage 1: restrict to records in the month that are late.
        {"$match": match},

        # Stage 2: join with employees to get name and department,
        # and (optionally) filter by department.
        {"$lookup": {
            "from": "employees",
            "localField": "emp_code",
            "foreignField": "emp_code",
            "as": "emp",
        }},
        {"$unwind": "$emp"},

        # Stage 3: filter by department if provided.
        *([{"$match": {"emp.department": department}}] if department else []),

        # Stage 4: group by emp_code to get total late_minutes and count.
        {"$group": {
            "_id": "$emp_code",
            "name": {"$first": "$emp.name"},
            "department": {"$first": "$emp.department"},
            "total_late_minutes": {"$sum": "$late_minutes"},
            "late_count": {"$sum": 1},
        }},

        # Stage 5: sort by total_late_minutes descending, emp_code ascending.
        {"$sort": {"total_late_minutes": -1, "_id": 1}},

        # Stage 6: assign competition rank via window function.
        {"$setWindowFields": {
            "sortBy": {"total_late_minutes": -1},
            "output": {
                "rank": {
                    "$rank": {},
                },
            },
        }},

        # Stage 7: keep only rows with rank <= limit.
        {"$match": {"rank": {"$lte": limit}}},

        # Stage 8: sort the final result (the $match above may not preserve order).
        {"$sort": {"total_late_minutes": -1, "_id": 1}},

        # Stage 9: project to the response shape.
        {"$project": {
            "_id": 0,
            "emp_code": "$_id",
            "name": 1,
            "department": 1,
            "total_late_minutes": 1,
            "late_count": 1,
            "rank": 1,
        }},
    ]

    items = list(db.attendance_logs.aggregate(pipeline))
    return {"month": month, "items": items}
@app.get("/analytics/departments/summary")
def department_summary(
    month: str = Query(..., pattern=r"^\d{4}-(0[1-9]|1[0-2])$"),
    department: Optional[str] = None,
):
    month_prefix = f"{month}-"
    month_last = f"{month}-31"

    emp_match = {"joined_on": {"$lte": month_last}}
    if department:
        emp_match["department"] = department

    pipeline = [
        {"$match": emp_match},

        {"$lookup": {
            "from": "attendance_logs",
            "let": {"code": "$emp_code"},
            "pipeline": [
                {"$match": {
                    "$expr": {"$eq": ["$emp_code", "$$code"]},
                    "date": {"$gte": f"{month_prefix}01", "$lte": month_last},
                }},
                {"$addFields": {
                    "is_presence": {"$in": ["$status", ["PRESENT", "WFH", "ON_DUTY"]]},
                    "is_weekday": {
                        "$in": [
                            {"$dayOfWeek": {"$dateFromString": {"dateString": "$date"}}},
                            [2, 3, 4, 5, 6],
                        ]
                    },
                }},
            ],
            "as": "logs",
        }},

        # Per-employee aggregates.
        {"$addFields": {
            "presence_days": {
                "$sum": {
                    "$map": {
                        "input": "$logs",
                        "as": "l",
                        "in": {
                            "$cond": [
                                {"$and": ["$$l.is_presence", "$$l.is_weekday"]},
                                {"$cond": [{"$ifNull": ["$$l.half_day", False]}, 0.5, 1.0]},
                                0,
                            ]
                        },
                    }
                }
            },
            "late_count": {
                "$sum": {
                    "$map": {
                        "input": "$logs",
                        "as": "l",
                        "in": {"$cond": [{"$gt": [{"$ifNull": ["$$l.late_minutes", 0]}, 0]}, 1, 0]},
                    }
                }
            },
            "total_late_minutes": {"$sum": {"$map": {"input": "$logs", "as": "l", "in": {"$ifNull": ["$$l.late_minutes", 0]}}}},
            "leave_count": {
                "$sum": {
                    "$map": {
                        "input": "$logs",
                        "as": "l",
                        "in": {"$cond": [{"$eq": ["$$l.status", "LEAVE"]}, 1, 0]},
                    }
                }
            },
            "on_duty_count": {
                "$sum": {
                    "$map": {
                        "input": "$logs",
                        "as": "l",
                        "in": {"$cond": [{"$eq": ["$$l.status", "ON_DUTY"]}, 1, 0]},
                    }
                }
            },
            "work_hours_sum": {
                "$sum": {
                    "$map": {
                        "input": "$logs",
                        "as": "l",
                        "in": {"$cond": [{"$ne": ["$$l.work_hours", None]}, "$$l.work_hours", 0]},
                    }
                }
            },
            "work_hours_count": {
                "$sum": {
                    "$map": {
                        "input": "$logs",
                        "as": "l",
                        "in": {"$cond": [{"$ne": ["$$l.work_hours", None]}, 1, 0]},
                    }
                }
            },
        }},

        # Group by department.
        {"$group": {
            "_id": "$department",
            "headcount": {"$sum": 1},
            "present_days": {"$sum": "$presence_days"},
            "late_count": {"$sum": "$late_count"},
            "total_late_minutes": {"$sum": "$total_late_minutes"},
            "leave_count": {"$sum": "$leave_count"},
            "on_duty_count": {"$sum": "$on_duty_count"},
            "work_hours_sum": {"$sum": "$work_hours_sum"},
            "work_hours_count": {"$sum": "$work_hours_count"},
        }},

        {"$addFields": {
            "avg_work_hours": {
                "$cond": [
                    {"$gt": ["$work_hours_count", 0]},
                    {"$divide": ["$work_hours_sum", "$work_hours_count"]},
                    None,
                ]
            },
        }},

        {"$project": {
            "_id": 0,
            "department": "$_id",
            "headcount": 1,
            "present_days": 1,
            "avg_work_hours": 1,
            "late_count": 1,
            "total_late_minutes": 1,
            "leave_count": 1,
            "on_duty_count": 1,
        }},
        {"$sort": {"department": 1}},
    ]

    raw_items = list(db.employees.aggregate(pipeline))

    # Post-process R8 rounding for avg_work_hours.
    items = []
    for it in raw_items:
        aw = it.get("avg_work_hours")
        if aw is not None:
            aw = float(Decimal(str(aw)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))
        it["avg_work_hours"] = aw
        it["present_days"] = float(it.get("present_days", 0))
        items.append(it)

    return {"month": month, "items": items}
@app.get("/analytics/departments/{department}/trend")
def department_trend(
    department: str,
    from_: str = Query(..., alias="from", pattern=r"^\d{4}-\d{2}-\d{2}$"),
    to: str = Query(..., pattern=r"^\d{4}-\d{2}-\d{2}$"),
):
    start = _parse_date(from_)
    end = _parse_date(to)
    if end < start:
        raise HTTPException(422, "to must be >= from")
    if (end - start).days > 91:
        raise HTTPException(422, "range exceeds 92 days")

    if db.employees.find_one({"department": department}) is None:
        raise HTTPException(404, "department not found")

    day_count = (end - start).days + 1

    pipeline = [
        # 1) Employees in this department.
        {"$match": {"department": department}},
        {"$project": {"_id": 0, "emp_code": 1, "joined_on": 1}},

        # 2) Collapse to one doc: employees list.
        {"$group": {
            "_id": None,
            "employees": {"$push": {"emp_code": "$emp_code", "joined_on": "$joined_on"}},
        }},

        # 3) Lookup logs for these employees within the range.
        {"$lookup": {
            "from": "attendance_logs",
            "let": {
                "codes": "$employees.emp_code",
            },
            "pipeline": [
                {"$match": {
                    "$expr": {"$in": ["$emp_code", "$$codes"]},
                    "date": {"$gte": from_, "$lte": to},
                }},
                {"$addFields": {
                    "is_presence": {"$in": ["$status", ["PRESENT", "WFH", "ON_DUTY"]]},
                }},
            ],
            "as": "logs",
        }},

        # 4) Build the days array.
        {"$project": {
            "_id": 0,
            "employees": 1,
            "logs": 1,
            "days": {
                "$map": {
                    "input": {"$range": [0, day_count]},
                    "as": "i",
                    "in": {
                        "$let": {
                            "vars": {
                                "day_str": {
                                    "$dateToString": {
                                        "format": "%Y-%m-%d",
                                        "date": {
                                            "$dateAdd": {
                                                "startDate": {"$dateFromString": {"dateString": from_}},
                                                "unit": "day",
                                                "amount": "$$i",
                                            }
                                        },
                                    }
                                },
                            },
                            "in": {
                                "date": "$$day_str",
                                "day_of_week": {
                                    "$dayOfWeek": {
                                        "$dateFromString": {"dateString": "$$day_str"}
                                    }
                                },
                                "headcount": {
                                    "$size": {
                                        "$filter": {
                                            "input": "$employees",
                                            "as": "e",
                                            "cond": {"$lte": ["$$e.joined_on", "$$day_str"]},
                                        }
                                    }
                                },
                                "present_count": {
                                    "$sum": {
                                        "$map": {
                                            "input": {
                                                "$filter": {
                                                    "input": "$logs",
                                                    "as": "l",
                                                    "cond": {"$eq": ["$$l.date", "$$day_str"]},
                                                }
                                            },
                                            "as": "l",
                                            "in": {
                                                "$cond": [
                                                    "$$l.is_presence",
                                                    {"$cond": [{"$ifNull": ["$$l.half_day", False]}, 0.5, 1.0]},
                                                    0,
                                                ]
                                            },
                                        }
                                    }
                                },
                                "late_count": {
                                    "$sum": {
                                        "$map": {
                                            "input": {
                                                "$filter": {
                                                    "input": "$logs",
                                                    "as": "l",
                                                    "cond": {"$eq": ["$$l.date", "$$day_str"]},
                                                }
                                            },
                                            "as": "l",
                                            "in": {
                                                "$cond": [
                                                    {"$gt": [{"$ifNull": ["$$l.late_minutes", 0]}, 0]},
                                                    1,
                                                    0,
                                                ]
                                            },
                                        }
                                    }
                                },
                            },
                        }
                    },
                }
            },
        }},

        # 5) Unwind days.
        {"$unwind": "$days"},
        {"$replaceRoot": {"newRoot": "$days"}},

        # 6) Compute is_working_day and attendance_rate.
        {"$addFields": {
            "is_working_day": {"$in": ["$day_of_week", [2, 3, 4, 5, 6]]},
        }},
        {"$addFields": {
            "attendance_rate": {
                "$cond": [
                    {"$and": ["$is_working_day", {"$gt": ["$headcount", 0]}]},
                    {"$divide": ["$present_count", "$headcount"]},
                    None,
                ]
            },
        }},

        # 7) Moving average over the last 7 non-null rates.
        {"$setWindowFields": {
            "sortBy": {"date": 1},
            "output": {
                "rate_window": {
                    "$push": "$attendance_rate",
                    "window": {"documents": [-6, 0]},
                },
            },
        }},
        {"$addFields": {
            "moving_avg_7d_raw": {
                "$let": {
                    "vars": {
                        "nonnull": {
                            "$filter": {
                                "input": "$rate_window",
                                "as": "r",
                                "cond": {"$ne": ["$$r", None]},
                            }
                        }
                    },
                    "in": {
                        "$cond": [
                            {"$gt": [{"$size": "$$nonnull"}, 0]},
                            {"$avg": "$$nonnull"},
                            None,
                        ]
                    },
                }
            },
        }},

        # 8) Final projection.
        {"$project": {
            "_id": 0,
            "date": 1,
            "is_working_day": 1,
            "headcount": 1,
            "present_count": {"$ifNull": ["$present_count", 0]},
            "late_count": 1,
            "attendance_rate": 1,
            "moving_avg_7d": "$moving_avg_7d_raw",
        }},
    ]

    raw_items = list(db.employees.aggregate(pipeline))

    # R8 rounding: 4 decimals half-up for both rates.
    items = []
    for it in raw_items:
        for key in ("attendance_rate", "moving_avg_7d"):
            v = it.get(key)
            if v is not None:
                v = float(Decimal(str(v)).quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP))
            it[key] = v
        it["present_count"] = float(it.get("present_count", 0))
        items.append(it)

    return {"department": department, "items": items}
@app.get("/admin/explain/{endpoint}")
def admin_explain(
    endpoint: str,
    emp_code: Optional[str] = None,
    month: Optional[str] = None,
    department: Optional[str] = None,
    limit: int = Query(10, ge=1, le=50),
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    status: Optional[str] = None,
    from_: Optional[str] = Query(None, alias="from"),
    to: Optional[str] = None,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
):
    if endpoint not in ("attendance_list", "employee_monthly", "department_summary", "late_leaderboard", "department_trend"):
        raise HTTPException(422, f"unknown endpoint: {endpoint}")

    if endpoint == "attendance_list":
        q = {}
        if emp_code:
            q["emp_code"] = emp_code
        if date_from or date_to:
            q["date"] = {}
            if date_from:
                q["date"]["$gte"] = date_from
            if date_to:
                q["date"]["$lte"] = date_to
        if status:
            q["status"] = status
        skip = (page - 1) * page_size
        cursor = db.attendance_logs.find(q).sort([("date", -1), ("emp_code", 1)]).skip(skip).limit(page_size)
        explain = cursor.explain()
        return {"endpoint": endpoint, "collection": "attendance_logs", "explain": explain}

    if endpoint == "employee_monthly":
        if not emp_code or not month:
            raise HTTPException(422, "emp_code and month are required")
        month_prefix = f"{month}-"
        pipeline = [
            {"$match": {
                "emp_code": emp_code,
                "date": {"$gte": f"{month_prefix}01", "$lte": f"{month_prefix}31"},
            }},
            {"$group": {
                "_id": None,
                "leave_days": {"$sum": {"$cond": [{"$eq": ["$status", "LEAVE"]}, 1, 0]}},
                "late_count": {"$sum": {"$cond": [{"$gt": [{"$ifNull": ["$late_minutes", 0]}, 0]}, 1, 0]}},
                "total_late_minutes": {"$sum": {"$ifNull": ["$late_minutes", 0]}},
                "total_overtime_minutes": {"$sum": {"$ifNull": ["$overtime_minutes", 0]}},
                "presence_records": {"$push": {
                    "$cond": [
                        {"$in": ["$status", ["PRESENT", "WFH", "ON_DUTY"]]},
                        {"date": "$date", "half_day": {"$ifNull": ["$half_day", False]}},
                        None,
                    ]
                }},
            }},
        ]
        explain = db.command({
            "explain": {"aggregate": "attendance_logs", "pipeline": pipeline, "cursor": {}},
            "verbosity": "executionStats",
        })
        return {"endpoint": endpoint, "collection": "attendance_logs", "explain": explain}

    if endpoint == "department_summary":
        if not month:
            raise HTTPException(422, "month is required")
        month_prefix = f"{month}-"
        month_last = f"{month}-31"
        emp_match = {"joined_on": {"$lte": month_last}}
        if department:
            emp_match["department"] = department
        pipeline = [
            {"$match": emp_match},
            {"$lookup": {
                "from": "attendance_logs",
                "let": {"code": "$emp_code"},
                "pipeline": [
                    {"$match": {
                        "$expr": {"$eq": ["$emp_code", "$$code"]},
                        "date": {"$gte": f"{month_prefix}01", "$lte": month_last},
                    }},
                    {"$addFields": {
                        "is_presence": {"$in": ["$status", ["PRESENT", "WFH", "ON_DUTY"]]},
                        "is_weekday": {"$in": [{"$dayOfWeek": {"$dateFromString": {"dateString": "$date"}}}, [2, 3, 4, 5, 6]]},
                    }},
                ],
                "as": "logs",
            }},
            {"$addFields": {
                "presence_days": {"$sum": {"$map": {"input": "$logs", "as": "l", "in": {
                    "$cond": [
                        {"$and": ["$$l.is_presence", "$$l.is_weekday"]},
                        {"$cond": [{"$ifNull": ["$$l.half_day", False]}, 0.5, 1.0]},
                        0,
                    ]
                }}}},
            }},
            {"$group": {
                "_id": "$department",
                "headcount": {"$sum": 1},
                "present_days": {"$sum": "$presence_days"},
            }},
        ]
        explain = db.command({
            "explain": {"aggregate": "employees", "pipeline": pipeline, "cursor": {}},
            "verbosity": "executionStats",
        })
        return {"endpoint": endpoint, "collection": "employees", "explain": explain}

    if endpoint == "late_leaderboard":
        if not month:
            raise HTTPException(422, "month is required")
        month_prefix = f"{month}-"
        pipeline = [
            {"$match": {
                "date": {"$gte": f"{month_prefix}01", "$lte": f"{month_prefix}31"},
                "late_minutes": {"$gt": 0},
            }},
            {"$lookup": {"from": "employees", "localField": "emp_code", "foreignField": "emp_code", "as": "emp"}},
            {"$unwind": "$emp"},
            {"$group": {"_id": "$emp_code", "total_late_minutes": {"$sum": "$late_minutes"}}},
            {"$sort": {"total_late_minutes": -1, "_id": 1}},
        ]
        explain = db.command({
            "explain": {"aggregate": "attendance_logs", "pipeline": pipeline, "cursor": {}},
            "verbosity": "executionStats",
        })
        return {"endpoint": endpoint, "collection": "attendance_logs", "explain": explain}

    if endpoint == "department_trend":
        if not department or not from_ or not to:
            raise HTTPException(422, "department, from, and to are required")
        pipeline = [
            {"$match": {"department": department}},
            {"$lookup": {
                "from": "attendance_logs",
                "let": {"codes": "$emp_code"},
                "pipeline": [
                    {"$match": {
                        "$expr": {"$eq": ["$emp_code", "$$codes"]},
                        "date": {"$gte": from_, "$lte": to},
                    }},
                ],
                "as": "logs",
            }},
        ]
        explain = db.command({
            "explain": {"aggregate": "employees", "pipeline": pipeline, "cursor": {}},
            "verbosity": "executionStats",
        })
        return {"endpoint": endpoint, "collection": "employees", "explain": explain}
# --------------------------------------------------------------------------- #
# TODO - the rest of the contract (see openapi.yaml):
#   POST  /attendance/punch-out
#   PATCH /attendance/{emp_code}/{date}
#   GET   /analytics/employees/{emp_code}/monthly
#   GET   /analytics/departments/summary
#   GET   /analytics/leaderboard/late
#   GET   /analytics/departments/{department}/trend
#   GET   /admin/explain/{endpoint}
# --------------------------------------------------------------------------- #
