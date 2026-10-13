"""
Dev helper: convert between human times and epoch millis.
Not part of the submission.
Run: python _epoch_helper.py
"""
from datetime import datetime, timezone, timedelta

IST = timezone(timedelta(hours=5, minutes=30))

def to_ms(year, month, day, hour, minute, second=0, tz=IST):
    dt = datetime(year, month, day, hour, minute, second, tzinfo=tz)
    return int(dt.timestamp() * 1000)

def from_ms(ms):
    dt = datetime.fromtimestamp(ms / 1000, tz=timezone.utc)
    return dt, dt.astimezone(IST)

# Add test cases here as needed


print("Test 1 — 2026-11-05 09:25 IST (5 min early):")
print("  millis =", to_ms(2026, 11, 5, 9, 25))
print()

print("Test 2 — 2026-11-06 09:45 IST (15 min late):")
print("  millis =", to_ms(2026, 11, 6, 9, 45))
print()

print("Test 3 — 2026-11-07 09:40:00 IST (exactly 10 min):")
print("  millis =", to_ms(2026, 11, 7, 9, 40, 0))
print()

print("Test 4 — 2026-11-08 09:40:01 IST (10 min 1 sec):")
print("  millis =", to_ms(2026, 11, 8, 9, 40, 1))