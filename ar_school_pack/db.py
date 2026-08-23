"""
db.py — Central database layer for School ERP.

Design goals (per upgrade spec):
- Single persistent connection (performance: avoid reopening SQLite per call).
- All schema in one place, additive only — never drops/alters away existing
  columns/tables so old databases upgrade in place without data loss.
- New tables added for: users(role/security), branding, grading config,
  passing criteria, accounting (revenue/expense), permission overrides.
"""

import sqlite3
import os
import threading
import contextlib
from datetime import datetime
from paths import get_database_path, get_backups_dir

DB_PATH = str(get_database_path())

_lock = threading.Lock()
_conn = None


def get_conn():
    """Return a single shared, thread-safe SQLite connection.

    Reusing one connection (instead of opening/closing per query like the
    original code did) removes a large amount of redundant file-open
    overhead and is the main DB-level performance fix requested.
    """
    global _conn
    if _conn is None:
        _conn = sqlite3.connect(DB_PATH, check_same_thread=False)
        _conn.execute("PRAGMA foreign_keys = ON")
        _conn.execute("PRAGMA journal_mode = WAL")
    return _conn


def close_conn():
    """Close the shared connection so the underlying .db file can be
    safely overwritten (restore) or copied while guaranteed consistent
    (backup) — WAL mode keeps in-flight data in a separate -wal file
    until checkpointed, so copying the .db file while a connection is
    still open risks grabbing a stale/incomplete snapshot. Safe to call
    even if no connection is currently open. get_conn() will silently
    reconnect on the next call."""
    global _conn
    with _lock:
        if _conn is not None:
            try:
                _conn.execute("PRAGMA wal_checkpoint(FULL)")
                _conn.commit()
            except Exception:
                pass
            _conn.close()
            _conn = None


def run(query, params=(), commit=False, fetchone=False, fetchall=False):
    """Thread-safe helper around the shared connection."""
    with _lock:
        cur = get_conn().cursor()
        cur.execute(query, params)
        result = None
        if fetchone:
            result = cur.fetchone()
        elif fetchall:
            result = cur.fetchall()
        if commit:
            get_conn().commit()
        return result


def executemany(query, seq_of_params, commit=True):
    with _lock:
        cur = get_conn().cursor()
        cur.executemany(query, seq_of_params)
        if commit:
            get_conn().commit()



# ---------------------------------------------------------------------------
# Centralized system_settings (key/value store)
# ---------------------------------------------------------------------------
_DEFAULT_SETTINGS = {
    # Attendance
    "school_start_time": "08:00",
    "late_threshold_time": "08:15",
    "school_closing_time": "14:00",
    "auto_absent_enabled": "1",
    # Messaging / WhatsApp
    "whatsapp_batch_delay": "5",
    "msg_template_fee_payment": (
        "Salam,\n\n"
        "{amount} PKR fees jama ho gayi hai.\n"
        "Student: {student_name}\n"
        "Baaki Raqam: {remaining} PKR\n"
        "Shukriya - {school_name}"
    ),
    "msg_template_admission": (
        "Salam {parent_name},\n\n"
        "Mubarak ho! {student_name} ka {school_name} me admission ho gaya hai.\n"
        "Class: {class_sec}\n\n"
        "Shukriya"
    ),
    "msg_template_attendance": (
        "Salam,\n\n"
        "{student_name} aaj {status} mark kiya gaya hai ({time}).\n"
        "Class: {class_sec}\n"
        "Shukriya - {school_name}"
    ),
    "msg_template_fee_reminder": (
        "Salam,\n\n"
        "Reminder: {student_name} ki fee baaki hai.\n"
        "Outstanding: {remaining} PKR\n"
        "Please clear at your earliest.\n"
        "Shukriya - {school_name}"
    ),
    # School Info
    "school_name": "AR Academy",
    "school_phone": "",
    "school_address": "",
    "school_email": "",
    "school_logo_path": "",
    # System
    "auto_backup_on_exit": "1",
    "backup_folder_path": "",
    # Fee automation — auto monthly cycle generation
    "auto_fee_cycle_enabled": "1",
    "auto_fee_due_day": "10",          # due date = this day of billing month (1-28)
    "auto_fee_grace_days": "0",        # grace period after due date
}


