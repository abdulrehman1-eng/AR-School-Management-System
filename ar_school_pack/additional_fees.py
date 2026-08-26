"""
additional_fees.py — One-time / periodic non-monthly fees
(Annual Fee, Exam Fee, Lab Fee, Sports Fee, etc.).

STANDALONE & ADDITIVE. Does not touch monthly fee_cycles or
students.total_fee/paid_fee. Uses its own tables:
  - additional_fee_types   (catalog: Annual Fee, Exam Fee, …)
  - additional_fee_charges (per-student charges)
  - additional_fee_payments (payment rows with permanent receipt_no)

Every payment posts to accounting_revenue via the existing
accounting.record_fee_revenue() path (source_type stays "Student Fee"
with a clear description that includes the fee type name).

Permissions reuse the existing student.fee.view / student.fee.edit
keys so Reception can collect these fees the same way as monthly fees.
Admin-only type management uses fee.cycle.generate (same as bulk
monthly cycle tools).
"""

from __future__ import annotations

from datetime import datetime
from typing import Optional

import db
import rbac
import accounting

DEFAULT_TYPES = [
    ("Annual Fee", "Yearly school charges", 0.0),
    ("Exam Fee", "Examination / board fee", 0.0),
    ("Lab Fee", "Science / computer lab", 0.0),
    ("Sports Fee", "Sports and games", 0.0),
    ("Transport Fee", "School transport (term)", 0.0),
    ("Other", "Miscellaneous one-time fee", 0.0),
]


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _today() -> str:
    return datetime.now().strftime("%Y-%m-%d")


def ensure_tables() -> None:
    """Idempotent schema — safe to call from UI entry points."""
    db.run(
        """
        CREATE TABLE IF NOT EXISTS additional_fee_types (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT UNIQUE NOT NULL,
            description TEXT,
            default_amount REAL NOT NULL DEFAULT 0,
            is_active INTEGER NOT NULL DEFAULT 1,
            created_at TEXT
        )
        """,
        commit=True,
    )
    db.run(
        """
        CREATE TABLE IF NOT EXISTS additional_fee_charges (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            student_id TEXT NOT NULL,
            fee_type_id INTEGER NOT NULL,
            academic_year TEXT,
            amount REAL NOT NULL DEFAULT 0,
            discount REAL NOT NULL DEFAULT 0,
            amount_paid REAL NOT NULL DEFAULT 0,
            status TEXT NOT NULL DEFAULT 'PENDING',
            due_date TEXT,
            remarks TEXT,
            created_at TEXT,
            updated_at TEXT,
            created_by TEXT,
            FOREIGN KEY(fee_type_id) REFERENCES additional_fee_types(id)
        )
        """,
        commit=True,
    )
    db.run(
        """
        CREATE TABLE IF NOT EXISTS additional_fee_payments (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            charge_id INTEGER NOT NULL,
            student_id TEXT NOT NULL,
            amount REAL NOT NULL,
            payment_method TEXT,
            receipt_no TEXT UNIQUE,
            paid_date TEXT,
            recorded_by TEXT,
            remarks TEXT,
            created_at TEXT,
            FOREIGN KEY(charge_id) REFERENCES additional_fee_charges(id)
        )
        """,
        commit=True,
    )
    db.run(
        "CREATE INDEX IF NOT EXISTS idx_afc_student ON additional_fee_charges(student_id)",
        commit=True,
    )
    db.run(
        "CREATE INDEX IF NOT EXISTS idx_afc_status ON additional_fee_charges(status)",
        commit=True,
    )
    # Seed default types once
    count = db.run("SELECT COUNT(*) FROM additional_fee_types", fetchone=True)
    if count and count[0] == 0:
        for name, desc, amt in DEFAULT_TYPES:
            db.run(
                """
                INSERT INTO additional_fee_types
                    (name, description, default_amount, is_active, created_at)
                VALUES (?, ?, ?, 1, ?)
                """,
                (name, desc, amt, _now()),
                commit=True,
            )


def _compute_status(amount: float, discount: float, paid: float) -> str:
    due = round((amount or 0) - (discount or 0), 2)
    balance = round(due - (paid or 0), 2)
    if due <= 0 and (paid or 0) <= 0:
        return "PAID"
    if balance < 0:
        return "ADVANCE"
    if balance == 0:
        return "PAID"
    if (paid or 0) > 0:
        return "PARTIAL"
    return "PENDING"


