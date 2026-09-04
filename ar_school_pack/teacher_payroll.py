"""Teacher & Payroll — the single, authoritative Teacher & Payroll screen.

This module owns Teacher registration, the Teachers directory, daily
teacher attendance, and salary/payslip generation — the same
functionality that used to live inline as a tab in app.py.

Everything that touches the database, RBAC, accounting, or reports is
kept byte-for-byte identical in behaviour to the original app.py code
(same SQL, same tables/columns, same rbac.can(...) permission strings,
same accounting.record_salary_expense(...) / reports.generate_payslip(...)
calls, same audit log messages). Only the presentation layer is new —
this file follows the same "modern popup window" pattern already used by
student_admission.py, student_fee_collection.py, student_profile.py, and
smart_attendance.py, and is opened the same way, via a
launch_teacher_payroll_window(root, user_role, current_user) function.

UI redesign (Sep 2026): balanced dashboard layout, structured registration
form (First/Last Name + DatePicker-style), per-row action icons, full-staff
attendance log with status counts + export, searchable teacher dropdown,
live payslip preview card, bonus/deduction inputs, and summary metrics.
"""

import os
from datetime import datetime, date
import calendar

import tkinter as tk
from tkinter import ttk, messagebox, filedialog

import db
import rbac
import reports
import accounting

try:
    import matplotlib.pyplot as plt
    HAS_MATPLOTLIB = True
except ImportError:
    HAS_MATPLOTLIB = False


def log_activity(username, action):
    try:
        db.run(
            "INSERT INTO audit_logs (username, action, timestamp) VALUES (?, ?, ?)",
            (username, action, datetime.now().strftime("%Y-%m-%d %H:%M:%S")),
            commit=True,
        )
    except Exception as e:
        print(f"Audit Log Error: {e}")


def generate_next_teacher_id():
    row = db.run("SELECT teacher_id FROM teachers ORDER BY ROWID DESC LIMIT 1", fetchone=True)
    if row and row[0].startswith("TCH-"):
        try:
            new_num = int(row[0].split("-")[-1]) + 1
        except ValueError:
            new_num = 1
    else:
        new_num = 1
    return f"TCH-{new_num:03d}"


def safe_float(raw_text, field_label, default=None, parent=None):
    """Same friendly-error numeric parser used across the app."""
    text = (raw_text or "").strip()
    if not text:
        if default is not None:
            return default, True
        messagebox.showerror("Invalid Input", f"{field_label} is required.", parent=parent)
        return None, False
    try:
        return float(text), True
    except ValueError:
        messagebox.showerror(
            "Invalid Input",
            f"{field_label} must be a valid number (e.g. 1500 or 1500.50).",
            parent=parent,
        )
        return None, False


def split_name(full_name):
    """Split a stored full name into (first, last) for the redesign form."""
    parts = (full_name or "").strip().split(None, 1)
    if not parts:
        return "", ""
    if len(parts) == 1:
        return parts[0], ""
    return parts[0], parts[1]


def join_name(first, last):
    return f"{(first or '').strip()} {(last or '').strip()}".strip()


def ensure_teacher_status_column():
    """Ensure teachers.status exists (Active / Inactive). Safe to call every launch."""
    try:
        cols = db.run("PRAGMA table_info(teachers)", fetchall=True) or []
        names = {c[1] for c in cols}  # (cid, name, type, ...)
        if "status" not in names:
            db.run(
                "ALTER TABLE teachers ADD COLUMN status TEXT NOT NULL DEFAULT 'Active'",
                commit=True,
            )
            # Backfill any NULL/empty rows
            db.run(
                "UPDATE teachers SET status='Active' WHERE status IS NULL OR TRIM(status)=''",
                commit=True,
            )
    except Exception as e:
        print(f"ensure_teacher_status_column: {e}")


def launch_teacher_payroll_window(root, user_role, current_user):
    """Open the Teacher & Payroll window.

    Mirrors the permission gate that used to guard the sidebar item:
    visible if the user can view teachers OR mark teacher attendance.
    """
    if not (rbac.can(user_role, "teacher.view") or rbac.can(user_role, "teacher.attendance.mark")):
        messagebox.showerror("Permission Denied", "You are not allowed to access Teachers & Payroll.")
        return None
    return TeacherPayrollWindow(root, user_role, current_user)


