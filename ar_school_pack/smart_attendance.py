"""
smart_attendance.py — Smart barcode/manual Attendance window with
duplicate-scan protection, live counters, and an automatic end-of-day
Absent process.

STANDALONE MODULE. The existing Attendance tab in app.py (and its
`attendance` table) is untouched — this window reads/writes the SAME
`attendance` table, so a record created here is identical to one created
by the original tab. The `attendance` table's existing
UNIQUE(student_id, date) constraint is what this module leans on for
duplicate-scan protection, rather than re-implementing that check by hand.

Two small, additive, self-contained tables are created here (never
touching db.py):
  - attendance_settings(id=1, start_time, closing_time, late_threshold_minutes)
    — configurable timing, per spec section 8, instead of hardcoding it.
  - attendance_auto_absent_log(date PK, run_at) — a one-row-per-day marker
    so the automatic end-of-day Absent sweep is provably safe to run more
    than once and will never double-insert.
"""

import tkinter as tk
from tkinter import ttk, messagebox
from datetime import datetime, time as dtime

import db
import rbac
import theme

STATUS_VALUES = ["Present", "Absent", "Leave", "Late"]


def _add_column_if_missing(table, column, coltype):
    cols = [r[1] for r in db.run(f"PRAGMA table_info({table})", fetchall=True)]
    if column not in cols:
        db.run(f"ALTER TABLE {table} ADD COLUMN {column} {coltype}", commit=True)


def _ensure_tables():
    # attendance.in_time doesn't exist on older/original schemas — added
    # here additively (never touches db.py) so a scan's clock time can be
    # recorded, matching the same non-destructive pattern db.py itself uses.
    _add_column_if_missing("attendance", "in_time", "TEXT")
    # Legacy table kept for backward compatibility; authoritative timings
    # now live in system_settings (db.get_setting / set_setting).
    db.run("""CREATE TABLE IF NOT EXISTS attendance_settings (
        id INTEGER PRIMARY KEY CHECK (id=1),
        start_time TEXT DEFAULT '08:00',
        closing_time TEXT DEFAULT '14:00',
        late_threshold_minutes INTEGER DEFAULT 15
    )""", commit=True)
    db.run("INSERT OR IGNORE INTO attendance_settings (id) VALUES (1)", commit=True)
    db.run("""CREATE TABLE IF NOT EXISTS attendance_auto_absent_log (
        date TEXT PRIMARY KEY,
        run_at TEXT
    )""", commit=True)