def _row_to_charge(row) -> dict:
    # id, student_id, fee_type_id, academic_year, amount, discount,
    # amount_paid, status, due_date, remarks, type_name
    return {
        "id": row[0],
        "student_id": row[1],
        "fee_type_id": row[2],
        "academic_year": row[3] or "",
        "amount": float(row[4] or 0),
        "discount": float(row[5] or 0),
        "amount_paid": float(row[6] or 0),
        "status": row[7] or "PENDING",
        "due_date": row[8] or "",
        "remarks": row[9] or "",
        "type_name": row[10] if len(row) > 10 else "",
        "amount_due": round(
            float(row[4] or 0) - float(row[5] or 0), 2
        ),
        "balance": round(
            float(row[4] or 0) - float(row[5] or 0) - float(row[6] or 0), 2
        ),
    }


# ---------------------------------------------------------------------------
# Fee types (catalog)
# ---------------------------------------------------------------------------

def list_fee_types(active_only: bool = True) -> list[dict]:
    ensure_tables()
    q = (
        "SELECT id, name, description, default_amount, is_active "
        "FROM additional_fee_types"
    )
    if active_only:
        q += " WHERE is_active=1"
    q += " ORDER BY name"
    rows = db.run(q, fetchall=True) or []
    return [
        {
            "id": r[0],
            "name": r[1],
            "description": r[2] or "",
            "default_amount": float(r[3] or 0),
            "is_active": bool(r[4]),
        }
        for r in rows
    ]


def add_fee_type(
    role: str, name: str, description: str = "", default_amount: float = 0.0
) -> dict:
    rbac.require(role, "fee.cycle.generate")
    ensure_tables()
    name = (name or "").strip()
    if not name:
        raise ValueError("Fee type name is required.")
    default_amount = float(default_amount or 0)
    if default_amount < 0:
        raise ValueError("Default amount cannot be negative.")
    try:
        db.run(
            """
            INSERT INTO additional_fee_types
                (name, description, default_amount, is_active, created_at)
            VALUES (?, ?, ?, 1, ?)
            """,
            (name, description or "", default_amount, _now()),
            commit=True,
        )
    except Exception as exc:
        if "UNIQUE" in str(exc).upper():
            raise ValueError(f"Fee type '{name}' already exists.") from exc
        raise
    row = db.run(
        "SELECT id, name, description, default_amount, is_active "
        "FROM additional_fee_types WHERE name=?",
        (name,),
        fetchone=True,
    )
    return {
        "id": row[0],
        "name": row[1],
        "description": row[2] or "",
        "default_amount": float(row[3] or 0),
        "is_active": bool(row[4]),
    }


def deactivate_fee_type(role: str, type_id: int) -> None:
    rbac.require(role, "fee.cycle.generate")
    ensure_tables()
    db.run(
        "UPDATE additional_fee_types SET is_active=0 WHERE id=?",
        (type_id,),
        commit=True,
    )


# ---------------------------------------------------------------------------
# Charges (assign fee to student)
# ---------------------------------------------------------------------------

def assign_charge(
    role: str,
    student_id: str,
    fee_type_id: int,
    amount: float,
    academic_year: str = "",
    due_date: str = "",
    remarks: str = "",
    actor: str = "",
) -> dict:
    """Create one additional-fee charge for a student."""
    rbac.require(role, "student.fee.edit")
    ensure_tables()

    student = db.run(
        "SELECT student_id, status FROM students WHERE student_id=?",
        (student_id,),
        fetchone=True,
    )
    if not student:
        raise ValueError(f"Student '{student_id}' not found.")
    if (student[1] or "Active") != "Active":
        raise ValueError(f"Student '{student_id}' is archived.")

    ftype = db.run(
        "SELECT id, name FROM additional_fee_types WHERE id=? AND is_active=1",
        (fee_type_id,),
        fetchone=True,
    )
    if not ftype:
        raise ValueError("Fee type not found or inactive.")

    amount = float(amount)
    if amount < 0:
        raise ValueError("Amount cannot be negative.")

    status = _compute_status(amount, 0, 0)
    now = _now()
    db.run(
        """
        INSERT INTO additional_fee_charges
            (student_id, fee_type_id, academic_year, amount, discount,
             amount_paid, status, due_date, remarks, created_at, updated_at, created_by)
        VALUES (?, ?, ?, ?, 0, 0, ?, ?, ?, ?, ?, ?)
        """,
        (
            student_id,
            fee_type_id,
            academic_year or "",
            amount,
            status,
            due_date or None,
            remarks or "",
            now,
            now,
            actor,
        ),
        commit=True,
    )
    cid = db.run("SELECT last_insert_rowid()", fetchone=True)[0]
    return get_charge(cid)


def list_active_classes() -> list[str]:
    """Distinct class_sec values for Active students (for bulk UI filters)."""
    rows = db.run(
        """
        SELECT DISTINCT class_sec FROM students
        WHERE COALESCE(status,'Active')='Active'
          AND class_sec IS NOT NULL AND TRIM(class_sec) <> ''
        ORDER BY class_sec
        """,
        fetchall=True,
    ) or []
    return [r[0] for r in rows]


