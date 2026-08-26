"""
fee_cycles.py — Monthly Fee Ledger engine (Fee System Upgrade, Phases 2-11).

STANDALONE, ADDITIVE MODULE. Does NOT rewrite historical accounting_revenue
/accounting_expense rows and does not touch UI files. It reads/writes the
fee_cycles, fee_payments, and fee_discount_history tables (see db.py), calls
the EXISTING accounting.record_fee_revenue() for every payment, and also
increments the legacy students.paid_fee cache so directory/dashboard screens
that still read that column stay consistent with the ledger.

Design (per spec):
- One row per student per (billing_month, billing_year) in fee_cycles —
  history is never overwritten. UNIQUE(student_id, billing_month,
  billing_year) is the duplicate-cycle protection.
- previous_balance carries forward automatically from the most recent
  prior cycle for that student at creation time, then is frozen — it is
  never silently recalculated after the fact, so a past cycle's numbers
  stay stable even if a later cycle is edited.
- amount_due = fee_amount - discount + previous_balance.
- status is always derived from amount_due/amount_paid/due_date — never
  hand-set — so it can't drift out of sync with the numbers.
- Every payment gets a unique, permanently stored receipt_no (unlike the
  existing receipt flow in student_fee_collection.py, which only
  generates a receipt number at print time and never stores it).
- Every mutation goes through rbac.require() with the SAME permissions
  already used by the rest of the fee system (student.fee.edit /
  student.fee.view), so nothing about who can do what changes for
  existing roles. Two new coarse actions (fee.cycle.generate,
  fee.reports.view) are Admin-only, matching the existing pattern for
  school-wide/reporting actions elsewhere in rbac.py.
"""

from __future__ import annotations

from datetime import datetime, date
from typing import Optional

import db
import rbac
import accounting

VALID_STATUSES = ("PAID", "PARTIAL", "PENDING", "OVERDUE", "ADVANCE")


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _today() -> str:
    return datetime.now().strftime("%Y-%m-%d")


def _assert_current_billing_period(billing_month: int, billing_year: int) -> None:
    """Reject any attempt to create past or future cycles.
    Ledger sequence stays strictly chronological from the real system date.
    """
    now = datetime.now()
    if int(billing_month) != now.month or int(billing_year) != now.year:
        raise ValueError(
            f"Fee cycles may only be generated for the current calendar month "
            f"({now.month:02d}/{now.year}). "
            f"Requested {int(billing_month):02d}/{int(billing_year)} is not allowed."
        )


def compute_status(amount_due: float, amount_paid: float, due_date: Optional[str],
                    grace_period_days: int = 0, as_of: Optional[str] = None) -> str:
    """Pure function — derives status from numbers only, never hand-set.

    ADVANCE : paid more than what's due (credit balance)
    PAID    : balance is exactly settled
    PARTIAL : some payment made, balance remains
    OVERDUE : nothing/partial paid AND due_date + grace period has passed
    PENDING : nothing paid yet, still within the due window (or no due date set)
    """
    balance = round(amount_due - amount_paid, 2)
    if balance < 0:
        return "ADVANCE"
    if balance == 0 and amount_due > 0:
        return "PAID"
    if balance == 0 and amount_due == 0:
        return "PAID"

    is_overdue = False
    if due_date:
        try:
            due = datetime.strptime(due_date, "%Y-%m-%d").date()
            ref = datetime.strptime(as_of, "%Y-%m-%d").date() if as_of else date.today()
            grace = grace_period_days or 0
            from datetime import timedelta
            is_overdue = ref > (due + timedelta(days=grace))
        except ValueError:
            is_overdue = False

    if is_overdue:
        return "OVERDUE"
    if amount_paid > 0:
        return "PARTIAL"
    return "PENDING"


