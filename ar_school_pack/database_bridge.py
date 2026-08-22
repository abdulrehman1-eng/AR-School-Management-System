"""database_bridge.py — Standalone diagnostic reader for AR-Whatsapp-Node.

IMPORTANT: this file does NOT recompute fee/pending status itself. It
locates the real AR School ERP project folder and imports its actual
db.py / fee_cycles.py, then calls the SAME
fee_cycles.pending_and_overdue_students() the in-app "WhatsApp Fees"
window and fee_whatsapp_bridge.py already use. That keeps exactly one
source of truth for "who is pending" (the fee_cycles ledger — discount,
carry-forward balance, grace period, OVERDUE detection, etc. all already
handled there) instead of a second, simpler total_fee/paid_fee
calculation drifting out of sync with it over time.

Locating the ERP project (fixes the "wrong school_system.db" risk):
Set the AR_SCHOOL_ERP_DIR environment variable to the AR School ERP
folder that actually contains db.py + fee_cycles.py + school_system.db.
A couple of common relative locations are tried as a convenience, but if
none of them resolve, this fails loudly with a clear message rather than
silently reading nothing or the wrong database.
"""
import os
import sys
import json

ERP_DIR_ENV = "AR_SCHOOL_ERP_DIR"

# Kept only as an absolute-last-resort fallback for whichever machine this
# was originally written for. Never trusted blindly — it's verified with
# the same db.py/fee_cycles.py check as every other candidate below, so if
# it's wrong on a different machine it's simply skipped, not silently used.
_LEGACY_HARDCODED_PATH = "/home/arehman03192332173/final/AR_School_ERP_Upgraded"


def _find_erp_dir() -> str:
    candidates = []

    env = os.environ.get(ERP_DIR_ENV, "").strip()
    if env:
        candidates.append(env)

    here = os.path.dirname(os.path.abspath(__file__))
    candidates += [
        here,                                              # in case this file is ever moved next to the ERP
        os.path.join(here, "..", "AR_School_ERP_Upgraded"),
        os.path.join(here, "..", "..", "AR_School_ERP_Upgraded"),
        os.path.join(os.path.expanduser("~"), "AR_School_ERP_Upgraded"),
        _LEGACY_HARDCODED_PATH,
    ]

    checked = []
    for c in candidates:
        c = os.path.abspath(c)
        if c in checked:
            continue
        checked.append(c)
        if os.path.isfile(os.path.join(c, "db.py")) and os.path.isfile(os.path.join(c, "fee_cycles.py")):
            return c

    raise FileNotFoundError(
        "Could not locate the AR School ERP project (looked for a folder containing "
        "both db.py and fee_cycles.py). Checked:\n  - " + "\n  - ".join(checked) +
        f"\n\nSet the {ERP_DIR_ENV} environment variable to the correct AR School ERP "
        "folder path and try again."
    )


def get_pending_fee_students(role: str = "Admin") -> list[dict]:
    """Returns one row per (student, outstanding fee cycle) — i.e. a
    student with two unpaid months appears twice, once per cycle — sourced
    entirely from the real fee_cycles ledger via fee_cycles.py. Never
    touches or recomputes students.total_fee/paid_fee."""
    erp_dir = _find_erp_dir()
    if erp_dir not in sys.path:
        sys.path.insert(0, erp_dir)

    import db          # the REAL db.py — resolves to whichever school_system.db it's built for
    import fee_cycles  # the REAL fee_cycles.py — single source of truth for pending/overdue status

    rows = fee_cycles.pending_and_overdue_students(role)  # no month/year filter -> every currently outstanding cycle

    # pending_and_overdue_students() doesn't include father_name (it isn't
    # needed by the sender flow, which builds messages via
    # fee_whatsapp_bridge.py instead) — fetch it here in one batch query
    # purely for this diagnostic's own display, not as a second status
    # calculation.
    student_ids = list({r["student_id"] for r in rows})
    father_names = {}
    if student_ids:
        placeholders = ",".join("?" for _ in student_ids)
        for sid, father_name in db.run(
            f"SELECT student_id, father_name FROM students WHERE student_id IN ({placeholders})",
            tuple(student_ids), fetchall=True,
        ) or []:
            father_names[sid] = father_name or ""

    students = []
    for r in rows:
        students.append({
            "student_id": r["student_id"],
            "student_name": r["name"] or "",
            "parent_name": father_names.get(r["student_id"], ""),
            "phone": r["phone"] or "",
            "class": r["class_sec"] or "",
            "cycle_id": r["cycle_id"],
            "billing_month": r["billing_month"],
            "billing_year": r["billing_year"],
            "amount_due": r["amount_due"],
            "amount_paid": r["amount_paid"],
            "pending_fee": r["balance"],
            "due_date": r["due_date"],
            "status": r["status"],
        })
    return students


def _resolved_db_path() -> str:
    erp_dir = _find_erp_dir()
    if erp_dir not in sys.path:
        sys.path.insert(0, erp_dir)
    import db
    return str(db.DB_PATH)


if __name__ == "__main__":

    try:
        db_path = _resolved_db_path()
        students = get_pending_fee_students()

        print("\n================================")
        print("AR SCHOOL DATABASE")
        print("Pending / Partial / Overdue Fee Cycles")
        print("================================\n")

        print(f"Connected database: {db_path}")
        print(f"Database exists: {os.path.exists(db_path)}")
        print(f"Total pending fee cycles: {len(students)}\n")

        print(json.dumps(
            students,
            indent=4,
            ensure_ascii=False
        ))

    except Exception as error:

        print("\n❌ DATABASE ERROR")
        print(error)

        sys.exit(1)
