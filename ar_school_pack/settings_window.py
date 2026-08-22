"""
settings_window.py — Settings panel for AR School Management System.

Extracted from app.py so Settings can evolve independently (SMS API,
printer settings, etc.) without touching the main application shell.

Public API:
    build_settings_tab(parent, user_role, current_user, **callbacks)

Callbacks (all optional):
    log_activity(username, action)   — audit trail
    run_fee_automation(force=False)  — returns fee_automation result dict
    refresh_students()               — reload student directory after fee runs
"""

from __future__ import annotations

import tkinter as tk
from tkinter import ttk, messagebox, filedialog
from datetime import datetime

import db
import rbac
import theme
import branding
import results_engine
from security import hash_password


def build_settings_tab(
    parent,
    user_role: str,
    current_user: str,
    *,
    log_activity=None,
    run_fee_automation=None,
    refresh_students=None,
):
    """Build the full Settings notebook into *parent*.

    Returns the SettingsPanel instance (useful for tests / future hooks).
    """
    panel = SettingsPanel(
        parent,
        user_role,
        current_user,
        log_activity=log_activity,
        run_fee_automation=run_fee_automation,
        refresh_students=refresh_students,
    )
    return panel


class SettingsPanel:
    """Self-contained Settings UI with categorized notebook tabs."""

    def __init__(
        self,
        parent,
        user_role: str,
        current_user: str,
        *,
        log_activity=None,
        run_fee_automation=None,
        refresh_students=None,
    ):
        self.parent = parent
        self.user_role = user_role
        self.current_user = current_user
        self._log_activity = log_activity or (lambda *_a, **_k: None)
        self._run_fee_automation = run_fee_automation
        self._refresh_students = refresh_students

        nb = ttk.Notebook(parent)
        nb.pack(fill=tk.BOTH, expand=True, padx=12, pady=12)
        self.notebook = nb

        # ---- School Profile ----
        if rbac.can(user_role, "settings.branding"):
            tab_school = ttk.Frame(nb)
            nb.add(tab_school, text="School Profile")
            self._build_general_school_settings(tab_school)

            # ---- System Configurations (grouped) ----
            tab_system = ttk.Frame(nb)
            nb.add(tab_system, text="System Configurations")
            self._build_system_configurations(tab_system)

        # ---- User Management / RBAC ----
        if rbac.can(user_role, "settings.users"):
            tab_users = ttk.Frame(nb)
            nb.add(tab_users, text="User Management / RBAC")
            self._build_user_settings(tab_users)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------
    def _settings_form_row(self, parent, row, label, widget, padx=8, pady=6):
        tk.Label(
            parent,
            text=label,
            font=theme.FONT_BODY,
            bg=theme.WHITE,
            fg=theme.TEXT_MUTED,
            anchor="w",
            width=28,
        ).grid(row=row, column=0, sticky="w", padx=padx, pady=pady)
        widget.grid(row=row, column=1, sticky="ew", padx=padx, pady=pady)
        parent.grid_columnconfigure(1, weight=1)

    def _log(self, action: str):
        try:
            self._log_activity(self.current_user, action)
        except Exception:
            pass

    # ------------------------------------------------------------------
    # School Profile
    # ------------------------------------------------------------------
    def _build_general_school_settings(self, parent):
        outer = tk.Frame(parent, bg=theme.SILVER)
        outer.pack(fill=tk.BOTH, expand=True, padx=12, pady=12)
        card, body = theme.section_card(outer, "School Identity (headers, receipts, WhatsApp)")
        card.pack(fill=tk.BOTH, expand=True)

        self._sys_school = {}
        fields = [
            ("School Name", "school_name"),
            ("Phone", "school_phone"),
            ("Address", "school_address"),
            ("Email", "school_email"),
            ("Logo Path", "school_logo_path"),
        ]
        for i, (lbl, key) in enumerate(fields):
            ent = tk.Entry(body, font=theme.FONT_BODY, width=48)
            ent.insert(0, db.get_setting(key, "") or "")
            self._settings_form_row(body, i, lbl + ":", ent)
            self._sys_school[key] = ent

        btn_row = tk.Frame(body, bg=theme.WHITE)
        btn_row.grid(row=len(fields), column=0, columnspan=2, sticky="w", pady=(12, 4), padx=8)
        theme.primary_button(btn_row, "Browse Logo…", self._browse_school_logo, bg=theme.SLATE).pack(
            side=tk.LEFT, padx=(0, 8)
        )
        theme.primary_button(
            btn_row, "💾 Save School Info", self._save_general_school_settings, bg=theme.SUCCESS
        ).pack(side=tk.LEFT)

    def _browse_school_logo(self):
        path = filedialog.askopenfilename(filetypes=[("Image Files", "*.png *.jpg *.jpeg")])
        if path and "school_logo_path" in self._sys_school:
            self._sys_school["school_logo_path"].delete(0, tk.END)
            self._sys_school["school_logo_path"].insert(0, path)

    def _save_general_school_settings(self):
        vals = {k: e.get().strip() for k, e in self._sys_school.items()}
        db.set_settings_bulk(vals)
        try:
            branding.set_branding(
                vals.get("school_name", ""),
                vals.get("school_logo_path", ""),
                vals.get("school_address", ""),
                vals.get("school_phone", ""),
                vals.get("school_email", ""),
            )
        except Exception:
            pass
        self._log("Updated school info (system_settings)")
        messagebox.showinfo(
            "Saved",
            "School information saved. Window title updates on next login.",
            parent=self.parent,
        )

    # ------------------------------------------------------------------
    # System Configurations (scrollable group of sections)
    # ------------------------------------------------------------------
    def _build_system_configurations(self, parent):
        # Scrollable container so many sections fit on smaller screens.
        canvas = tk.Canvas(parent, bg=theme.SILVER, highlightthickness=0)
        scrollbar = ttk.Scrollbar(parent, orient="vertical", command=canvas.yview)
        inner = tk.Frame(canvas, bg=theme.SILVER)

        inner.bind(
            "<Configure>",
            lambda e: canvas.configure(scrollregion=canvas.bbox("all")),
        )
        canvas.create_window((0, 0), window=inner, anchor="nw")
        canvas.configure(yscrollcommand=scrollbar.set)

        canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)

        # Mouse-wheel support (Windows / Linux / macOS)
        def _on_mousewheel(event):
            delta = -1 * (event.delta // 120) if event.delta else (1 if event.num == 5 else -1)
            canvas.yview_scroll(delta, "units")

        canvas.bind_all("<MouseWheel>", _on_mousewheel)
        canvas.bind_all("<Button-4>", _on_mousewheel)
        canvas.bind_all("<Button-5>", _on_mousewheel)

        self._build_attendance_timing_settings(inner)
        self._build_fee_automation_settings(inner)
        self._build_message_template_settings(inner)
        self._build_backup_security_settings(inner)
        self._build_grading_settings(inner)

    # ---- Attendance ----
    def _build_attendance_timing_settings(self, parent):
        outer = tk.Frame(parent, bg=theme.SILVER)
        outer.pack(fill=tk.X, padx=12, pady=(12, 6))
        card, body = theme.section_card(outer, "School Day Timings & Auto-Absent")
        card.pack(fill=tk.X)

        self._sys_att = {}
        for i, (lbl, key, default) in enumerate(
            [
                ("School Start Time (HH:MM)", "school_start_time", "08:00"),
                ("Late Cutoff Time (HH:MM)", "late_threshold_time", "08:15"),
                ("School Closing Time (HH:MM)", "school_closing_time", "14:00"),
            ]
        ):
            ent = tk.Entry(body, font=theme.FONT_BODY, width=16)
            ent.insert(0, db.get_setting(key, default) or default)
            self._settings_form_row(body, i, lbl + ":", ent)
            self._sys_att[key] = ent

        self._var_auto_absent = tk.BooleanVar(
            value=str(db.get_setting("auto_absent_enabled", "1")) in ("1", "true", "True", "yes")
        )
        chk = tk.Checkbutton(
            body,
            text="Enable automatic end-of-day Absent marking",
            variable=self._var_auto_absent,
            bg=theme.WHITE,
            font=theme.FONT_BODY,
            activebackground=theme.WHITE,
        )
        chk.grid(row=3, column=0, columnspan=2, sticky="w", padx=8, pady=(10, 4))

        tk.Label(
            body,
            text=(
                "After closing time, active students with no attendance record for today "
                "are marked Absent (method: Auto System)."
            ),
            font=theme.FONT_SMALL,
            bg=theme.WHITE,
            fg=theme.TEXT_MUTED,
            wraplength=520,
            justify="left",
        ).grid(row=4, column=0, columnspan=2, sticky="w", padx=8, pady=(0, 8))

        theme.primary_button(
            body,
            "💾 Save Attendance Timings",
            self._save_attendance_timing_settings,
            bg=theme.SUCCESS,
        ).grid(row=5, column=0, columnspan=2, sticky="w", padx=8, pady=12)

    def _save_attendance_timing_settings(self):
        start = self._sys_att["school_start_time"].get().strip()
        late = self._sys_att["late_threshold_time"].get().strip()
        close = self._sys_att["school_closing_time"].get().strip()
        for label, val in (("Start", start), ("Late cutoff", late), ("Closing", close)):
            try:
                datetime.strptime(val, "%H:%M")
            except ValueError:
                messagebox.showerror(
                    "Invalid Time",
                    f"{label} time must be HH:MM (e.g. 08:15).",
                    parent=self.parent,
                )
                return
        db.set_settings_bulk(
            {
                "school_start_time": start,
                "late_threshold_time": late,
                "school_closing_time": close,
                "auto_absent_enabled": "1" if self._var_auto_absent.get() else "0",
            }
        )
        try:
            from smart_attendance import set_settings as att_set

            att_set(start, close, late_threshold_time=late)
        except Exception:
            pass
        self._log("Updated attendance timings (system_settings)")
        messagebox.showinfo("Saved", "Attendance timings saved.", parent=self.parent)

    # ---- Fee Automation ----
    def _build_fee_automation_settings(self, parent):
        outer = tk.Frame(parent, bg=theme.SILVER)
        outer.pack(fill=tk.X, padx=12, pady=6)
        card, body = theme.section_card(outer, "Auto Monthly Fee Cycle Generation")
        card.pack(fill=tk.X)

        self._var_auto_fee = tk.BooleanVar(
            value=str(db.get_setting("auto_fee_cycle_enabled", "1")) in ("1", "true", "True", "yes")
        )
        tk.Checkbutton(
            body,
            text="Automatically generate fee cycles for all Active students each month",
            variable=self._var_auto_fee,
            bg=theme.WHITE,
            font=theme.FONT_BODY,
            activebackground=theme.WHITE,
        ).grid(row=0, column=0, columnspan=2, sticky="w", padx=8, pady=(8, 4))

        self._ent_fee_due_day = tk.Entry(body, font=theme.FONT_BODY, width=8)
        self._ent_fee_due_day.insert(0, db.get_setting("auto_fee_due_day", "10") or "10")
        self._settings_form_row(body, 1, "Due Day of Month (1–28):", self._ent_fee_due_day)

        self._ent_fee_grace = tk.Entry(body, font=theme.FONT_BODY, width=8)
        self._ent_fee_grace.insert(0, db.get_setting("auto_fee_grace_days", "0") or "0")
        self._settings_form_row(body, 2, "Grace Period (days):", self._ent_fee_grace)

        last_run = db.get_setting("auto_fee_last_run", "") or "—"
        tk.Label(
            body,
            text=(
                "On Admin login (and if the app stays open past month-end), a cycle is created "
                "for every Active student who does not already have one for the current month. "
                "Existing cycles are never overwritten. Fee amount comes from each student's "
                f"Total Fee. Previous unpaid balance is carried forward.\nLast auto-run month: {last_run}"
            ),
            font=theme.FONT_SMALL,
            bg=theme.WHITE,
            fg=theme.TEXT_MUTED,
            wraplength=560,
            justify="left",
        ).grid(row=3, column=0, columnspan=2, sticky="w", padx=8, pady=(8, 4))

        btn_row = tk.Frame(body, bg=theme.WHITE)
        btn_row.grid(row=4, column=0, columnspan=2, sticky="w", padx=8, pady=12)
        theme.primary_button(
            btn_row,
            "💾 Save Fee Automation Settings",
            self._save_fee_automation_settings,
            bg=theme.SUCCESS,
        ).pack(side=tk.LEFT, padx=(0, 8))
        if rbac.can(self.user_role, "fee.cycle.generate"):
            theme.primary_button(
                btn_row, "▶ Run Now (This Month)", self._run_fee_automation_now, bg=theme.SLATE
            ).pack(side=tk.LEFT)

    def _save_fee_automation_settings(self):
        due_day = self._ent_fee_due_day.get().strip() or "10"
        grace = self._ent_fee_grace.get().strip() or "0"
        try:
            d = int(due_day)
            if not (1 <= d <= 28):
                raise ValueError
        except ValueError:
            messagebox.showerror(
                "Invalid Due Day", "Due day must be a number from 1 to 28.", parent=self.parent
            )
            return
        try:
            g = int(grace)
            if g < 0:
                raise ValueError
        except ValueError:
            messagebox.showerror(
                "Invalid Grace",
                "Grace period must be 0 or a positive whole number.",
                parent=self.parent,
            )
            return
        db.set_settings_bulk(
            {
                "auto_fee_cycle_enabled": "1" if self._var_auto_fee.get() else "0",
                "auto_fee_due_day": str(d),
                "auto_fee_grace_days": str(g),
            }
        )
        self._log("Updated fee automation settings")
        messagebox.showinfo("Saved", "Fee automation settings saved.", parent=self.parent)

    def _run_fee_automation_now(self):
        if not rbac.can(self.user_role, "fee.cycle.generate"):
            messagebox.showerror(
                "Permission Denied", "You cannot generate fee cycles.", parent=self.parent
            )
            return
        if not callable(self._run_fee_automation):
            messagebox.showwarning(
                "Fee Automation",
                "Fee automation runner is not available in this context.",
                parent=self.parent,
            )
            return
        result = self._run_fee_automation(force=True)
        if not result:
            return
        if result.get("success"):
            messagebox.showinfo(
                "Fee Automation Complete",
                f"Month {result['month']:02d}/{result['year']}\n"
                f"Created: {len(result.get('created') or [])}\n"
                f"Already existed: {len(result.get('skipped') or [])}\n"
                f"Errors: {len(result.get('errors') or [])}\n"
                f"Overdue refreshed: {result.get('overdue_changed', 0)}\n"
                f"Due date used: {result.get('due_date') or '—'}",
                parent=self.parent,
            )
            try:
                if callable(self._refresh_students):
                    self._refresh_students()
            except Exception:
                pass
        else:
            messagebox.showwarning(
                "Fee Automation",
                result.get("reason") or "Could not run fee automation.",
                parent=self.parent,
            )

    # ---- Message Templates ----
    def _build_message_template_settings(self, parent):
        outer = tk.Frame(parent, bg=theme.SILVER)
        outer.pack(fill=tk.X, padx=12, pady=6)

        top_card, top_body = theme.section_card(outer, "WhatsApp / SMS — Batch delay & variables")
        top_card.pack(fill=tk.X, pady=(0, 8))
        row = tk.Frame(top_body, bg=theme.WHITE)
        row.pack(fill=tk.X, pady=4)
        tk.Label(
            row,
            text="WhatsApp batch delay (seconds):",
            font=theme.FONT_BODY,
            bg=theme.WHITE,
            fg=theme.TEXT_MUTED,
        ).pack(side=tk.LEFT)
        self._ent_wa_delay = tk.Entry(row, font=theme.FONT_BODY, width=8)
        self._ent_wa_delay.insert(0, db.get_setting("whatsapp_batch_delay", "5") or "5")
        self._ent_wa_delay.pack(side=tk.LEFT, padx=8)
        tk.Label(
            top_body,
            text=(
                "Available variables: {student_name} {parent_name} {class_sec} {amount} "
                "{remaining} {status} {time} {school_name}"
            ),
            font=theme.FONT_SMALL,
            bg=theme.WHITE,
            fg=theme.TEXT_MUTED,
            wraplength=640,
            justify="left",
        ).pack(anchor="w", pady=(4, 2))

        self._msg_templates = {}
        templates = [
            ("Fee Payment Confirmation", "msg_template_fee_payment"),
            ("Admission Welcome", "msg_template_admission"),
            ("Attendance Notice", "msg_template_attendance"),
            ("Fee Reminder", "msg_template_fee_reminder"),
        ]
        for title, key in templates:
            card, body = theme.section_card(outer, title)
            card.pack(fill=tk.X, pady=(0, 6))
            txt = tk.Text(
                body,
                height=4,
                wrap="word",
                font=theme.FONT_BODY,
                bg="#f8fafc",
                relief="solid",
                bd=1,
                padx=6,
                pady=4,
            )
            txt.insert("1.0", db.get_setting(key, "") or "")
            txt.pack(fill=tk.X, expand=True)
            self._msg_templates[key] = txt

        theme.primary_button(
            outer, "💾 Save Message Templates", self._save_message_templates, bg=theme.SUCCESS
        ).pack(anchor="w", pady=(4, 8), padx=12)

    def _save_message_templates(self):
        delay = self._ent_wa_delay.get().strip() or "5"
        try:
            int(delay)
        except ValueError:
            messagebox.showerror(
                "Invalid Delay",
                "Batch delay must be a whole number of seconds.",
                parent=self.parent,
            )
            return
        mapping = {"whatsapp_batch_delay": delay}
        for key, txt in self._msg_templates.items():
            mapping[key] = txt.get("1.0", "end").rstrip("\n")
        db.set_settings_bulk(mapping)
        self._log("Updated WhatsApp/SMS message templates")
        messagebox.showinfo("Saved", "Message templates saved.", parent=self.parent)

    # ---- Backup & Security ----
    def _build_backup_security_settings(self, parent):
        outer = tk.Frame(parent, bg=theme.SILVER)
        outer.pack(fill=tk.X, padx=12, pady=6)
        card, body = theme.section_card(outer, "Automatic Backup")
        card.pack(fill=tk.X)

        self._var_auto_backup = tk.BooleanVar(
            value=str(db.get_setting("auto_backup_on_exit", "1")) in ("1", "true", "True", "yes")
        )
        tk.Checkbutton(
            body,
            text="Create a backup automatically on logout / exit",
            variable=self._var_auto_backup,
            bg=theme.WHITE,
            font=theme.FONT_BODY,
            activebackground=theme.WHITE,
        ).grid(row=0, column=0, columnspan=2, sticky="w", padx=8, pady=(6, 4))

        self._ent_backup_path = tk.Entry(body, font=theme.FONT_BODY, width=48)
        self._ent_backup_path.insert(0, db.get_setting("backup_folder_path", "") or "")
        self._settings_form_row(body, 1, "Backup Folder Path:", self._ent_backup_path)

        btn_row = tk.Frame(body, bg=theme.WHITE)
        btn_row.grid(row=2, column=0, columnspan=2, sticky="w", padx=8, pady=(8, 4))
        theme.primary_button(btn_row, "Browse Folder…", self._browse_backup_folder, bg=theme.SLATE).pack(
            side=tk.LEFT, padx=(0, 8)
        )
        theme.primary_button(
            btn_row, "💾 Save Backup Settings", self._save_backup_settings, bg=theme.SUCCESS
        ).pack(side=tk.LEFT)

        tk.Label(
            body,
            text="If the folder is empty, backups are written next to the database under backups/.",
            font=theme.FONT_SMALL,
            bg=theme.WHITE,
            fg=theme.TEXT_MUTED,
            wraplength=520,
            justify="left",
        ).grid(row=3, column=0, columnspan=2, sticky="w", padx=8, pady=(4, 8))

    def _browse_backup_folder(self):
        path = filedialog.askdirectory(title="Select Backup Folder")
        if path:
            self._ent_backup_path.delete(0, tk.END)
            self._ent_backup_path.insert(0, path)

    def _save_backup_settings(self):
        db.set_settings_bulk(
            {
                "auto_backup_on_exit": "1" if self._var_auto_backup.get() else "0",
                "backup_folder_path": self._ent_backup_path.get().strip(),
            }
        )
        self._log("Updated backup / security settings")
        messagebox.showinfo("Saved", "Backup settings saved.", parent=self.parent)

    # ---- Grading ----
    def _build_grading_settings(self, parent):
        outer = tk.Frame(parent, bg=theme.SILVER)
        outer.pack(fill=tk.X, padx=12, pady=(6, 16))

        criteria = results_engine.get_passing_criteria()
        frame = tk.LabelFrame(
            outer, text="Pass/Fail Criteria", font=("Segoe UI", 10, "bold"), padx=10, pady=10
        )
        frame.pack(fill=tk.X, pady=(0, 8))

        tk.Label(frame, text="Minimum Overall Percentage to Pass:").grid(
            row=0, column=0, sticky="w", pady=5
        )
        self.ent_min_overall = tk.Entry(frame, width=10)
        self.ent_min_overall.insert(0, str(criteria["min_overall_percent"]))
        self.ent_min_overall.grid(row=0, column=1, pady=5)

        self.var_require_each = tk.BooleanVar(value=criteria["require_pass_each_subject"])
        tk.Checkbutton(
            frame, text="Require passing each individual subject", variable=self.var_require_each
        ).grid(row=1, column=0, columnspan=2, sticky="w")

        tk.Label(frame, text="Minimum % per Subject:").grid(row=2, column=0, sticky="w", pady=5)
        self.ent_min_subject = tk.Entry(frame, width=10)
        self.ent_min_subject.insert(0, str(criteria["min_subject_percent"]))
        self.ent_min_subject.grid(row=2, column=1, pady=5)

        tk.Button(
            frame,
            text="Save Pass/Fail Rules",
            command=self._save_passing_criteria,
            bg="#16a34a",
            fg="white",
            font=("Segoe UI", 9, "bold"),
        ).grid(row=3, column=0, pady=10, sticky="w")

        grade_frame = tk.LabelFrame(
            outer,
            text="Grade Bands (Grade, Min %, Max %)",
            font=("Segoe UI", 10, "bold"),
            padx=10,
            pady=10,
        )
        grade_frame.pack(fill=tk.X)

        self.tree_grades = ttk.Treeview(
            grade_frame, columns=("grade", "min", "max"), show="headings", height=6
        )
        for col, h in [("grade", "Grade"), ("min", "Min %"), ("max", "Max %")]:
            self.tree_grades.heading(col, text=h)
            self.tree_grades.column(col, anchor="center")
        self.tree_grades.pack(fill=tk.X, pady=5)
        self._load_grade_table()

        add_frame = tk.Frame(grade_frame)
        add_frame.pack(fill=tk.X, pady=5)
        tk.Label(add_frame, text="Grade:").pack(side=tk.LEFT, padx=5)
        self.ent_new_grade = tk.Entry(add_frame, width=6)
        self.ent_new_grade.pack(side=tk.LEFT, padx=5)
        tk.Label(add_frame, text="Min %:").pack(side=tk.LEFT, padx=5)
        self.ent_new_grade_min = tk.Entry(add_frame, width=6)
        self.ent_new_grade_min.pack(side=tk.LEFT, padx=5)
        tk.Label(add_frame, text="Max %:").pack(side=tk.LEFT, padx=5)
        self.ent_new_grade_max = tk.Entry(add_frame, width=6)
        self.ent_new_grade_max.pack(side=tk.LEFT, padx=5)
        tk.Button(
            add_frame, text="Add Grade Band", command=self._add_grade_band, bg="#2563eb", fg="white"
        ).pack(side=tk.LEFT, padx=10)
        tk.Button(
            add_frame,
            text="Clear All Bands",
            command=self._clear_grade_bands,
            bg="#dc2626",
            fg="white",
        ).pack(side=tk.LEFT, padx=5)

    def _load_grade_table(self):
        self.tree_grades.delete(*self.tree_grades.get_children())
        for grade, lo, hi in results_engine.get_grading_bands():
            self.tree_grades.insert("", tk.END, values=(grade, lo, hi))

    def _add_grade_band(self):
        try:
            grade = self.ent_new_grade.get().strip()
            lo = float(self.ent_new_grade_min.get())
            hi = float(self.ent_new_grade_max.get())
        except ValueError:
            messagebox.showerror("Error", "Enter valid numeric percentages.", parent=self.parent)
            return
        existing = results_engine.get_grading_bands()
        bands = [(g, mn, mx) for g, mn, mx in existing] + [(grade, lo, hi)]
        results_engine.set_grading_bands(bands)
        self._load_grade_table()

    def _clear_grade_bands(self):
        if messagebox.askyesno("Confirm", "Remove all grade bands?", parent=self.parent):
            results_engine.set_grading_bands([])
            self._load_grade_table()

    def _save_passing_criteria(self):
        try:
            min_overall = float(self.ent_min_overall.get())
            min_subject = float(self.ent_min_subject.get())
        except ValueError:
            messagebox.showerror("Error", "Enter valid numeric percentages.", parent=self.parent)
            return
        results_engine.set_passing_criteria(
            min_overall, self.var_require_each.get(), min_subject
        )
        self._log("Updated pass/fail grading criteria")
        messagebox.showinfo("Saved", "Pass/Fail criteria updated.", parent=self.parent)

    # ------------------------------------------------------------------
    # User Management / RBAC
    # ------------------------------------------------------------------
    def _build_user_settings(self, parent):
        frame = tk.LabelFrame(
            parent, text="Create User Account", font=("Segoe UI", 10, "bold"), padx=10, pady=10
        )
        frame.pack(fill=tk.X, padx=10, pady=10)

        tk.Label(frame, text="Username:").grid(row=0, column=0, padx=5, pady=5)
        self.ent_new_username = tk.Entry(frame)
        self.ent_new_username.grid(row=0, column=1, padx=5, pady=5)

        tk.Label(frame, text="Password:").grid(row=1, column=0, padx=5, pady=5)
        self.ent_new_password = tk.Entry(frame, show="*")
        self.ent_new_password.grid(row=1, column=1, padx=5, pady=5)

        tk.Label(frame, text="Role:").grid(row=2, column=0, padx=5, pady=5)
        self.combo_new_role = ttk.Combobox(frame, values=rbac.all_roles(), state="readonly")
        self.combo_new_role.current(0)
        self.combo_new_role.grid(row=2, column=1, padx=5, pady=5)

        tk.Button(
            frame,
            text="Create User",
            command=self._create_user,
            bg="#16a34a",
            fg="white",
            font=("Segoe UI", 9, "bold"),
        ).grid(row=3, column=0, columnspan=2, pady=10)

        self.tree_users = ttk.Treeview(
            parent, columns=("id", "username", "role", "status"), show="headings"
        )
        for col, h in [
            ("id", "ID"),
            ("username", "Username"),
            ("role", "Role"),
            ("status", "Status"),
        ]:
            self.tree_users.heading(col, text=h)
            self.tree_users.column(col, anchor="center")
        self.tree_users.pack(fill=tk.BOTH, expand=True, padx=10, pady=10)

        user_actions = tk.Frame(parent)
        user_actions.pack(fill=tk.X, padx=10, pady=(0, 10))
        tk.Button(
            user_actions,
            text="Deactivate Selected User",
            command=lambda: self._set_user_active(False),
            bg="#dc2626",
            fg="white",
            font=("Segoe UI", 9, "bold"),
        ).pack(side=tk.LEFT, padx=(0, 10))
        tk.Button(
            user_actions,
            text="Reactivate Selected User",
            command=lambda: self._set_user_active(True),
            bg="#16a34a",
            fg="white",
            font=("Segoe UI", 9, "bold"),
        ).pack(side=tk.LEFT)

        self._load_users_table()

    def _load_users_table(self):
        self.tree_users.delete(*self.tree_users.get_children())
        for r_id, username, role, is_active in db.run(
            "SELECT id, username, role, COALESCE(is_active,1) FROM users", fetchall=True
        ):
            self.tree_users.insert(
                "",
                tk.END,
                values=(r_id, username, role, "Active" if is_active else "Deactivated"),
            )

    def _set_user_active(self, make_active):
        sel = self.tree_users.selection()
        if not sel:
            messagebox.showerror("Error", "Select a user from the list first.", parent=self.parent)
            return
        vals = self.tree_users.item(sel[0], "values")
        u_id, username, role = vals[0], vals[1], vals[2]

        if not make_active:
            if username == self.current_user:
                messagebox.showerror(
                    "Error",
                    "You cannot deactivate the account you're currently logged in with.",
                    parent=self.parent,
                )
                return
            if role == "Admin":
                active_admins = db.run(
                    "SELECT COUNT(*) FROM users WHERE role='Admin' AND COALESCE(is_active,1)=1",
                    fetchone=True,
                )[0]
                if active_admins <= 1:
                    messagebox.showerror(
                        "Error",
                        "Cannot deactivate the last active Admin account — the system would "
                        "become inaccessible to manage.",
                        parent=self.parent,
                    )
                    return
            if not messagebox.askyesno(
                "Confirm",
                f"Deactivate user '{username}'? They will no longer be able to log in.",
                parent=self.parent,
            ):
                return

        db.run(
            "UPDATE users SET is_active=? WHERE id=?",
            (1 if make_active else 0, u_id),
            commit=True,
        )
        self._log(
            f"{'Reactivated' if make_active else 'Deactivated'} user account '{username}'"
        )
        self._load_users_table()
        messagebox.showinfo(
            "Success",
            f"User '{username}' {'reactivated' if make_active else 'deactivated'}.",
            parent=self.parent,
        )

    def _create_user(self):
        username = self.ent_new_username.get().strip()
        password = self.ent_new_password.get().strip()
        role = self.combo_new_role.get()
        if not username or not password:
            messagebox.showerror(
                "Error", "Username and password required.", parent=self.parent
            )
            return
        try:
            db.run(
                "INSERT INTO users (username, password, role, is_hashed) VALUES (?, ?, ?, 1)",
                (username, hash_password(password), role),
                commit=True,
            )
        except Exception:
            messagebox.showerror("Error", "Username already exists.", parent=self.parent)
            return
        self._log(f"Created new user account '{username}' with role {role}")
        self.ent_new_username.delete(0, tk.END)
        self.ent_new_password.delete(0, tk.END)
        self._load_users_table()
        messagebox.showinfo(
            "Success", f"User '{username}' created with role {role}.", parent=self.parent
        )