def _recalc_and_save(cur, cycle_id: int) -> None:
    """Recompute amount_due/status for a cycle from its current
    fee_amount/discount/previous_balance/amount_paid and persist it.
    Must be called with the db.transaction() cursor (or db.run) already
    holding the write."""
    row = cur.execute(
        "SELECT fee_amount, discount, previous_balance, amount_paid, due_date, grace_period_days "
        "FROM fee_cycles WHERE id=?", (cycle_id,),
    ).fetchone()
    if not row:
        return
    fee_amount, discount, previous_balance, amount_paid, due_date, grace = row
    amount_due = round((fee_amount or 0) - (discount or 0) + (previous_balance or 0), 2)
    status = compute_status(amount_due, amount_paid or 0, due_date, grace or 0)
    cur.execute(
        "UPDATE fee_cycles SET amount_due=?, status=?, updated_at=? WHERE id=?",
        (amount_due, status, _now(), cycle_id),
    )


# ---------------------------------------------------------------------------
# Cycle creation (Phase 3/4/5/7 — ledger, billing cycle, auto-generation,
# carry-forward)
# ---------------------------------------------------------------------------

def get_latest_cycle(student_id: str) -> Optional[dict]:
    row = db.run(
        """SELECT id, student_id, billing_month, billing_year, class_sec, fee_amount, discount,
                  previous_balance, amount_due, amount_paid, due_date, grace_period_days, status
           FROM fee_cycles WHERE student_id=?
           ORDER BY billing_year DESC, billing_month DESC, id DESC LIMIT 1""",
        (student_id,), fetchone=True,
    )
    return _row_to_cycle(row) if row else None


def _row_to_cycle(row) -> dict:
    return {
        "id": row[0], "student_id": row[1], "billing_month": row[2], "billing_year": row[3],
        "class_sec": row[4], "fee_amount": row[5], "discount": row[6], "previous_balance": row[7],
        "amount_due": row[8], "amount_paid": row[9], "due_date": row[10],
        "grace_period_days": row[11], "status": row[12],
    }


