"""
AR School ERP — Fee Automation

Auto monthly cycle generation:
- On Admin login (and periodically if the app stays open across a month
  boundary), create a fee_cycles row for every Active student who does
  not yet have one for the current calendar month.
- Never overwrites an existing cycle (duplicate protection in fee_cycles).
- Refreshes OVERDUE statuses at the same time.
- Controlled by system_settings:
    auto_fee_cycle_enabled  "1" / "0"
    auto_fee_due_day        1-28  (due date day-of-month for new cycles)
    auto_fee_grace_days     integer grace period after due date
"""

from datetime import datetime
import calendar

import db
import fee_cycles
import rbac


def _setting_on(key: str, default: str = "1") -> bool:
    return str(db.get_setting(key, default) or default).strip().lower() in (
        "1", "true", "yes", "on",
    )


def _due_date_for(year: int, month: int) -> str:
    """Build YYYY-MM-DD due date from auto_fee_due_day setting.

    Clamps the day to the last day of that month so February / short
    months never produce an invalid date.
    """
    try:
        day = int(db.get_setting("auto_fee_due_day", "10") or 10)
    except (TypeError, ValueError):
        day = 10
    day = max(1, min(day, 28))  # safe upper bound before month-length clamp
    last = calendar.monthrange(year, month)[1]
    day = min(day, last)
    return f"{year:04d}-{month:02d}-{day:02d}"


def _grace_days() -> int:
    try:
        return max(0, int(db.get_setting("auto_fee_grace_days", "0") or 0))
    except (TypeError, ValueError):
        return 0


def run_fee_automation(role: str, actor: str = "", force: bool = False) -> dict:
    """Generate current-month cycles + refresh overdue statuses.

    force=True skips the enable-toggle check (manual "Run now" from Settings).
    """
    result = {
        "success": False,
        "month": None,
        "year": None,
        "overdue_changed": 0,
        "created": [],
        "skipped": [],
        "errors": [],
        "reason": "",
        "due_date": "",
    }

    now = datetime.now()
    billing_month = now.month
    billing_year = now.year
    result["month"] = billing_month
    result["year"] = billing_year

    if not force and not _setting_on("auto_fee_cycle_enabled", "1"):
        result["reason"] = "Auto fee cycle generation is disabled in Settings."
        return result

    if not rbac.can(role, "fee.cycle.generate"):
        result["reason"] = (
            f"Role '{role}' does not have fee-cycle automation permission."
        )
        return result

    due_date = _due_date_for(billing_year, billing_month)
    grace = _grace_days()
    result["due_date"] = due_date

    try:
        result["overdue_changed"] = fee_cycles.refresh_overdue_statuses(
            role,
            actor=actor,
        )

        generated = fee_cycles.bulk_generate_cycle(
            role,
            billing_month,
            billing_year,
            actor=actor or "system",
            due_date=due_date,
            grace_period_days=grace,
        )

        result["created"] = generated.get("created", [])
        result["skipped"] = generated.get("skipped", [])
        result["errors"] = generated.get("errors", [])
        result["success"] = True

        # Marker so a month-change worker knows this month was already handled
        db.set_setting(
            "auto_fee_last_run",
            f"{billing_year:04d}-{billing_month:02d}",
        )

    except Exception as exc:
        result["reason"] = str(exc)

    return result


def needs_month_run() -> bool:
    """True if current calendar month has not yet been auto-generated."""
    now = datetime.now()
    marker = (db.get_setting("auto_fee_last_run", "") or "").strip()
    return marker != f"{now.year:04d}-{now.month:02d}"


__all__ = ["run_fee_automation", "needs_month_run"]
