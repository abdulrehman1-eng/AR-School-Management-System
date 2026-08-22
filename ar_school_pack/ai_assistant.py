"""
ai_assistant.py — Database-Grounded Admin Data Assistant.

IMPORTANT — what this actually is: a rule-based (regex/keyword) natural-
language query engine over the school database, NOT a call to an
external generative AI service. This environment has no network access
and the spec itself asks to "keep deterministic database calculations
local" and to avoid sending school data to an external API — a fully
local, deterministic engine satisfies the "never hallucinate" and
"read-only by default" requirements exactly, with zero risk of an LLM
inventing a number. It understands a defined set of question patterns
(student fee status, attendance, class counts, revenue/expense,
admissions, teacher attendance, results, etc.) in English and
Roman-Urdu/English mixed phrasing, matching the example questions in
the spec. Anything it doesn't recognize, or that isn't in the database,
gets an explicit "I couldn't find this" answer — never a guess.

Every answer is built from parameterized SQL through db.run() / the
existing business-logic modules (accounting, results_engine) — the same
functions the rest of the app already uses and that are already
individually tested. This module adds no new write paths: it is
read-only by construction (it never calls commit=True).

RBAC: answers respect the same permissions as the rest of the app —
a role without accounting.dashboard/student.fee.view doesn't get
financial answers through the assistant either.
"""

import re
from datetime import datetime

import db
import rbac
import accounting
import results_engine

NOT_FOUND = "I couldn't find this information in the current school records."


def _month_year_from_text(text):
    """Very small helper: defaults to the current month/year unless the
    question names a month. Returns (month_num, year, 'YYYY-MM')."""
    months = {
        "january": 1, "february": 2, "march": 3, "april": 4, "may": 5, "june": 6,
        "july": 7, "august": 8, "september": 9, "october": 10, "november": 11, "december": 12,
    }
    now = datetime.now()
    month, year = now.month, now.year
    for name, num in months.items():
        if name in text:
            month = num
            break
    ym = re.search(r"(20\d{2})", text)
    if ym:
        year = int(ym.group(1))
    return month, year, f"{year:04d}-{month:02d}"


def _find_students_by_name_or_id(text):
    """Look for an explicit Student ID pattern first (unambiguous); fall
    back to a name search. Returns a list of (student_id, name, class_sec)."""
    id_match = re.search(r"\b([A-Za-z]{2,5}-\d{2,4}-\d{2,4}|[A-Za-z]{2,5}\d{3,6})\b", text)
    if id_match:
        row = db.run("SELECT student_id, name, class_sec FROM students WHERE student_id=?",
                      (id_match.group(1).upper(),), fetchone=True)
        return [row] if row else []

    # crude name extraction: strip common question words, keep capitalized-looking tokens
    stopwords = {"the", "is", "was", "ki", "ka", "ke", "kitni", "kitna", "kya", "hai", "hain",
                 "paid", "fee", "fees", "balance", "total", "batao", "student", "marks", "result",
                 "attendance", "this", "month", "please", "of", "for", "show", "me", "abhi", "pending"}
    words = [w for w in re.findall(r"[A-Za-z]+", text) if w.lower() not in stopwords and len(w) > 1]
    if not words:
        return []
    name_guess = " ".join(words[:3])  # most questions name the student near the start
    rows = db.run("SELECT student_id, name, class_sec FROM students WHERE name LIKE ? AND COALESCE(status,'Active')='Active'",
                   (f"%{name_guess}%",), fetchall=True)
    if not rows:
        # try each individual word in case the guess included extra noise
        for w in words:
            rows = db.run("SELECT student_id, name, class_sec FROM students WHERE name LIKE ? AND COALESCE(status,'Active')='Active'",
                           (f"%{w}%",), fetchall=True)
            if rows:
                break
    return rows


def _disambiguate(rows):
    lines = [f"{i+1}. {name} — {sid} — Class {cls}" for i, (sid, name, cls) in enumerate(rows[:8])]
    return "Please select which student you mean. I found {} matching student(s):\n{}".format(
        len(rows), "\n".join(lines))