def generate_cycle(role: str, student_id: str, billing_month: int, billing_year: int,
                    fee_amount: Optional[float] = None, due_date: str = "",
                    grace_period_days: int = 0, actor: str = "") -> dict:
    """Create ONE billing cycle for one student. Raises ValueError if a
    cycle already exists for that student+month+year (duplicate-cycle
    protection) or the student doesn't exist/is archived.

    fee_amount defaults to the student's current students.total_fee (the
    existing, already-trusted per-student fee figure) if not given
    explicitly, so this keeps working the same way even for a school that
    hasn't customized anything yet.
    """
    rbac.require(role, "student.fee.edit")

    billing_month = int(billing_month)
    billing_year = int(billing_year)
    if not (1 <= billing_month <= 12):
        raise ValueError("billing_month must be between 1 and 12.")

    _assert_current_billing_period(billing_month, billing_year)

    student = db.run(
        "SELECT student_id, class_sec, total_fee, status FROM students WHERE student_id=?",
        (student_id,), fetchone=True,
    )
    if not student:
        raise ValueError(f"Student '{student_id}' not found.")
    _, class_sec, students_total_fee, status = student
    if (status or "Active") != "Active":
        raise ValueError(f"Student '{student_id}' is archived; cannot generate a fee cycle.")

    existing = db.run(
        "SELECT id FROM fee_cycles WHERE student_id=? AND billing_month=? AND billing_year=?",
        (student_id, billing_month, billing_year), fetchone=True,
    )
    if existing:
        raise ValueError(
            f"A fee cycle for {billing_month:02d}/{billing_year} already exists for "
            f"student '{student_id}' (cycle id {existing[0]}). Duplicate cycles are not allowed."
        )

    if fee_amount is None:
        fee_amount = students_total_fee or 0.0
    if fee_amount < 0:
        raise ValueError("fee_amount cannot be negative.")

    # Chronologically prior cycle only (not merely the most-recent row).
    # Using "latest by id/year" would incorrectly import a future month's
    # balance when an Admin back-fills an earlier billing period.
    prior_row = db.run(
        """SELECT id, student_id, billing_month, billing_year, class_sec, fee_amount, discount,
                  previous_balance, amount_due, amount_paid, due_date, grace_period_days, status
           FROM fee_cycles
           WHERE student_id=?
             AND (billing_year < ? OR (billing_year = ? AND billing_month < ?))
           ORDER BY billing_year DESC, billing_month DESC, id DESC LIMIT 1""",
        (student_id, billing_year, billing_year, billing_month),
        fetchone=True,
    )
    prior = _row_to_cycle(prior_row) if prior_row else None
    previous_balance = 0.0
    if prior:
        prior_balance = round((prior["amount_due"] or 0) - (prior["amount_paid"] or 0), 2)
        # Only an outstanding (positive) balance carries forward as a debt.
        # A credit (ADVANCE, negative balance) is intentionally NOT
        # auto-applied here — applying someone's credit to a brand new
        # cycle without an explicit action is a financial decision, not
        # something this function should do silently.
        previous_balance = prior_balance if prior_balance > 0 else 0.0

    amount_due = round(fee_amount + previous_balance, 2)
    now = _now()

    db.run(
        """INSERT INTO fee_cycles
               (student_id, billing_month, billing_year, class_sec, fee_amount, discount,
                previous_balance, amount_due, amount_paid, due_date, grace_period_days,
                status, created_at, updated_at, created_by)
           VALUES (?, ?, ?, ?, ?, 0, ?, ?, 0, ?, ?, ?, ?, ?, ?)""",
        (student_id, billing_month, billing_year, class_sec, fee_amount, previous_balance,
         amount_due, due_date or None, int(grace_period_days or 0),
         compute_status(amount_due, 0, due_date or None, grace_period_days or 0),
         now, now, actor),
        commit=True,
    )
    cycle_id = db.run("SELECT last_insert_rowid()", fetchone=True)[0]
    _audit(actor, f"Generated fee cycle {billing_month:02d}/{billing_year} for {student_id} "
                   f"(amount due Rs.{amount_due:,.2f})")
    return get_cycle(cycle_id)


def bulk_generate_cycle(role: str, billing_month: int, billing_year: int, actor: str = "",
                         fee_amount_by_student: Optional[dict] = None,
                         due_date: str = "", grace_period_days: int = 0) -> dict:
    """Generate a billing cycle for every Active student that doesn't
    already have one for this month/year. Never overwrites an existing
    cycle — students who already have one for this period are skipped
    and reported, not touched. Returns {"created": [...], "skipped": [...],
    "errors": [...]}.

    due_date / grace_period_days are applied to every newly created cycle
    (used by auto monthly generation). Existing cycles are never modified.
    """
    rbac.require(role, "fee.cycle.generate")
    _assert_current_billing_period(billing_month, billing_year)

    students = db.run(
        "SELECT student_id FROM students WHERE COALESCE(status,'Active')='Active'", fetchall=True,
    ) or []

    created, skipped, errors = [], [], []
    for (student_id,) in students:
        try:
            fee_amount = (fee_amount_by_student or {}).get(student_id)
            cycle = generate_cycle(
                role, student_id, billing_month, billing_year,
                fee_amount=fee_amount, due_date=due_date or "",
                grace_period_days=int(grace_period_days or 0), actor=actor,
            )
            created.append(cycle["id"])
        except ValueError as e:
            if "already exists" in str(e):
                skipped.append(student_id)
            else:
                errors.append((student_id, str(e)))
    _audit(actor, f"Bulk-generated fee cycles for {billing_month:02d}/{billing_year}: "
                   f"{len(created)} created, {len(skipped)} skipped (already existed), {len(errors)} errors")
    return {"created": created, "skipped": skipped, "errors": errors}


