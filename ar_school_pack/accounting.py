"""
accounting.py — Revenue, expenses, and the fee/salary integration.

Key integration rules (per spec sections 19-24):
- A student fee payment (an increase in `paid_fee`) automatically creates a
  matching accounting_revenue row — no need to enter it twice.
- A salary payslip run automatically creates a matching accounting_expense
  row categorized as 'Salary'.
- Every write here goes through rbac.require() so a Teacher/Reception
  account cannot record or read accounting entries even by calling these
  functions directly.
"""

from datetime import datetime
import db
import rbac


def record_fee_revenue(role, student_id, amount, recorded_by, description="Fee payment", payment_method="Cash"):
    """Called automatically when a student's paid_fee increases. Guarded by
    the same 'student.fee.edit' permission the caller already needed."""
    if amount <= 0:
        return
    rbac.require(role, "student.fee.edit")
    db.run(
        """INSERT INTO accounting_revenue
           (source_type, student_id, amount, date, description, reference, payment_method, recorded_by)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        ("Student Fee", student_id, amount, datetime.now().strftime("%Y-%m-%d"),
         description, f"STU-{student_id}", payment_method, recorded_by),
        commit=True,
    )


def record_admission_fee_revenue(role, student_id, amount, recorded_by,
                                 description="One-time admission fee", payment_method="Cash"):
    """Post one-time Admission Fee into accounting_revenue.

    Kept separate from monthly 'Student Fee' so dashboards and student
    profile can split Admission Fee vs Monthly Fee revenue. Uses the same
    student.fee.edit gate as regular fee collection (admission staff already
    has it), not the stricter accounting.revenue.add permission.
    """
    if amount <= 0:
        return
    # Prefer fee-edit; fall back to revenue.add for pure finance roles.
    try:
        rbac.require(role, "student.fee.edit")
    except Exception:
        rbac.require(role, "accounting.revenue.add")
    db.run(
        """INSERT INTO accounting_revenue
           (source_type, student_id, amount, date, description, reference, payment_method, recorded_by)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        ("Admission Fee", student_id, amount, datetime.now().strftime("%Y-%m-%d"),
         description, f"ADM-{student_id}", payment_method, recorded_by),
        commit=True,
    )


def add_revenue(role, source_type, amount, description, reference, payment_method, recorded_by, student_id=None):
    rbac.require(role, "accounting.revenue.add")
    db.run(
        """INSERT INTO accounting_revenue
           (source_type, student_id, amount, date, description, reference, payment_method, recorded_by)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        (source_type, student_id, amount, datetime.now().strftime("%Y-%m-%d"),
         description, reference, payment_method, recorded_by),
        commit=True,
    )


def record_salary_expense(role, teacher_id, teacher_name, amount, month, recorded_by, reference=""):
    rbac.require(role, "teacher.salary.pay")
    db.run(
        """INSERT INTO accounting_expense
           (category, amount, date, description, vendor_or_person, payment_method, reference, recorded_by)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        ("Salary", amount, datetime.now().strftime("%Y-%m-%d"),
         f"Salary payment for {month}", f"{teacher_name} ({teacher_id})", "Bank/Cash", reference, recorded_by),
        commit=True,
    )


def add_expense(role, category, amount, description, vendor, payment_method, reference, recorded_by):
    rbac.require(role, "accounting.expense.add")
    db.run(
        """INSERT INTO accounting_expense
           (category, amount, date, description, vendor_or_person, payment_method, reference, recorded_by)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        (category, amount, datetime.now().strftime("%Y-%m-%d"), description, vendor, payment_method, reference, recorded_by),
        commit=True,
    )


def list_revenue(role, start_date=None, end_date=None):
    rbac.require(role, "accounting.revenue.view")
    if start_date and end_date:
        return db.run(
            "SELECT id, source_type, student_id, amount, date, description, payment_method FROM accounting_revenue "
            "WHERE date BETWEEN ? AND ? ORDER BY id DESC", (start_date, end_date), fetchall=True,
        )
    return db.run(
        "SELECT id, source_type, student_id, amount, date, description, payment_method FROM accounting_revenue ORDER BY id DESC",
        fetchall=True,
    )


def list_expense(role, start_date=None, end_date=None):
    rbac.require(role, "accounting.expense.view")
    if start_date and end_date:
        return db.run(
            "SELECT id, category, amount, date, description, vendor_or_person, payment_method FROM accounting_expense "
            "WHERE date BETWEEN ? AND ? ORDER BY id DESC", (start_date, end_date), fetchall=True,
        )
    return db.run(
        "SELECT id, category, amount, date, description, vendor_or_person, payment_method FROM accounting_expense ORDER BY id DESC",
        fetchall=True,
    )


def dashboard_totals(role, start_date=None, end_date=None):
    rbac.require(role, "accounting.dashboard")
    if start_date and end_date:
        rev = db.run("SELECT COALESCE(SUM(amount),0) FROM accounting_revenue WHERE date BETWEEN ? AND ?",
                      (start_date, end_date), fetchone=True)[0]
        exp = db.run("SELECT COALESCE(SUM(amount),0) FROM accounting_expense WHERE date BETWEEN ? AND ?",
                      (start_date, end_date), fetchone=True)[0]
    else:
        rev = db.run("SELECT COALESCE(SUM(amount),0) FROM accounting_revenue", fetchone=True)[0]
        exp = db.run("SELECT COALESCE(SUM(amount),0) FROM accounting_expense", fetchone=True)[0]

    today = datetime.now().strftime("%Y-%m-%d")
    month = datetime.now().strftime("%Y-%m")
    today_rev = db.run("SELECT COALESCE(SUM(amount),0) FROM accounting_revenue WHERE date=?", (today,), fetchone=True)[0]
    today_exp = db.run("SELECT COALESCE(SUM(amount),0) FROM accounting_expense WHERE date=?", (today,), fetchone=True)[0]
    month_rev = db.run("SELECT COALESCE(SUM(amount),0) FROM accounting_revenue WHERE date LIKE ?", (f"{month}%",), fetchone=True)[0]
    month_exp = db.run("SELECT COALESCE(SUM(amount),0) FROM accounting_expense WHERE date LIKE ?", (f"{month}%",), fetchone=True)[0]

    return {
        "total_revenue": rev, "total_expense": exp, "net_income": rev - exp,
        "today_revenue": today_rev, "today_expense": today_exp,
        "month_revenue": month_rev, "month_expense": month_exp,
    }
