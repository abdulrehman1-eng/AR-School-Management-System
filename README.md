# AR School Management System — Upgraded Edition

**AR Software Solutions** · Smart Software. Simple Solutions.

## 1. Install

```bash
pip install -r requirements.txt
```

> `qrcode` is optional. If it isn't installed, ID cards still generate —
> they show a printed student-ID code stamp instead of a scannable QR
> square. Everything else works identically either way.

## 2. Run

```bash
python3 app.py
```

A fresh `school_system.db` is created automatically on first run. If you're
upgrading from an older copy of this project, drop your existing
`school_system.db` into this folder before running — see **Section 5**
below; your data is upgraded in place, nothing is dropped or deleted.

## 3. Default accounts

The application creates initial accounts for development/testing.
For production use, change or disable all default accounts and create
individual accounts for school staff under Settings → Users & Roles.

Never publish real production credentials in this repository.

## 4. File map

| File | Purpose |
|---|---|
| `app.py` | Tkinter UI (all tabs) + login window |
| `db.py` | Schema, single shared connection, indexes, non-destructive migrations |
| `security.py` | PBKDF2 password hashing + legacy plain-text auto-upgrade |
| `rbac.py` | Permission matrix (Admin/Teacher/Reception) + DB-backed overrides |
| `branding.py` | Centralized org name/logo/contact used everywhere |
| `accounting.py` | Revenue/expense ledger, fee & salary integration |
| `results_engine.py` | Configurable grading bands + pass/fail rules |
| `reports.py` | PDF generation (ID card w/ QR, payslip, marksheet) |
| `theme.py` | Central design system — palette, fonts, sidebar/card/button styling |
| `ai_assistant.py` | Rule-based, database-grounded AI Admin Assistant (no external API — see § 6) |
| `academic_year.py` | Academic Year/session management — create/close years, per-student year enrollment history |
| `student_lifecycle.py` | Safe Archive/Restore (reversible) and Permanent Delete (backup + transaction + rollback) for students |

## How to generate ID cards, receipts, and reports

- **ID cards:** Students page → select a student → "Generate ID Card". Produces
  a PDF with a QR code and a Code128 barcode (both require the optional
  `qrcode` / `python-barcode` packages — see § 6 below for what happens
  without them).
- **Fee receipts:** recording a fee payment (new admission or editing an
  existing student's Paid Fee) automatically offers a receipt dialog
  right after the save succeeds — "Print Slip" opens the generated PDF
  in your system's default PDF viewer (print from there), "Save Slip
  As..." lets you choose where to save it.
- **Marksheets/report cards:** Results & Academics page → enter a
  Student ID → "🧾 Marksheet PDF".
- **Payslips:** Teachers & Payroll page → select a teacher → "Generate
  Payslip" (blocked from silently duplicating if one was already issued
  this month for that teacher).

## Students: Archive vs. Permanent Delete, and Academic Years

- **Archive Student** (reversible): removes a student from the default
  Students directory view while keeping every attendance, marks, and fee
  record exactly as it was. Use this for anyone who left, graduated, or
  transferred. **Restore Student** reverses it at any time. Toggle
  "Show Archived" in the directory to see archived students again.
- **Permanently Delete** (irreversible, Admin only): actually removes the
  student and their attendance/marks/academic-year/admission-profile
  rows from the database. Before doing anything, it (1) shows exactly
  what will be removed, (2) takes a full timestamped database backup
  into `backups/`, then (3) deletes everything in one all-or-nothing
  transaction — if any step fails, nothing is changed. Fee/accounting
  ledger entries (`accounting_revenue`) are never deleted by this, even
  on permanent delete, since they're a financial audit trail. Use this
  only for genuine mistaken entries, not for students who simply left.
- **Academic Years:** Settings → Academic Year (Admin only). Only one
  year is ever "Current" at a time — new admissions and the Students
  directory's year filter default to it. **Close Selected Year** always
  takes a full database backup first (so it's always reversible by
  restoring that file), marks the year Closed, and can open the next
  year as Current in the same step. Closing a year never touches
  student/attendance/marks/fee data by itself.

## 5. Backup & restore

**Backup:** Settings → Backup, or copy `school_system.db` anywhere while
the app is closed (it's a single SQLite file — no other state to copy).
A timestamped backup file (`school_backup_YYYYMMDD_HHMMSS.db`) is what the
in-app "Backup" button produces.

**Restore:** close the app, replace `school_system.db` with the backup
file (rename it back to `school_system.db`), then relaunch. There is no
in-app restore button yet — see Known Limitations.

**Upgrading an older install:** just copy the old `school_system.db` into
this project folder and run `app.py`. `db.init_db()` only ever *adds*
tables/columns — it never drops or alters existing ones — so existing
students, fees, attendance, marks, and audit history all carry forward
untouched. This was verified against a synthetic pre-upgrade database as
part of QA (see `QA_REPORT.md`).

## 6. Known limitations

- No self-service "change my own password" screen for logged-in users.
- Real QR codes require the optional `qrcode` package; without it, ID
  cards fall back to a printed code stamp (still scannable by name/typed
  entry into the attendance field, just not a QR image). The same applies
  to the Code128 barcode and the optional `python-barcode` package —
  without it, the barcode area falls back to a printed-text stamp.
  **Neither library is installed in the environment this build was
  tested in (no network access), so only the fallback paths have been
  exercised — install both packages and scan a generated ID card with an
  actual barcode scanner before relying on this for real students.**
- `branding.logo_path` is stored but not yet drawn onto generated PDFs
  (org name/address/phone/email are).
- Archiving/restoring a student now uses its own `student.archive`
  permission (separate from `student.delete`) — see `rbac.py`.
- The AI Admin Assistant (sidebar → 🤖 AI Admin Assistant) is a **local,
  rule-based query engine**, not a call to an external AI service — this
  environment has no network access to reach one, and the design keeps
  every answer traceable to a real database query so it can never
  hallucinate a number. It understands a defined set of question types
  (fee status, attendance, class counts, revenue/expense, results,
  teacher attendance, etc.) in English and Roman Urdu/English mixed
  phrasing. Questions outside that set get an honest "I couldn't find
  this information" rather than a wrong or invented answer.
- `student_lifecycle.permanent_delete_student()` and
  `academic_year.close_year()` write their backups to a `backups/`
  folder created next to `school_system.db` — that folder isn't part of
  version control/USB backups by default, so periodically copy it
  offsite too (or run Settings → USB Backup, which is separate and
  unaffected by this).
- The following areas from the production-hardening request were **not**
  implemented in this release and remain open work: professional ID
  card redesign, a separate admission dialog window, a redesigned report
  card, extended teacher profile fields, timetable conflict detection,
  finance date-range reporting, a dedicated Fee Collection window, and a
  formal fee receipt PDF, per-student promotion/graduation workflow UI
  during year closing, and pagination for very large student directories.
  See `QA_REPORT.md` for the full itemized status.

None of these affect data safety or core daily workflows (admissions,
attendance, fees, results, payroll, accounting, reporting all work fully).

## 7. Support

AR Software Solutions — see in-app About/Help for contact details once
configured under Settings.