def get_cycle(cycle_id: int) -> Optional[dict]:
    row = db.run(
        """SELECT id, student_id, billing_month, billing_year, class_sec, fee_amount, discount,
                  previous_balance, amount_due, amount_paid, due_date, grace_period_days, status
           FROM fee_cycles WHERE id=?""",
        (cycle_id,), fetchone=True,
    )
    return _row_to_cycle(row) if row else None


def get_student_ledger(role: str, student_id: str) -> list[dict]:
    """Full cycle-by-cycle history for a student, most recent first."""
    rbac.require(role, "student.fee.view")
    rows = db.run(
        """SELECT id, student_id, billing_month, billing_year, class_sec, fee_amount, discount,
                  previous_balance, amount_due, amount_paid, due_date, grace_period_days, status
           FROM fee_cycles WHERE student_id=? ORDER BY billing_year DESC, billing_month DESC""",
        (student_id,), fetchall=True,
    ) or []
    return [_row_to_cycle(r) for r in rows]


# ---------------------------------------------------------------------------
# Payments & receipts (Phase 8)
# ---------------------------------------------------------------------------

def _generate_receipt_no(cur, student_id: str) -> str:
    """A permanently unique receipt number, stored once and never
    regenerated — unlike the print-time-only numbers in the existing
    receipt dialogs. Retries on the (extremely unlikely) collision."""
    for _ in range(5):
        candidate = f"RCPT-{datetime.now().strftime('%Y%m%d%H%M%S%f')}-{student_id}"
        exists = cur.execute("SELECT 1 FROM fee_payments WHERE receipt_no=?", (candidate,)).fetchone()
        if not exists:
            return candidate
    raise RuntimeError("Could not generate a unique receipt number after 5 attempts.")


