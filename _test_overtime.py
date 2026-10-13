from app.main import compute_work_hours

def test_work_hours():
    from datetime import datetime, timezone, timedelta
    UTC = timezone.utc
    
    # 3 hours 30 minutes exactly
    a = datetime(2026, 1, 1, 9, 0, tzinfo=UTC)
    b = datetime(2026, 1, 1, 12, 30, tzinfo=UTC)
    print("3h30m:", compute_work_hours(a, b))   # expect 3.5
    
    # Case that trips up banker's rounding: exactly X.XX5 hours
    # 3.505 hours = 3h 30m 18s
    a = datetime(2026, 1, 1, 9, 0, 0, tzinfo=UTC)
    b = datetime(2026, 1, 1, 12, 30, 18, tzinfo=UTC)
    print("3h30m18s:", compute_work_hours(a, b))   # expect 3.51 (half-up)
    # With banker's rounding, would be 3.5
    
    # 8.995 hours → should round half-up to 9.0
    a = datetime(2026, 1, 1, 9, 0, 0, tzinfo=UTC)
    b = datetime(2026, 1, 1, 17, 59, 42, tzinfo=UTC)   # 8h 59m 42s = 8.995
    print("8h59m42s:", compute_work_hours(a, b))   # expect 9.0

test_work_hours()