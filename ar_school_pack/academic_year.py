"""
academic_year.py — Academic Year / Session management.

A school year (e.g. "2026-27") is tracked centrally so a student's
enrollment, class, and status can be recorded per year instead of a
single mutable field on `students`. This lets multiple years of
attendance/marks/fees stay attributable to the correct session even
after a student is promoted, repeats a year, or leaves — nothing about
a past year is overwritten when a new one starts.

Only one academic year is ever "current" (is_current=1) at a time —
that's the year new admissions and the Students directory default to.
Closing a year (`close_year`) always takes a full database backup first
(via db.backup_database), so a mistake made while closing a year can
always be undone by restoring that file — closing is not destructive by
itself, it only flips a status flag and optionally activates the next
year.
"""

from datetime import datetime
from typing import Optional

import db
import rbac


def _default_year_label(reference_date=None) -> str:
    d = reference_date or datetime.now()
    start = d.year if d.month >= 4 else d.year - 1
    return f"{start}-{str(start + 1)[-2:]}"


def list_years() -> list[dict]:
    """All academic years, most recent label first."""
    rows = db.run(
        """SELECT year_label, start_date, end_date, is_current, status, closed_at, closed_by
           FROM academic_years ORDER BY year_label DESC""",
        fetchall=True,
    ) or []
    return [
        {
            "year_label": r[0], "start_date": r[1], "end_date": r[2],
            "is_current": bool(r[3]), "status": r[4], "closed_at": r[5], "closed_by": r[6],
        }
        for r in rows
    ]


def get_current_year() -> Optional[dict]:
    row = db.run(
        "SELECT year_label, start_date, end_date, status FROM academic_years WHERE is_current=1",
        fetchone=True,
    )
    if not row:
        return None
    return {"year_label": row[0], "start_date": row[1], "end_date": row[2], "status": row[3]}


def get_current_year_label() -> str:
    """Convenience accessor for other modules (admission form, Students
    directory filter). Always returns a usable label even if
    academic_years hasn't been seeded yet for some reason."""
    current = get_current_year()
    return current["year_label"] if current else _default_year_label()


def create_year(role: str, year_label: str, start_date: str = "", end_date: str = "",
                 make_current: bool = False) -> None:
    rbac.require(role, "academic_year.manage")
    year_label = year_label.strip()
    if not year_label:
        raise ValueError("Academic year label cannot be empty.")

    existing = db.run("SELECT 1 FROM academic_years WHERE year_label=?", (year_label,), fetchone=True)
    if existing:
        raise ValueError(f"Academic year '{year_label}' already exists.")

    db.run(
        "INSERT INTO academic_years (year_label, start_date, end_date, is_current, status) "
        "VALUES (?, ?, ?, 0, 'Open')",
        (year_label, start_date, end_date), commit=True,
    )
    if make_current:
        set_current_year(role, year_label)


def set_current_year(role: str, year_label: str) -> None:
    rbac.require(role, "academic_year.manage")
    row = db.run("SELECT status FROM academic_years WHERE year_label=?", (year_label,), fetchone=True)
    if not row:
        raise ValueError(f"Academic year '{year_label}' does not exist.")
    if row[0] == "Closed":
        raise ValueError(f"Academic year '{year_label}' is closed and cannot be made current again.")

    db.run("UPDATE academic_years SET is_current=0", commit=True)
    db.run("UPDATE academic_years SET is_current=1 WHERE year_label=?", (year_label,), commit=True)


def enroll_student(student_id: str, academic_year: str, class_sec: str = "",
                    enrollment_status: str = "Active") -> None:
    """Record/refresh a student's per-year enrollment row and keep
    students.current_academic_year in sync when this happens to be the
    is_current year. Idempotent — safe to call again for the same
    student+year (e.g. an admission edit, or re-running an upgrade)."""
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    db.run(
        """INSERT INTO student_academic_year
               (student_id, academic_year, class_sec, enrollment_status, created_at, updated_at)
           VALUES (?, ?, ?, ?, ?, ?)
           ON CONFLICT(student_id, academic_year) DO UPDATE SET
               class_sec=excluded.class_sec,
               enrollment_status=excluded.enrollment_status,
               updated_at=excluded.updated_at""",
        (student_id, academic_year, class_sec, enrollment_status, now, now),
        commit=True,
    )
    current = get_current_year()
    if current and current["year_label"] == academic_year:
        db.run(
            "UPDATE students SET current_academic_year=? WHERE student_id=?",
            (academic_year, student_id), commit=True,
        )


def get_student_year_history(student_id: str) -> list[dict]:
    rows = db.run(
        """SELECT academic_year, class_sec, enrollment_status, created_at, updated_at
           FROM student_academic_year WHERE student_id=? ORDER BY academic_year DESC""",
        (student_id,), fetchall=True,
    ) or []
    return [
        {"academic_year": r[0], "class_sec": r[1], "enrollment_status": r[2],
         "created_at": r[3], "updated_at": r[4]}
        for r in rows
    ]


def students_in_year(academic_year: str, include_archived: bool = False) -> list[tuple]:
    """Full `students` rows for everyone enrolled in a given academic
    year — same column shape as `SELECT * FROM students`, used by the
    Students directory's Academic Year filter."""
    query = """
        SELECT s.* FROM students s
        JOIN student_academic_year say
          ON say.student_id = s.student_id AND say.academic_year = ?
    """
    if not include_archived:
        query += " WHERE COALESCE(s.status, 'Active') = 'Active'"
    return db.run(query, (academic_year,), fetchall=True) or []


def close_year(role: str, year_label: str, closed_by: str,
                next_year_label: Optional[str] = None) -> str:
    """Close an academic year: back up the database first (so this step
    is always reversible by restoring that file), mark the year Closed,
    and optionally create+activate the next year in the same call.

    This never touches student/attendance/marks/fee rows by itself — it
    only changes bookkeeping on the academic_years table. Per-student
    promotion/leaving decisions stay explicit, via archive_student /
    academic_year.enroll_student for whoever continues into the next year.

    Returns the backup file path.
    """
    rbac.require(role, "academic_year.manage")
    row = db.run("SELECT status FROM academic_years WHERE year_label=?", (year_label,), fetchone=True)
    if not row:
        raise ValueError(f"Academic year '{year_label}' does not exist.")
    if row[0] == "Closed":
        raise ValueError(f"Academic year '{year_label}' is already closed.")

    backup_path = db.backup_database(f"YearClose_{year_label}")

    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    db.run(
        "UPDATE academic_years SET status='Closed', closed_at=?, closed_by=?, is_current=0 WHERE year_label=?",
        (now, closed_by, year_label), commit=True,
    )

    if next_year_label:
        existing_next = db.run("SELECT 1 FROM academic_years WHERE year_label=?", (next_year_label,), fetchone=True)
        if not existing_next:
            create_year(role, next_year_label, make_current=True)
        else:
            set_current_year(role, next_year_label)

    return backup_path


__all__ = [
    "list_years", "get_current_year", "get_current_year_label",
    "create_year", "set_current_year", "enroll_student",
    "get_student_year_history", "students_in_year", "close_year",
]
