"""
Concurrency test: fire 10 punch-ins at once.
Expected: 1 success (201), 9 conflicts (409).
Run: python _test_race.py
"""
import concurrent.futures
import json
import urllib.request

URL = "http://localhost:8000/attendance/punch-in"
BODY = json.dumps({
    "emp_code": "EMP0004",
    "punched_at": 1797211800000,   # 2026-12-14 09:30 IST
    "status": "PRESENT",
}).encode()

def post_once():
    req = urllib.request.Request(
        URL, data=BODY, headers={"Content-Type": "application/json"}, method="POST"
    )
    try:
        with urllib.request.urlopen(req) as resp:
            return resp.status
    except urllib.error.HTTPError as e:
        return e.code

with concurrent.futures.ThreadPoolExecutor(max_workers=10) as pool:
    results = list(pool.map(lambda _: post_once(), range(10)))

print("Statuses:", sorted(results))
print(f"201s: {results.count(201)}, 409s: {results.count(409)}")
assert results.count(201) == 1, f"expected exactly 1 success, got {results.count(201)}"
assert results.count(409) == 9, f"expected 9 conflicts, got {results.count(409)}"
print("✅ PASS")