def record_payment(role: str, cycle_id: int, amount: float, payment_method: str,
                    recorded_by: str, remarks: str = "") -> dict:
    """Record one payment against a specific billing cycle, atomically:
    insert the payment row (permanent receipt number), update the
    cycle's amount_paid/status, and record the matching accounting
    revenue via the EXISTING accounting.record_fee_revenue() — never a
    second, duplicate revenue path.

    Payment safety: amount must be a positive number. Overpayment is
    allowed (becomes ADVANCE / credit) but never negative or zero.
    """
    rbac.require(role, "student.fee.edit")
    amount = float(amount)
    if amount <= 0:
        raise ValueError("Payment amount must be greater than zero.")

    cycle = get_cycle(cycle_id)
    if not cycle:
        raise ValueError(f"Fee cycle id {cycle_id} not found.")

    student = db.run("SELECT status FROM students WHERE student_id=?", (cycle["student_id"],), fetchone=True)
    if not student or (student[0] or "Active") != "Active":
        raise ValueError(f"Student '{cycle['student_id']}' is archived; cannot record a payment.")

    with db.transaction() as cur:
        receipt_no = _generate_receipt_no(cur, cycle["student_id"])
        now = _now()
        cur.execute(
            """INSERT INTO fee_payments
                   (cycle_id, student_id, amount, payment_method, receipt_no, paid_date,
                    recorded_by, remarks, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (cycle_id, cycle["student_id"], amount, payment_method, receipt_no,
             _today(), recorded_by, remarks, now),
        )
        new_paid = round((cycle["amount_paid"] or 0) + amount, 2)
        cur.execute("UPDATE fee_cycles SET amount_paid=? WHERE id=?", (new_paid, cycle_id))
        _recalc_and_save(cur, cycle_id)
        # Keep the legacy students.paid_fee cache in sync so directory /
        # dashboard / AI-assistant screens that still read it stay correct
        # after cycle-based payments (additive; never decreases here).
        cur.execute(
            "UPDATE students SET paid_fee = COALESCE(paid_fee, 0) + ? WHERE student_id=?",
            (amount, cycle["student_id"]),
        )

    # Existing, single-source-of-truth accounting integration — unchanged
    # signature/behavior, called exactly like every other fee-collection
    # path in this app already does.
    desc = f"Fee cycle payment {cycle['billing_month']:02d}/{cycle['billing_year']} (Receipt {receipt_no})"
    if remarks:
        desc += f" — {remarks}"
    accounting.record_fee_revenue(role, cycle["student_id"], amount, recorded_by,
                                   description=desc, payment_method=payment_method)

    _audit(recorded_by, f"Recorded fee payment Rs.{amount:,.2f} for {cycle['student_id']} "
                          f"(cycle {cycle_id}, receipt {receipt_no})")
    return {"receipt_no": receipt_no, "cycle": get_cycle(cycle_id)}


def get_payment_history(role: str, student_id: str) -> list[dict]:
    rbac.require(role, "student.fee.view")
    rows = db.run(
        """SELECT id, cycle_id, amount, payment_method, receipt_no, paid_date, recorded_by, remarks
           FROM fee_payments WHERE student_id=? ORDER BY id DESC""",
        (student_id,), fetchall=True,
    ) or []
    return [
        {"id": r[0], "cycle_id": r[1], "amount": r[2], "payment_method": r[3],
         "receipt_no": r[4], "paid_date": r[5], "recorded_by": r[6], "remarks": r[7]}
        for r in rows
    ]


# ---------------------------------------------------------------------------
# Discounts / adjustments (Phase 9)
# ---------------------------------------------------------------------------

def apply_discount(role: str, cycle_id: int, amount: float, reason: str, given_by: str) -> dict:
    """Apply a discount to a specific cycle, with a permanent history
    row (separate from the destructive total_fee edit the old flow
    used) so a discount is always traceable to who/why/when."""
    rbac.require(role, "student.fee.edit")
    amount = float(amount)
    if amount < 0:
        raise ValueError("Discount amount cannot be negative.")

    cycle = get_cycle(cycle_id)
    if not cycle:
        raise ValueError(f"Fee cycle id {cycle_id} not found.")

    new_discount = round((cycle["discount"] or 0) + amount, 2)
    if new_discount > (cycle["fee_amount"] or 0):
        raise ValueError(
            f"Total discount (Rs.{new_discount:,.2f}) cannot exceed the cycle's fee amount "
            f"(Rs.{cycle['fee_amount']:,.2f})."
        )

    with db.transaction() as cur:
        cur.execute("UPDATE fee_cycles SET discount=? WHERE id=?", (new_discount, cycle_id))
        _recalc_and_save(cur, cycle_id)
        cur.execute(
            """INSERT INTO fee_discount_history (cycle_id, student_id, amount, reason, given_by, created_at)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (cycle_id, cycle["student_id"], amount, reason, given_by, _now()),
        )

    _audit(given_by, f"Applied discount Rs.{amount:,.2f} to cycle {cycle_id} "
                       f"({cycle['student_id']}) — {reason or 'no reason given'}")
    return get_cycle(cycle_id)


# ---------------------------------------------------------------------------
# Status maintenance (Phase 6) — recompute PENDING -> OVERDUE as due dates pass
# ---------------------------------------------------------------------------

def refresh_overdue_statuses(role: str, actor: str = "") -> int:
    """Re-derive status for every cycle that isn't already PAID/ADVANCE,
    so a PENDING cycle whose due date has passed flips to OVERDUE without
    anyone having to re-open it. Safe to call as often as needed (e.g. on
    app startup, or a daily job) — never touches amount_due/amount_paid,
    only status. Returns the number of cycles whose status changed."""
    rbac.require(role, "fee.cycle.generate")
    rows = db.run(
        "SELECT id, amount_due, amount_paid, due_date, grace_period_days, status FROM fee_cycles "
        "WHERE status NOT IN ('PAID','ADVANCE')", fetchall=True,
    ) or []
    changed = 0
    with db.transaction() as cur:
        for cid, amount_due, amount_paid, due_date, grace, old_status in rows:
            new_status = compute_status(amount_due or 0, amount_paid or 0, due_date, grace or 0)
            if new_status != old_status:
                cur.execute("UPDATE fee_cycles SET status=?, updated_at=? WHERE id=?",
                            (new_status, _now(), cid))
                changed += 1
    if changed:
        _audit(actor, f"Refreshed overdue status on {changed} fee cycle(s)")
    return changed