def count_active_students(
    class_secs: list[str] | None = None,
    all_active: bool = False,
) -> int:
    """Preview how many Active students a bulk assign will touch."""
    if all_active:
        row = db.run(
            "SELECT COUNT(*) FROM students WHERE COALESCE(status,'Active')='Active'",
            fetchone=True,
        )
        return int(row[0] or 0) if row else 0
    if not class_secs:
        return 0
    placeholders = ",".join("?" * len(class_secs))
    row = db.run(
        f"""
        SELECT COUNT(*) FROM students
        WHERE COALESCE(status,'Active')='Active'
          AND class_sec IN ({placeholders})
        """,
        tuple(class_secs),
        fetchone=True,
    )
    return int(row[0] or 0) if row else 0


def bulk_assign(
    role: str,
    fee_type_id: int,
    amount: float,
    *,
    class_secs: list[str] | None = None,
    all_active: bool = False,
    academic_year: str = "",
    due_date: str = "",
    remarks: str = "",
    actor: str = "",
    skip_if_exists: bool = True,
) -> dict:
    """Assign the same additional-fee charge to many Active students.

    Scope (exactly one):
      - all_active=True  → every Active student
      - class_secs=[...] → Active students in those class_sec values

    Does NOT touch monthly fee_cycles / students.total_fee / paid_fee.

    skip_if_exists: if True, skip a student who already has the same
    fee_type_id + academic_year charge (avoids accidental duplicates).
    """
    rbac.require(role, "fee.cycle.generate")
    ensure_tables()

    if all_active:
        students = db.run(
            "SELECT student_id FROM students WHERE COALESCE(status,'Active')='Active'",
            fetchall=True,
        ) or []
    elif class_secs:
        class_secs = [c for c in class_secs if c and str(c).strip()]
        if not class_secs:
            raise ValueError("Select at least one class.")
        placeholders = ",".join("?" * len(class_secs))
        students = db.run(
            f"""
            SELECT student_id FROM students
            WHERE COALESCE(status,'Active')='Active'
              AND class_sec IN ({placeholders})
            """,
            tuple(class_secs),
            fetchall=True,
        ) or []
    else:
        raise ValueError("Specify all_active=True or a non-empty class_secs list.")

    # Validate type + amount once up front
    ftype = db.run(
        "SELECT id, name FROM additional_fee_types WHERE id=? AND is_active=1",
        (fee_type_id,),
        fetchone=True,
    )
    if not ftype:
        raise ValueError("Fee type not found or inactive.")
    amount = float(amount)
    if amount < 0:
        raise ValueError("Amount cannot be negative.")

    ay = (academic_year or "").strip()
    created, skipped, errors = [], [], []

    for (sid,) in students:
        try:
            if skip_if_exists and ay:
                exists = db.run(
                    """
                    SELECT 1 FROM additional_fee_charges
                    WHERE student_id=? AND fee_type_id=? AND academic_year=?
                    LIMIT 1
                    """,
                    (sid, fee_type_id, ay),
                    fetchone=True,
                )
                if exists:
                    skipped.append(sid)
                    continue
            elif skip_if_exists and not ay:
                exists = db.run(
                    """
                    SELECT 1 FROM additional_fee_charges
                    WHERE student_id=? AND fee_type_id=?
                      AND (academic_year IS NULL OR academic_year='')
                    LIMIT 1
                    """,
                    (sid, fee_type_id),
                    fetchone=True,
                )
                if exists:
                    skipped.append(sid)
                    continue

            c = assign_charge(
                role,
                sid,
                fee_type_id,
                amount,
                academic_year=ay,
                due_date=due_date,
                remarks=remarks,
                actor=actor,
            )
            created.append(c["id"])
        except Exception as exc:
            errors.append((sid, str(exc)))

    return {
        "created": created,
        "skipped": skipped,
        "errors": errors,
        "fee_type_name": ftype[1],
        "amount": amount,
        "target_count": len(students),
    }


def bulk_assign_by_class(
    role: str,
    class_sec: str,
    fee_type_id: int,
    amount: float,
    academic_year: str = "",
    due_date: str = "",
    actor: str = "",
) -> dict:
    """Backward-compatible wrapper — assign to one class."""
    return bulk_assign(
        role,
        fee_type_id,
        amount,
        class_secs=[class_sec],
        academic_year=academic_year,
        due_date=due_date,
        actor=actor,
    )


def get_charge(charge_id: int) -> Optional[dict]:
    ensure_tables()
    row = db.run(
        """
        SELECT c.id, c.student_id, c.fee_type_id, c.academic_year,
               c.amount, c.discount, c.amount_paid, c.status,
               c.due_date, c.remarks, t.name
        FROM additional_fee_charges c
        JOIN additional_fee_types t ON t.id = c.fee_type_id
        WHERE c.id=?
        """,
        (charge_id,),
        fetchone=True,
    )
    return _row_to_charge(row) if row else None