class TeacherPayrollWindow:
    NAVY = "#0f172a"
    CARD = "#ffffff"
    BORDER = "#e2e8f0"
    BG = "#f1f5f9"
    MUTED = "#64748b"
    BLUE = "#2563eb"
    GREEN = "#16a34a"
    PURPLE = "#7c3aed"
    CYAN = "#0284c7"
    RED = "#dc2626"
    AMBER = "#d97706"
    LIGHT_BLUE = "#dbeafe"
    LIGHT_GREEN = "#dcfce7"
    LIGHT_AMBER = "#fef3c7"
    TABLE_HEADER = "#1e293b"

    def __init__(self, root, user_role, current_user):
        self.root = root
        self.user_role = user_role
        self.current_user = current_user

        self.can_add_teacher = rbac.can(user_role, "teacher.add")
        self.can_edit_teacher = (
            rbac.can(user_role, "teacher.edit") or rbac.can(user_role, "teacher.add")
        )
        # teacher.delete permission is reused for Activate/Deactivate (no hard delete).
        self.can_toggle_status = (
            rbac.can(user_role, "teacher.delete") or rbac.can(user_role, "teacher.add")
        )
        self.can_mark_attendance = rbac.can(user_role, "teacher.attendance.mark")
        self.can_view_salary = rbac.can(user_role, "teacher.salary.view")
        self.can_pay_salary = rbac.can(user_role, "teacher.salary.pay")

        ensure_teacher_status_column()

        self.win = tk.Toplevel(root)
        self.win.title("Teacher & Payroll — AR School Management System")
        self.win.geometry("1280x760")
        self.win.minsize(1100, 650)
        self.win.config(bg=self.BG)
        # Keep this window above the main app and restore focus after dialogs
        try:
            self.win.transient(root)
        except Exception:
            pass
        self.win.lift()
        self.win.focus_force()
        self.win.protocol("WM_DELETE_WINDOW", self._on_close)

        self._style_treeviews()

        self._build_header()
        self._build_nav()
        self._build_pages()

        self.show_page("directory")
        # Final raise so it stays on top after widgets are built
        self.win.after(50, self._bring_to_front)

    def _on_close(self):
        try:
            self.win.destroy()
        except Exception:
            pass

    def _bring_to_front(self):
        """Raise Teacher & Payroll window and restore keyboard focus.

        After messagebox / child dialogs close, window managers often push
        Toplevels behind the main root. Call this after every dialog so the
        screen does not sink or appear to 'turn off'.
        """
        try:
            if not self.win.winfo_exists():
                return
            self.win.deiconify()
            self.win.lift()
            # Brief topmost pulse forces Z-order on stubborn WMs
            self.win.attributes("-topmost", True)
            self.win.after(80, self._clear_topmost)
            self.win.focus_force()
        except Exception:
            pass

    def _clear_topmost(self):
        try:
            if self.win.winfo_exists():
                self.win.attributes("-topmost", False)
        except Exception:
            pass

    def _style_treeviews(self):
        style = ttk.Style()
        try:
            style.theme_use("clam")
        except Exception:
            pass
        style.configure(
            "TP.Treeview",
            background=self.CARD,
            foreground=self.NAVY,
            rowheight=42,
            fieldbackground=self.CARD,
            borderwidth=0,
            font=("Segoe UI", 9),
        )
        style.configure(
            "TP.Treeview.Heading",
            background=self.TABLE_HEADER,
            foreground="white",
            font=("Segoe UI", 9, "bold"),
            relief="flat",
            borderwidth=0,
        )
        style.map("TP.Treeview", background=[("selected", "#bfdbfe")])
        style.map("TP.Treeview.Heading", background=[("active", "#334155")])

    # ------------------------------------------------------------
    # Chrome: header + section nav
    # ------------------------------------------------------------
    def _build_header(self):
        header = tk.Frame(self.win, bg=self.NAVY, padx=22, pady=14)
        header.pack(fill=tk.X)
        title_row = tk.Frame(header, bg=self.NAVY)
        title_row.pack(anchor="w")
        tk.Label(title_row, text="🏫", font=("Segoe UI", 18), bg=self.NAVY, fg="white").pack(side=tk.LEFT, padx=(0, 8))
        tk.Label(title_row, text="TEACHER & PAYROLL", font=("Segoe UI", 18, "bold"),
                 bg=self.NAVY, fg="white").pack(side=tk.LEFT)
        tk.Label(header, text="Register teachers, mark daily attendance, and process salary payslips",
                 font=("Segoe UI", 9), bg=self.NAVY, fg="#94a3b8").pack(anchor="w", pady=(2, 0))

    def _build_nav(self):
        nav = tk.Frame(self.win, bg=self.CARD, highlightbackground=self.BORDER, highlightthickness=1)
        nav.pack(fill=tk.X)
        self.nav_buttons = {}
        items = [
            ("directory", "👤  DIRECTORY & REGISTRATION"),
            ("attendance", "📅  DAILY ATTENDANCE"),
            ("payroll", "💵  PAYROLL & PAYSLIP"),
        ]
        for key, label in items:
            btn = tk.Button(
                nav, text=label, command=lambda k=key: self.show_page(k),
                bg=self.CARD, fg=self.NAVY, activebackground=self.LIGHT_BLUE,
                relief="flat", bd=0, padx=18, pady=12, font=("Segoe UI", 9, "bold"),
                cursor="hand2",
            )
            btn.pack(side=tk.LEFT)
            self.nav_buttons[key] = btn

    def _set_nav_active(self, key):
        for k, btn in self.nav_buttons.items():
            if k == key:
                btn.config(bg=self.LIGHT_BLUE, fg=self.BLUE)
            else:
                btn.config(bg=self.CARD, fg=self.NAVY)

    def _build_pages(self):
        self.pages_container = tk.Frame(self.win, bg=self.BG)
        self.pages_container.pack(fill=tk.BOTH, expand=True)
        self.pages = {}

        self.page_directory = tk.Frame(self.pages_container, bg=self.BG)
        self.page_attendance = tk.Frame(self.pages_container, bg=self.BG)
        self.page_payroll = tk.Frame(self.pages_container, bg=self.BG)
        for key, frame in [
            ("directory", self.page_directory),
            ("attendance", self.page_attendance),
            ("payroll", self.page_payroll),
        ]:
            frame.place(x=0, y=0, relwidth=1, relheight=1)
            self.pages[key] = frame

        self._build_directory_page()
        self._build_attendance_page()
        self._build_payroll_page()

    def show_page(self, key):
        self.pages[key].tkraise()
        self._set_nav_active(key)
        if key == "directory":
            self.load_teacher_table()
        elif key == "attendance":
            self.load_attendance_log()
        elif key == "payroll":
            self._refresh_payroll_teacher_list()
            self._update_payroll_metrics()

    # ============================================================
    # PAGE 1: Directory & Registration  (redesigned)
    # ============================================================
    def _build_directory_page(self):
        outer = self.page_directory

        # ---- Left: Registration form ----
        form_card = tk.Frame(outer, bg=self.CARD, highlightbackground=self.BORDER, highlightthickness=1)
        form_card.place(x=14, y=14, width=340, relheight=1.0, height=-28)

        tk.Label(form_card, text="TEACHER REGISTRATION", font=("Segoe UI", 11, "bold"),
                 bg=self.CARD, fg=self.NAVY).pack(anchor="w", padx=16, pady=(14, 2))
        tk.Label(form_card, text="Add a new teacher profile.", font=("Segoe UI", 8),
                 bg=self.CARD, fg=self.MUTED).pack(anchor="w", padx=16, pady=(0, 10))

        id_row = tk.Frame(form_card, bg=self.CARD)
        id_row.pack(fill=tk.X, padx=16, pady=(0, 10))
        tk.Label(id_row, text="Teacher ID:", font=("Segoe UI", 9, "bold"), bg=self.CARD, fg=self.MUTED).pack(side=tk.LEFT)
        self.lbl_tch_id = tk.Label(id_row, text=generate_next_teacher_id(), font=("Segoe UI", 10, "bold"),
                                   bg=self.CARD, fg=self.CYAN)
        self.lbl_tch_id.pack(side=tk.LEFT, padx=(8, 0))

        # First Name + Last Name side-by-side
        name_lbl = tk.Frame(form_card, bg=self.CARD)
        name_lbl.pack(fill=tk.X, padx=16, pady=(4, 2))
        tk.Label(name_lbl, text="First Name *", font=("Segoe UI", 8, "bold"), bg=self.CARD, fg=self.MUTED).pack(
            side=tk.LEFT, expand=True, fill=tk.X)
        tk.Label(name_lbl, text="Last Name", font=("Segoe UI", 8, "bold"), bg=self.CARD, fg=self.MUTED).pack(
            side=tk.LEFT, expand=True, fill=tk.X, padx=(8, 0))

        name_row = tk.Frame(form_card, bg=self.CARD)
        name_row.pack(fill=tk.X, padx=16, pady=(0, 6))
        self.ent_tch_first = tk.Entry(name_row, font=("Segoe UI", 10), relief="solid", bd=1)
        self.ent_tch_first.pack(side=tk.LEFT, fill=tk.X, expand=True, ipady=4)
        self.ent_tch_last = tk.Entry(name_row, font=("Segoe UI", 10), relief="solid", bd=1)
        self.ent_tch_last.pack(side=tk.LEFT, fill=tk.X, expand=True, ipady=4, padx=(8, 0))

        # Designation
        tk.Label(form_card, text="Designation", font=("Segoe UI", 8, "bold"), bg=self.CARD, fg=self.MUTED).pack(
            anchor="w", padx=16, pady=(4, 2))
        self.ent_tch_desig = tk.Entry(form_card, font=("Segoe UI", 10), relief="solid", bd=1)
        self.ent_tch_desig.pack(fill=tk.X, padx=16, ipady=4)

        # Phone
        tk.Label(form_card, text="Phone", font=("Segoe UI", 8, "bold"), bg=self.CARD, fg=self.MUTED).pack(
            anchor="w", padx=16, pady=(6, 2))
        self.ent_tch_phone = tk.Entry(form_card, font=("Segoe UI", 10), relief="solid", bd=1)
        self.ent_tch_phone.pack(fill=tk.X, padx=16, ipady=4)

        # Salary
        tk.Label(form_card, text="Salary (Rs)", font=("Segoe UI", 8, "bold"), bg=self.CARD, fg=self.MUTED).pack(
            anchor="w", padx=16, pady=(6, 2))
        self.ent_tch_sal = tk.Entry(form_card, font=("Segoe UI", 10), relief="solid", bd=1)
        self.ent_tch_sal.pack(fill=tk.X, padx=16, ipady=4)
        if not self.can_view_salary:
            self.ent_tch_sal.config(show="•")

        # Joining Date (YYYY-MM-DD) with simple picker helpers
        tk.Label(form_card, text="Joining Date (YYYY-MM-DD)", font=("Segoe UI", 8, "bold"),
                 bg=self.CARD, fg=self.MUTED).pack(anchor="w", padx=16, pady=(6, 2))
        join_row = tk.Frame(form_card, bg=self.CARD)
        join_row.pack(fill=tk.X, padx=16)
        self.ent_tch_join = tk.Entry(join_row, font=("Segoe UI", 10), relief="solid", bd=1)
        self.ent_tch_join.pack(side=tk.LEFT, fill=tk.X, expand=True, ipady=4)
        self.ent_tch_join.insert(0, date.today().isoformat())
        tk.Button(
            join_row, text="📅", command=self._pick_joining_date,
            bg="#f1f5f9", fg=self.NAVY, relief="flat", padx=8, pady=2,
            font=("Segoe UI", 10), cursor="hand2",
        ).pack(side=tk.LEFT, padx=(6, 0))

        # Buttons
        btn_row = tk.Frame(form_card, bg=self.CARD)
        btn_row.pack(fill=tk.X, padx=16, pady=16)
        add_btn = tk.Button(
            btn_row, text="＋ Add New Teacher", command=self.save_teacher,
            bg=self.GREEN, fg="white", relief="flat", padx=10, pady=9,
            font=("Segoe UI", 9, "bold"), cursor="hand2",
        )
        add_btn.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 6))
        add_btn.config(state="normal" if self.can_add_teacher else "disabled")
        tk.Button(
            btn_row, text="Clear", command=self.clear_teacher_form,
            bg="#f1f5f9", fg=self.NAVY, relief="flat", padx=10, pady=9,
            font=("Segoe UI", 9, "bold"), cursor="hand2",
        ).pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(6, 0))

        if not self.can_add_teacher:
            tk.Label(
                form_card, text="You don't have permission to add teachers.",
                font=("Segoe UI", 8), bg=self.CARD, fg=self.RED, wraplength=300, justify="left",
            ).pack(anchor="w", padx=16, pady=(0, 10))

        # ---- Right: Directory table ----
        right = tk.Frame(outer, bg=self.CARD, highlightbackground=self.BORDER, highlightthickness=1)
        right.place(x=368, y=14, relwidth=1.0, width=-382, relheight=1.0, height=-28)

        toolbar = tk.Frame(right, bg=self.CARD)
        toolbar.pack(fill=tk.X, padx=14, pady=(12, 6))
        tk.Label(toolbar, text="TEACHERS DIRECTORY", font=("Segoe UI", 11, "bold"),
                 bg=self.CARD, fg=self.NAVY).pack(side=tk.LEFT)
        self.lbl_salary_month_status = tk.Label(
            toolbar, text="", font=("Segoe UI", 8, "bold"), bg=self.CARD, fg=self.MUTED,
        )
        self.lbl_salary_month_status.pack(side=tk.LEFT, padx=(14, 0))
        tk.Button(
            toolbar, text="↻ Refresh", command=self.load_teacher_table,
            bg="#f1f5f9", fg=self.NAVY, relief="flat", padx=10, pady=4,
            font=("Segoe UI", 8, "bold"), cursor="hand2",
        ).pack(side=tk.RIGHT)

        search_bar = tk.Frame(right, bg=self.CARD)
        search_bar.pack(fill=tk.X, padx=14, pady=(0, 6))
        tk.Label(search_bar, text="🔍", font=("Segoe UI", 10), bg=self.CARD, fg=self.MUTED).pack(side=tk.LEFT)
        self.ent_tch_search = tk.Entry(search_bar, font=("Segoe UI", 9), relief="solid", bd=1)
        self.ent_tch_search.pack(side=tk.LEFT, padx=(4, 6), ipady=4, fill=tk.X, expand=True)
        self.ent_tch_search.bind("<Return>", lambda e: self.load_teacher_table())
        self.ent_tch_search.bind("<KeyRelease>", lambda e: self.load_teacher_table())
        tk.Button(
            search_bar, text="Search", command=self.load_teacher_table,
            bg="#64748b", fg="white", relief="flat", padx=12, pady=4,
            font=("Segoe UI", 8, "bold"), cursor="hand2",
        ).pack(side=tk.LEFT)

        # Large action buttons for selected row (easy to click — not tiny cell icons)
        action_bar = tk.Frame(right, bg=self.CARD)
        action_bar.pack(fill=tk.X, padx=14, pady=(0, 8))
        tk.Label(
            action_bar, text="Selected:", font=("Segoe UI", 8, "bold"),
            bg=self.CARD, fg=self.MUTED,
        ).pack(side=tk.LEFT, padx=(0, 8))
        self.btn_dir_view = tk.Button(
            action_bar, text="👤  View Profile", command=self.open_teacher_profile,
            bg=self.BLUE, fg="white", relief="flat", padx=14, pady=7,
            font=("Segoe UI", 9, "bold"), cursor="hand2",
        )
        self.btn_dir_view.pack(side=tk.LEFT, padx=(0, 6))
        if not rbac.can(self.user_role, "teacher.view"):
            self.btn_dir_view.config(state="disabled")

        self.btn_dir_edit = tk.Button(
            action_bar, text="✏️  Edit", command=self.edit_selected_teacher,
            bg="#475569", fg="white", relief="flat", padx=14, pady=7,
            font=("Segoe UI", 9, "bold"), cursor="hand2",
        )
        self.btn_dir_edit.pack(side=tk.LEFT, padx=(0, 6))
        self.btn_dir_edit.config(state="normal" if self.can_edit_teacher else "disabled")

        self.btn_dir_toggle = tk.Button(
            action_bar, text="⏸  Deactivate", command=self.toggle_teacher_status,
            bg=self.AMBER, fg="white", relief="flat", padx=14, pady=7,
            font=("Segoe UI", 9, "bold"), cursor="hand2",
        )
        self.btn_dir_toggle.pack(side=tk.LEFT, padx=(0, 6))
        self.btn_dir_toggle.config(state="normal" if self.can_toggle_status else "disabled")

        tk.Label(
            action_bar, text="← select a row first, then click",
            font=("Segoe UI", 8), bg=self.CARD, fg=self.MUTED,
        ).pack(side=tk.LEFT, padx=(8, 0))

        # Table
        table_frame = tk.Frame(right, bg=self.CARD)
        table_frame.pack(fill=tk.BOTH, expand=True, padx=14, pady=(0, 8))

        if self.can_view_salary:
            cols = ("id", "name", "desig", "phone", "salary", "joining", "emp_status", "salary_status", "actions")
        else:
            cols = ("id", "name", "desig", "phone", "joining", "emp_status", "salary_status", "actions")
        headers = {
            "id": "Teacher ID", "name": "Name", "desig": "Designation", "phone": "Phone",
            "salary": "Basic Salary", "joining": "Joining Date",
            "emp_status": "Status", "salary_status": "Salary Status", "actions": "Actions",
        }
        widths = {
            "id": 85, "name": 105, "desig": 95, "phone": 95,
            "salary": 90, "joining": 90, "emp_status": 85, "salary_status": 95, "actions": 160,
        }
        self.tree_teacher = ttk.Treeview(table_frame, columns=cols, show="headings", style="TP.Treeview")
        for col in cols:
            self.tree_teacher.heading(col, text=headers[col])
            self.tree_teacher.column(col, anchor="center", width=widths.get(col, 90), minwidth=60)
        self.tree_teacher.tag_configure("paid", foreground="#166534")
        self.tree_teacher.tag_configure("pending", foreground="#b45309")
        self.tree_teacher.tag_configure("inactive", foreground="#94a3b8")
        self.tree_teacher.tag_configure("odd", background="#f8fafc")
        self.tree_teacher.tag_configure("even", background="#ffffff")

        scroll = ttk.Scrollbar(table_frame, orient=tk.VERTICAL, command=self.tree_teacher.yview)
        self.tree_teacher.configure(yscroll=scroll.set)
        scroll.pack(side=tk.RIGHT, fill=tk.Y)
        self.tree_teacher.pack(fill=tk.BOTH, expand=True)

        self.tree_teacher.bind("<ButtonRelease-1>", self._on_directory_click)
        self.tree_teacher.bind("<Double-1>", lambda e: self.open_teacher_profile())

        # Footer count
        self.lbl_dir_count = tk.Label(right, text="", font=("Segoe UI", 8), bg=self.CARD, fg=self.MUTED)
        self.lbl_dir_count.pack(anchor="e", padx=14, pady=(0, 10))

        self.load_teacher_table()

    def _pick_joining_date(self):
        """Simple today/clear helper (Tk has no native DatePicker)."""
        self.ent_tch_join.delete(0, tk.END)
        self.ent_tch_join.insert(0, date.today().isoformat())

    def save_teacher(self):
        if not rbac.can(self.user_role, "teacher.add"):
            messagebox.showerror("Permission Denied", "Not allowed to add teachers.", parent=self.win)
            self._bring_to_front()
            return
        t_id = self.lbl_tch_id.cget("text")
        first = self.ent_tch_first.get().strip()
        last = self.ent_tch_last.get().strip()
        name = join_name(first, last)
        if not name:
            messagebox.showerror("Error", "Enter Teacher Name!", parent=self.win)
            self._bring_to_front()
            return
        sal, ok = safe_float(self.ent_tch_sal.get(), "Basic Salary", default=0.0, parent=self.win)
        if not ok:
            self._bring_to_front()
            return
        db.run(
            "INSERT INTO teachers (teacher_id, name, designation, phone, basic_salary, joining_date, status) "
            "VALUES (?, ?, ?, ?, ?, ?, 'Active')",
            (t_id, name, self.ent_tch_desig.get().strip(), self.ent_tch_phone.get().strip(),
             sal, self.ent_tch_join.get().strip()),
            commit=True,
        )
        log_activity(self.current_user, f"Added teacher profile {name} ({t_id})")
        messagebox.showinfo("Success", f"Teacher Profile Registered: {t_id}", parent=self.win)
        self.clear_teacher_form()
        self.load_teacher_table()
        self._bring_to_front()

    def load_teacher_table(self):
        self.tree_teacher.delete(*self.tree_teacher.get_children())
        search = self.ent_tch_search.get().strip() if hasattr(self, "ent_tch_search") else ""
        # Always include status column (ensured at launch)
        if self.can_view_salary:
            cols_sql = "teacher_id, name, designation, phone, basic_salary, joining_date, COALESCE(status,'Active')"
        else:
            cols_sql = "teacher_id, name, designation, phone, joining_date, COALESCE(status,'Active')"
        if search:
            rows = db.run(
                f"SELECT {cols_sql} FROM teachers WHERE teacher_id LIKE ? OR name LIKE ?",
                (f"%{search}%", f"%{search}%"),
                fetchall=True,
            ) or []
        else:
            rows = db.run(f"SELECT {cols_sql} FROM teachers", fetchall=True) or []

        ym = datetime.now().strftime("%Y-%m")
        month_label = datetime.now().strftime("%b %Y")
        try:
            paid_map = accounting.teacher_salary_status_map(ym)
        except Exception:
            paid_map = {}

        paid_count = pending_count = active_count = inactive_count = 0
        for idx, r in enumerate(rows):
            t_id = r[0]
            if self.can_view_salary:
                # id, name, desig, phone, salary, joining, emp_status
                emp_status = (r[6] or "Active").strip()
                joining = r[5] or ""
                sal_display = f"Rs. {float(r[4] or 0):,.2f}"
            else:
                # id, name, desig, phone, joining, emp_status
                emp_status = (r[5] or "Active").strip()
                joining = r[4] or ""
                sal_display = None

            is_active = emp_status.lower() != "inactive"
            if is_active:
                emp_badge = "✅ Active"
                active_count += 1
            else:
                emp_badge = "⏸ Inactive"
                inactive_count += 1

            info = paid_map.get(t_id)
            if info and info.get("paid"):
                sal_status = "✅ Paid"
                sal_tag = "paid"
                paid_count += 1
            else:
                sal_status = "⏳ Pending"
                sal_tag = "pending"
                pending_count += 1

            # Actions hint in row (real clicks use the large buttons above the table)
            if is_active:
                action_icons = "View | Edit | Off"
            else:
                action_icons = "View | Edit | On"

            if self.can_view_salary:
                values = (r[0], r[1], r[2] or "", r[3] or "", sal_display, joining, emp_badge, sal_status, action_icons)
            else:
                values = (r[0], r[1], r[2] or "", r[3] or "", joining, emp_badge, sal_status, action_icons)

            tags = [sal_tag, "even" if idx % 2 == 0 else "odd"]
            if not is_active:
                tags.append("inactive")
            self.tree_teacher.insert("", tk.END, values=values, tags=tuple(tags))

        if hasattr(self, "lbl_salary_month_status"):
            color = "#166534" if pending_count == 0 and paid_count > 0 else self.MUTED
            self.lbl_salary_month_status.config(
                text=f"  {month_label}:  {paid_count} Paid  ·  {pending_count} Pending  ·  {active_count} Active  ·  {inactive_count} Inactive",
                fg=color,
            )
        if hasattr(self, "lbl_dir_count"):
            self.lbl_dir_count.config(text=f"{len(rows)} teacher(s)  ({active_count} active)")

    def _on_directory_click(self, event):
        """Handle click: fill form on row select; detect action column clicks."""
        region = self.tree_teacher.identify("region", event.x, event.y)
        if region != "cell":
            return
        col = self.tree_teacher.identify_column(event.x)
        item = self.tree_teacher.identify_row(event.y)
        if not item:
            return
        self.tree_teacher.focus(item)
        self.tree_teacher.selection_set(item)
        vals = self.tree_teacher.item(item, "values")
        if not vals:
            return

        # Update large action-bar toggle label from this row's status
        self._sync_dir_action_bar(vals)

        # Actions column is last — still support cell clicks, with wider zones
        cols = self.tree_teacher["columns"]
        if col == f"#{len(cols)}":  # actions
            bbox = self.tree_teacher.bbox(item, col)
            if bbox:
                rel_x = event.x - bbox[0]
                zone = rel_x // max(bbox[2] // 3, 1)
                if zone <= 0:
                    self.open_teacher_profile()
                elif zone == 1:
                    self.edit_selected_teacher()
                else:
                    self.toggle_teacher_status()
            return

        # Normal row click → fill form + sync other tabs
        self.fill_teacher_form_from_vals(vals)

    def _sync_dir_action_bar(self, vals):
        """Set Deactivate/Activate button text from selected row status."""
        if not hasattr(self, "btn_dir_toggle"):
            return
        # emp_status is near the end: ... emp_status, salary_status, actions
        emp = ""
        for v in vals:
            s = str(v)
            if "Active" in s or "Inactive" in s:
                emp = s
                break
        if "Inactive" in emp:
            self.btn_dir_toggle.config(text="▶  Activate", bg=self.GREEN)
        else:
            self.btn_dir_toggle.config(text="⏸  Deactivate", bg=self.AMBER)

    def fill_teacher_form_from_vals(self, vals):
        self.lbl_tch_id.config(text=vals[0])
        first, last = split_name(vals[1])
        self.ent_tch_first.delete(0, tk.END)
        self.ent_tch_first.insert(0, first)
        self.ent_tch_last.delete(0, tk.END)
        self.ent_tch_last.insert(0, last)
        self.ent_tch_desig.delete(0, tk.END)
        self.ent_tch_desig.insert(0, vals[2] or "")
        self.ent_tch_phone.delete(0, tk.END)
        self.ent_tch_phone.insert(0, vals[3] or "")
        if self.can_view_salary and len(vals) > 5:
            # salary is display string "Rs. x,xxx.xx" — strip for entry
            sal_raw = str(vals[4]).replace("Rs.", "").replace(",", "").strip()
            self.ent_tch_sal.delete(0, tk.END)
            self.ent_tch_sal.insert(0, sal_raw)
            self.ent_tch_join.delete(0, tk.END)
            self.ent_tch_join.insert(0, vals[5] or "")
        else:
            self.ent_tch_join.delete(0, tk.END)
            self.ent_tch_join.insert(0, vals[4] if len(vals) > 4 else "")

        # Keep Attendance / Payroll tabs in sync
        if hasattr(self, "ent_att_tch_id"):
            self._load_teacher_into_attendance_page()
        if hasattr(self, "combo_pay_teacher"):
            self._select_payroll_teacher(vals[0])

    def fill_teacher_form(self, ev):
        """Compatibility shim for older call sites."""
        selected = self.tree_teacher.focus()
        vals = self.tree_teacher.item(selected, "values") if selected else ()
        if vals:
            self.fill_teacher_form_from_vals(vals)

    def clear_teacher_form(self):
        self.lbl_tch_id.config(text=generate_next_teacher_id())
        for ent in [
            self.ent_tch_first, self.ent_tch_last, self.ent_tch_desig,
            self.ent_tch_phone, self.ent_tch_sal, self.ent_tch_join,
        ]:
            ent.delete(0, tk.END)
        self.ent_tch_join.insert(0, date.today().isoformat())

    def _get_selected_teacher_id(self):
        selected = self.tree_teacher.focus()
        vals = self.tree_teacher.item(selected, "values") if selected else ()
        if not vals:
            return None, None
        return vals[0], vals[1] if len(vals) > 1 else ""

    def edit_selected_teacher(self):
        if not (rbac.can(self.user_role, "teacher.edit") or rbac.can(self.user_role, "teacher.add")):
            messagebox.showerror("Permission Denied", "You are not allowed to edit teachers.", parent=self.win)
            return

        t_id, _ = self._get_selected_teacher_id()
        if not t_id:
            messagebox.showinfo("Select Teacher", "Please select a teacher from the directory first.", parent=self.win)
            return

        row = db.run(
            "SELECT teacher_id, name, designation, phone, basic_salary, joining_date FROM teachers WHERE teacher_id=?",
            (t_id,), fetchone=True,
        )
        if not row:
            messagebox.showerror("Error", "Teacher not found.", parent=self.win)
            return

        t_id, name, desig, phone, basic_sal, joining = row
        first, last = split_name(name)

        edit_win = tk.Toplevel(self.win)
        edit_win.title(f"Edit Teacher — {t_id}")
        edit_win.geometry("520x580")
        edit_win.minsize(460, 520)
        edit_win.config(bg=self.BG)
        edit_win.transient(self.win)
        edit_win.grab_set()
        edit_win.resizable(True, True)

        header = tk.Frame(edit_win, bg=self.NAVY, padx=18, pady=12)
        header.pack(side=tk.TOP, fill=tk.X)
        tk.Label(header, text=f"✏️ EDIT TEACHER — {t_id}", font=("Segoe UI", 14, "bold"),
                 bg=self.NAVY, fg="white").pack(anchor="w")
        tk.Label(header, text="Update teacher profile details, then click Save Changes",
                 font=("Segoe UI", 9), bg=self.NAVY, fg="#94a3b8").pack(anchor="w", pady=(2, 0))

        btn_row = tk.Frame(edit_win, bg=self.BG, padx=16, pady=14)
        btn_row.pack(side=tk.BOTTOM, fill=tk.X)

        form = tk.Frame(edit_win, bg=self.CARD, padx=20, pady=16)
        form.pack(side=tk.TOP, fill=tk.BOTH, expand=True, padx=12, pady=12)

        fields = {}

        def add_field(key, label, value="", state="normal"):
            tk.Label(form, text=label, font=("Segoe UI", 8, "bold"), bg=self.CARD, fg=self.MUTED).pack(
                anchor="w", pady=(6, 2))
            ent = tk.Entry(form, font=("Segoe UI", 10), relief="solid", bd=1)
            ent.pack(fill=tk.X, ipady=5)
            ent.insert(0, "" if value is None else str(value))
            if state != "normal":
                ent.config(state=state)
            fields[key] = ent
            return ent

        add_field("teacher_id", "Teacher ID", t_id, "disabled")
        add_field("first_name", "First Name *", first)
        add_field("last_name", "Last Name", last)
        add_field("designation", "Designation", desig or "")
        add_field("phone", "Phone Number", phone or "")
        sal_state = "normal" if self.can_view_salary else "disabled"
        add_field("basic_salary", "Basic Salary (Rs)", basic_sal if basic_sal is not None else "0", sal_state)
        add_field("joining_date", "Joining Date (YYYY-MM-DD)", joining or "")

        def do_save():
            new_name = join_name(fields["first_name"].get(), fields["last_name"].get())
            if not new_name:
                messagebox.showerror("Required Fields", "Teacher Name is required.", parent=edit_win)
                return

            if self.can_view_salary:
                sal, ok = safe_float(fields["basic_salary"].get(), "Basic Salary", default=0.0, parent=edit_win)
                if not ok:
                    return
            else:
                sal = float(basic_sal or 0)

            try:
                db.run(
                    """UPDATE teachers SET
                       name=?, designation=?, phone=?, basic_salary=?, joining_date=?
                       WHERE teacher_id=?""",
                    (
                        new_name,
                        fields["designation"].get().strip(),
                        fields["phone"].get().strip(),
                        sal,
                        fields["joining_date"].get().strip(),
                        t_id,
                    ),
                    commit=True,
                )
            except Exception as exc:
                messagebox.showerror("Save Failed", f"Could not update teacher.\n\n{exc}", parent=edit_win)
                return

            log_activity(self.current_user, f"Updated teacher profile {new_name} ({t_id})")
            self.load_teacher_table()
            if self.lbl_tch_id.cget("text") == t_id:
                first2, last2 = split_name(new_name)
                self.ent_tch_first.delete(0, tk.END)
                self.ent_tch_first.insert(0, first2)
                self.ent_tch_last.delete(0, tk.END)
                self.ent_tch_last.insert(0, last2)
                self.ent_tch_desig.delete(0, tk.END)
                self.ent_tch_desig.insert(0, fields["designation"].get().strip())
                self.ent_tch_phone.delete(0, tk.END)
                self.ent_tch_phone.insert(0, fields["phone"].get().strip())
                if self.can_view_salary:
                    self.ent_tch_sal.delete(0, tk.END)
                    self.ent_tch_sal.insert(0, str(sal))
                    self.ent_tch_join.delete(0, tk.END)
                    self.ent_tch_join.insert(0, fields["joining_date"].get().strip())
            messagebox.showinfo("Updated", f"Teacher '{new_name}' ({t_id}) updated successfully.", parent=edit_win)
            try:
                edit_win.grab_release()
            except Exception:
                pass
            edit_win.destroy()
            self._bring_to_front()

        tk.Button(
            btn_row, text="💾 Save Changes", command=do_save,
            bg=self.GREEN, fg="white", relief="flat", padx=18, pady=10,
            font=("Segoe UI", 10, "bold"), cursor="hand2",
        ).pack(side=tk.LEFT)
        def _cancel_edit():
            try:
                edit_win.grab_release()
            except Exception:
                pass
            edit_win.destroy()
            self._bring_to_front()

        tk.Button(
            btn_row, text="Cancel", command=_cancel_edit,
            bg="#f1f5f9", fg=self.NAVY, relief="flat", padx=18, pady=10,
            font=("Segoe UI", 10, "bold"), cursor="hand2",
        ).pack(side=tk.LEFT, padx=10)
        edit_win.protocol("WM_DELETE_WINDOW", _cancel_edit)

    def toggle_teacher_status(self):
        """Activate or Deactivate a teacher (soft status — no hard delete)."""
        if not (rbac.can(self.user_role, "teacher.delete") or rbac.can(self.user_role, "teacher.add")):
            messagebox.showerror(
                "Permission Denied",
                "You are not allowed to change teacher active/inactive status.",
                parent=self.win,
            )
            return

        t_id, t_name = self._get_selected_teacher_id()
        if not t_id:
            messagebox.showinfo("Select Teacher", "Please select a teacher from the directory first.", parent=self.win)
            return

        row = db.run(
            "SELECT COALESCE(status,'Active') FROM teachers WHERE teacher_id=?",
            (t_id,), fetchone=True,
        )
        if not row:
            messagebox.showerror("Error", "Teacher not found.", parent=self.win)
            return

        current = (row[0] or "Active").strip()
        is_active = current.lower() != "inactive"
        new_status = "Inactive" if is_active else "Active"
        action_word = "Deactivate" if is_active else "Activate"

        confirm = messagebox.askyesno(
            f"Confirm {action_word}",
            f"{action_word} this teacher?\n\n"
            f"Name: {t_name}\n"
            f"Teacher ID: {t_id}\n"
            f"Current status: {current}\n"
            f"New status: {new_status}\n\n"
            + (
                "Inactive teachers stay in the system (attendance & salary history preserved)\n"
                "but are hidden from day-to-day attendance and payroll pickers."
                if is_active
                else "Teacher will appear again in attendance and payroll lists."
            ),
            parent=self.win,
        )
        if not confirm:
            self._bring_to_front()
            return

        try:
            db.run(
                "UPDATE teachers SET status=? WHERE teacher_id=?",
                (new_status, t_id),
                commit=True,
            )
            log_activity(
                self.current_user,
                f"{action_word}d teacher {t_name} ({t_id}) → {new_status}",
            )
            self.load_teacher_table()
            # Refresh attendance / payroll lists so inactive teachers drop out
            try:
                self.load_attendance_log()
            except Exception:
                pass
            try:
                self._refresh_payroll_teacher_list()
                self._update_payroll_metrics()
            except Exception:
                pass
            messagebox.showinfo(
                "Status Updated",
                f"Teacher '{t_name}' ({t_id}) is now {new_status}.",
                parent=self.win,
            )
        except Exception as exc:
            messagebox.showerror(
                "Update Failed",
                f"Could not change teacher status.\n\nError:\n{exc}",
                parent=self.win,
            )
        self._bring_to_front()

    # ============================================================
    # PAGE 2: Daily Attendance  (full staff log redesign)
    # ============================================================
    def _build_attendance_page(self):
        outer = self.page_attendance

        # ---- Left: Mark form + today counts ----
        left = tk.Frame(outer, bg=self.CARD, highlightbackground=self.BORDER, highlightthickness=1)
        left.place(x=14, y=14, width=300, relheight=1.0, height=-28)

        tk.Label(left, text="Daily Attendance", font=("Segoe UI", 12, "bold"),
                 bg=self.CARD, fg=self.NAVY).pack(anchor="w", padx=16, pady=(16, 12))

        tk.Label(left, text="Teacher ID", font=("Segoe UI", 8, "bold"), bg=self.CARD, fg=self.MUTED).pack(
            anchor="w", padx=16, pady=(0, 2))
        id_row = tk.Frame(left, bg=self.CARD)
        id_row.pack(fill=tk.X, padx=16)
        self.ent_att_tch_id = tk.Entry(id_row, font=("Segoe UI", 10), relief="solid", bd=1)
        self.ent_att_tch_id.pack(side=tk.LEFT, fill=tk.X, expand=True, ipady=4)
        self.ent_att_tch_id.bind("<Return>", lambda e: self.load_attendance_teacher())
        tk.Button(
            id_row, text="🔍 Search & Load", command=self.load_attendance_teacher,
            bg="#64748b", fg="white", relief="flat", padx=8, pady=4,
            font=("Segoe UI", 8, "bold"), cursor="hand2",
        ).pack(side=tk.LEFT, padx=(6, 0))

        self.lbl_att_tch_name = tk.Label(
            left, text="Enter Teacher ID and click Search & Load.",
            font=("Segoe UI", 9), bg=self.CARD, fg=self.MUTED, wraplength=260, justify="left",
        )
        self.lbl_att_tch_name.pack(anchor="w", padx=16, pady=(8, 10))

        tk.Label(left, text="Attendance Status", font=("Segoe UI", 8, "bold"), bg=self.CARD, fg=self.MUTED).pack(
            anchor="w", padx=16, pady=(0, 2))
        self.combo_tch_att = ttk.Combobox(
            left, values=["Present", "Absent", "Late", "Leave"], width=18, state="readonly", font=("Segoe UI", 10),
        )
        self.combo_tch_att.current(0)
        self.combo_tch_att.pack(anchor="w", padx=16, pady=(0, 12), ipady=2)

        btn_mark = tk.Button(
            left, text="✓ Mark Today's Attendance", command=self.mark_teacher_attendance,
            bg=self.CYAN, fg="white", relief="flat", padx=12, pady=8,
            font=("Segoe UI", 9, "bold"), cursor="hand2",
        )
        btn_mark.pack(fill=tk.X, padx=16, pady=(0, 8))
        btn_mark.config(state="normal" if self.can_mark_attendance else "disabled")

        if not self.can_mark_attendance:
            tk.Label(
                left, text="You don't have permission to mark teacher attendance.",
                font=("Segoe UI", 8), bg=self.CARD, fg=self.RED, wraplength=260, justify="left",
            ).pack(anchor="w", padx=16, pady=(0, 8))

        # Today's status card
        status_card = tk.Frame(left, bg="#f8fafc", highlightbackground=self.BORDER, highlightthickness=1)
        status_card.pack(fill=tk.X, padx=16, pady=(16, 12))
        tk.Label(status_card, text="Today's Status:", font=("Segoe UI", 9, "bold"),
                 bg="#f8fafc", fg=self.MUTED).pack(anchor="w", padx=12, pady=(10, 4))
        self.lbl_today_present = tk.Label(
            status_card, text="0 Present", font=("Segoe UI", 11, "bold"),
            bg="#f8fafc", fg=self.GREEN,
        )
        self.lbl_today_present.pack(anchor="w", padx=12)
        self.lbl_today_absent = tk.Label(
            status_card, text="0 Absent", font=("Segoe UI", 11, "bold"),
            bg="#f8fafc", fg=self.RED,
        )
        self.lbl_today_absent.pack(anchor="w", padx=12, pady=(2, 12))

        # ---- Right: Daily Attendance Log ----
        right = tk.Frame(outer, bg=self.CARD, highlightbackground=self.BORDER, highlightthickness=1)
        right.place(x=328, y=14, relwidth=1.0, width=-342, relheight=1.0, height=-28)

        top_bar = tk.Frame(right, bg=self.CARD)
        top_bar.pack(fill=tk.X, padx=14, pady=(12, 6))
        tk.Label(top_bar, text="DAILY ATTENDANCE LOG", font=("Segoe UI", 11, "bold"),
                 bg=self.CARD, fg=self.NAVY).pack(side=tk.LEFT)
        tk.Button(
            top_bar, text="↓ Export Daily Log", command=self.export_attendance_log,
            bg="#e0e7ff", fg=self.BLUE, relief="flat", padx=12, pady=5,
            font=("Segoe UI", 8, "bold"), cursor="hand2",
        ).pack(side=tk.RIGHT)
        tk.Button(
            top_bar, text="↻ Refresh", command=self.load_attendance_log,
            bg="#f1f5f9", fg=self.NAVY, relief="flat", padx=10, pady=5,
            font=("Segoe UI", 8, "bold"), cursor="hand2",
        ).pack(side=tk.RIGHT, padx=(0, 8))

        search_bar = tk.Frame(right, bg=self.CARD)
        search_bar.pack(fill=tk.X, padx=14, pady=(0, 8))
        tk.Label(search_bar, text="🔍", font=("Segoe UI", 10), bg=self.CARD, fg=self.MUTED).pack(side=tk.LEFT)
        self.ent_att_search = tk.Entry(search_bar, font=("Segoe UI", 9), relief="solid", bd=1)
        self.ent_att_search.pack(side=tk.LEFT, padx=(4, 0), ipady=4, fill=tk.X, expand=True)
        self.ent_att_search.bind("<KeyRelease>", lambda e: self.load_attendance_log())

        table_frame = tk.Frame(right, bg=self.CARD)
        table_frame.pack(fill=tk.BOTH, expand=True, padx=14, pady=(0, 8))

        att_cols = ("id", "name", "desig", "phone", "time_in", "status", "actions")
        self.tree_attendance = ttk.Treeview(table_frame, columns=att_cols, show="headings", style="TP.Treeview")
        headers = {
            "id": "Teacher ID", "name": "Name", "desig": "Designation", "phone": "Phone",
            "time_in": "Time In", "status": "Status", "actions": "Actions",
        }
        widths = {"id": 90, "name": 120, "desig": 110, "phone": 110, "time_in": 90, "status": 100, "actions": 150}
        for col in att_cols:
            self.tree_attendance.heading(col, text=headers[col])
            self.tree_attendance.column(col, anchor="center", width=widths[col], minwidth=60)
        self.tree_attendance.tag_configure("present", foreground="#166534")
        self.tree_attendance.tag_configure("absent", foreground="#b91c1c")
        self.tree_attendance.tag_configure("pending", foreground="#b45309")
        self.tree_attendance.tag_configure("late", foreground="#1d4ed8")
        self.tree_attendance.tag_configure("leave", foreground="#7c3aed")

        scroll = ttk.Scrollbar(table_frame, orient=tk.VERTICAL, command=self.tree_attendance.yview)
        self.tree_attendance.configure(yscroll=scroll.set)
        scroll.pack(side=tk.RIGHT, fill=tk.Y)
        self.tree_attendance.pack(fill=tk.BOTH, expand=True)
        self.tree_attendance.bind("<ButtonRelease-1>", self._on_attendance_click)

        self.lbl_att_count = tk.Label(right, text="", font=("Segoe UI", 8), bg=self.CARD, fg=self.MUTED)
        self.lbl_att_count.pack(anchor="e", padx=14, pady=(0, 10))

    def load_attendance_teacher(self):
        t_id = self.ent_att_tch_id.get().strip()
        if not t_id:
            self.lbl_att_tch_name.config(text="Enter a Teacher ID first.", fg=self.RED)
            return
        row = db.run("SELECT name, designation FROM teachers WHERE teacher_id=?", (t_id,), fetchone=True)
        if not row:
            self.lbl_att_tch_name.config(text=f"Teacher ID '{t_id}' not found.", fg=self.RED)
            return
        name, desig = row
        self.lbl_att_tch_name.config(text=f"✓ {name}  ({desig or 'Teacher'})", fg=self.GREEN)

    def _load_teacher_into_attendance_page(self):
        if not hasattr(self, "ent_att_tch_id"):
            return
        self.ent_att_tch_id.delete(0, tk.END)
        self.ent_att_tch_id.insert(0, self.lbl_tch_id.cget("text"))
        self.load_attendance_teacher()

    def mark_teacher_attendance(self):
        if not rbac.can(self.user_role, "teacher.attendance.mark"):
            messagebox.showerror("Permission Denied", "Not allowed to mark teacher attendance.", parent=self.win)
            self._bring_to_front()
            return
        t_id = self.ent_att_tch_id.get().strip()
        if not t_id:
            messagebox.showerror("Error", "Enter a Teacher ID (or select one from Directory) first.", parent=self.win)
            self._bring_to_front()
            return
        teacher = db.run(
            "SELECT name, COALESCE(status,'Active') FROM teachers WHERE teacher_id=?",
            (t_id,), fetchone=True,
        )
        if not teacher:
            messagebox.showerror("Error", f"Teacher ID '{t_id}' not found.", parent=self.win)
            self._bring_to_front()
            return
        if (teacher[1] or "Active").strip().lower() == "inactive":
            messagebox.showerror(
                "Inactive Teacher",
                f"'{teacher[0]}' ({t_id}) is Inactive.\n"
                "Activate the teacher from Directory first before marking attendance.",
                parent=self.win,
            )
            self._bring_to_front()
            return
        status = self.combo_tch_att.get()
        today = datetime.now().strftime("%Y-%m-%d")
        now_time = datetime.now().strftime("%H:%M:%S")
        try:
            db.run(
                "INSERT INTO teacher_attendance (teacher_id, date, status, in_time) VALUES (?, ?, ?, ?)",
                (t_id, today, status, now_time),
                commit=True,
            )
            messagebox.showinfo(
                "Success",
                f"Teacher Attendance ({status}) Marked for {teacher[0]} ({t_id})",
                parent=self.win,
            )
            self.load_attendance_log()
        except Exception:
            messagebox.showwarning(
                "Warning",
                "Today's Attendance already marked for this teacher!",
                parent=self.win,
            )
        self._bring_to_front()

    def load_attendance_log(self):
        """Show all teachers with today's attendance status (or Pending)."""
        if not hasattr(self, "tree_attendance"):
            return
        self.tree_attendance.delete(*self.tree_attendance.get_children())
        today = datetime.now().strftime("%Y-%m-%d")
        search = self.ent_att_search.get().strip() if hasattr(self, "ent_att_search") else ""

        # Only Active teachers appear in the daily attendance grid
        teachers = db.run(
            "SELECT teacher_id, name, designation, phone FROM teachers "
            "WHERE COALESCE(status,'Active') != 'Inactive' ORDER BY teacher_id",
            fetchall=True,
        ) or []

        # Today's attendance map
        att_rows = db.run(
            "SELECT teacher_id, status, in_time FROM teacher_attendance WHERE date=?",
            (today,),
            fetchall=True,
        ) or []
        att_map = {r[0]: (r[1], r[2]) for r in att_rows}

        present = absent = late = leave = pending = 0
        shown = 0
        for t_id, name, desig, phone in teachers:
            if search and search.lower() not in (t_id + " " + (name or "")).lower():
                continue
            if t_id in att_map:
                status, in_time = att_map[t_id]
                time_display = in_time or "—"
                if status == "Present":
                    badge, tag = "✅ Present", "present"
                    present += 1
                elif status == "Absent":
                    badge, tag = "❌ Absent", "absent"
                    absent += 1
                elif status == "Late":
                    badge, tag = "⏰ Late", "late"
                    late += 1
                else:
                    badge, tag = "🏖 Leave", "leave"
                    leave += 1
            else:
                status, time_display = "Pending", "—"
                badge, tag = "⏳ Pending", "pending"
                pending += 1

            self.tree_attendance.insert(
                "", tk.END,
                values=(t_id, name, desig or "", phone or "", time_display, badge, "Profile | Load | Clear"),
                tags=(tag,),
            )
            shown += 1

        if hasattr(self, "lbl_today_present"):
            self.lbl_today_present.config(text=f"{present} Present")
            self.lbl_today_absent.config(text=f"{absent} Absent")
        if hasattr(self, "lbl_att_count"):
            self.lbl_att_count.config(
                text=f"{shown} teacher(s)  ·  Present {present}  ·  Absent {absent}  ·  Pending {pending}"
            )

    def _on_attendance_click(self, event):
        region = self.tree_attendance.identify("region", event.x, event.y)
        if region != "cell":
            return
        col = self.tree_attendance.identify_column(event.x)
        item = self.tree_attendance.identify_row(event.y)
        if not item:
            return
        vals = self.tree_attendance.item(item, "values")
        if not vals:
            return
        t_id = vals[0]
        cols = self.tree_attendance["columns"]
        if col == f"#{len(cols)}":
            bbox = self.tree_attendance.bbox(item, col)
            if bbox:
                rel_x = event.x - bbox[0]
                zone = rel_x // max(bbox[2] // 3, 1)
                if zone <= 0:
                    self._build_teacher_profile_window(t_id)
                elif zone == 1:
                    # quick-edit: load into left form
                    self.ent_att_tch_id.delete(0, tk.END)
                    self.ent_att_tch_id.insert(0, t_id)
                    self.load_attendance_teacher()
                else:
                    # delete today's attendance if exists
                    self._delete_today_attendance(t_id)
            return
        # row click → load into form
        self.ent_att_tch_id.delete(0, tk.END)
        self.ent_att_tch_id.insert(0, t_id)
        self.load_attendance_teacher()

    def _delete_today_attendance(self, t_id):
        if not self.can_mark_attendance:
            messagebox.showerror("Permission Denied", "Not allowed to modify attendance.")
            return
        today = datetime.now().strftime("%Y-%m-%d")
        confirm = messagebox.askyesno(
            "Remove Attendance",
            f"Remove today's attendance record for {t_id}?",
            parent=self.win,
        )
        if not confirm:
            return
        try:
            db.run(
                "DELETE FROM teacher_attendance WHERE teacher_id=? AND date=?",
                (t_id, today),
                commit=True,
            )
            log_activity(self.current_user, f"Removed today's attendance for teacher {t_id}")
            self.load_attendance_log()
        except Exception as exc:
            messagebox.showerror("Error", str(exc), parent=self.win)

    def export_attendance_log(self):
        """Export today's attendance log to a simple CSV/TXT."""
        today = datetime.now().strftime("%Y-%m-%d")
        path = filedialog.asksaveasfilename(
            parent=self.win,
            title="Export Daily Attendance Log",
            defaultextension=".csv",
            initialfile=f"Teacher_Attendance_{today}.csv",
            filetypes=[("CSV files", "*.csv"), ("All files", "*.*")],
        )
        if not path:
            return
        teachers = db.run(
            "SELECT teacher_id, name, designation, phone FROM teachers "
            "WHERE COALESCE(status,'Active') != 'Inactive' ORDER BY teacher_id",
            fetchall=True,
        ) or []
        att_rows = db.run(
            "SELECT teacher_id, status, in_time FROM teacher_attendance WHERE date=?",
            (today,),
            fetchall=True,
        ) or []
        att_map = {r[0]: (r[1], r[2]) for r in att_rows}
        try:
            with open(path, "w", encoding="utf-8") as f:
                f.write("Teacher ID,Name,Designation,Phone,Time In,Status\n")
                for t_id, name, desig, phone in teachers:
                    if t_id in att_map:
                        status, in_time = att_map[t_id]
                    else:
                        status, in_time = "Pending", ""
                    f.write(f'"{t_id}","{name or ""}","{desig or ""}","{phone or ""}","{in_time or ""}","{status}"\n')
            messagebox.showinfo("Exported", f"Daily attendance log saved:\n{path}", parent=self.win)
        except Exception as exc:
            messagebox.showerror("Export Failed", str(exc), parent=self.win)

    # ============================================================
    # PAGE 3: Payroll & Payslip  (redesigned)
    # ============================================================
    def _build_payroll_page(self):
        outer = self.page_payroll

        if not self.can_view_salary:
            msg = tk.Frame(outer, bg=self.CARD, highlightbackground=self.BORDER, highlightthickness=1)
            msg.place(x=14, y=14, relwidth=1.0, width=-28, height=80)
            tk.Label(
                msg, text="You don't have permission to view or process salaries.",
                font=("Segoe UI", 11), bg=self.CARD, fg=self.RED,
            ).pack(expand=True)
            return

        # ---- Left panel: Select teacher & process ----
        left = tk.Frame(outer, bg=self.CARD, highlightbackground=self.BORDER, highlightthickness=1)
        left.place(x=14, y=14, width=300, relheight=1.0, height=-28)

        tk.Label(left, text="SELECT TEACHER & PROCESS SALARY", font=("Segoe UI", 10, "bold"),
                 bg=self.CARD, fg=self.NAVY).pack(anchor="w", padx=14, pady=(14, 4))
        tk.Label(left, text="Search and select teacher.", font=("Segoe UI", 8),
                 bg=self.CARD, fg=self.MUTED).pack(anchor="w", padx=14, pady=(0, 8))

        tk.Label(left, text="Teacher", font=("Segoe UI", 8, "bold"), bg=self.CARD, fg=self.MUTED).pack(
            anchor="w", padx=14, pady=(0, 2))
        self.combo_pay_teacher = ttk.Combobox(left, state="normal", font=("Segoe UI", 10))
        self.combo_pay_teacher.pack(fill=tk.X, padx=14, ipady=3)
        self.combo_pay_teacher.bind("<<ComboboxSelected>>", lambda e: self._on_payroll_teacher_selected())
        self.combo_pay_teacher.bind("<KeyRelease>", self._filter_pay_teacher_combo)
        self.combo_pay_teacher.bind("<Return>", lambda e: self._on_payroll_teacher_selected())

        # Month / Year
        my_row = tk.Frame(left, bg=self.CARD)
        my_row.pack(fill=tk.X, padx=14, pady=(10, 0))
        tk.Label(my_row, text="Month", font=("Segoe UI", 8, "bold"), bg=self.CARD, fg=self.MUTED).pack(
            side=tk.LEFT, expand=True, fill=tk.X)
        tk.Label(my_row, text="Year", font=("Segoe UI", 8, "bold"), bg=self.CARD, fg=self.MUTED).pack(
            side=tk.LEFT, expand=True, fill=tk.X, padx=(8, 0))
        my_vals = tk.Frame(left, bg=self.CARD)
        my_vals.pack(fill=tk.X, padx=14, pady=(2, 0))
        months = [f"{i:02d} - {calendar.month_abbr[i]}" for i in range(1, 13)]
        self.combo_pay_month = ttk.Combobox(my_vals, values=months, state="readonly", width=12, font=("Segoe UI", 9))
        self.combo_pay_month.current(datetime.now().month - 1)
        self.combo_pay_month.pack(side=tk.LEFT, fill=tk.X, expand=True)
        self.ent_pay_year = tk.Entry(my_vals, font=("Segoe UI", 10), relief="solid", bd=1, width=8)
        self.ent_pay_year.insert(0, str(datetime.now().year))
        self.ent_pay_year.pack(side=tk.LEFT, padx=(8, 0), ipady=3)

        # Bonus
        tk.Label(left, text="Bonus / Adjustments (Rs.)", font=("Segoe UI", 8, "bold"),
                 bg=self.CARD, fg=self.MUTED).pack(anchor="w", padx=14, pady=(10, 2))
        self.ent_pay_bonus = tk.Entry(left, font=("Segoe UI", 10), relief="solid", bd=1)
        self.ent_pay_bonus.pack(fill=tk.X, padx=14, ipady=4)
        self.ent_pay_bonus.insert(0, "0")
        self.ent_pay_bonus.bind("<KeyRelease>", lambda e: self._refresh_payslip_preview())

        # Deductions
        tk.Label(left, text="Deductions (Rs.)", font=("Segoe UI", 8, "bold"),
                 bg=self.CARD, fg=self.MUTED).pack(anchor="w", padx=14, pady=(8, 2))
        self.ent_pay_deductions = tk.Entry(left, font=("Segoe UI", 10), relief="solid", bd=1)
        self.ent_pay_deductions.pack(fill=tk.X, padx=14, ipady=4)
        self.ent_pay_deductions.insert(0, "0")
        self.ent_pay_deductions.bind("<KeyRelease>", lambda e: self._refresh_payslip_preview())

        # Deduction reason
        tk.Label(left, text="Deduction Reason", font=("Segoe UI", 8, "bold"),
                 bg=self.CARD, fg=self.MUTED).pack(anchor="w", padx=14, pady=(8, 2))
        self.txt_pay_reason = tk.Text(left, font=("Segoe UI", 9), relief="solid", bd=1, height=3, wrap="word")
        self.txt_pay_reason.pack(fill=tk.X, padx=14)

        # Action buttons
        btn_frame = tk.Frame(left, bg=self.CARD)
        btn_frame.pack(fill=tk.X, padx=14, pady=14)
        process_btn = tk.Button(
            btn_frame, text="💵 Process & Generate Payslip",
            command=self.generate_salary_payslip,
            bg=self.GREEN, fg="white", relief="flat", padx=10, pady=9,
            font=("Segoe UI", 9, "bold"), cursor="hand2",
        )
        process_btn.pack(fill=tk.X, pady=(0, 6))
        process_btn.config(state="normal" if self.can_pay_salary else "disabled")

        calc_row = tk.Frame(btn_frame, bg=self.CARD)
        calc_row.pack(fill=tk.X)
        tk.Button(
            calc_row, text="Calculate Net Salary", command=self._refresh_payslip_preview,
            bg=self.BLUE, fg="white", relief="flat", padx=8, pady=7,
            font=("Segoe UI", 8, "bold"), cursor="hand2",
        ).pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 4))
        tk.Button(
            calc_row, text="Clear", command=self._clear_payroll_form,
            bg="#f1f5f9", fg=self.NAVY, relief="flat", padx=8, pady=7,
            font=("Segoe UI", 8, "bold"), cursor="hand2",
        ).pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(4, 0))

        if not self.can_pay_salary:
            tk.Label(
                left, text="You can view salary details but you're not permitted to process payments.",
                font=("Segoe UI", 8), bg=self.CARD, fg=self.AMBER, wraplength=260, justify="left",
            ).pack(anchor="w", padx=14, pady=(0, 10))

        # ---- Middle: Payslip preview card ----
        mid = tk.Frame(outer, bg=self.CARD, highlightbackground=self.BORDER, highlightthickness=1)
        mid.place(x=328, y=14, width=360, relheight=1.0, height=-28)

        mid_top = tk.Frame(mid, bg=self.CARD)
        mid_top.pack(fill=tk.X, padx=12, pady=(12, 6))
        tk.Label(mid_top, text="PAYSLIP REVIEW & ACTIONS", font=("Segoe UI", 10, "bold"),
                 bg=self.CARD, fg=self.NAVY).pack(side=tk.LEFT)
        tk.Label(mid, text="Beautifully designed, compact digital preview.", font=("Segoe UI", 8),
                 bg=self.CARD, fg=self.MUTED).pack(anchor="w", padx=12)

        actions_row = tk.Frame(mid, bg=self.CARD)
        actions_row.pack(fill=tk.X, padx=12, pady=(8, 6))
        tk.Button(
            actions_row, text="↓ Download PDF Payslip", command=self._download_payslip_pdf,
            bg="#e0e7ff", fg=self.BLUE, relief="flat", padx=10, pady=5,
            font=("Segoe UI", 8, "bold"), cursor="hand2",
        ).pack(side=tk.LEFT)
        tk.Button(
            actions_row, text="🖨 Print Payslip", command=self._print_payslip,
            bg="#f1f5f9", fg=self.NAVY, relief="flat", padx=10, pady=5,
            font=("Segoe UI", 8, "bold"), cursor="hand2",
        ).pack(side=tk.LEFT, padx=(6, 0))

        # Preview card body
        self.payslip_preview = tk.Frame(mid, bg="#f8fafc", highlightbackground=self.BORDER, highlightthickness=1)
        self.payslip_preview.pack(fill=tk.BOTH, expand=True, padx=12, pady=(4, 12))

        self.lbl_preview_name = tk.Label(
            self.payslip_preview, text="Select a teacher", font=("Segoe UI", 12, "bold"),
            bg="#f8fafc", fg=self.NAVY, anchor="w",
        )
        self.lbl_preview_name.pack(fill=tk.X, padx=14, pady=(12, 0))
        self.lbl_preview_meta = tk.Label(
            self.payslip_preview, text="ID: —   ·   Month: —", font=("Segoe UI", 8),
            bg="#f8fafc", fg=self.MUTED, anchor="w",
        )
        self.lbl_preview_meta.pack(fill=tk.X, padx=14, pady=(2, 10))

        # Earnings section
        earn_hdr = tk.Frame(self.payslip_preview, bg="#e2e8f0")
        earn_hdr.pack(fill=tk.X, padx=10)
        tk.Label(earn_hdr, text="Earnings", font=("Segoe UI", 8, "bold"), bg="#e2e8f0", fg=self.NAVY).pack(
            side=tk.LEFT, padx=8, pady=4)
        tk.Label(earn_hdr, text="Amount", font=("Segoe UI", 8, "bold"), bg="#e2e8f0", fg=self.NAVY).pack(
            side=tk.RIGHT, padx=8, pady=4)

        self.lbl_preview_basic = tk.Label(
            self.payslip_preview, text="Basic Salary          Rs. 0.00",
            font=("Segoe UI", 9), bg="#f8fafc", fg=self.NAVY, anchor="w",
        )
        self.lbl_preview_basic.pack(fill=tk.X, padx=18, pady=(6, 2))
        self.lbl_preview_bonus = tk.Label(
            self.payslip_preview, text="Bonus                 Rs. 0.00",
            font=("Segoe UI", 9), bg="#f8fafc", fg=self.NAVY, anchor="w",
        )
        self.lbl_preview_bonus.pack(fill=tk.X, padx=18, pady=2)

        # Deductions section
        ded_hdr = tk.Frame(self.payslip_preview, bg="#e2e8f0")
        ded_hdr.pack(fill=tk.X, padx=10, pady=(10, 0))
        tk.Label(ded_hdr, text="Deductions", font=("Segoe UI", 8, "bold"), bg="#e2e8f0", fg=self.NAVY).pack(
            side=tk.LEFT, padx=8, pady=4)
        tk.Label(ded_hdr, text="Amount", font=("Segoe UI", 8, "bold"), bg="#e2e8f0", fg=self.NAVY).pack(
            side=tk.RIGHT, padx=8, pady=4)

        self.lbl_preview_absences = tk.Label(
            self.payslip_preview, text="Absences              Rs. 0.00",
            font=("Segoe UI", 9), bg="#f8fafc", fg=self.NAVY, anchor="w",
        )
        self.lbl_preview_absences.pack(fill=tk.X, padx=18, pady=(6, 2))
        self.lbl_preview_other_ded = tk.Label(
            self.payslip_preview, text="Other Deductions      Rs. 0.00",
            font=("Segoe UI", 9), bg="#f8fafc", fg=self.NAVY, anchor="w",
        )
        self.lbl_preview_other_ded.pack(fill=tk.X, padx=18, pady=2)

        # Net
        net_frame = tk.Frame(self.payslip_preview, bg=self.LIGHT_GREEN)
        net_frame.pack(fill=tk.X, padx=10, pady=(12, 14))
        tk.Label(net_frame, text="Net Salary", font=("Segoe UI", 10, "bold"),
                 bg=self.LIGHT_GREEN, fg="#166534").pack(side=tk.LEFT, padx=10, pady=8)
        self.lbl_preview_net = tk.Label(
            net_frame, text="Rs. 0.00", font=("Segoe UI", 12, "bold"),
            bg=self.LIGHT_GREEN, fg="#166534",
        )
        self.lbl_preview_net.pack(side=tk.RIGHT, padx=10, pady=8)

        self.lbl_preview_note = tk.Label(
            self.payslip_preview, text="Select a teacher to see live breakdown.",
            font=("Segoe UI", 8), bg="#f8fafc", fg=self.MUTED, anchor="w", wraplength=320, justify="left",
        )
        self.lbl_preview_note.pack(fill=tk.X, padx=14, pady=(0, 10))

        # ---- Right: Metrics + reference table ----
        right = tk.Frame(outer, bg=self.CARD, highlightbackground=self.BORDER, highlightthickness=1)
        right.place(x=702, y=14, relwidth=1.0, width=-716, relheight=1.0, height=-28)

        # Metrics
        metrics = tk.Frame(right, bg=self.CARD)
        metrics.pack(fill=tk.X, padx=12, pady=(12, 6))

        m1 = tk.Frame(metrics, bg="#f0f9ff", highlightbackground="#bae6fd", highlightthickness=1)
        m1.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 6))
        tk.Label(m1, text="Total Salary Processed\n(this month)", font=("Segoe UI", 8),
                 bg="#f0f9ff", fg=self.MUTED, justify="left").pack(anchor="w", padx=10, pady=(8, 2))
        self.lbl_metric_total = tk.Label(m1, text="Rs. 0.00", font=("Segoe UI", 14, "bold"),
                                         bg="#f0f9ff", fg=self.NAVY)
        self.lbl_metric_total.pack(anchor="w", padx=10, pady=(0, 8))

        m2 = tk.Frame(metrics, bg="#fefce8", highlightbackground="#fde68a", highlightthickness=1)
        m2.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(6, 0))
        tk.Label(m2, text="Teachers To Pay", font=("Segoe UI", 8),
                 bg="#fefce8", fg=self.MUTED, justify="left").pack(anchor="w", padx=10, pady=(8, 2))
        self.lbl_metric_pending = tk.Label(m2, text="0", font=("Segoe UI", 14, "bold"),
                                           bg="#fefce8", fg=self.AMBER)
        self.lbl_metric_pending.pack(anchor="w", padx=10, pady=(0, 8))

        # Batch button
        tk.Button(
            right, text="⚡ Batch Process Salaries (for all pending)",
            command=self._batch_process_salaries,
            bg="#ede9fe", fg=self.PURPLE, relief="flat", padx=10, pady=6,
            font=("Segoe UI", 8, "bold"), cursor="hand2",
        ).pack(fill=tk.X, padx=12, pady=(4, 8))
        if not self.can_pay_salary:
            # disable batch if no pay permission
            pass

        # Reference table
        tk.Label(right, text="Teachers Reference", font=("Segoe UI", 9, "bold"),
                 bg=self.CARD, fg=self.NAVY).pack(anchor="w", padx=12, pady=(4, 4))
        ref_frame = tk.Frame(right, bg=self.CARD)
        ref_frame.pack(fill=tk.BOTH, expand=True, padx=12, pady=(0, 12))
        ref_cols = ("id", "desig", "salary", "join")
        self.tree_pay_ref = ttk.Treeview(ref_frame, columns=ref_cols, show="headings", style="TP.Treeview", height=10)
        for col, h, w in [
            ("id", "ID", 80), ("desig", "Designation", 100),
            ("salary", "Basic Salary", 100), ("join", "Join Date", 90),
        ]:
            self.tree_pay_ref.heading(col, text=h)
            self.tree_pay_ref.column(col, anchor="center", width=w, minwidth=50)
        scroll = ttk.Scrollbar(ref_frame, orient=tk.VERTICAL, command=self.tree_pay_ref.yview)
        self.tree_pay_ref.configure(yscroll=scroll.set)
        scroll.pack(side=tk.RIGHT, fill=tk.Y)
        self.tree_pay_ref.pack(fill=tk.BOTH, expand=True)
        self.tree_pay_ref.bind("<ButtonRelease-1>", self._on_pay_ref_click)

        # Internal state for current payroll calculation
        self._pay_current = {
            "t_id": None, "name": None, "basic": 0.0,
            "absents": 0, "absence_ded": 0.0, "bonus": 0.0,
            "other_ded": 0.0, "net": 0.0, "ym": None,
            "out_path": None,
        }

    def _refresh_payroll_teacher_list(self):
        if not hasattr(self, "combo_pay_teacher"):
            return
        # Payroll picker: Active teachers only
        rows = db.run(
            "SELECT teacher_id, name FROM teachers "
            "WHERE COALESCE(status,'Active') != 'Inactive' ORDER BY teacher_id",
            fetchall=True,
        ) or []
        self._pay_teacher_options = [f"{r[0]} — {r[1]}" for r in rows]
        self.combo_pay_teacher["values"] = self._pay_teacher_options
        # Reference table also shows Active only (inactive stay in Directory)
        if hasattr(self, "tree_pay_ref"):
            self.tree_pay_ref.delete(*self.tree_pay_ref.get_children())
            full = db.run(
                "SELECT teacher_id, designation, basic_salary, joining_date FROM teachers "
                "WHERE COALESCE(status,'Active') != 'Inactive' ORDER BY teacher_id",
                fetchall=True,
            ) or []
            for r in full:
                sal = f"Rs. {float(r[2] or 0):,.0f}"
                self.tree_pay_ref.insert("", tk.END, values=(r[0], r[1] or "", sal, r[3] or ""))

    def _filter_pay_teacher_combo(self, event=None):
        typed = self.combo_pay_teacher.get().strip().lower()
        if not hasattr(self, "_pay_teacher_options"):
            return
        if not typed:
            self.combo_pay_teacher["values"] = self._pay_teacher_options
            return
        filtered = [o for o in self._pay_teacher_options if typed in o.lower()]
        self.combo_pay_teacher["values"] = filtered

    def _on_payroll_teacher_selected(self, event=None):
        raw = self.combo_pay_teacher.get().strip()
        if not raw:
            return
        t_id = raw.split("—")[0].strip() if "—" in raw else raw.split()[0].strip()
        self._select_payroll_teacher(t_id)

    def _select_payroll_teacher(self, t_id):
        if not hasattr(self, "combo_pay_teacher"):
            return
        row = db.run("SELECT name, basic_salary FROM teachers WHERE teacher_id=?", (t_id,), fetchone=True)
        if not row:
            return
        name, basic = row
        # set combo display
        display = f"{t_id} — {name}"
        self.combo_pay_teacher.set(display)
        self._pay_current["t_id"] = t_id
        self._pay_current["name"] = name
        self._pay_current["basic"] = float(basic or 0)
        self._refresh_payslip_preview()

    def _on_pay_ref_click(self, event):
        item = self.tree_pay_ref.focus()
        vals = self.tree_pay_ref.item(item, "values") if item else ()
        if vals:
            self._select_payroll_teacher(vals[0])

    def _get_selected_pay_ym(self):
        month_str = self.combo_pay_month.get()
        year = self.ent_pay_year.get().strip()
        if not (month_str and year.isdigit() and len(year) == 4):
            return datetime.now().strftime("%Y-%m")
        month_num = month_str.split("-")[0].strip()
        return f"{year}-{month_num}"

    def _refresh_payslip_preview(self):
        t_id = self._pay_current.get("t_id")
        if not t_id:
            self.lbl_preview_name.config(text="Select a teacher")
            self.lbl_preview_meta.config(text="ID: —   ·   Month: —")
            self.lbl_preview_basic.config(text="Basic Salary          Rs. 0.00")
            self.lbl_preview_bonus.config(text="Bonus                 Rs. 0.00")
            self.lbl_preview_absences.config(text="Absences              Rs. 0.00")
            self.lbl_preview_other_ded.config(text="Other Deductions      Rs. 0.00")
            self.lbl_preview_net.config(text="Rs. 0.00")
            self.lbl_preview_note.config(text="Select a teacher to see live breakdown.")
            return

        name = self._pay_current["name"]
        basic = self._pay_current["basic"]
        ym = self._get_selected_pay_ym()
        month_label = datetime.strptime(ym + "-01", "%Y-%m-%d").strftime("%b %Y") if len(ym) == 7 else ym

        # Absence-based deduction (same formula as original)
        absents = db.run(
            "SELECT COUNT(*) FROM teacher_attendance WHERE teacher_id=? AND status='Absent' AND date LIKE ?",
            (t_id, f"{ym}%"),
            fetchone=True,
        )[0]
        per_day = basic / 30.0
        absence_ded = per_day * absents

        bonus, ok1 = safe_float(self.ent_pay_bonus.get(), "Bonus", default=0.0)
        if not ok1:
            bonus = 0.0
        other_ded, ok2 = safe_float(self.ent_pay_deductions.get(), "Deductions", default=0.0)
        if not ok2:
            other_ded = 0.0

        net = basic + bonus - absence_ded - other_ded
        if net < 0:
            net = 0.0

        self._pay_current.update({
            "absents": absents,
            "absence_ded": absence_ded,
            "bonus": bonus,
            "other_ded": other_ded,
            "net": net,
            "ym": ym,
        })

        self.lbl_preview_name.config(text=f"Teacher: {name}")
        self.lbl_preview_meta.config(text=f"ID: {t_id}   ·   {month_label} Payslip")
        self.lbl_preview_basic.config(text=f"Basic Salary          Rs. {basic:,.2f}")
        self.lbl_preview_bonus.config(text=f"Bonus                 Rs. {bonus:,.2f}")
        self.lbl_preview_absences.config(text=f"Absences ({absents})         Rs. {absence_ded:,.2f}")
        self.lbl_preview_other_ded.config(text=f"Other Deductions      Rs. {other_ded:,.2f}")
        self.lbl_preview_net.config(text=f"Rs. {net:,.2f}")

        try:
            paid = accounting.is_teacher_salary_paid_this_month(t_id) if ym == datetime.now().strftime("%Y-%m") else False
        except Exception:
            paid = False
        note = f"Absences this month: {absents} day(s). "
        note += "✅ Already paid this month." if paid else "⏳ Pending payment."
        self.lbl_preview_note.config(text=note)

    def _clear_payroll_form(self):
        if hasattr(self, "combo_pay_teacher"):
            self.combo_pay_teacher.set("")
        self.ent_pay_bonus.delete(0, tk.END)
        self.ent_pay_bonus.insert(0, "0")
        self.ent_pay_deductions.delete(0, tk.END)
        self.ent_pay_deductions.insert(0, "0")
        self.txt_pay_reason.delete("1.0", tk.END)
        self._pay_current = {
            "t_id": None, "name": None, "basic": 0.0,
            "absents": 0, "absence_ded": 0.0, "bonus": 0.0,
            "other_ded": 0.0, "net": 0.0, "ym": None, "out_path": None,
        }
        self._refresh_payslip_preview()

    def _update_payroll_metrics(self):
        if not hasattr(self, "lbl_metric_total"):
            return
        ym = datetime.now().strftime("%Y-%m")
        try:
            paid_map = accounting.teacher_salary_status_map(ym)
        except Exception:
            paid_map = {}
        total_paid = 0.0
        pending = 0
        teachers = db.run(
            "SELECT teacher_id FROM teachers WHERE COALESCE(status,'Active') != 'Inactive'",
            fetchall=True,
        ) or []
        for (t_id,) in teachers:
            info = paid_map.get(t_id)
            if info and info.get("paid"):
                total_paid += float(info.get("amount") or 0)
            else:
                pending += 1
        # fallback: sum from accounting_expense if map lacks amount
        if total_paid == 0:
            rows = db.run(
                "SELECT amount FROM accounting_expense WHERE category='Salary' AND date LIKE ?",
                (f"{ym}%",),
                fetchall=True,
            ) or []
            total_paid = sum(float(r[0] or 0) for r in rows)
        self.lbl_metric_total.config(text=f"Rs. {total_paid:,.2f}")
        self.lbl_metric_pending.config(text=str(pending))

    def generate_salary_payslip(self):
        if not rbac.can(self.user_role, "teacher.salary.pay"):
            messagebox.showerror("Permission Denied", "Not allowed to process salaries.", parent=self.win)
            self._bring_to_front()
            return

        self._refresh_payslip_preview()
        t_id = self._pay_current.get("t_id")
        if not t_id:
            messagebox.showerror("Error", "Select a teacher first.", parent=self.win)
            self._bring_to_front()
            return

        name = self._pay_current["name"]
        basic_sal = self._pay_current["basic"]
        st_row = db.run(
            "SELECT COALESCE(status,'Active') FROM teachers WHERE teacher_id=?",
            (t_id,), fetchone=True,
        )
        if st_row and (st_row[0] or "Active").strip().lower() == "inactive":
            messagebox.showerror(
                "Inactive Teacher",
                f"'{name}' ({t_id}) is Inactive.\nActivate from Directory before processing salary.",
                parent=self.win,
            )
            self._bring_to_front()
            return
        if basic_sal <= 0:
            messagebox.showerror(
                "Error",
                "This teacher's Basic Salary is not set (0). Update it from Directory & Registration first.",
                parent=self.win,
            )
            self._bring_to_front()
            return

        ym = self._pay_current["ym"] or self._get_selected_pay_ym()
        absents = self._pay_current["absents"]
        absence_ded = self._pay_current["absence_ded"]
        bonus = self._pay_current["bonus"]
        other_ded = self._pay_current["other_ded"]
        # Net includes bonus; absence + other deductions already applied
        net_sal = self._pay_current["net"]
        # For the classic payslip generator we pass absence-only deductions
        # (original API); bonus is noted in the description via accounting.
        classic_deductions = absence_ded
        classic_net = basic_sal - absence_ded  # original formula
        # Prefer the richer net if bonus/other were entered
        if bonus or other_ded:
            final_net = net_sal
            final_deductions = absence_ded + other_ded
        else:
            final_net = classic_net
            final_deductions = classic_deductions

        # Idempotency guard (same as original)
        already_paid = db.run(
            "SELECT id FROM accounting_expense WHERE category='Salary' AND date LIKE ? AND vendor_or_person LIKE ?",
            (f"{ym}%", f"%({t_id})%"),
            fetchone=True,
        )
        if already_paid:
            if not messagebox.askyesno(
                "Salary Already Recorded",
                f"A salary payment for {name} ({t_id}) was already recorded for {ym}.\n"
                "Generating another payslip will record a SECOND expense entry for this teacher this month.\n"
                "Continue anyway?",
                parent=self.win,
            ):
                self._bring_to_front()
                return

        out_path = os.path.join(os.getcwd(), f"Payslip_{t_id}_{ym}.pdf")
        reports.generate_payslip(
            t_id, name, ym, basic_sal, absents, final_deductions, final_net, out_path,
        )
        accounting.record_salary_expense(
            self.user_role, t_id, name, final_net, ym, self.current_user, reference=out_path,
        )
        log_activity(self.current_user, f"Generated salary payslip for teacher {t_id}")

        self._pay_current["out_path"] = out_path
        self._pay_current["net"] = final_net

        reason = self.txt_pay_reason.get("1.0", tk.END).strip()
        note = f"{name} ({t_id}) — {ym}\n"
        note += f"Basic: Rs. {basic_sal:,.2f}"
        if bonus:
            note += f"  + Bonus: Rs. {bonus:,.2f}"
        note += f"  | Absences: {absents}  | Deductions: Rs. {final_deductions:,.2f}\n"
        note += f"Net Payable: Rs. {final_net:,.2f}"
        if reason:
            note += f"\nReason: {reason}"
        self.lbl_preview_note.config(text=note)

        messagebox.showinfo(
            "Success",
            f"Payslip PDF Generated:\n{out_path}\nNet Payable: Rs. {final_net:.2f}\n"
            "(Recorded as an accounting expense.)",
            parent=self.win,
        )
        try:
            self.load_teacher_table()
            self._update_payroll_metrics()
        except Exception:
            pass
        self._bring_to_front()

    def _download_payslip_pdf(self):
        path = self._pay_current.get("out_path")
        if path and os.path.isfile(path):
            messagebox.showinfo("Payslip Ready", f"Payslip PDF is available at:\n{path}", parent=self.win)
            return
        # Generate if not yet processed
        if self._pay_current.get("t_id"):
            if messagebox.askyesno(
                "Generate Payslip?",
                "No payslip has been generated yet for the current selection.\nGenerate now?",
                parent=self.win,
            ):
                self.generate_salary_payslip()
        else:
            messagebox.showinfo("Select Teacher", "Select a teacher and process salary first.", parent=self.win)

    def _print_payslip(self):
        path = self._pay_current.get("out_path")
        if not path or not os.path.isfile(path):
            messagebox.showinfo(
                "No Payslip",
                "Generate the payslip first (Process & Generate Payslip), then print.",
                parent=self.win,
            )
            return
        try:
            if os.name == "nt":
                os.startfile(path, "print")
            else:
                # best-effort open
                import subprocess
                subprocess.Popen(["xdg-open", path])
            messagebox.showinfo("Print", f"Opened payslip for printing:\n{path}", parent=self.win)
        except Exception as exc:
            messagebox.showerror("Print Failed", str(exc), parent=self.win)

    def _batch_process_salaries(self):
        if not self.can_pay_salary:
            messagebox.showerror("Permission Denied", "Not allowed to process salaries.", parent=self.win)
            self._bring_to_front()
            return
        ym = datetime.now().strftime("%Y-%m")
        try:
            paid_map = accounting.teacher_salary_status_map(ym)
        except Exception:
            paid_map = {}
        teachers = db.run(
            "SELECT teacher_id, name, basic_salary FROM teachers "
            "WHERE COALESCE(status,'Active') != 'Inactive'",
            fetchall=True,
        ) or []
        pending = [t for t in teachers if not (paid_map.get(t[0]) and paid_map[t[0]].get("paid"))]
        if not pending:
            messagebox.showinfo("All Paid", "All teachers already have salary recorded for this month.", parent=self.win)
            self._bring_to_front()
            return
        if not messagebox.askyesno(
            "Batch Process",
            f"Process salary payslips for {len(pending)} pending teacher(s) for {ym}?\n"
            "(Uses basic salary minus absence deductions; no extra bonus/deductions.)",
            parent=self.win,
        ):
            self._bring_to_front()
            return
        ok_count = 0
        for t_id, name, basic_sal in pending:
            basic_sal = float(basic_sal or 0)
            if basic_sal <= 0:
                continue
            absents = db.run(
                "SELECT COUNT(*) FROM teacher_attendance WHERE teacher_id=? AND status='Absent' AND date LIKE ?",
                (t_id, f"{ym}%"),
                fetchone=True,
            )[0]
            per_day = basic_sal / 30.0
            deductions = per_day * absents
            net_sal = basic_sal - deductions
            out_path = os.path.join(os.getcwd(), f"Payslip_{t_id}_{ym}.pdf")
            try:
                reports.generate_payslip(t_id, name, ym, basic_sal, absents, deductions, net_sal, out_path)
                accounting.record_salary_expense(
                    self.user_role, t_id, name, net_sal, ym, self.current_user, reference=out_path,
                )
                log_activity(self.current_user, f"Batch-generated salary payslip for teacher {t_id}")
                ok_count += 1
            except Exception as exc:
                print(f"Batch payslip error for {t_id}: {exc}")
        messagebox.showinfo("Batch Complete", f"Generated payslips for {ok_count} teacher(s).", parent=self.win)
        self.load_teacher_table()
        self._update_payroll_metrics()
        self._bring_to_front()

    # ============================================================
    # TEACHER PROFILE — full monthly + yearly attendance & salary history
    # ============================================================
    def open_teacher_profile(self):
        if not rbac.can(self.user_role, "teacher.view"):
            messagebox.showerror("Permission Denied", "You are not allowed to view teacher profiles.", parent=self.win)
            self._bring_to_front()
            return
        selected = self.tree_teacher.focus()
        vals = self.tree_teacher.item(selected, "values") if selected else ()
        if not vals:
            messagebox.showinfo("Select Teacher", "Please select a teacher from the directory first.", parent=self.win)
            self._bring_to_front()
            return
        self._build_teacher_profile_window(vals[0])

    def _build_teacher_profile_window(self, t_id):
        row = db.run(
            "SELECT teacher_id, name, designation, phone, basic_salary, joining_date FROM teachers WHERE teacher_id=?",
            (t_id,), fetchone=True,
        )
        if not row:
            messagebox.showerror("Error", "Teacher not found.", parent=self.win)
            self._bring_to_front()
            return
        t_id, name, desig, phone, basic_sal, joining = row

        win = tk.Toplevel(self.win)
        win.title(f"Teacher Profile — {name}")
        win.geometry("880x720")
        win.minsize(760, 600)
        win.config(bg=self.BG)
        try:
            win.transient(self.win)
        except Exception:
            pass
        win.lift()
        win.focus_force()

        def _close_profile():
            try:
                win.destroy()
            except Exception:
                pass
            self._bring_to_front()

        win.protocol("WM_DELETE_WINDOW", _close_profile)

        header = tk.Frame(win, bg=self.NAVY, padx=20, pady=16)
        header.pack(fill=tk.X)
        tk.Label(header, text=f"👤 {name}", font=("Segoe UI", 16, "bold"), bg=self.NAVY, fg="white").pack(anchor="w")
        tk.Label(
            header, text=f"{t_id}  ·  {desig or '—'}  ·  {phone or '—'}",
            font=("Segoe UI", 9), bg=self.NAVY, fg="#cbd5e1",
        ).pack(anchor="w", pady=(2, 0))
        if self.can_view_salary:
            tk.Label(
                header, text=f"Basic Salary: Rs. {basic_sal:,.0f}   |   Joined: {joining or '—'}",
                font=("Segoe UI", 9), bg=self.NAVY, fg="#94a3b8",
            ).pack(anchor="w", pady=(2, 0))

        body_canvas = tk.Canvas(win, bg=self.BG, highlightthickness=0)
        vscroll = ttk.Scrollbar(win, orient=tk.VERTICAL, command=body_canvas.yview)
        body = tk.Frame(body_canvas, bg=self.BG)
        body.bind("<Configure>", lambda e: body_canvas.configure(scrollregion=body_canvas.bbox("all")))
        body_canvas.create_window((0, 0), window=body, anchor="nw", width=860)
        body_canvas.configure(yscrollcommand=vscroll.set)
        body_canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        vscroll.pack(side=tk.RIGHT, fill=tk.Y)

        # Monthly attendance
        att_card = tk.Frame(body, bg=self.CARD, highlightbackground=self.BORDER, highlightthickness=1)
        att_card.pack(fill=tk.X, padx=16, pady=(16, 8))
        tk.Label(att_card, text="ATTENDANCE — MONTHLY SUMMARY", font=("Segoe UI", 10, "bold"),
                 bg=self.CARD, fg=self.NAVY).pack(anchor="w", padx=16, pady=(14, 6))

        picker = tk.Frame(att_card, bg=self.CARD)
        picker.pack(fill=tk.X, padx=16, pady=(0, 8))
        tk.Label(picker, text="Month:", bg=self.CARD, font=("Segoe UI", 9)).pack(side=tk.LEFT)
        combo_month = ttk.Combobox(picker, values=[f"{i:02d}" for i in range(1, 13)], width=5, state="readonly")
        combo_month.current(datetime.now().month - 1)
        combo_month.pack(side=tk.LEFT, padx=(6, 16))
        tk.Label(picker, text="Year:", bg=self.CARD, font=("Segoe UI", 9)).pack(side=tk.LEFT)
        ent_year = tk.Entry(picker, width=8)
        ent_year.insert(0, str(datetime.now().year))
        ent_year.pack(side=tk.LEFT, padx=(6, 16))

        month_summary_lbl = tk.Label(
            att_card, text="", font=("Segoe UI", 10, "bold"), bg=self.CARD, fg=self.NAVY,
            justify="left", anchor="w", wraplength=780,
        )
        month_summary_lbl.pack(fill=tk.X, padx=16, pady=(0, 14))

        def compute_month():
            month = combo_month.get()
            year = ent_year.get().strip()
            if not (month and year.isdigit() and len(year) == 4):
                messagebox.showerror("Error", "Enter a valid Month and 4-digit Year.")
                return
            ym = f"{year}-{month}"
            total_working_days = db.run(
                "SELECT COUNT(DISTINCT date) FROM teacher_attendance WHERE date LIKE ?",
                (f"{ym}%",), fetchone=True,
            )[0]
            day_rows = db.run(
                "SELECT status FROM teacher_attendance WHERE teacher_id=? AND date LIKE ?",
                (t_id, f"{ym}%"), fetchall=True,
            )
            present = sum(1 for (s,) in day_rows if s == "Present")
            absent = sum(1 for (s,) in day_rows if s == "Absent")
            leave = sum(1 for (s,) in day_rows if s == "Leave")
            late = sum(1 for (s,) in day_rows if s == "Late")
            pct = (present / total_working_days * 100) if total_working_days else 0.0
            month_summary_lbl.config(
                text=(
                    f"{ym} — Working days: {total_working_days}  |  Present: {present}  |  Absent: {absent}  |  "
                    f"Leave: {leave}  |  Late: {late}  |  Attendance: {pct:.1f}%"
                )
            )

        tk.Button(
            picker, text="Show Month", command=compute_month, bg=self.CYAN, fg="white",
            relief="flat", padx=10, pady=4, font=("Segoe UI", 8, "bold"), cursor="hand2",
        ).pack(side=tk.LEFT)
        compute_month()

        # Yearly breakdown
        year_card = tk.Frame(body, bg=self.CARD, highlightbackground=self.BORDER, highlightthickness=1)
        year_card.pack(fill=tk.X, padx=16, pady=8)
        tk.Label(year_card, text="ATTENDANCE — YEARLY BREAKDOWN", font=("Segoe UI", 10, "bold"),
                 bg=self.CARD, fg=self.NAVY).pack(anchor="w", padx=16, pady=(14, 6))

        year_picker = tk.Frame(year_card, bg=self.CARD)
        year_picker.pack(fill=tk.X, padx=16, pady=(0, 8))
        tk.Label(year_picker, text="Year:", bg=self.CARD, font=("Segoe UI", 9)).pack(side=tk.LEFT)
        ent_year2 = tk.Entry(year_picker, width=8)
        ent_year2.insert(0, str(datetime.now().year))
        ent_year2.pack(side=tk.LEFT, padx=(6, 16))

        year_table_frame = tk.Frame(year_card, bg=self.CARD)
        year_table_frame.pack(fill=tk.X, padx=16, pady=(0, 8))
        year_cols = ("month", "present", "absent", "leave", "late", "pct")
        tree_year = ttk.Treeview(year_table_frame, columns=year_cols, show="headings", height=6, style="TP.Treeview")
        for col, h in [
            ("month", "Month"), ("present", "Present"), ("absent", "Absent"),
            ("leave", "Leave"), ("late", "Late"), ("pct", "Attendance %"),
        ]:
            tree_year.heading(col, text=h)
            tree_year.column(col, anchor="center", width=110)
        tree_year.pack(fill=tk.X)

        year_total_lbl = tk.Label(year_card, text="", font=("Segoe UI", 9, "bold"),
                                  bg=self.CARD, fg=self.NAVY, anchor="w")
        year_total_lbl.pack(fill=tk.X, padx=16, pady=(6, 14))

        def compute_year():
            year = ent_year2.get().strip()
            if not (year.isdigit() and len(year) == 4):
                messagebox.showerror("Error", "Enter a valid 4-digit Year.")
                return
            tree_year.delete(*tree_year.get_children())
            total_present = total_absent = total_working = 0
            for m in range(1, 13):
                ym = f"{year}-{m:02d}"
                working = db.run(
                    "SELECT COUNT(DISTINCT date) FROM teacher_attendance WHERE date LIKE ?",
                    (f"{ym}%",), fetchone=True,
                )[0]
                rows = db.run(
                    "SELECT status FROM teacher_attendance WHERE teacher_id=? AND date LIKE ?",
                    (t_id, f"{ym}%"), fetchall=True,
                )
                if working == 0 and not rows:
                    continue
                present = sum(1 for (s,) in rows if s == "Present")
                absent = sum(1 for (s,) in rows if s == "Absent")
                leave = sum(1 for (s,) in rows if s == "Leave")
                late = sum(1 for (s,) in rows if s == "Late")
                pct = (present / working * 100) if working else 0.0
                tree_year.insert("", tk.END, values=(ym, present, absent, leave, late, f"{pct:.1f}%"))
                total_present += present
                total_absent += absent
                total_working += working
            overall_pct = (total_present / total_working * 100) if total_working else 0.0
            year_total_lbl.config(
                text=f"Year {year} total — Present: {total_present}  |  Absent: {total_absent}  |  Overall Attendance: {overall_pct:.1f}%"
            )

        tk.Button(
            year_picker, text="Show Year", command=compute_year, bg=self.CYAN, fg="white",
            relief="flat", padx=10, pady=4, font=("Segoe UI", 8, "bold"), cursor="hand2",
        ).pack(side=tk.LEFT)
        compute_year()

        # Salary history
        if self.can_view_salary:
            pay_card = tk.Frame(body, bg=self.CARD, highlightbackground=self.BORDER, highlightthickness=1)
            pay_card.pack(fill=tk.X, padx=16, pady=8)
            tk.Label(pay_card, text="SALARY / PAYSLIP HISTORY", font=("Segoe UI", 10, "bold"),
                     bg=self.CARD, fg=self.NAVY).pack(anchor="w", padx=16, pady=(14, 6))

            pay_rows = db.run(
                "SELECT date, amount, description FROM accounting_expense WHERE category='Salary' AND vendor_or_person LIKE ? ORDER BY id DESC",
                (f"%({t_id})%",), fetchall=True,
            )

            if pay_rows:
                pay_table_frame = tk.Frame(pay_card, bg=self.CARD)
                pay_table_frame.pack(fill=tk.X, padx=16, pady=(0, 14))
                pay_cols = ("date", "amount", "desc")
                tree_pay = ttk.Treeview(pay_table_frame, columns=pay_cols, show="headings", height=6, style="TP.Treeview")
                tree_pay.heading("date", text="Month / Date")
                tree_pay.column("date", anchor="center", width=120)
                tree_pay.heading("amount", text="Net Paid (Rs.)")
                tree_pay.column("amount", anchor="center", width=140)
                tree_pay.heading("desc", text="Description")
                tree_pay.column("desc", anchor="w", width=440)
                tree_pay.pack(fill=tk.X)
                total_paid = 0.0
                for d, amount, desc in pay_rows:
                    tree_pay.insert("", tk.END, values=(d, f"{amount:,.2f}", desc or ""))
                    total_paid += amount
                tk.Label(
                    pay_card, text=f"Total paid to date: Rs. {total_paid:,.2f}",
                    font=("Segoe UI", 9, "bold"), bg=self.CARD, fg=self.GREEN,
                ).pack(anchor="w", padx=16, pady=(0, 14))
            else:
                tk.Label(
                    pay_card, text="No salary payments recorded yet for this teacher.",
                    font=("Segoe UI", 9), bg=self.CARD, fg=self.MUTED,
                ).pack(anchor="w", padx=16, pady=(0, 14))

        tk.Frame(body, bg=self.BG, height=16).pack()