def _fee_status_answer(row, month_label=None):
    s_id, name, cls = row
    # Prefer monthly fee_cycles ledger (source of truth); fall back to
    # students.total_fee / paid_fee for older databases without cycles.
    try:
        import fee_cycles
        latest = fee_cycles.get_latest_cycle(s_id)
        if latest:
            total = float(latest.get("amount_due") or 0)
            paid = float(latest.get("amount_paid") or 0)
            balance = round(total - paid, 2)
            status = latest.get("status") or ("Paid" if balance <= 0 else "Pending")
            when = f" ({month_label})" if month_label else (
                f" ({latest['billing_month']:02d}/{latest['billing_year']})"
            )
            verdict = (
                f"Ji, {name} ({s_id}) ki fee{when} paid hai."
                if balance <= 0
                else f"{name} ({s_id}) ki fee{when} abhi pending hai."
            )
            return (
                f"{verdict}\n\n"
                f"Amount Due: Rs. {total:,.0f}\n"
                f"Paid: Rs. {paid:,.0f}\n"
                f"Balance: Rs. {balance:,.0f}\n"
                f"Status: {status}"
            )
    except Exception:
        pass
    student = db.run("SELECT total_fee, paid_fee FROM students WHERE student_id=?", (s_id,), fetchone=True)
    if not student:
        return NOT_FOUND
    total, paid = student
    balance = total - paid
    status = "Paid" if balance <= 0 else "Pending"
    when = f" ({month_label})" if month_label else ""
    verdict = f"Ji, {name} ({s_id}) ki fee{when} paid hai." if status == "Paid" else f"{name} ({s_id}) ki fee{when} abhi pending hai."
    return (f"{verdict}\n\n"
            f"Total Fee: Rs. {total:,.0f}\n"
            f"Paid: Rs. {paid:,.0f}\n"
            f"Balance: Rs. {balance:,.0f}\n"
            f"Status: {status}")


