# DECISIONS.md

## 1. Which indexes did you create and why?

- **`employees.emp_code` (unique)** — `emp_code` is the business key everywhere. The unique constraint turns concurrent creates into deterministic 409s and backs every `$lookup` and `$match` on `emp_code`.
- **`employees.department`** — speeds up `GET /employees?department=X` and the department `$lookup` / grouping used by the analytics endpoints.
- **`attendance_logs.(emp_code, date)` (unique)** — enforces one record per employee per attendance day (R1). Enables race-safe punch-in via `DuplicateKeyError` and backs monthly summaries and the punch-out lookup.
- **`attendance_logs.(emp_code, date desc)`** — supports `GET /attendance?emp_code=X` sorted by date descending without an in-memory sort.
- **`attendance_logs.(date, status)`** — accelerates date-range + status filters used by analytics (trend, leaderboard, department summary).

All are created at startup and idempotently, so a fresh clone against the grader's seeded database creates them without any manual step.

## 2. What exactly happens when two identical punch-ins arrive at the same instant?

Both requests reach the app. Neither performs a pre-check. Both attempt `insert_one` on the same `(emp_code, date)`. MongoDB accepts one and rejects the other with `E11000 duplicate key error` (`DuplicateKeyError`). The endpoint catches that and returns **409 Conflict**; the winner returns **201 Created**. The guarantee comes from the unique compound index at the database level, not from application logic. The same reasoning protects employee creation.

## 3. What does your leaderboard return for a tie at the cutoff?

Ranks are assigned by MongoDB's `$setWindowFields` / `$rank`, which implements standard competition ranking: ties share a rank and the next rank is skipped (1, 2, 2, 4). The `limit` is applied **after** ranking as `$match: {rank: {$lte: limit}}`. So if `limit=3` and four employees are tied at rank 2, **all four** are returned. The response can therefore contain more rows than `limit` when ties exist at or below the cutoff. Ties are ordered by `emp_code` ascending, which gives deterministic output.

## 4. How does your department summary count employees with no logs?

The aggregation starts from `employees` (not from logs). It filters by `joined_on <= "{month}-31"` per R9, then uses `$lookup` into `attendance_logs` and computes per-employee aggregates over the returned array with `$map`/`$sum`. Empty arrays contribute zero. Grouping by department then counts `headcount = $sum: 1`, so employees with **zero** logs still appear in the headcount. `avg_work_hours` divides the sum of non-null `work_hours` by their count, so log-less employees do not skew the average. A department with headcount 0 is omitted naturally because there is no group to produce it.

## 5. What would you change for 100× the data?

- **Time-series collections** (MongoDB 5+) for `attendance_logs`, keyed on `punch_in`, to reduce storage and speed up time-range scans.
- **Archive older logs** to a separate collection or database so month-scoped analytics only scan a small slice.
- **Materialized views** per `(department, month)` and per `(emp_code, month)`, refreshed nightly, to avoid recomputing `$lookup` + `$group` on every request.
- **Bounded history arrays** with a TTL or a hard cap if the audit trail grows unbounded.
- **Horizontal sharding** on `emp_code` for write scalability.
- **Replace `/health`'s synchronous ping** with a cached readiness flag refreshed by a small background task, so probes do not hit MongoDB on every call.