def get_settings():
    """Attendance timings from centralized system_settings.

    late_threshold_time is an absolute clock time (e.g. 08:15). For UI
    compatibility we also expose late_threshold_minutes derived from the
    gap between start and late cutoff when possible.
    """
    start = db.get_setting("school_start_time", "08:00") or "08:00"
    late = db.get_setting("late_threshold_time", "08:15") or "08:15"
    closing = db.get_setting("school_closing_time", "14:00") or "14:00"
    auto_enabled = db.get_setting("auto_absent_enabled", "1")
    minutes = 15
    try:
        s = datetime.strptime(start, "%H:%M")
        l = datetime.strptime(late, "%H:%M")
        minutes = max(0, int((l - s).total_seconds() // 60))
    except Exception:
        pass
    return {
        "start_time": start,
        "closing_time": closing,
        "late_threshold_time": late,
        "late_threshold_minutes": minutes,
        "auto_absent_enabled": str(auto_enabled) in ("1", "true", "True", "yes"),
    }


def set_settings(start_time, closing_time, late_threshold_minutes=None, late_threshold_time=None):
    """Persist attendance timings into system_settings (and mirror legacy table)."""
    from datetime import timedelta
    start_time = (start_time or "08:00").strip()
    closing_time = (closing_time or "14:00").strip()
    if late_threshold_time:
        late_time = str(late_threshold_time).strip()
    else:
        try:
            base = datetime.strptime(start_time, "%H:%M")
            mins = int(late_threshold_minutes if late_threshold_minutes is not None else 15)
            late_time = (base + timedelta(minutes=mins)).strftime("%H:%M")
        except Exception:
            late_time = "08:15"
    db.set_settings_bulk({
        "school_start_time": start_time,
        "school_closing_time": closing_time,
        "late_threshold_time": late_time,
    })
    try:
        mins = 15
        try:
            s = datetime.strptime(start_time, "%H:%M")
            l = datetime.strptime(late_time, "%H:%M")
            mins = max(0, int((l - s).total_seconds() // 60))
        except Exception:
            pass
        db.run(
            "UPDATE attendance_settings SET start_time=?, closing_time=?, late_threshold_minutes=? WHERE id=1",
            (start_time, closing_time, mins),
            commit=True,
        )
    except Exception:
        pass


def run_auto_absent(for_date=None, force=False):
    """Mark every Active student with NO attendance row for `for_date` as
    Absent (method='Auto System').

    Safe to call more than once for the same date — students who already have
    ANY attendance row (Present / Late / Leave / Absent) are left untouched.
    Returns the number of students newly marked.

    When force=False (background worker), respects auto_absent_enabled.
    When force=True (manual Admin button), runs regardless of the toggle.
    """
    _ensure_tables()
    d = for_date or datetime.now().strftime("%Y-%m-%d")
    if not force:
        if for_date is None or d == datetime.now().strftime("%Y-%m-%d"):
            if not get_settings().get("auto_absent_enabled", True):
                return 0
    active_ids = [r[0] for r in db.run(
        "SELECT student_id FROM students WHERE COALESCE(status,'Active')='Active'", fetchall=True)]
    already = {r[0] for r in db.run(
        "SELECT student_id FROM attendance WHERE date=?", (d,), fetchall=True)}
    to_mark = [sid for sid in active_ids if sid not in already]
    now_str = datetime.now().strftime("%H:%M")
    marked = 0
    for sid in to_mark:
        try:
            db.run(
                "INSERT INTO attendance (student_id, date, status, method) VALUES (?, ?, 'Absent', 'Auto System')",
                (sid, d), commit=True,
            )
            marked += 1
        except Exception:
            # UNIQUE(student_id, date) already satisfied by a concurrent write
            pass
    db.run(
        "INSERT INTO attendance_auto_absent_log (date, run_at) VALUES (?, ?) "
        "ON CONFLICT(date) DO UPDATE SET run_at=excluded.run_at",
        (d, now_str), commit=True,
    )
    return marked



def auto_absent_already_run_today(d=None):
    d = d or datetime.now().strftime("%Y-%m-%d")
    row = db.run("SELECT 1 FROM attendance_auto_absent_log WHERE date=?", (d,), fetchone=True)
    return row is not None


def is_past_school_closing():
    """True if the wall clock is at or after school_closing_time today."""
    closing_str = db.get_setting("school_closing_time", "14:00") or "14:00"
    try:
        closing = datetime.strptime(closing_str, "%H:%M").time()
    except ValueError:
        return False
    return datetime.now().time() >= closing


def try_auto_absent_now(force=False):
    """One-shot attempt: if enabled (or force) and past closing and not yet
    run today, mark missing students Absent. Returns (ran, count).
    Never raises."""
    try:
        if not force:
            if not get_settings().get("auto_absent_enabled", True):
                return False, 0
            if not is_past_school_closing():
                return False, 0
        if auto_absent_already_run_today() and not force:
            return False, 0
        n = run_auto_absent(force=force)
        return True, n
    except Exception as exc:
        print(f"[Auto-Absent] try_auto_absent_now error: {exc}")
        return False, 0


def start_auto_absent_worker(tk_root, poll_ms=60000, first_delay_ms=2000):
    """Background worker: while the main window is open, periodically check
    whether school closing time has passed and, if so, auto-mark Absents.

    - App opened AFTER closing time → runs within first_delay_ms
    - App opened BEFORE closing time → keeps polling; fires once closing arrives
    - Already ran today → idle checks, no duplicate rows
    - auto_absent_enabled=0 → does nothing

    Safe: every tick is wrapped; a failure never kills the UI loop.
    """
    state = {"done_date": None}

    def tick():
        try:
            today = datetime.now().strftime("%Y-%m-%d")
            # Reset daily so next calendar day can run again
            if state["done_date"] and state["done_date"] != today:
                state["done_date"] = None

            if state["done_date"] == today:
                pass  # already completed for today
            elif not get_settings().get("auto_absent_enabled", True):
                pass
            elif not is_past_school_closing():
                pass
            elif auto_absent_already_run_today():
                state["done_date"] = today
            else:
                ran, n = try_auto_absent_now(force=False)
                if ran:
                    state["done_date"] = today
                    print(
                        f"[Auto-Absent] Worker marked {n} student(s) Absent "
                        f"at {datetime.now().strftime('%H:%M')} "
                        f"(closing={db.get_setting('school_closing_time', '14:00')})."
                    )
        except Exception as exc:
            print(f"[Auto-Absent] Worker tick error (non-fatal): {exc}")
        finally:
            try:
                if tk_root.winfo_exists():
                    tk_root.after(poll_ms, tick)
            except Exception:
                pass

    try:
        tk_root.after(first_delay_ms, tick)
    except Exception as exc:
        print(f"[Auto-Absent] Could not start worker: {exc}")


class AttendanceWindow:
    def __init__(self, parent, user_role, current_user):
        self.parent = parent
        self.user_role = user_role
        self.current_user = current_user

        if not rbac.can(self.user_role, "attendance.mark") and not rbac.can(self.user_role, "attendance.view"):
            messagebox.showerror("Permission Denied",
                                  f"Role '{self.user_role}' cannot access attendance.", parent=parent)
            return

        _ensure_tables()
        self.can_mark = rbac.can(self.user_role, "attendance.mark")

        self.win = tk.Toplevel(parent)
        self.win.title("Smart Attendance")
        self.win.geometry("980x760")
        self.win.config(bg=theme.SILVER)
        self.win.transient(parent)

        self._build_ui()
        self._maybe_run_auto_absent_on_open()
        self.win.after(150, lambda: self.ent_scan.focus_set() if self.can_mark else None)

    # ------------------------------------------------------------------
    def _build_ui(self):
        header = tk.Frame(self.win, bg=theme.NAVY, padx=20, pady=12)
        header.pack(fill=tk.X)
        tk.Label(header, text="📇  SMART ATTENDANCE", font=theme.FONT_H1, bg=theme.NAVY, fg="white").pack(anchor="w")
        settings = get_settings()
        tk.Label(
            header,
            text=(
                f"School Time: {settings['start_time']} – {settings['closing_time']}  |  "
                f"Late after {settings.get('late_threshold_time', settings['start_time'])}"
            ),
            font=theme.FONT_SMALL, bg=theme.NAVY, fg="#94a3b8",
        ).pack(anchor="w")

        body = tk.Frame(self.win, bg=theme.SILVER, padx=16, pady=12)
        body.pack(fill=tk.BOTH, expand=True)

        # ---- Scanner ----
        if self.can_mark:
            scan_card, scan_body = theme.section_card(body, "Scanner — Ready")
            scan_card.pack(fill=tk.X, pady=(0, 10))
            row = tk.Frame(scan_body, bg=theme.WHITE)
            row.pack(fill=tk.X)
            tk.Label(row, text="Scan / Enter Student ID:", font=theme.FONT_BODY_BOLD, bg=theme.WHITE).pack(
                side=tk.LEFT)
            self.ent_scan = tk.Entry(row, font=("Segoe UI", 12, "bold"), width=22, bg="#fef08a")
            self.ent_scan.pack(side=tk.LEFT, padx=8, ipady=4)
            self.ent_scan.bind("<Return>", lambda e: self.process_scan())
            theme.primary_button(row, "Mark Present", self.process_scan).pack(side=tk.LEFT, padx=4)
            self.lbl_scan_result = tk.Label(scan_body, text="Ready for next scan...", font=theme.FONT_BODY_BOLD,
                                             bg=theme.WHITE, fg=theme.TEXT_MUTED)
            self.lbl_scan_result.pack(anchor="w", pady=(8, 0))

        # ---- Live summary ----
        self.summary_frame = tk.Frame(body, bg=theme.SILVER)
        self.summary_frame.pack(fill=tk.X, pady=(0, 10))
        self._render_summary()

        # ---- Manual attendance ----
        if self.can_mark:
            man_card, man_body = theme.section_card(body, "Manual Attendance")
            man_card.pack(fill=tk.X, pady=(0, 10))
            row = tk.Frame(man_body, bg=theme.WHITE)
            row.pack(fill=tk.X)
            tk.Label(row, text="Search (ID / Name / Class):", bg=theme.WHITE, font=theme.FONT_SMALL).pack(
                side=tk.LEFT)
            self.ent_manual_search = tk.Entry(row, font=theme.FONT_BODY, width=24)
            self.ent_manual_search.pack(side=tk.LEFT, padx=6)
            self.ent_manual_search.bind("<Return>", lambda e: self._manual_search())
            theme.primary_button(row, "Search", self._manual_search).pack(side=tk.LEFT)

            self.tree_manual = ttk.Treeview(man_body, columns=("id", "name", "class", "today_status"),
                                             show="headings", height=5)
            for c, h, w in [("id", "Student ID", 110), ("name", "Name", 160), ("class", "Class", 100),
                            ("today_status", "Today's Status", 120)]:
                self.tree_manual.heading(c, text=h)
                self.tree_manual.column(c, width=w, anchor="center")
            self.tree_manual.pack(fill=tk.X, pady=6)

            btnrow = tk.Frame(man_body, bg=theme.WHITE)
            btnrow.pack(fill=tk.X)
            for status in STATUS_VALUES:
                theme.primary_button(btnrow, status, lambda st=status: self._manual_mark(st),
                                      bg=self._status_color(status)).pack(side=tk.LEFT, padx=4)

        # ---- Recent scans ----
        recent_card, recent_body = theme.section_card(body, "Recent Attendance")
        recent_card.pack(fill=tk.BOTH, expand=True)
        self.tree_recent = ttk.Treeview(
            recent_body,
            columns=("time", "id", "name", "class", "method", "status"),
            show="headings",
            height=12,
        )
        col_cfg = [
            ("time", "Time", 70),
            ("id", "Student ID", 120),
            ("name", "Name", 160),
            ("class", "Class", 90),
            ("method", "Method", 100),
            ("status", "Status", 90),
        ]
        for c, h, w in col_cfg:
            self.tree_recent.heading(c, text=h)
            self.tree_recent.column(c, width=w, anchor="center")
        for st, color in [
            ("Present", theme.SUCCESS),
            ("Absent", theme.DANGER),
            ("Leave", theme.INFO),
            ("Late", theme.WARNING),
        ]:
            self.tree_recent.tag_configure(st, foreground=color)
        recent_scroll = ttk.Scrollbar(recent_body, orient=tk.VERTICAL, command=self.tree_recent.yview)
        self.tree_recent.configure(yscrollcommand=recent_scroll.set)
        recent_scroll.pack(side=tk.RIGHT, fill=tk.Y)
        self.tree_recent.pack(fill=tk.BOTH, expand=True)

        # ---- Reports / actions ----
        actions = tk.Frame(body, bg=theme.SILVER)
        actions.pack(fill=tk.X, pady=(10, 0))
        theme.primary_button(actions, "📅 Monthly Report", self._open_report_dialog).pack(side=tk.LEFT, padx=4)
        theme.primary_button(actions, "📆 Custom Range Report", self._open_range_dialog,
                              bg=theme.SLATE).pack(side=tk.LEFT, padx=4)
        if rbac.can(self.user_role, "settings.branding") or self.user_role == "Admin":
            theme.primary_button(actions, "⚙ Attendance Timing", self._open_settings_dialog,
                                  bg=theme.SLATE).pack(side=tk.LEFT, padx=4)
            theme.primary_button(actions, "🌙 Run End-of-Day Absent", self._manual_run_auto_absent,
                                  bg=theme.WARNING).pack(side=tk.LEFT, padx=4)

        self._refresh_recent()

    @staticmethod
    def _status_color(status):
        return {"Present": theme.SUCCESS, "Absent": theme.DANGER, "Leave": theme.INFO,
                "Late": theme.WARNING}.get(status, theme.SLATE)

    # ------------------------------------------------------------------
    def _render_summary(self):
        for w in self.summary_frame.winfo_children():
            w.destroy()
        today = datetime.now().strftime("%Y-%m-%d")
        counts = {}
        for st in STATUS_VALUES:
            counts[st] = db.run("SELECT COUNT(*) FROM attendance WHERE date=? AND status=?",
                                 (today, st), fetchone=True)[0]
        total = sum(counts.values())
        row = tk.Frame(self.summary_frame, bg=theme.SILVER)
        row.pack(fill=tk.X)
        for st in STATUS_VALUES + ["Total"]:
            val = counts.get(st, total) if st != "Total" else total
            card = theme.stat_card(row, st, val, accent=self._status_color(st) if st != "Total" else theme.NAVY)
            card.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=4)

    def _refresh_recent(self):
        """Reload today's attendance into the Recent Attendance table.

        Shows scan/mark time (in_time), student, class, method, and status.
        Newest records first (by id DESC). Safe if in_time column is missing
        on very old DBs — falls back to '—'.
        """
        self.tree_recent.delete(*self.tree_recent.get_children())
        today = datetime.now().strftime("%Y-%m-%d")
        try:
            rows = db.run(
                "SELECT a.id, a.student_id, s.name, s.class_sec, a.method, a.status, a.in_time "
                "FROM attendance a "
                "LEFT JOIN students s ON s.student_id = a.student_id "
                "WHERE a.date=? ORDER BY a.id DESC LIMIT 50",
                (today,), fetchall=True,
            ) or []
        except Exception:
            # Older schema without in_time
            rows = db.run(
                "SELECT a.id, a.student_id, s.name, s.class_sec, a.method, a.status "
                "FROM attendance a "
                "LEFT JOIN students s ON s.student_id = a.student_id "
                "WHERE a.date=? ORDER BY a.id DESC LIMIT 50",
                (today,), fetchall=True,
            ) or []
            rows = [tuple(list(r) + [None]) for r in rows]

        for row in rows:
            if len(row) >= 7:
                aid, sid, name, cls, method, status, in_time = row[:7]
            else:
                aid, sid, name, cls, method, status = row[:6]
                in_time = None
            time_str = (in_time or "").strip() or "—"
            tag = status if status in STATUS_VALUES else ""
            self.tree_recent.insert(
                "", tk.END,
                values=(time_str, sid, name or "?", cls or "-", method or "-", status or "-"),
                tags=(tag,) if tag else (),
            )

    # ------------------------------------------------------------------
    # Barcode / scan
    # ------------------------------------------------------------------
    def process_scan(self):
        sid = self.ent_scan.get().strip()
        self.ent_scan.delete(0, tk.END)
        if not sid:
            return
        if not self.can_mark:
            return
        self._mark(sid, "Present", method="Barcode", scan_ui=True)

    # ------------------------------------------------------------------
    # Manual
    # ------------------------------------------------------------------
    def _manual_search(self):
        q = self.ent_manual_search.get().strip()
        self.tree_manual.delete(*self.tree_manual.get_children())
        if not q:
            return
        rows = db.run(
            "SELECT student_id, name, class_sec FROM students WHERE "
            "(student_id LIKE ? OR name LIKE ? OR class_sec LIKE ?) AND COALESCE(status,'Active')='Active'",
            (f"%{q}%", f"%{q}%", f"%{q}%"), fetchall=True)
        today = datetime.now().strftime("%Y-%m-%d")
        for sid, name, cls in rows:
            existing = db.run("SELECT status FROM attendance WHERE student_id=? AND date=?",
                               (sid, today), fetchone=True)
            self.tree_manual.insert("", tk.END, values=(sid, name, cls or "-", existing[0] if existing else "—"))

    def _manual_mark(self, status):
        sel = self.tree_manual.focus()
        if not sel:
            messagebox.showinfo("No Selection", "Select a student in the search results first.", parent=self.win)
            return
        sid = self.tree_manual.item(sel, "values")[0]
        self._mark(sid, status, method="Manual", scan_ui=False)
        self._manual_search()

    # ------------------------------------------------------------------
    def _mark(self, sid, status, method, scan_ui):
        if not self.can_mark:
            messagebox.showerror("Permission Denied", "You are not allowed to mark attendance.", parent=self.win)
            return
        student = db.run("SELECT name, class_sec, photo_path, status FROM students WHERE student_id=?",
                          (sid,), fetchone=True)
        if not student:
            if scan_ui:
                self.lbl_scan_result.config(text=f"✖ Student Not Found — '{sid}' does not match any active record.",
                                             fg=theme.DANGER)
            else:
                messagebox.showerror("Student Not Found", f"'{sid}' does not match any student.", parent=self.win)
            return
        name, cls, photo_path, active_status = student
        if (active_status or "Active") != "Active":
            msg = f"'{name}' is Archived and cannot be marked."
            if scan_ui:
                self.lbl_scan_result.config(text=f"✖ {msg}", fg=theme.DANGER)
            else:
                messagebox.showerror("Student Archived", msg, parent=self.win)
            return

        today = datetime.now().strftime("%Y-%m-%d")
        now_time = datetime.now().strftime("%H:%M")
        existing = db.run("SELECT status, in_time FROM attendance WHERE student_id=? AND date=?",
                           (sid, today), fetchone=True)
        if existing:
            ex_status, ex_time = existing
            msg = f"Already Marked — {name}'s attendance is already recorded today.\nFirst: {ex_time or '-'}  Status: {ex_status}"
            if scan_ui:
                self.lbl_scan_result.config(text=f"⚠ {msg}", fg=theme.WARNING)
            else:
                messagebox.showinfo("Already Marked", msg, parent=self.win)
            return

        # Late detection — compare wall clock to late_threshold_time from system_settings
        if status == "Present" and method == "Barcode":
            settings = get_settings()
            try:
                late_cutoff = datetime.strptime(
                    settings.get("late_threshold_time") or "08:15", "%H:%M"
                ).time()
                if datetime.now().time() > late_cutoff:
                    status = "Late"
            except Exception:
                pass

        try:
            db.run("INSERT INTO attendance (student_id, date, status, method, in_time) VALUES (?, ?, ?, ?, ?)",
                   (sid, today, status, method, now_time), commit=True)
        except Exception as e:
            # UNIQUE(student_id, date) constraint hit — a duplicate slipped
            # in between our SELECT and INSERT (e.g. two scans milliseconds
            # apart). Treat exactly like "already marked", never a crash.
            existing2 = db.run("SELECT status, in_time FROM attendance WHERE student_id=? AND date=?",
                                (sid, today), fetchone=True)
            msg = f"Already Marked — {name}'s attendance was just recorded.\n{existing2}"
            if scan_ui:
                self.lbl_scan_result.config(text=f"⚠ {msg}", fg=theme.WARNING)
            else:
                messagebox.showinfo("Already Marked", msg, parent=self.win)
            return

        if scan_ui:
            self.lbl_scan_result.config(
                text=f"✓ ATTENDANCE MARKED — {name}  |  {sid}  |  {cls or '-'}  |  {now_time}  |  {status.upper()}",
                fg=theme.SUCCESS)

        self._render_summary()
        self._refresh_recent()

    # ------------------------------------------------------------------
    # Automatic end-of-day absent
    # ------------------------------------------------------------------
    def _maybe_run_auto_absent_on_open(self):
        """When Attendance window opens after closing time, apply auto-absent
        if the background worker has not already done so today."""
        settings = get_settings()
        if not settings.get("auto_absent_enabled", True):
            return
        if not is_past_school_closing():
            return
        if auto_absent_already_run_today():
            return
        ran, n = try_auto_absent_now(force=False)
        if ran and n:
            messagebox.showinfo(
                "End-of-Day Absent Applied",
                f"School closing time ({settings['closing_time']}) has passed.\n"
                f"{n} active student(s) with no Present/Late/Leave record today were "
                f"automatically marked ABSENT (Auto System).",
                parent=self.win,
            )
            self._render_summary()
            self._refresh_recent()

    def _manual_run_auto_absent(self):
        if auto_absent_already_run_today():
            if not messagebox.askyesno("Already Run Today",
                                        "The end-of-day Absent sweep already ran today. Run it again?\n"
                                        "(It only affects students still missing a record — no duplicates "
                                        "will be created.)", parent=self.win):
                return
        n = run_auto_absent(force=True)
        messagebox.showinfo("Done", f"{n} student(s) marked Absent.", parent=self.win)
        self._render_summary()
        self._refresh_recent()

    # ------------------------------------------------------------------
    # Reports
    # ------------------------------------------------------------------
    def _class_breakdown(self, start_date, end_date):
        rows = db.run(
            "SELECT s.class_sec, a.status, COUNT(*) FROM attendance a "
            "JOIN students s ON s.student_id=a.student_id WHERE a.date BETWEEN ? AND ? "
            "GROUP BY s.class_sec, a.status", (start_date, end_date), fetchall=True)
        return rows

    def _open_report_dialog(self):
        self._range_report_window("Monthly Attendance Report", monthly=True)

    def _open_range_dialog(self):
        self._range_report_window("Custom Date-Range Attendance Report", monthly=False)

    def _range_report_window(self, title, monthly):
        win = tk.Toplevel(self.win)
        win.title(title)
        win.geometry("480x420")
        win.config(bg=theme.WHITE)
        tk.Label(win, text=title, font=theme.FONT_H1, bg=theme.WHITE).pack(pady=10)

        form = tk.Frame(win, bg=theme.WHITE)
        form.pack(pady=4)
        if monthly:
            tk.Label(form, text="Month (YYYY-MM):", bg=theme.WHITE).grid(row=0, column=0, sticky="e", padx=6, pady=6)
            ent_range = tk.Entry(form, width=12)
            ent_range.insert(0, datetime.now().strftime("%Y-%m"))
            ent_range.grid(row=0, column=1, pady=6)
        else:
            tk.Label(form, text="From (YYYY-MM-DD):", bg=theme.WHITE).grid(row=0, column=0, sticky="e", padx=6, pady=6)
            ent_from = tk.Entry(form, width=12)
            ent_from.grid(row=0, column=1, pady=6)
            tk.Label(form, text="To (YYYY-MM-DD):", bg=theme.WHITE).grid(row=1, column=0, sticky="e", padx=6, pady=6)
            ent_to = tk.Entry(form, width=12)
            ent_to.grid(row=1, column=1, pady=6)

        tk.Label(form, text="Class/Section (blank = all):", bg=theme.WHITE).grid(
            row=2, column=0, sticky="e", padx=6, pady=6)
        ent_class = tk.Entry(form, width=14)
        ent_class.grid(row=2, column=1, pady=6)

        result_lbl = tk.Label(win, text="", font=theme.FONT_SMALL, bg=theme.WHITE, justify="left")
        result_lbl.pack(pady=10, padx=10)

        def compute():
            if monthly:
                m = ent_range.get().strip()
                start_date, end_date = f"{m}-01", f"{m}-31"
                like_clause = f"{m}%"
            else:
                start_date, end_date = ent_from.get().strip(), ent_to.get().strip()
                for d in (start_date, end_date):
                    try:
                        datetime.strptime(d, "%Y-%m-%d")
                    except ValueError:
                        messagebox.showerror("Invalid Date", "Use YYYY-MM-DD.", parent=win)
                        return
                like_clause = None

            cls = ent_class.get().strip()
            base_q = ("SELECT a.status, COUNT(*) FROM attendance a JOIN students s ON s.student_id=a.student_id "
                       "WHERE a.date BETWEEN ? AND ?")
            params = [start_date, end_date]
            if cls:
                base_q += " AND s.class_sec=?"
                params.append(cls)
            base_q += " GROUP BY a.status"
            rows = db.run(base_q, tuple(params), fetchall=True)
            counts = {st: 0 for st in STATUS_VALUES}
            for st, c in rows:
                if st in counts:
                    counts[st] = c
            total = sum(counts.values())
            pct = (counts["Present"] / total * 100) if total else 0.0
            result_lbl.config(text=(
                f"Period: {start_date} to {end_date}" + (f"  |  Class: {cls}" if cls else "") + "\n\n"
                f"Present: {counts['Present']}   Absent: {counts['Absent']}   "
                f"Leave: {counts['Leave']}   Late: {counts['Late']}   Total: {total}\n"
                f"Attendance Rate: {pct:.1f}%"))

        theme.primary_button(win, "Generate Report", compute).pack(pady=6)

    # ------------------------------------------------------------------
    def _open_settings_dialog(self):
        settings = get_settings()
        win = tk.Toplevel(self.win)
        win.title("Attendance Timing Settings")
        win.geometry("380x300")
        win.config(bg=theme.WHITE)
        tk.Label(win, text="Attendance Timing", font=theme.FONT_H1, bg=theme.WHITE).pack(pady=(12, 6))
        tk.Label(
            win, text="Values are stored in System Settings and apply app-wide.",
            font=theme.FONT_SMALL, bg=theme.WHITE, fg=theme.TEXT_MUTED,
        ).pack(pady=(0, 8))

        form = tk.Frame(win, bg=theme.WHITE)
        form.pack(pady=4, padx=16, fill=tk.X)
        tk.Label(form, text="School Start (HH:MM):", bg=theme.WHITE, font=theme.FONT_BODY).grid(
            row=0, column=0, sticky="e", padx=6, pady=8)
        ent_start = tk.Entry(form, width=12, font=theme.FONT_BODY)
        ent_start.insert(0, settings["start_time"])
        ent_start.grid(row=0, column=1, pady=8, sticky="w")

        tk.Label(form, text="Late Cutoff (HH:MM):", bg=theme.WHITE, font=theme.FONT_BODY).grid(
            row=1, column=0, sticky="e", padx=6, pady=8)
        ent_late = tk.Entry(form, width=12, font=theme.FONT_BODY)
        ent_late.insert(0, settings.get("late_threshold_time") or "08:15")
        ent_late.grid(row=1, column=1, pady=8, sticky="w")

        tk.Label(form, text="Closing Time (HH:MM):", bg=theme.WHITE, font=theme.FONT_BODY).grid(
            row=2, column=0, sticky="e", padx=6, pady=8)
        ent_close = tk.Entry(form, width=12, font=theme.FONT_BODY)
        ent_close.insert(0, settings["closing_time"])
        ent_close.grid(row=2, column=1, pady=8, sticky="w")

        def save():
            try:
                datetime.strptime(ent_start.get().strip(), "%H:%M")
                datetime.strptime(ent_close.get().strip(), "%H:%M")
                datetime.strptime(ent_late.get().strip(), "%H:%M")
            except ValueError:
                messagebox.showerror(
                    "Invalid Input", "All times must be HH:MM (e.g. 08:15).", parent=win,
                )
                return
            set_settings(
                ent_start.get().strip(),
                ent_close.get().strip(),
                late_threshold_time=ent_late.get().strip(),
            )
            messagebox.showinfo("Saved", "Attendance timing updated in System Settings.", parent=win)
            win.destroy()
            self.win.destroy()

        theme.primary_button(win, "💾 Save", save).pack(pady=14)


def launch_attendance_window(parent, user_role, current_user):
    return AttendanceWindow(parent, user_role, current_user)