def answer_question(role, question, current_user="admin"):
    """Main entry point. Returns a plain-text answer string. Never raises
    for a normal bad/unrecognized question — returns NOT_FOUND-style text
    instead, since a crash here would be a much worse failure mode than
    an honest 'I don't understand'."""
    if not question or not question.strip():
        return "Please ask a question — e.g. 'Aaj kitne students present hain?' or 'STU-2026-001 ki fee paid hai?'"

    q = question.strip()
    ql = q.lower()

    try:
        # ---------- Today's attendance ----------
        if ("present" in ql or "absent" in ql) and ("today" in ql or "aaj" in ql):
            today = datetime.now().strftime("%Y-%m-%d")
            present = db.run("SELECT COUNT(*) FROM attendance WHERE date=? AND status='Present'", (today,), fetchone=True)[0]
            absent = db.run("SELECT COUNT(*) FROM attendance WHERE date=? AND status='Absent'", (today,), fetchone=True)[0]
            return f"Today ({today}): Present = {present}, Absent = {absent}."

        # ---------- Pending / overdue fees list this month (checked before
        # the generic single-student fee branch below, since these queries
        # also contain the word "fee" but mean something completely different) ----------
        if any(k in ql for k in ["pending fees", "overdue", "kin students", "kaun se students",
                                  "sabse zyada pending", "kis student ki hai"]):
            if not rbac.can(role, "student.fee.view"):
                return "You don't have permission to view fee information."
            # Prefer fee_cycles ledger (PENDING / PARTIAL / OVERDUE). Fall back
            # to students.total_fee - paid_fee when no cycle rows exist yet.
            rows = []
            try:
                import fee_cycles
                pending = fee_cycles.pending_and_overdue_students(role)
                # Aggregate per student (a student may have multiple open cycles)
                by_sid = {}
                for p in pending:
                    sid = p["student_id"]
                    if sid not in by_sid:
                        by_sid[sid] = {
                            "student_id": sid, "name": p["name"], "class_sec": p["class_sec"],
                            "balance": 0.0,
                        }
                    by_sid[sid]["balance"] += float(p.get("balance") or 0)
                rows = sorted(by_sid.values(), key=lambda x: -x["balance"])[:10]
            except Exception:
                rows = []
            if not rows:
                legacy = db.run(
                    "SELECT student_id, name, class_sec, total_fee, paid_fee FROM students "
                    "WHERE COALESCE(status,'Active')='Active' AND total_fee > paid_fee "
                    "ORDER BY (total_fee - paid_fee) DESC LIMIT 10", fetchall=True,
                ) or []
                rows = [
                    {"student_id": r[0], "name": r[1], "class_sec": r[2],
                     "balance": float(r[3] or 0) - float(r[4] or 0)}
                    for r in legacy
                ]
            if not rows:
                return "No students currently have pending fees."
            if "sabse zyada" in ql or "highest" in ql:
                top = rows[0]
                return (
                    f"The student with the highest pending fee is {top['name']} "
                    f"({top['student_id']}, Class {top['class_sec']}):\n"
                    f"Balance: Rs. {top['balance']:,.0f}"
                )
            lines = [
                f"- {r['name']} ({r['student_id']}, Class {r['class_sec']}): "
                f"Rs. {r['balance']:,.0f} pending"
                for r in rows
            ]
            return f"{len(rows)} student(s) with pending fees (showing up to 10):\n" + "\n".join(lines)

        # ---------- This month's revenue / expense / net income (also
        # checked before the generic fee branch, for the same reason) ----------
        if any(k in ql for k in ["revenue", "collection", "total fee collection", "is month ki total fee"]):
            if not rbac.can(role, "accounting.dashboard"):
                return "You don't have permission to view financial information."
            totals = accounting.dashboard_totals(role)
            return f"This month's revenue (fee collection): Rs. {totals['month_revenue']:,.0f}."

        if "expense" in ql:
            if not rbac.can(role, "accounting.dashboard"):
                return "You don't have permission to view financial information."
            totals = accounting.dashboard_totals(role)
            return f"This month's total expense: Rs. {totals['month_expense']:,.0f}."

        if "net income" in ql or "net balance" in ql:
            if not rbac.can(role, "accounting.dashboard"):
                return "You don't have permission to view financial information."
            totals = accounting.dashboard_totals(role)
            return f"This month's net income: Rs. {totals['net_income']:,.0f} (Revenue Rs. {totals['month_revenue']:,.0f} − Expense Rs. {totals['month_expense']:,.0f})."

        # ---------- Class-wise student count / attendance percentage ----------
        cls_match = re.search(r"class\s*[-]?\s*(\w+)", ql)
        if cls_match and ("kitne students" in ql or "how many students" in ql or "student count" in ql):
            cls_val = cls_match.group(1)
            rows = db.run("SELECT class_sec FROM students WHERE COALESCE(status,'Active')='Active'", fetchall=True)
            matches = [r[0] for r in rows if r[0] and cls_val.lower() in r[0].lower()]
            if not matches:
                return NOT_FOUND
            return f"Class {matches[0]} has {len(matches)} active student(s)." if len(set(matches)) == 1 else \
                   f"Found {len(matches)} active student(s) across classes matching '{cls_val}'."

        if cls_match and ("attendance percentage" in ql or ("attendance" in ql and "percent" in ql)):
            cls_val = cls_match.group(1)
            students_in_class = [r[0] for r in db.run(
                "SELECT student_id FROM students WHERE class_sec LIKE ? AND COALESCE(status,'Active')='Active'",
                (f"%{cls_val}%",), fetchall=True)]
            if not students_in_class:
                return NOT_FOUND
            placeholders = ",".join("?" * len(students_in_class))
            total_marks_rows = db.run(
                f"SELECT COUNT(*) FROM attendance WHERE student_id IN ({placeholders})",
                tuple(students_in_class), fetchone=True)[0]
            present_rows = db.run(
                f"SELECT COUNT(*) FROM attendance WHERE student_id IN ({placeholders}) AND status='Present'",
                tuple(students_in_class), fetchone=True)[0]
            if total_marks_rows == 0:
                return "No attendance records found yet for that class."
            pct = present_rows / total_marks_rows * 100
            return f"Attendance for that class: {pct:.1f}% present across {total_marks_rows} recorded attendance entries."

        # ---------- Admissions this month ----------
        if "admission" in ql:
            # No admission_date column exists on students, so a true
            # date-of-admission count isn't available yet — honest
            # "not found" rather than a fabricated number. See
            # README/QA known limitations.
            return NOT_FOUND

        # ---------- Active teachers ----------
        if "teachers" in ql and ("active" in ql or "kitne" in ql or "how many" in ql):
            count = db.run("SELECT COUNT(*) FROM teachers", fetchone=True)[0]
            return f"There are {count} teacher(s) on record."

        # ---------- Teacher attendance this month ----------
        if "teacher" in ql and "attendance" in ql:
            trows = db.run("SELECT teacher_id, name FROM teachers", fetchall=True)
            found = None
            for t_id, t_name in trows:
                first_name = t_name.split()[0].lower()
                if first_name in ql:
                    found = (t_id, t_name)
                    break
            if not found:
                return NOT_FOUND
            t_id, t_name = found
            month, year, ym = _month_year_from_text(ql)
            present = db.run("SELECT COUNT(*) FROM teacher_attendance WHERE teacher_id=? AND status='Present' AND date LIKE ?",
                              (t_id, f"{ym}%"), fetchone=True)[0]
            absent = db.run("SELECT COUNT(*) FROM teacher_attendance WHERE teacher_id=? AND status='Absent' AND date LIKE ?",
                             (t_id, f"{ym}%"), fetchone=True)[0]
            if present + absent == 0:
                return f"No attendance records found yet for {t_name} in {ym}."
            return f"{t_name} ({t_id}) attendance for {ym}: Present {present}, Absent {absent}."

        # ---------- Last payment ----------
        if "last payment" in ql:
            if not rbac.can(role, "accounting.revenue.view"):
                return "You don't have permission to view financial information."
            row = db.run("SELECT student_id, amount, date FROM accounting_revenue ORDER BY id DESC LIMIT 1", fetchone=True)
            if not row:
                return NOT_FOUND
            s_id, amount, date = row
            sname = db.run("SELECT name FROM students WHERE student_id=?", (s_id,), fetchone=True)
            name = sname[0] if sname else s_id
            return f"The most recent payment was Rs. {amount:,.0f} from {name} ({s_id}) on {date}."

        # ---------- Marks / result ----------
        if "marks" in ql or "result" in ql:
            rows = _find_students_by_name_or_id(q)
            if not rows:
                return NOT_FOUND
            if len(rows) > 1:
                return _disambiguate(rows)
            s_id, name, cls = rows[0]
            if not rbac.can(role, "results.view"):
                return "You don't have permission to view results."
            result = results_engine.compute_result(s_id)
            if not result:
                return f"No marks are on record yet for {name} ({s_id})."
            lines = [f"  {s['subject']}: {s['obtained']:.0f}/{s['total']:.0f} ({s['percent']:.1f}%)" for s in result["subjects"]]
            status = "PASS" if result["passed"] else "FAIL"
            return (f"{name} ({s_id}) result:\n" + "\n".join(lines) +
                    f"\n\nOverall: {result['total_obtained']:.0f}/{result['total_marks']:.0f} "
                    f"({result['percentage']:.1f}%) — Grade {result['grade']} — {status}")

        # ---------- Today's / monthly school summary ----------
        if "summary" in ql or ("today" in ql and "school" in ql) or "monthly school report" in ql or "monthly report" in ql:
            today = datetime.now().strftime("%Y-%m-%d")
            total_students = db.run("SELECT COUNT(*) FROM students WHERE COALESCE(status,'Active')='Active'", fetchone=True)[0]
            present = db.run("SELECT COUNT(*) FROM attendance WHERE date=? AND status='Present'", (today,), fetchone=True)[0]
            absent = db.run("SELECT COUNT(*) FROM attendance WHERE date=? AND status='Absent'", (today,), fetchone=True)[0]
            pct = (present / (present + absent) * 100) if (present + absent) else 0
            lines = [f"Today's Summary ({today}):", f"Students: {total_students}",
                     f"Present: {present}", f"Absent: {absent}", f"Attendance: {pct:.1f}%"]
            if rbac.can(role, "accounting.dashboard"):
                totals = accounting.dashboard_totals(role)
                lines += [f"Fee collected this month: Rs. {totals['month_revenue']:,.0f}",
                          f"Expenses this month: Rs. {totals['month_expense']:,.0f}",
                          f"Net income this month: Rs. {totals['net_income']:,.0f}"]
            teachers_total = db.run("SELECT COUNT(*) FROM teachers", fetchone=True)[0]
            lines.append(f"Teachers on record: {teachers_total}")
            return "\n".join(lines)

        # ---------- Fee status / balance for a SPECIFIC student (generic
        # catch-all — checked last since almost every fee-related question
        # contains the word "fee", but only this one needs a named student) ----------
        if any(k in ql for k in ["fee", "balance", "paid hai", "kitni fee", "kitna fee"]):
            rows = _find_students_by_name_or_id(q)
            if not rows:
                return NOT_FOUND
            if len(rows) > 1:
                return _disambiguate(rows)
            if not rbac.can(role, "student.fee.view"):
                return "You don't have permission to view fee information."
            return _fee_status_answer(rows[0])

        return NOT_FOUND
    except Exception:
        # A bug in one intent branch should never crash the assistant window
        # or, worse, silently produce a wrong number — fail safe to NOT_FOUND.
        return NOT_FOUND


SUGGESTED_QUESTIONS = [
    "Aaj kitne students present hain?",
    "Is month ki total fee collection kitni hai?",
    "Kaun se students ki fees overdue hain?",
    "Class 6 mein kitne students hain?",
    "Give me today's school summary.",
    "Sabse zyada pending fee kis student ki hai?",
]