def get_setting(key, default=None):
    """Return a system setting value. Falls back to built-in defaults, then
    the caller-supplied default."""
    try:
        row = run(
            "SELECT setting_value FROM system_settings WHERE setting_key=?",
            (key,),
            fetchone=True,
        )
        if row is not None and row[0] is not None:
            return row[0]
    except Exception:
        pass
    if key in _DEFAULT_SETTINGS:
        return _DEFAULT_SETTINGS[key]
    return default


def set_setting(key, value):
    """Upsert a single system setting. Value is stored as TEXT."""
    run(
        "INSERT INTO system_settings (setting_key, setting_value) VALUES (?, ?) "
        "ON CONFLICT(setting_key) DO UPDATE SET setting_value=excluded.setting_value",
        (key, "" if value is None else str(value)),
        commit=True,
    )


def get_settings_group(prefix_or_keys):
    """Return a dict of settings. Accepts a list of keys or a string prefix."""
    if isinstance(prefix_or_keys, str):
        rows = run(
            "SELECT setting_key, setting_value FROM system_settings WHERE setting_key LIKE ?",
            (prefix_or_keys + "%",),
            fetchall=True,
        ) or []
        result = {k: v for k, v in rows}
        for k, v in _DEFAULT_SETTINGS.items():
            if k.startswith(prefix_or_keys) and k not in result:
                result[k] = v
        return result
    return {k: get_setting(k) for k in prefix_or_keys}


def set_settings_bulk(mapping):
    """Upsert many settings at once."""
    if not mapping:
        return
    with _lock:
        cur = get_conn().cursor()
        cur.executemany(
            "INSERT INTO system_settings (setting_key, setting_value) VALUES (?, ?) "
            "ON CONFLICT(setting_key) DO UPDATE SET setting_value=excluded.setting_value",
            [(k, "" if v is None else str(v)) for k, v in mapping.items()],
        )
        get_conn().commit()


def render_msg_template(template_key, **variables):
    """Load a message template by key and format with provided variables.
    Missing placeholders are left as empty strings."""
    tmpl = get_setting(template_key, "") or ""
    if "school_name" not in variables:
        variables["school_name"] = get_setting("school_name", "AR Academy")
    class _Safe(dict):
        def __missing__(self, key):
            return ""
    try:
        return tmpl.format_map(_Safe(**{k: ("" if v is None else v) for k, v in variables.items()}))
    except Exception:
        return tmpl


@contextlib.contextmanager
def transaction():
    """Multi-statement atomic transaction on the shared connection.

    Yields a cursor. Every statement executed through it commits together
    when the block exits normally, or rolls back together if anything
    raises -- used for operations like permanent student deletion where a
    partial failure must never leave the database half-changed.

    IMPORTANT: callers must use the yielded cursor directly (cur.execute),
    not db.run()/db.executemany(), inside this block -- _lock is not
    reentrant, so calling back into db.run() here would deadlock.
    """
    with _lock:
        conn = get_conn()
        cur = conn.cursor()
        try:
            yield cur
            conn.commit()
        except Exception:
            conn.rollback()
            raise


