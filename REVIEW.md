# REVIEW.md — Part A

Review of the starter `app/main.py`. Each entry: **where**, **what was wrong**, **how you would notice**, **how I fixed it**.

## Bugs fixed

### 1. No indexes created at startup

- **Where:** module-level — there was no startup handler.
- **What:** The assignment requires all indexes to be created idempotently at startup. Without them, concurrent inserts race and common queries fall back to a collection scan.
- **How to notice:** Two concurrent punch-ins both return 201. `db.attendance_logs.getIndexes()` shows only `_id_`.
- **Fix:** Added an `asynccontextmanager` lifespan that calls `create_index` for a unique `(emp_code, date)` index, a date+emp_code index for listing, an employee `emp_code` unique index, and supporting indexes for `department`, `(date, status)`.

### 2. Naive datetimes from `datetime.fromtimestamp` / `datetime.now`

- **Where:** `punch_in`, `create_employee`.
- **What:** Both produced timezone-naive datetimes (local time). At the API boundary the value must be UTC epoch millis; on a non-IST host the stored instant was off by the local offset.
- **How to notice:** Run on a UTC host: a punch-in for 10:05 IST is stored as 10:05 UTC instead of 04:35 UTC.
- **Fix:** Added `tz=timezone.utc` to `fromtimestamp` and used `datetime.now(timezone.utc)`; truncated to whole seconds with `.replace(microsecond=0)` per R1.

### 3. Record `date` used the UTC calendar date

- **Where:** `punch_in`.
- **What:** `ts.date()` on a UTC datetime yields the UTC date, but R1 requires the IST date. Punches between 18:30 and 23:59 UTC were assigned the previous IST day.
- **How to notice:** Punch in at 22:00 UTC (03:30 IST next day); stored `date` is `"2026-10-31"`, should be `"2026-11-01"`.
- **Fix:** `ts.astimezone(IST).date().isoformat()`.

### 4. `compute_late_minutes` compared a UTC time to an IST shift start

- **Where:** `compute_late_minutes`.
- **What:** `punch_in.replace(hour=h, minute=m)` was applied to a UTC datetime, so 09:30 IST was being compared as 09:30 UTC. A punch-in at 22:00 UTC yielded `late_minutes = 750`.
- **How to notice:** Punch in at 22:00 UTC with shift start 09:30 → `late_minutes: 750`.
- **Fix:** Convert `punch_in` to IST first (`ist = punch_in.astimezone(IST)`), then compare against the IST shift start. Also switched to floor division (`//`) so partial minutes floor per R2 and gated the grace period on raw seconds (`> 600`).

### 5. `compute_overtime` ignored the 30-minute threshold

- **Where:** `compute_overtime`.
- **What:** R3 counts overtime only when the raw minutes are `>= 30`; the code returned any positive value.
- **How to notice:** Punch out 5 minutes late with a shift end of 18:30 → `overtime_minutes: 5` instead of `0`.
- **Fix:** `minutes if minutes >= 30 else 0`.

### 6. `compute_overtime` mishandled overnight shifts

- **Where:** `compute_overtime`.
- **What:** `shift_end` was constructed on the same calendar day as the shift start; for a `22:00–06:00` shift the shift end is on the following day.
- **How to notice:** Overnight shift 22:00–06:00 on 2026-07-06, punch out at 06:40 → the computed overtime was negative and clamped to 0, dropping 40 legitimate overtime minutes.
- **Fix:** Added `shift_start` to the function signature; when `(shift_end_h, shift_end_m) <= (shift_start_h, shift_start_m)`, add one day to the shift-end instant.

### 7. `compute_work_hours` used Python's `round`

- **Where:** `compute_work_hours`.
- **What:** R4 requires **half-up** rounding, not Python's banker's rounding.
- **How to notice:** 8h 59m 42s → `round(..., 2)` yields `8.99`; half-up yields `9.0`.
- **Fix:** `Decimal(str(hours)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)`.

### 8. `/health` did not check MongoDB

- **Where:** `health`.
- **What:** The contract says 200 only when MongoDB answers, otherwise 503. It always returned 200.
- **How to notice:** Stop MongoDB; `/health` still returns 200.
- **Fix:** `db.command("ping")` inside a try/except; raise 503 on failure.

### 9. Race condition on employee creation

- **Where:** `create_employee`.
- **What:** Check-then-insert; two concurrent requests both pass the check and both insert.
- **How to notice:** Fire 10 concurrent `POST /employees` with the same `emp_code`; more than one returns 201.
- **Fix:** Added a unique index on `employees.emp_code` and switched to `try / except DuplicateKeyError → 409`.

### 10. Race condition on punch-in

- **Where:** `punch_in`.
- **What:** Same check-then-insert pattern on `(emp_code, date)`.
- **How to notice:** Fire 10 concurrent punch-ins for the same employee/date; more than one returns 201.
- **Fix:** Unique index on `(emp_code, date)` and `try / except DuplicateKeyError → 409`.

### 11. Race condition on punch-out