def get_student_charges(role: str, student_id: str) -> list[dict]:
    rbac.require(role, "student.fee.view")
    ensure_tables()
    rows = db.run(
        """
        SELECT c.id, c.student_id, c.fee_type_id, c.academic_year,
               c.amount, c.discount, c.amount_paid, c.status,
               c.due_date, c.remarks, t.name
        FROM additional_fee_charges c
        JOIN additional_fee_types t ON t.id = c.fee_type_id
        WHERE c.student_id=?
        ORDER BY c.id DESC
        """,
        (student_id,),
        fetchall=True,
    ) or []
    return [_row_to_charge(r) for r in rows]


# ---------------------------------------------------------------------------
# Payments
# ---------------------------------------------------------------------------

def _generate_receipt_no(cur, student_id: str) -> str:
    for _ in range(5):
        candidate = (
            f"AF-{datetime.now().strftime('%Y%m%d%H%M%S%f')}-{student_id}"
        )
        exists = cur.execute(
            "SELECT 1 FROM additional_fee_payments WHERE receipt_no=?",
            (candidate,),
        ).fetchone()
        if not exists:
            return candidate
    raise RuntimeError("Could not generate unique receipt number.")


def record_payment(
    role: str,
    charge_id: int,
    amount: float,
    payment_method: str,
    recorded_by: str,
    remarks: str = "",
) -> dict:
    """Record payment against an additional-fee charge; posts to accounting."""
    rbac.require(role, "student.fee.edit")
    ensure_tables()
    amount = float(amount)
    if amount <= 0:
        raise ValueError("Payment amount must be greater than zero.")

    charge = get_charge(charge_id)
    if not charge:
        raise ValueError(f"Charge id {charge_id} not found.")

    student = db.run(
        "SELECT status FROM students WHERE student_id=?",
        (charge["student_id"],),
        fetchone=True,
    )
    if not student or (student[0] or "Active") != "Active":
        raise ValueError(
            f"Student '{charge['student_id']}' is archived; cannot record payment."
        )

    with db.transaction() as cur:
        receipt_no = _generate_receipt_no(cur, charge["student_id"])
        now = _now()
        cur.execute(
            """
            INSERT INTO additional_fee_payments
                (charge_id, student_id, amount, payment_method, receipt_no,
                 paid_date, recorded_by, remarks, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                charge_id,
                charge["student_id"],
                amount,
                payment_method,
                receipt_no,
                _today(),
                recorded_by,
                remarks,
                now,
            ),
        )
        new_paid = round((charge["amount_paid"] or 0) + amount, 2)
        new_status = _compute_status(
            charge["amount"], charge["discount"], new_paid
        )
        cur.execute(
            """
            UPDATE additional_fee_charges
            SET amount_paid=?, status=?, updated_at=?
            WHERE id=?
            """,
            (new_paid, new_status, now, charge_id),
        )

    desc = (
        f"Additional fee: {charge['type_name']} "
        f"(Receipt {receipt_no})"
    )
    if remarks:
        desc += f" — {remarks}"
    accounting.record_fee_revenue(
        role,
        charge["student_id"],
        amount,
        recorded_by,
        description=desc,
        payment_method=payment_method,
    )

    return {"receipt_no": receipt_no, "charge": get_charge(charge_id)}


def apply_discount(
    role: str, charge_id: int, amount: float, reason: str, given_by: str
) -> dict:
    rbac.require(role, "student.fee.edit")
    ensure_tables()
    amount = float(amount)
    if amount < 0:
        raise ValueError("Discount cannot be negative.")
    charge = get_charge(charge_id)
    if not charge:
        raise ValueError(f"Charge id {charge_id} not found.")
    new_discount = round((charge["discount"] or 0) + amount, 2)
    if new_discount > (charge["amount"] or 0):
        raise ValueError("Total discount cannot exceed the charge amount.")
    new_status = _compute_status(
        charge["amount"], new_discount, charge["amount_paid"]
    )
    db.run(
        """
        UPDATE additional_fee_charges
        SET discount=?, status=?, updated_at=?, remarks=?
        WHERE id=?
        """,
        (
            new_discount,
            new_status,
            _now(),
            (charge["remarks"] + " | Discount: " + (reason or "")).strip(" |"),
            charge_id,
        ),
        commit=True,
    )
    return get_charge(charge_id)


__all__ = [
    "ensure_tables",
    "list_fee_types",
    "add_fee_type",
    "deactivate_fee_type",
    "assign_charge",
    "bulk_assign",
    "bulk_assign_by_class",
    "list_active_classes",
    "count_active_students",
    "get_charge",
    "get_student_charges",
    "record_payment",
    "apply_discount",
]