# ---------------------------------------------------------------------------
# Reports (Phase 10)
# ---------------------------------------------------------------------------

def class_wise_report(role: str, billing_month: int, billing_year: int) -> list[dict]:
    rbac.require(role, "fee.reports.view")
    rows = db.run(
        """SELECT COALESCE(class_sec,'Unassigned') AS cls,
                  COUNT(*), SUM(amount_due), SUM(amount_paid), SUM(amount_due - amount_paid)
           FROM fee_cycles WHERE billing_month=? AND billing_year=?
           GROUP BY cls ORDER BY cls""",
        (billing_month, billing_year), fetchall=True,
    ) or []
    return [
        {"class_sec": r[0], "students": r[1], "total_due": r[2] or 0,
         "total_paid": r[3] or 0, "total_outstanding": r[4] or 0}
        for r in rows
    ]


def monthly_collection_report(role: str, billing_year: int) -> list[dict]:
    rbac.require(role, "fee.reports.view")
    rows = db.run(
        """SELECT billing_month, COUNT(*), SUM(amount_due), SUM(amount_paid), SUM(amount_due - amount_paid)
           FROM fee_cycles WHERE billing_year=? GROUP BY billing_month ORDER BY billing_month""",
        (billing_year,), fetchall=True,
    ) or []
    return [
        {"billing_month": r[0], "cycles": r[1], "total_due": r[2] or 0,
         "total_paid": r[3] or 0, "total_outstanding": r[4] or 0}
        for r in rows
    ]


def pending_and_overdue_students(role: str, billing_month: Optional[int] = None,
                                  billing_year: Optional[int] = None) -> list[dict]:
    """Used by the WhatsApp reminder bridge (Phase 12/13) and any pending-
    fee dashboard widget — every cycle currently PENDING/PARTIAL/OVERDUE,
    joined with the student's contact info."""
    rbac.require(role, "fee.reports.view")
    query = """
        SELECT fc.id, fc.student_id, s.name, s.phone, fc.class_sec, fc.billing_month,
               fc.billing_year, fc.amount_due, fc.amount_paid, fc.due_date, fc.status
        FROM fee_cycles fc
        JOIN students s ON s.student_id = fc.student_id
        WHERE fc.status IN ('PENDING','PARTIAL','OVERDUE')
          AND COALESCE(s.status,'Active') = 'Active'
    """
    params = []
    if billing_month and billing_year:
        query += " AND fc.billing_month=? AND fc.billing_year=?"
        params += [billing_month, billing_year]
    query += " ORDER BY fc.billing_year DESC, fc.billing_month DESC"
    rows = db.run(query, tuple(params), fetchall=True) or []
    return [
        {"cycle_id": r[0], "student_id": r[1], "name": r[2], "phone": r[3], "class_sec": r[4],
         "billing_month": r[5], "billing_year": r[6], "amount_due": r[7], "amount_paid": r[8],
         "balance": round((r[7] or 0) - (r[8] or 0), 2), "due_date": r[9], "status": r[10]}
        for r in rows
    ]


def _audit(username: str, action: str) -> None:
    try:
        db.run(
            "INSERT INTO audit_logs (username, action, timestamp) VALUES (?, ?, ?)",
            (username or "system", action, _now()), commit=True,
        )
    except Exception as exc:
        print(f"Audit Log Error: {exc}")


__all__ = [
    "compute_status", "generate_cycle", "bulk_generate_cycle", "get_cycle",
    "get_latest_cycle", "get_student_ledger", "record_payment", "get_payment_history",
    "apply_discount", "refresh_overdue_statuses", "class_wise_report",
    "monthly_collection_report", "pending_and_overdue_students",
]