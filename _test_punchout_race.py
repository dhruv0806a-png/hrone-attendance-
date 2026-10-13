import concurrent.futures, json, urllib.request, urllib.error, time

# First: punch in
punch_in_url = "http://localhost:8000/attendance/punch-in"
punch_in_body = json.dumps({
    "emp_code": "EMP0003",
    "punched_at": 1799294400000,   # some fresh date in Jan 2027, 09:30 IST
    "status": "PRESENT",
}).encode()

try:
    req = urllib.request.Request(punch_in_url, data=punch_in_body,
                                 headers={"Content-Type": "application/json"}, method="POST")
    urllib.request.urlopen(req)
except urllib.error.HTTPError as e:
    print("punch-in:", e.code)
    if e.code != 409:
        raise

# Now: fire 2 punch-outs concurrently
punch_out_url = "http://localhost:8000/attendance/punch-out"
punch_out_body = json.dumps({
    "emp_code": "EMP0003",
    "punched_at": 1799326800000,   # 18:30 IST same day
}).encode()

def post_once():
    req = urllib.request.Request(punch_out_url, data=punch_out_body,
                                 headers={"Content-Type": "application/json"}, method="POST")
    try:
        with urllib.request.urlopen(req) as r:
            return r.status
    except urllib.error.HTTPError as e:
        return e.code

with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
    results = list(pool.map(lambda _: post_once(), range(2)))

print("Statuses:", sorted(results))
assert sorted(results) == [200, 409], f"expected [200, 409], got {sorted(results)}"
print("✅ PASS")