def backup_database(reason: str = "manual") -> str:
    """Take a consistent online backup of the live database into a
    backups/ folder next to school_system.db, using sqlite3's built-in
    backup API. Safe to call while the app is running / under WAL mode --
    it does not require closing the shared connection first.

    Returns the path to the backup file that was created.
    """
    backups_dir = str(get_backups_dir())
    os.makedirs(backups_dir, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    safe_reason = "".join(ch for ch in reason if ch.isalnum() or ch in ("-", "_")) or "manual"
    dest_path = os.path.join(backups_dir, f"Backup_{safe_reason}_{timestamp}.db")

    with _lock:
        source = get_conn()
        dest = sqlite3.connect(dest_path)
        try:
            source.backup(dest)
        finally:
            dest.close()
    return dest_path


def _default_year_label(reference_date=None) -> str:
    """Best-effort first-run academic-year label (e.g. '2026-27') for
    databases with no existing academic_year data to infer from. Assumes
    an April-start school year, which is common locally; an Admin can
    add/rename years freely afterwards via academic_year.py -- this is
    only a first-run seed, never re-applied once academic_years has rows."""
    d = reference_date or datetime.now()
    start = d.year if d.month >= 4 else d.year - 1
    return f"{start}-{str(start + 1)[-2:]}"


def init_db():
    conn = get_conn()
    cur = conn.cursor()

    # ---------------- Existing tables (preserved, unchanged shape) ----------
    cur.execute("""
    CREATE TABLE IF NOT EXISTS users (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        username TEXT UNIQUE NOT NULL,
        password TEXT NOT NULL,
        role TEXT NOT NULL
    )
    """)

    cur.execute("""
    CREATE TABLE IF NOT EXISTS students (
        student_id TEXT PRIMARY KEY,
        name TEXT NOT NULL,
        father_name TEXT,
        dob TEXT,
        phone TEXT,
        address TEXT,
        class_sec TEXT,
        photo_path TEXT,
        prev_education TEXT,
        total_fee REAL DEFAULT 0,
        paid_fee REAL DEFAULT 0
    )
    """)

    cur.execute("""
    CREATE TABLE IF NOT EXISTS attendance (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        student_id TEXT,
        date TEXT,
        status TEXT,
        method TEXT DEFAULT 'Manual',
        UNIQUE(student_id, date),
        FOREIGN KEY(student_id) REFERENCES students(student_id)
    )
    """)

    cur.execute("""
    CREATE TABLE IF NOT EXISTS subjects (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        class_name TEXT,
        subject_name TEXT
    )
    """)

    cur.execute("""
    CREATE TABLE IF NOT EXISTS marks (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        student_id TEXT,
        exam_type TEXT,
        subject_name TEXT,
        obtained_marks REAL,
        total_marks REAL,
        entered_by TEXT,
        entered_at TEXT,
        FOREIGN KEY(student_id) REFERENCES students(student_id)
    )
    """)

    cur.execute("""
    CREATE TABLE IF NOT EXISTS teachers (
        teacher_id TEXT PRIMARY KEY,
        name TEXT NOT NULL,
        designation TEXT,
        phone TEXT,
        basic_salary REAL DEFAULT 0,
        joining_date TEXT,
        linked_username TEXT
    )
    """)

    cur.execute("""
    CREATE TABLE IF NOT EXISTS teacher_attendance (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        teacher_id TEXT,
        date TEXT,
        status TEXT,
        in_time TEXT,
        UNIQUE(teacher_id, date),
        FOREIGN KEY(teacher_id) REFERENCES teachers(teacher_id)
    )
    """)

    cur.execute("""
    CREATE TABLE IF NOT EXISTS timetable (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        class_name TEXT,
        day_name TEXT,
        time_slot TEXT,
        subject_name TEXT,
        teacher_name TEXT
    )
    """)

    cur.execute("""
    CREATE TABLE IF NOT EXISTS audit_logs (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        username TEXT,
        action TEXT,
        timestamp TEXT
    )
    """)

    # ---------------- New tables (additive upgrade) -------------------------

    # Centralized branding — single-row config table.
    cur.execute("""
    CREATE TABLE IF NOT EXISTS branding (
        id INTEGER PRIMARY KEY CHECK (id = 1),
        org_name TEXT DEFAULT 'My School / Academy',
        logo_path TEXT DEFAULT '',
        address TEXT DEFAULT '',
        phone TEXT DEFAULT '',
        email TEXT DEFAULT ''
    )
    """)
    cur.execute("INSERT OR IGNORE INTO branding (id) VALUES (1)")


    # Centralized dynamic system settings (key/value). Additive — never
    # replaces the branding table; school_* keys mirror branding fields
    # so screens can read either source.
    cur.execute("""
    CREATE TABLE IF NOT EXISTS system_settings (
        setting_key TEXT PRIMARY KEY,
        setting_value TEXT
    )
    """)
    for _sk, _sv in _DEFAULT_SETTINGS.items():
        cur.execute(
            "INSERT OR IGNORE INTO system_settings (setting_key, setting_value) VALUES (?, ?)",
            (_sk, _sv),
        )
    # One-time bridge: if branding has real data and school_name is still
    # the default, copy branding → system_settings so the Settings UI and
    # WhatsApp templates pick up the existing school identity.
    try:
        cur.execute("SELECT org_name, logo_path, address, phone, email FROM branding WHERE id=1")
        brow = cur.fetchone()
        if brow and brow[0] and brow[0] not in ("My School / Academy", ""):
            cur.execute(
                "SELECT setting_value FROM system_settings WHERE setting_key='school_name'"
            )
            srow = cur.fetchone()
            if not srow or srow[0] in ("AR Academy", "My School / Academy", ""):
                cur.execute(
                    "INSERT OR REPLACE INTO system_settings (setting_key, setting_value) VALUES (?, ?)",
                    ("school_name", brow[0]),
                )
                if brow[1]:
                    cur.execute(
                        "INSERT OR REPLACE INTO system_settings (setting_key, setting_value) VALUES (?, ?)",
                        ("school_logo_path", brow[1]),
                    )
                if brow[2]:
                    cur.execute(
                        "INSERT OR REPLACE INTO system_settings (setting_key, setting_value) VALUES (?, ?)",
                        ("school_address", brow[2]),
                    )
                if brow[3]:
                    cur.execute(
                        "INSERT OR REPLACE INTO system_settings (setting_key, setting_value) VALUES (?, ?)",
                        ("school_phone", brow[3]),
                    )
                if brow[4]:
                    cur.execute(
                        "INSERT OR REPLACE INTO system_settings (setting_key, setting_value) VALUES (?, ?)",
                        ("school_email", brow[4]),
                    )
    except Exception:
        pass


    # Configurable grading bands (e.g. A+ 90-100, A 80-89, ...).
    cur.execute("""
    CREATE TABLE IF NOT EXISTS grading_config (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        grade TEXT NOT NULL,
        min_percent REAL NOT NULL,
        max_percent REAL NOT NULL
    )
    """)

    # Configurable pass/fail rule — single-row config table.
    cur.execute("""
    CREATE TABLE IF NOT EXISTS passing_criteria (
        id INTEGER PRIMARY KEY CHECK (id = 1),
        min_overall_percent REAL DEFAULT 40,
        require_pass_each_subject INTEGER DEFAULT 1,
        min_subject_percent REAL DEFAULT 33
    )
    """)
    cur.execute("INSERT OR IGNORE INTO passing_criteria (id) VALUES (1)")

    # Accounting: revenue (fees, admission fees, other income).
    cur.execute("""
    CREATE TABLE IF NOT EXISTS accounting_revenue (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        source_type TEXT NOT NULL,          -- 'Student Fee', 'Admission Fee', 'Other'
        student_id TEXT,
        amount REAL NOT NULL,
        date TEXT NOT NULL,
        description TEXT,
        reference TEXT,
        payment_method TEXT,
        recorded_by TEXT
    )
    """)

    # Accounting: expenses (salaries, rent, utilities, etc.)
    cur.execute("""
    CREATE TABLE IF NOT EXISTS accounting_expense (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        category TEXT NOT NULL,             -- 'Salary', 'Rent', 'Utilities', 'Other', ...
        amount REAL NOT NULL,
        date TEXT NOT NULL,
        description TEXT,
        vendor_or_person TEXT,
        payment_method TEXT,
        reference TEXT,
        recorded_by TEXT
    )
    """)

    # Permission overrides on top of the default role matrix in rbac.py —
    # lets an Admin fine-tune Teacher/Reception access without code changes.
    cur.execute("""
    CREATE TABLE IF NOT EXISTS permission_overrides (
        role TEXT NOT NULL,
        feature TEXT NOT NULL,
        allowed INTEGER NOT NULL,
        PRIMARY KEY (role, feature)
    )
    """)

    # Academic Year / session management. Only one row ever has
    # is_current=1 -- that's the year new admissions and daily screens
    # default to. status flips to 'Closed' when an Admin closes the year
    # (see academic_year.close_year), which is separate from a student's
    # own Active/Archived status.
    cur.execute("""
    CREATE TABLE IF NOT EXISTS academic_years (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        year_label TEXT UNIQUE NOT NULL,
        start_date TEXT,
        end_date TEXT,
        is_current INTEGER DEFAULT 0,
        status TEXT DEFAULT 'Open',
        closed_at TEXT,
        closed_by TEXT
    )
    """)

    # Per-year enrollment history: one row per student per academic year,
    # so a promotion, repeat, or mid-year class change never overwrites a
    # prior year's record the way a single mutable students.class_sec
    # would. students.current_academic_year (added below) mirrors the
    # row for whichever year is currently marked is_current, so existing
    # screens that just need "their year right now" don't need a join.
    cur.execute("""
    CREATE TABLE IF NOT EXISTS student_academic_year (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        student_id TEXT NOT NULL,
        academic_year TEXT NOT NULL,
        class_sec TEXT,
        enrollment_status TEXT DEFAULT 'Active',
        created_at TEXT,
        updated_at TEXT,
        UNIQUE(student_id, academic_year),
        FOREIGN KEY(student_id) REFERENCES students(student_id)
    )
    """)

    # ---------------- Monthly Fee Ledger (additive — new tables only) -------
    # Phase 2-9 of the fee-system upgrade. Does NOT touch students.total_fee/
    # paid_fee or accounting_revenue/accounting_expense in any way — those
    # keep working exactly as before for every existing screen. This is a
    # parallel, structured per-billing-month record so history is never
    # overwritten the way a single mutable students.paid_fee is.
    cur.execute("""
    CREATE TABLE IF NOT EXISTS fee_cycles (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        student_id TEXT NOT NULL,
        billing_month INTEGER NOT NULL,        -- 1-12
        billing_year INTEGER NOT NULL,
        class_sec TEXT,
        fee_amount REAL NOT NULL DEFAULT 0,    -- this cycle's base fee, before discount
        discount REAL NOT NULL DEFAULT 0,
        previous_balance REAL NOT NULL DEFAULT 0,  -- carried forward from the prior cycle
        amount_due REAL NOT NULL DEFAULT 0,    -- fee_amount - discount + previous_balance
        amount_paid REAL NOT NULL DEFAULT 0,
        due_date TEXT,
        grace_period_days INTEGER NOT NULL DEFAULT 0,
        status TEXT NOT NULL DEFAULT 'PENDING',   -- PAID/PARTIAL/PENDING/OVERDUE/ADVANCE
        created_at TEXT,
        updated_at TEXT,
        created_by TEXT,
        UNIQUE(student_id, billing_month, billing_year)
        -- Deliberately NO FOREIGN KEY on student_id: this is a financial
        -- ledger table, the same category as accounting_revenue (see
        -- accounting.py / student_lifecycle.py), which the app already
        -- never deletes even when a student is permanently removed. A
        -- hard FK here would reintroduce the exact "FOREIGN KEY
        -- constraint failed" bug student_lifecycle.py was built to fix,
        -- the moment a student with fee history was permanently deleted.
    )
    """)

    cur.execute("""
    CREATE TABLE IF NOT EXISTS fee_payments (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        cycle_id INTEGER NOT NULL,
        student_id TEXT NOT NULL,
        amount REAL NOT NULL,
        payment_method TEXT,
        receipt_no TEXT UNIQUE,
        paid_date TEXT,
        recorded_by TEXT,
        remarks TEXT,
        created_at TEXT,
        FOREIGN KEY(cycle_id) REFERENCES fee_cycles(id)
        -- No FK on student_id either, for the same audit-trail reason as
        -- fee_cycles above. cycle_id -> fee_cycles(id) is safe to keep as
        -- a hard FK because fee_cycles rows are likewise never deleted.
    )
    """)

    cur.execute("""
    CREATE TABLE IF NOT EXISTS fee_discount_history (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        cycle_id INTEGER NOT NULL,
        student_id TEXT NOT NULL,
        amount REAL NOT NULL,
        reason TEXT,
        given_by TEXT,
        created_at TEXT,
        FOREIGN KEY(cycle_id) REFERENCES fee_cycles(id)
    )
    """)

    # ---------------- Non-destructive column upgrades on old DBs -----------
    _add_column_if_missing(cur, "students", "photo_path", "TEXT")
    _add_column_if_missing(cur, "attendance", "method", "TEXT DEFAULT 'Manual'")
    _add_column_if_missing(cur, "marks", "entered_by", "TEXT")
    _add_column_if_missing(cur, "marks", "entered_at", "TEXT")
    _add_column_if_missing(cur, "teachers", "linked_username", "TEXT")
    _add_column_if_missing(cur, "users", "full_name", "TEXT")
    _add_column_if_missing(cur, "users", "is_hashed", "INTEGER DEFAULT 0")
    _add_column_if_missing(cur, "users", "linked_teacher_id", "TEXT")
    # Soft-delete flag for students — "Archived" instead of a hard DELETE so
    # attendance/marks/accounting rows tied to a student_id are never orphaned.
    _add_column_if_missing(cur, "students", "status", "TEXT DEFAULT 'Active'")
    cur.execute("UPDATE students SET status='Active' WHERE status IS NULL")
    _add_column_if_missing(cur, "subjects", "is_active", "INTEGER DEFAULT 1")
    _add_column_if_missing(cur, "users", "is_active", "INTEGER DEFAULT 1")
    # Mirrors whichever academic_years row is_current for this student —
    # kept in sync by academic_year.enroll_student(); the source of truth
    # for a student's full year-by-year history is student_academic_year.
    _add_column_if_missing(cur, "students", "current_academic_year", "TEXT")

    # ---------------- Indexes for performance -------------------------------
    cur.execute("CREATE INDEX IF NOT EXISTS idx_att_student ON attendance(student_id)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_att_date ON attendance(date)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_marks_student ON marks(student_id)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_rev_date ON accounting_revenue(date)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_exp_date ON accounting_expense(date)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_students_name ON students(name)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_students_status ON students(status)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_say_student ON student_academic_year(student_id)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_say_year ON student_academic_year(academic_year)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_academic_years_label ON academic_years(year_label)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_fee_cycles_student ON fee_cycles(student_id)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_fee_cycles_period ON fee_cycles(billing_year, billing_month)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_fee_cycles_status ON fee_cycles(status)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_fee_payments_cycle ON fee_payments(cycle_id)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_fee_payments_student ON fee_payments(student_id)")

    # ---------------- Seed default accounts & config ------------------------
    from security import hash_password
    _seed_user(cur, "admin", "admin123", "Admin")
    _seed_user(cur, "teacher", "teacher123", "Teacher")
    _seed_user(cur, "reception", "reception123", "Reception")

    cur.execute("SELECT COUNT(*) FROM grading_config")
    if cur.fetchone()[0] == 0:
        default_grades = [
            ("A+", 90, 100), ("A", 80, 89.99), ("B", 70, 79.99),
            ("C", 60, 69.99), ("D", 40, 59.99), ("F", 0, 39.99),
        ]
        cur.executemany(
            "INSERT INTO grading_config (grade, min_percent, max_percent) VALUES (?, ?, ?)",
            default_grades,
        )

    # Seed the academic-year system on first run so it's usable
    # immediately after upgrading an old database, with no setup wizard
    # required. Only runs once (academic_years starts empty); an Admin's
    # subsequent year-closing/creation via academic_year.py is never
    # touched by this block again.
    cur.execute("SELECT COUNT(*) FROM academic_years")
    if cur.fetchone()[0] == 0:
        # student_admission_extra is created lazily by student_admission.py
        # the first time the Admission window opens, so on a genuinely
        # fresh install it may not exist yet at this point -- guard for that.
        cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='student_admission_extra'")
        existing_labels = []
        if cur.fetchone():
            cur.execute(
                "SELECT DISTINCT academic_year FROM student_admission_extra "
                "WHERE academic_year IS NOT NULL AND TRIM(academic_year) <> ''"
            )
            existing_labels = sorted({r[0].strip() for r in cur.fetchall() if r[0] and r[0].strip()})
        default_label = existing_labels[-1] if existing_labels else _default_year_label()

        for label in (existing_labels or [default_label]):
            is_current = 1 if label == default_label else 0
            status = "Open" if is_current else "Closed"
            cur.execute(
                "INSERT OR IGNORE INTO academic_years (year_label, is_current, status) VALUES (?, ?, ?)",
                (label, is_current, status),
            )

        # Backfill every pre-existing student into the current year so
        # nobody silently disappears from a future "current year" filter
        # immediately after upgrading.
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        cur.execute("SELECT student_id, class_sec FROM students")
        for s_id, cls in cur.fetchall():
            cur.execute(
                """INSERT OR IGNORE INTO student_academic_year
                   (student_id, academic_year, class_sec, enrollment_status, created_at, updated_at)
                   VALUES (?, ?, ?, 'Active', ?, ?)""",
                (s_id, default_label, cls, now, now),
            )
        cur.execute(
            "UPDATE students SET current_academic_year=? WHERE current_academic_year IS NULL",
            (default_label,),
        )

    conn.commit()


def _add_column_if_missing(cur, table, column, coltype):
    cur.execute(f"PRAGMA table_info({table})")
    cols = [r[1] for r in cur.fetchall()]
    if column not in cols:
        cur.execute(f"ALTER TABLE {table} ADD COLUMN {column} {coltype}")


def _seed_user(cur, username, plain_password, role):
    from security import hash_password
    cur.execute("SELECT id FROM users WHERE username=?", (username,))
    if cur.fetchone() is None:
        pw_hash = hash_password(plain_password)
        cur.execute(
            "INSERT INTO users (username, password, role, is_hashed) VALUES (?, ?, ?, 1)",
            (username, pw_hash, role),
        )