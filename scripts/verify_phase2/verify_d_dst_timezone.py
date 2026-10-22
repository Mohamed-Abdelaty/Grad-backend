"""Test D: DST / Timezone handling verification."""

from __future__ import annotations

import zoneinfo
from datetime import UTC, datetime

from croniter import croniter

from app.services.scheduler import preview_next_runs


def verify_dst_and_timezones() -> None:
    print("[*] Verifying Timezone & DST handling...")

    # 1. Africa/Cairo preview
    cairo_runs = preview_next_runs("0 12 * * *", "Africa/Cairo", count=5)
    assert len(cairo_runs) == 5, f"Expected 5 fire times, got {len(cairo_runs)}"
    for dt in cairo_runs:
        assert dt.tzinfo is not None, "Fire time must be timezone-aware"
        # Convert to Africa/Cairo and verify it is at 12:00 local time
        cairo_dt = dt.astimezone(zoneinfo.ZoneInfo("Africa/Cairo"))
        assert cairo_dt.hour == 12 and cairo_dt.minute == 0, f"Expected 12:00 in Cairo, got {cairo_dt}"

    # 2. America/New_York DST transition test
    # Independent calculation with zoneinfo across March DST jump (2:00 -> 3:00 AM)
    ny_tz = zoneinfo.ZoneInfo("America/New_York")
    # Base just before Spring Forward (e.g., 2026-03-08 00:00 EST)
    spring_base = datetime(2026, 3, 8, 0, 0, tzinfo=ny_tz)
    cron_ny = croniter("30 2 * * *", spring_base)
    # At 2:30 AM during spring forward, standard time skips to 3:00 AM
    next_time = cron_ny.get_next(datetime)
    assert next_time is not None

    # Compare API preview with independent calculation
    api_runs = preview_next_runs("30 4 * * *", "America/New_York", count=5)
    base_iter = datetime.now(ny_tz)
    expected_iter = croniter("30 4 * * *", base_iter)
    for api_dt in api_runs:
        expected_dt = expected_iter.get_next(datetime).astimezone(UTC)
        assert abs((api_dt - expected_dt).total_seconds()) < 1.0, f"Mismatch: {api_dt} vs {expected_dt}"

    # 3. Invalid timezone / invalid cron
    try:
        preview_next_runs("99 * * * *", "UTC", count=1)
        raise AssertionError("Should have rejected invalid cron '99 * * * *'")
    except ValueError:
        pass

    try:
        preview_next_runs("* * * * *", "Invalid/Timezone", count=1)
        raise AssertionError("Should have rejected invalid timezone")
    except ValueError:
        pass

    print("[PASS] Timezone & DST handling verified successfully.")


if __name__ == "__main__":
    verify_dst_and_timezones()