- **Where:** `punch_out`.
- **What:** Two concurrent punch-outs must yield exactly one 200.
- **Fix:** `find_one_and_update` with filter `{"_id": ..., "punch_out": None}` — the loser sees `None` and returns 409.

### 12. Pagination off-by-one in `list_employees`

- **Where:** `list_employees`.
- **What:** `skip = page * page_size` — page 1 skipped the first page.
- **How to notice:** `GET /employees?page=1&page_size=3` returned employees 4–6.
- **Fix:** `skip = (page - 1) * page_size`.

### 13. `total` ignored the department filter

- **Where:** `list_employees`.
- **What:** `count_documents({})` counted the whole collection, not the filtered set.
- **How to notice:** `GET /employees?department=Engineering` returned the total of all employees.
- **Fix:** `count_documents(q)`.

### 14. `page_size` and `page` were unbounded

- **Where:** `list_employees`, `list_attendance`.
- **What:** R10 says page ≥ 1 and page_size ≤ 100.
- **How to notice:** `GET /employees?page_size=100000` was accepted.
- **Fix:** `Query(1, ge=1)` and `Query(20, ge=1, le=100)`.

### 15. No sort in `list_employees`

- **Where:** `list_employees`.
- **What:** The contract requires sorting by `emp_code` ascending.
- **Fix:** `.sort("emp_code", 1)`.

### 16. Missing 404 checks

- **Where:** `punch_in` (unknown employee) and every new endpoint.
- **What:** Accessing `emp["shift_start"]` on `None` raised a 500.
- **How to notice:** `POST /attendance/punch-in` with `emp_code: "EMP9999"` returned 500.
- **Fix:** Explicit `if emp is None: raise HTTPException(404, ...)` at the top of each endpoint.

### 17. `id` leaked into responses

- **Where:** `punch_in`, `list_attendance`.
- **What:** The code added `doc["id"] = str(res.inserted_id)` and `d["id"] = str(d.pop("_id"))`. The contract says `_id` is internal and attendance records are addressed by `(emp_code, date)`.
- **How to notice:** Any response from these endpoints contained an extra `id` field.
- **Fix:** Removed the field entirely; added `_serialize_attendance` (and `_serialize_employee`) to strip `_id` and convert datetimes to epoch millis.

### 18. `list_attendance` loaded the entire collection into memory

- **Where:** `list_attendance`.
- **What:** `list(db.attendance_logs.find(q))` fetched every matching document and then sorted and paginated in Python. At 100k records this is catastrophic.
- **How to notice:** Slow first response as the dataset grows; the explain plan shows a `COLLSCAN`.
- **Fix:** Pushed sorting, skipping and limiting to MongoDB (`.sort(...).skip(...).limit(...)`) and used `count_documents(q)` for `total`.

### 19. `PunchInIn.punched_at` accepted strings and floats

- **Where:** `PunchInIn`.
- **What:** Pydantic coerced `"1700000000000"` and `1700000000000.5` to int, but the contract rejects non-integers with 422.
- **Fix:** `StrictInt`.

### 20. `PunchInIn.status` accepted arbitrary strings

- **Where:** `PunchInIn`.
- **What:** Only `PRESENT`, `WFH`, `ON_DUTY` are valid punch-in statuses.
- **Fix:** `Literal["PRESENT", "WFH", "ON_DUTY"]`.

### 21. `EmployeeIn` did not validate `shift_start` / `shift_end` / `joined_on`

- **Where:** `EmployeeIn`.
- **Fix:** `Field(pattern=...)` on each field.

### 22. `created_at` serialized as an ISO string

- **Where:** `create_employee`, `list_employees`.
- **What:** The response `created_at` must be epoch millis, not an ISO 8601 string.
- **Fix:** `_serialize_employee` converts `created_at` to `int(dt.timestamp() * 1000)` in responses.

### 23. `created_at` written with microseconds

- **Where:** `create_employee`.
- **What:** The contract truncates instants to whole seconds (R1). `datetime.now(timezone.utc)` included microseconds.
- **Fix:** `.replace(microsecond=0)` before storage.

## Items examined and found acceptable

- The starter's module-level `load_dotenv()` and use of `os.getenv` for `MONGO_URI` / `MONGO_DB` is correct: real environment variables take precedence over `.env`.
- `IST = timezone(timedelta(hours=5, minutes=30))` is the correct IST offset.
- Using FastAPI's automatic 422 on validation errors is idiomatic and matches the contract.
- `create_employee` sets `created_at` server-side (the client cannot supply it), which is correct.
- The five `status` values are correctly handled by the sample data; the starter did not need any change to their storage.

## How I tested

- Timezone: `_epoch_helper.py` computes epoch millis for arbitrary IST times.
- Late minutes: tested 09:25 (early), 09:45 (15 min late), 09:40:00 (exactly grace, 0), 09:40:01 (10 min + 1s → 10).
- Overtime: `_test_overtime.py` covers under/at/over 30 minutes, early exit, and overnight shifts.
- Concurrency: `_test_race.py` fires 10 concurrent punch-ins and asserts `[1x201, 9x409]`.
- Pagination and filters: exercised via Swagger UI against the sample data.
