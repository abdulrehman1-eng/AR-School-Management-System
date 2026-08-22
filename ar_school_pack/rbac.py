"""
rbac.py — Role-Based Access Control.

Per the spec: "Do not simply hide buttons... every sensitive action and
data request must verify Current User -> Role -> Permission -> Allowed
Action." So `can()` is called both when building the UI (to decide what to
show) AND inside every data-access function in modules/*.py right before
touching the database (to decide what to actually allow), so a restricted
feature can never be reached by scripting around the UI either.

Roles: Admin, Teacher, Reception. Feature keys are coarse-grained on
purpose (one row per module/action) so new roles/features can be added by
editing this one table — the "flexible enough for future roles" spec
requirement.
"""

import db

# feature -> {role: bool}
DEFAULT_MATRIX = {
    "student.view":            {"Admin": True, "Teacher": True,  "Reception": True},
    "student.add":             {"Admin": True, "Teacher": False, "Reception": True},
    "student.edit":            {"Admin": True, "Teacher": False, "Reception": True},
    "student.delete":          {"Admin": True, "Teacher": False, "Reception": False},
    "student.archive":         {"Admin": True, "Teacher": False, "Reception": True},
    "student.fee.view":        {"Admin": True, "Teacher": False, "Reception": True},
    "student.fee.edit":        {"Admin": True, "Teacher": False, "Reception": True},
    "student.idcard":          {"Admin": True, "Teacher": False, "Reception": True},

    "attendance.mark":         {"Admin": True, "Teacher": True,  "Reception": True},
    "attendance.view":         {"Admin": True, "Teacher": True,  "Reception": True},

    "results.subject.manage":  {"Admin": True, "Teacher": True,  "Reception": False},
    "results.marks.edit":      {"Admin": True, "Teacher": True,  "Reception": False},
    "results.view":            {"Admin": True, "Teacher": True,  "Reception": False},
    "results.grading.config":  {"Admin": True, "Teacher": False, "Reception": False},

    "teacher.view":            {"Admin": True, "Teacher": True,  "Reception": False},
    "teacher.add":             {"Admin": True, "Teacher": False, "Reception": False},
    "teacher.attendance.mark": {"Admin": True, "Teacher": False, "Reception": True},
    "teacher.salary.view":     {"Admin": True, "Teacher": False, "Reception": False},
    "teacher.salary.pay":      {"Admin": True, "Teacher": False, "Reception": False},

    "timetable.manage":        {"Admin": True, "Teacher": True,  "Reception": False},
    "timetable.view":          {"Admin": True, "Teacher": True,  "Reception": True},

    "accounting.revenue.view": {"Admin": True, "Teacher": False, "Reception": False},
    "accounting.revenue.add":  {"Admin": True, "Teacher": False, "Reception": False},
    "accounting.expense.view": {"Admin": True, "Teacher": False, "Reception": False},
    "accounting.expense.add":  {"Admin": True, "Teacher": False, "Reception": False},
    "accounting.dashboard":    {"Admin": True, "Teacher": False, "Reception": False},

    # Monthly fee-cycle engine (additive — Phase 3+ of the fee-system
    # upgrade). Day-to-day cycle payments/discounts reuse the existing
    # student.fee.edit/view permissions below via fee_cycles.py; these two
    # are for the heavier, school-wide actions.
    "fee.cycle.generate":      {"Admin": True, "Teacher": False, "Reception": False},
    "fee.reports.view":        {"Admin": True, "Teacher": False, "Reception": False},

    "academic_year.manage":    {"Admin": True, "Teacher": False, "Reception": False},
    "settings.branding":       {"Admin": True, "Teacher": False, "Reception": False},
    "settings.users":          {"Admin": True, "Teacher": False, "Reception": False},
    "audit.view":              {"Admin": True, "Teacher": False, "Reception": False},
    "backup.run":              {"Admin": True, "Teacher": False, "Reception": False},
}


class PermissionDenied(Exception):
    pass


def can(role: str, feature: str) -> bool:
    """Check an override table first (Admin-configurable at runtime),
    falling back to the built-in default matrix."""
    row = db.run(
        "SELECT allowed FROM permission_overrides WHERE role=? AND feature=?",
        (role, feature), fetchone=True,
    )
    if row is not None:
        return bool(row[0])
    return DEFAULT_MATRIX.get(feature, {}).get(role, False)


def require(role: str, feature: str):
    """Raise if not allowed — call this at the top of any data-mutating
    or sensitive data-reading function, not just in UI code."""
    if not can(role, feature):
        raise PermissionDenied(f"Role '{role}' is not permitted to '{feature}'.")


def set_override(role: str, feature: str, allowed: bool):
    db.run(
        "INSERT INTO permission_overrides (role, feature, allowed) VALUES (?, ?, ?) "
        "ON CONFLICT(role, feature) DO UPDATE SET allowed=excluded.allowed",
        (role, feature, 1 if allowed else 0), commit=True,
    )


def all_features():
    return sorted(DEFAULT_MATRIX.keys())


def all_roles():
    return ["Admin", "Teacher", "Reception"]