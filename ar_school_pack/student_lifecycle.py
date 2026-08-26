"""
student_lifecycle.py — Archive / Restore / Permanent Delete for students.

Three operations, deliberately different weight:

- archive_student / restore_student: reversible. Only flips
  students.status between 'Active' and 'Archived'. Nothing else in the
  database is touched, so attendance/marks/fees/accounting history stay
  exactly as they were and the student simply drops off the default
  (Active-only) Students directory view. This is the normal way to
  handle a student who left, graduated, or transferred.

- permanent_delete_student: the actual hard DELETE, for the rare case an
  Admin genuinely needs a record gone (e.g. a duplicate/mistaken entry).
  This is what used to raise "FOREIGN KEY constraint failed" — attendance
  and marks rows for that student were never removed before deleting the
  student row itself, and both tables declare
  FOREIGN KEY(student_id) REFERENCES students(student_id).

  This version: checks dependencies up front, takes a full database
  backup, then removes every dependent row and the student row inside a
  single atomic transaction (db.transaction()) — all-or-nothing, so a
  failure partway through can never leave the database half-deleted.

  accounting_revenue rows are intentionally NEVER deleted here, even on
  permanent delete — they're a financial ledger/audit trail, and
  accounting_revenue.student_id carries no FK constraint to students, so
  leaving them in place (with the student_id now dangling) is both safe
  and the financially correct choice.

  fee_cycles / fee_payments / fee_discount_history (the monthly fee
  ledger, added by fee_cycles.py) follow the exact same policy and for
  the exact same reason — they carry no FK constraint to students either,
  so a permanent delete can never fail on them, and a student's fee
  history remains a permanent financial record even after the student
  row itself is gone.
"""

from datetime import datetime

import db
import rbac


def get_dependency_counts(student_id: str) -> dict:
    """Read-only summary of everything linked to a student — used to show
    an Admin exactly what a permanent delete will remove before they
    confirm it, and recorded in the audit log afterwards."""
    attendance = db.run("SELECT COUNT(*) FROM attendance WHERE student_id=?", (student_id,), fetchone=True)[0]
    marks = db.run("SELECT COUNT(*) FROM marks WHERE student_id=?", (student_id,), fetchone=True)[0]
    accounting_rows = db.run(
        "SELECT COUNT(*) FROM accounting_revenue WHERE student_id=?", (student_id,), fetchone=True
    )[0]
    year_rows = db.run(
        "SELECT COUNT(*) FROM student_academic_year WHERE student_id=?", (student_id,), fetchone=True
    )[0]
    # student_admission_extra is created lazily by student_admission.py (see
    # db.py), so it may not exist yet on a fresh database / for a student
    # added before that table was first created — guard the same way the
    # fee ledger tables below are guarded.
    admission_extra = 0
    try:
        admission_extra = db.run(
            "SELECT COUNT(*) FROM student_admission_extra WHERE student_id=?", (student_id,), fetchone=True
        )[0]
    except Exception:
        pass
    # Fee ledger tables may not exist on a database that hasn't run the
    # (additive) fee-system migration yet — guard so this still works on
    # an older DB, same defensive style as the rest of this function.
    fee_cycle_rows = 0
    try:
        fee_cycle_rows = db.run(
            "SELECT COUNT(*) FROM fee_cycles WHERE student_id=?", (student_id,), fetchone=True
        )[0]
    except Exception:
        pass
    return {
        "attendance": attendance,
        "marks": marks,
        "accounting_revenue": accounting_rows,
        "academic_year_records": year_rows,
        "admission_extra": admission_extra,
        "fee_cycles": fee_cycle_rows,
    }


def archive_student(role: str, student_id: str, actor: str, reason: str = "") -> None:
    rbac.require(role, "student.archive")
    row = db.run("SELECT status FROM students WHERE student_id=?", (student_id,), fetchone=True)
    if not row:
        raise ValueError(f"Student '{student_id}' not found.")
    if (row[0] or "Active") == "Archived":
        raise ValueError(f"Student '{student_id}' is already archived.")

    db.run("UPDATE students SET status='Archived' WHERE student_id=?", (student_id,), commit=True)
    _audit(actor, f"Archived student {student_id}" + (f" — {reason}" if reason else ""))


def restore_student(role: str, student_id: str, actor: str) -> None:
    rbac.require(role, "student.archive")
    row = db.run("SELECT status FROM students WHERE student_id=?", (student_id,), fetchone=True)
    if not row:
        raise ValueError(f"Student '{student_id}' not found.")
    if (row[0] or "Active") != "Archived":
        raise ValueError(f"Student '{student_id}' is not archived.")

    db.run("UPDATE students SET status='Active' WHERE student_id=?", (student_id,), commit=True)
    _audit(actor, f"Restored student {student_id} from archive")


def permanent_delete_student(role: str, student_id: str, actor: str) -> dict:
    """Hard-delete a student and every row that FOREIGN KEY-references
    them (attendance, marks, student_academic_year,
    student_admission_extra), inside one atomic transaction, after taking
    a full database backup first.

    Returns {"backup_path": ..., "removed": {...counts...}} on success.
    Raises (ValueError for a missing student, PermissionDenied for RBAC,
    or the underlying DB error) on failure — the transaction rolls back
    automatically on any exception, so the database is left exactly as it
    was before the call.
    """
    rbac.require(role, "student.delete")

    student = db.run("SELECT student_id, name FROM students WHERE student_id=?", (student_id,), fetchone=True)
    if not student:
        raise ValueError(f"Student '{student_id}' not found.")

    counts = get_dependency_counts(student_id)
    backup_path = db.backup_database(f"PreDelete_{student_id}")

    with db.transaction() as cur:
        cur.execute("DELETE FROM attendance WHERE student_id=?", (student_id,))
        cur.execute("DELETE FROM marks WHERE student_id=?", (student_id,))
        cur.execute("DELETE FROM student_academic_year WHERE student_id=?", (student_id,))
        # student_admission_extra is created lazily by student_admission.py,
        # so it may not exist yet — check before deleting from it, same as
        # the read-only count in get_dependency_counts() above.
        cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='student_admission_extra'")
        if cur.fetchone():
            cur.execute("DELETE FROM student_admission_extra WHERE student_id=?", (student_id,))
        cur.execute("DELETE FROM students WHERE student_id=?", (student_id,))

        cur.execute("SELECT 1 FROM students WHERE student_id=?", (student_id,))
        if cur.fetchone():
            # Should be unreachable, but never report success on a delete
            # that didn't actually happen.
            raise RuntimeError("Student row still present after DELETE — aborting.")

    _audit(
        actor,
        f"Permanently deleted student {student_id} ({student[1]}) — removed "
        f"{counts['attendance']} attendance, {counts['marks']} marks, "
        f"{counts['academic_year_records']} academic-year record(s), "
        f"{'1' if counts['admission_extra'] else '0'} admission-profile row. "
        f"{counts['accounting_revenue']} accounting_revenue row(s) and "
        f"{counts['fee_cycles']} fee_cycles row(s) preserved. "
        f"Backup: {backup_path}",
    )

    return {"backup_path": backup_path, "removed": counts}


def _audit(username: str, action: str) -> None:
    try:
        db.run(
            "INSERT INTO audit_logs (username, action, timestamp) VALUES (?, ?, ?)",
            (username, action, datetime.now().strftime("%Y-%m-%d %H:%M:%S")),
            commit=True,
        )
    except Exception as exc:
        print(f"Audit Log Error: {exc}")


__all__ = [
    "get_dependency_counts", "archive_student", "restore_student", "permanent_delete_student",
]