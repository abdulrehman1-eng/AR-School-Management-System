"""
timetable_window.py — Professional Class Timetable module.

STANDALONE MODULE. Same `timetable` table as the legacy inline tab in app.py.
Embed into the main shell via build_timetable_into(), or open as a popup with
launch_timetable_window().

Features:
  - Add / edit / remove period assignments
  - Class + Day filters
  - Teacher list from teachers table (fallback free-text)
  - Distinct classes from students
  - Conflict detection (class slot + teacher double-booked)
  - Weekly grid view by class
  - Clean themed UI consistent with Smart Attendance / Finance
"""

import tkinter as tk
from tkinter import ttk, messagebox

import db
import rbac
import theme

DAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday"]
DEFAULT_SLOTS = [
    "08:00 - 08:45",
    "08:45 - 09:30",
    "09:30 - 10:15",
    "10:30 - 11:15",
    "11:15 - 12:00",
    "12:00 - 12:45",
    "13:30 - 14:15",
    "14:15 - 15:00",
]


def _class_options():
    rows = db.run(
        "SELECT DISTINCT class_sec FROM students "
        "WHERE class_sec IS NOT NULL AND TRIM(class_sec) <> '' "
        "ORDER BY class_sec",
        fetchall=True,
    ) or []
    classes = [r[0] for r in rows if r and r[0]]
    # Also include classes already used in timetable
    tt = db.run(
        "SELECT DISTINCT class_name FROM timetable "
        "WHERE class_name IS NOT NULL AND TRIM(class_name) <> '' "
        "ORDER BY class_name",
        fetchall=True,
    ) or []
    for r in tt:
        if r and r[0] and r[0] not in classes:
            classes.append(r[0])
    return sorted(classes, key=lambda x: (len(x), x))


def _teacher_options():
    rows = db.run(
        "SELECT name FROM teachers WHERE name IS NOT NULL AND TRIM(name) <> '' ORDER BY name",
        fetchall=True,
    ) or []
    return [r[0] for r in rows if r and r[0]]


def _time_slot_options():
    slots = list(DEFAULT_SLOTS)
    rows = db.run(
        "SELECT DISTINCT time_slot FROM timetable "
        "WHERE time_slot IS NOT NULL AND TRIM(time_slot) <> '' "
        "ORDER BY time_slot",
        fetchall=True,
    ) or []
    for r in rows:
        if r and r[0] and r[0] not in slots:
            slots.append(r[0])
    return slots


class TimetableWorkspace:
    """Embeddable timetable manager (used inside main app tab or popup)."""

    def __init__(self, parent, user_role, current_user, log_activity=None):
        self.parent = parent
        self.user_role = user_role
        self.current_user = current_user
        self.log_activity = log_activity
        self.can_manage = rbac.can(user_role, "timetable.manage")
        self._edit_id = None  # when set, save updates instead of insert

        for child in parent.winfo_children():
            child.destroy()

        self.root_frame = tk.Frame(parent, bg=theme.SILVER)
        self.root_frame.pack(fill=tk.BOTH, expand=True)
        self._build_ui()
        self.load_table()

    def _log(self, action):
        if callable(self.log_activity):
            try:
                self.log_activity(self.current_user, action)
            except Exception:
                pass

    # ------------------------------------------------------------------ UI
    def _build_ui(self):
        body = self.root_frame

        header = tk.Frame(body, bg=theme.NAVY, padx=16, pady=12)
        header.pack(fill=tk.X)
        tk.Label(
            header, text="🕐  CLASS TIMETABLE",
            font=theme.FONT_H1, bg=theme.NAVY, fg="white",
        ).pack(anchor="w")
        tk.Label(
            header,
            text="Assign periods · detect conflicts · filter by class or day",
            font=theme.FONT_SMALL, bg=theme.NAVY, fg="#94a3b8",
        ).pack(anchor="w")

        content = tk.Frame(body, bg=theme.SILVER, padx=14, pady=12)
        content.pack(fill=tk.BOTH, expand=True)

        # ---- Add / Edit form ----
        form_card, form_body = theme.section_card(
            content, "Add / Edit Period" if self.can_manage else "Period Details (view only)"
        )
        form_card.pack(fill=tk.X, pady=(0, 10))

        grid = tk.Frame(form_body, bg=theme.WHITE)
        grid.pack(fill=tk.X)

        classes = _class_options()
        teachers = _teacher_options()
        slots = _time_slot_options()

        tk.Label(grid, text="Class *", bg=theme.WHITE, font=theme.FONT_SMALL).grid(
            row=0, column=0, sticky="w", padx=(0, 6), pady=4
        )
        self.cmb_class = ttk.Combobox(grid, values=classes, width=14)
        self.cmb_class.grid(row=1, column=0, padx=(0, 10), pady=(0, 6), sticky="w")

        tk.Label(grid, text="Day *", bg=theme.WHITE, font=theme.FONT_SMALL).grid(
            row=0, column=1, sticky="w", padx=(0, 6), pady=4
        )
        self.cmb_day = ttk.Combobox(grid, values=DAYS, width=12, state="readonly")
        self.cmb_day.current(0)
        self.cmb_day.grid(row=1, column=1, padx=(0, 10), pady=(0, 6), sticky="w")

        tk.Label(grid, text="Time Slot *", bg=theme.WHITE, font=theme.FONT_SMALL).grid(
            row=0, column=2, sticky="w", padx=(0, 6), pady=4
        )
        self.cmb_time = ttk.Combobox(grid, values=slots, width=16)
        self.cmb_time.set(DEFAULT_SLOTS[0])
        self.cmb_time.grid(row=1, column=2, padx=(0, 10), pady=(0, 6), sticky="w")

        tk.Label(grid, text="Subject *", bg=theme.WHITE, font=theme.FONT_SMALL).grid(
            row=0, column=3, sticky="w", padx=(0, 6), pady=4
        )
        self.ent_subject = tk.Entry(grid, font=theme.FONT_BODY, width=16)
        self.ent_subject.grid(row=1, column=3, padx=(0, 10), pady=(0, 6), sticky="w")

        tk.Label(grid, text="Teacher", bg=theme.WHITE, font=theme.FONT_SMALL).grid(
            row=0, column=4, sticky="w", padx=(0, 6), pady=4
        )
        self.cmb_teacher = ttk.Combobox(grid, values=teachers, width=16)
        self.cmb_teacher.grid(row=1, column=4, padx=(0, 10), pady=(0, 6), sticky="w")

        btn_row = tk.Frame(form_body, bg=theme.WHITE)
        btn_row.pack(fill=tk.X, pady=(6, 0))
        if self.can_manage:
            theme.primary_button(btn_row, "💾 Save Period", self.save_period).pack(
                side=tk.LEFT, padx=(0, 8)
            )
            theme.primary_button(
                btn_row, "Clear", self.clear_form, bg=theme.SLATE
            ).pack(side=tk.LEFT, padx=(0, 8))
            theme.primary_button(
                btn_row, "🗑 Remove Selected", self.remove_selected, bg=theme.DANGER
            ).pack(side=tk.LEFT, padx=(0, 8))
        self.lbl_form_status = tk.Label(
            btn_row, text="", bg=theme.WHITE, fg=theme.TEXT_MUTED, font=theme.FONT_SMALL
        )
        self.lbl_form_status.pack(side=tk.LEFT, padx=10)

        # ---- Filters ----
        filt_card, filt_body = theme.section_card(content, "Filters & Views")
        filt_card.pack(fill=tk.X, pady=(0, 10))
        frow = tk.Frame(filt_body, bg=theme.WHITE)
        frow.pack(fill=tk.X)

        tk.Label(frow, text="Class:", bg=theme.WHITE, font=theme.FONT_SMALL).pack(
            side=tk.LEFT
        )
        self.cmb_filter_class = ttk.Combobox(
            frow, values=["All Classes"] + classes, width=14, state="readonly"
        )
        self.cmb_filter_class.set("All Classes")
        self.cmb_filter_class.pack(side=tk.LEFT, padx=(4, 12))
        self.cmb_filter_class.bind("<<ComboboxSelected>>", lambda e: self.load_table())

        tk.Label(frow, text="Day:", bg=theme.WHITE, font=theme.FONT_SMALL).pack(
            side=tk.LEFT
        )
        self.cmb_filter_day = ttk.Combobox(
            frow, values=["All Days"] + DAYS, width=12, state="readonly"
        )
        self.cmb_filter_day.set("All Days")
        self.cmb_filter_day.pack(side=tk.LEFT, padx=(4, 12))
        self.cmb_filter_day.bind("<<ComboboxSelected>>", lambda e: self.load_table())

        theme.primary_button(frow, "Refresh", self.load_table, bg=theme.SLATE).pack(
            side=tk.LEFT, padx=(0, 8)
        )
        theme.primary_button(
            frow, "📅 Weekly Grid", self._show_weekly_grid, bg=theme.SLATE
        ).pack(side=tk.LEFT, padx=(0, 8))

        # ---- Table ----
        list_card, list_body = theme.section_card(content, "Scheduled Periods")
        list_card.pack(fill=tk.BOTH, expand=True)

        cols = ("id", "class", "day", "time", "subject", "teacher")
        self.tree = ttk.Treeview(list_body, columns=cols, show="headings", height=14)
        headers = {
            "id": ("ID", 50),
            "class": ("Class", 100),
            "day": ("Day", 100),
            "time": ("Time Slot", 120),
            "subject": ("Subject", 140),
            "teacher": ("Teacher", 140),
        }
        for c in cols:
            h, w = headers[c]
            self.tree.heading(c, text=h)
            self.tree.column(c, width=w, anchor="center")
        scroll = ttk.Scrollbar(list_body, orient=tk.VERTICAL, command=self.tree.yview)
        self.tree.configure(yscrollcommand=scroll.set)
        scroll.pack(side=tk.RIGHT, fill=tk.Y)
        self.tree.pack(fill=tk.BOTH, expand=True)
        self.tree.bind("<ButtonRelease-1>", self._on_select)
        self.tree.bind("<Double-1>", self._on_select)

    # ------------------------------------------------------------------ data
    def load_table(self):
        self.tree.delete(*self.tree.get_children())
        q = "SELECT id, class_name, day_name, time_slot, subject_name, teacher_name FROM timetable WHERE 1=1"
        params = []
        fc = (self.cmb_filter_class.get() or "All Classes").strip()
        fd = (self.cmb_filter_day.get() or "All Days").strip()
        if fc and fc != "All Classes":
            q += " AND class_name=?"
            params.append(fc)
        if fd and fd != "All Days":
            q += " AND day_name=?"
            params.append(fd)
        # Order: day order, then time
        q += " ORDER BY CASE day_name "
        for i, d in enumerate(DAYS):
            q += f"WHEN '{d}' THEN {i} "
        q += "ELSE 99 END, time_slot, class_name"
        try:
            if params:
                rows = db.run(q, tuple(params), fetchall=True) or []
            else:
                rows = db.run(q, fetchall=True) or []
        except Exception as exc:
            print(f"[Timetable] load_table error: {exc}")
            rows = []
        for r in rows:
            self.tree.insert("", tk.END, values=r)

    def _on_select(self, _ev=None):
        sel = self.tree.focus()
        if not sel:
            return
        vals = self.tree.item(sel, "values")
        if not vals:
            return
        self._edit_id = vals[0]
        self.cmb_class.set(vals[1] or "")
        self.cmb_day.set(vals[2] or DAYS[0])
        self.cmb_time.set(vals[3] or "")
        self.ent_subject.delete(0, tk.END)
        self.ent_subject.insert(0, vals[4] or "")
        self.cmb_teacher.set(vals[5] or "")
        self.lbl_form_status.config(
            text=f"Editing period #{self._edit_id}", fg=theme.BRAND_BLUE
        )

    def clear_form(self):
        self._edit_id = None
        self.cmb_class.set("")
        self.cmb_day.current(0)
        self.cmb_time.set(DEFAULT_SLOTS[0])
        self.ent_subject.delete(0, tk.END)
        self.cmb_teacher.set("")
        self.lbl_form_status.config(text="", fg=theme.TEXT_MUTED)

    def save_period(self):
        if not self.can_manage:
            messagebox.showerror(
                "Permission Denied", "Not allowed to manage timetable.", parent=self.parent
            )
            return
        cls = (self.cmb_class.get() or "").strip()
        day = (self.cmb_day.get() or "").strip()
        time_slot = (self.cmb_time.get() or "").strip()
        sub = (self.ent_subject.get() or "").strip()
        tch = (self.cmb_teacher.get() or "").strip()

        if not (cls and day and time_slot and sub):
            messagebox.showerror(
                "Missing Fields",
                "Class, Day, Time Slot and Subject are required.",
                parent=self.parent,
            )
            return

        # Conflict checks (exclude current row when editing)
        warnings = []
        same_class = db.run(
            "SELECT id, subject_name, teacher_name FROM timetable "
            "WHERE class_name=? AND day_name=? AND time_slot=?",
            (cls, day, time_slot),
            fetchone=True,
        )
        if same_class and str(same_class[0]) != str(self._edit_id or ""):
            warnings.append(
                f"Class {cls} already has '{same_class[1]}' with "
                f"{same_class[2] or '(no teacher)'} on {day} at {time_slot}."
            )
        if tch:
            teacher_busy = db.run(
                "SELECT id, class_name, subject_name FROM timetable "
                "WHERE teacher_name=? AND day_name=? AND time_slot=? AND class_name!=?",
                (tch, day, time_slot, cls),
                fetchone=True,
            )
            if teacher_busy and str(teacher_busy[0]) != str(self._edit_id or ""):
                warnings.append(
                    f"Teacher '{tch}' is already with Class {teacher_busy[1]} "
                    f"('{teacher_busy[2]}') on {day} at {time_slot}."
                )

        if warnings:
            if not messagebox.askyesno(
                "Scheduling Conflict",
                "Conflict(s) found:\n\n"
                + "\n".join(f"• {w}" for w in warnings)
                + "\n\nSave anyway?",
                parent=self.parent,
            ):
                return

        if self._edit_id:
            db.run(
                "UPDATE timetable SET class_name=?, day_name=?, time_slot=?, "
                "subject_name=?, teacher_name=? WHERE id=?",
                (cls, day, time_slot, sub, tch, self._edit_id),
                commit=True,
            )
            self._log(
                f"Updated timetable id={self._edit_id}: Class {cls}, {day} {time_slot}, {sub}, {tch}"
            )
            self.lbl_form_status.config(text="Updated.", fg=theme.SUCCESS)
        else:
            db.run(
                "INSERT INTO timetable (class_name, day_name, time_slot, subject_name, teacher_name) "
                "VALUES (?, ?, ?, ?, ?)",
                (cls, day, time_slot, sub, tch),
                commit=True,
            )
            self._log(
                f"Added timetable: Class {cls}, {day} {time_slot}, {sub}, {tch}"
            )
            self.lbl_form_status.config(text="Saved.", fg=theme.SUCCESS)

        # Refresh class/teacher combos in case new values were typed
        self.cmb_class["values"] = _class_options()
        self.cmb_filter_class["values"] = ["All Classes"] + _class_options()
        self.cmb_teacher["values"] = _teacher_options()
        self.cmb_time["values"] = _time_slot_options()

        self.clear_form()
        self.load_table()

    def remove_selected(self):
        if not self.can_manage:
            messagebox.showerror(
                "Permission Denied", "Not allowed to manage timetable.", parent=self.parent
            )
            return
        sel = self.tree.selection()
        if not sel:
            messagebox.showerror(
                "No Selection", "Select a timetable row first.", parent=self.parent
            )
            return
        vals = self.tree.item(sel[0], "values")
        tt_id = vals[0]
        if not messagebox.askyesno(
            "Confirm Remove",
            f"Remove this assignment?\n\n"
            f"Class {vals[1]} · {vals[2]} · {vals[3]}\n"
            f"{vals[4]} — {vals[5] or 'No teacher'}",
            parent=self.parent,
        ):
            return
        db.run("DELETE FROM timetable WHERE id=?", (tt_id,), commit=True)
        self._log(f"Removed timetable assignment id={tt_id}")
        self.clear_form()
        self.load_table()

    # ------------------------------------------------------------------ weekly grid
    def _show_weekly_grid(self):
        cls = (self.cmb_filter_class.get() or "").strip()
        if not cls or cls == "All Classes":
            # Prefer form class, else ask
            cls = (self.cmb_class.get() or "").strip()
        if not cls:
            messagebox.showinfo(
                "Select Class",
                "Choose a class in the filter (or form) to view its weekly grid.",
                parent=self.parent,
            )
            return

        win = tk.Toplevel(self.parent)
        win.title(f"Weekly Timetable — {cls}")
        win.geometry("920x480")
        win.config(bg=theme.SILVER)
        win.transient(self.parent.winfo_toplevel())

        header = tk.Frame(win, bg=theme.NAVY, padx=16, pady=10)
        header.pack(fill=tk.X)
        tk.Label(
            header, text=f"📅  Weekly Grid — Class {cls}",
            font=theme.FONT_H1, bg=theme.NAVY, fg="white",
        ).pack(anchor="w")

        body = tk.Frame(win, bg=theme.WHITE, padx=12, pady=12)
        body.pack(fill=tk.BOTH, expand=True)

        rows = db.run(
            "SELECT day_name, time_slot, subject_name, teacher_name FROM timetable "
            "WHERE class_name=? ORDER BY time_slot",
            (cls,),
            fetchall=True,
        ) or []

        # Collect unique time slots in order
        times = []
        for _, t, _, _ in rows:
            if t and t not in times:
                times.append(t)
        # Prefer DEFAULT_SLOTS order when possible
        ordered = [s for s in DEFAULT_SLOTS if s in times]
        for t in times:
            if t not in ordered:
                ordered.append(t)
        if not ordered:
            ordered = list(DEFAULT_SLOTS[:6])

        # cell map: (day, time) -> "Subject\nTeacher"
        cell = {}
        for day, t, sub, tch in rows:
            label = sub or "—"
            if tch:
                label += f"\n{tch}"
            cell[(day, t)] = label

        # Header row
        tk.Label(
            body, text="Time", font=theme.FONT_BODY_BOLD,
            bg=theme.NAVY, fg="white", width=14, relief="solid", bd=1,
        ).grid(row=0, column=0, sticky="nsew", padx=1, pady=1)
        for c, day in enumerate(DAYS, start=1):
            tk.Label(
                body, text=day[:3], font=theme.FONT_BODY_BOLD,
                bg=theme.NAVY, fg="white", width=12, relief="solid", bd=1,
            ).grid(row=0, column=c, sticky="nsew", padx=1, pady=1)

        for r, t in enumerate(ordered, start=1):
            tk.Label(
                body, text=t, font=theme.FONT_SMALL,
                bg="#e2e8f0", fg=theme.TEXT_DARK, width=14, relief="solid", bd=1,
            ).grid(row=r, column=0, sticky="nsew", padx=1, pady=1)
            for c, day in enumerate(DAYS, start=1):
                text = cell.get((day, t), "")
                bg = "#ecfdf5" if text else theme.WHITE
                tk.Label(
                    body, text=text, font=theme.FONT_SMALL,
                    bg=bg, fg=theme.TEXT_DARK, width=12, height=2,
                    relief="solid", bd=1, justify="center",
                ).grid(row=r, column=c, sticky="nsew", padx=1, pady=1)

        if not rows:
            tk.Label(
                body,
                text=f"No periods scheduled for class “{cls}” yet.",
                font=theme.FONT_BODY, bg=theme.WHITE, fg=theme.TEXT_MUTED,
            ).grid(row=len(ordered) + 1, column=0, columnspan=7, pady=16)


def build_timetable_into(parent, user_role, current_user, log_activity=None):
    """Embed the professional Timetable workspace into a parent frame (main tab)."""
    return TimetableWorkspace(parent, user_role, current_user, log_activity=log_activity)


def launch_timetable_window(parent, user_role, current_user, log_activity=None):
    """Open Timetable as a dedicated popup window."""
    win = tk.Toplevel(parent)
    win.title("Class Timetable — AR School Management System")
    win.geometry("1100x720")
    win.minsize(900, 560)
    win.config(bg=theme.SILVER)
    win.transient(parent)
    build_timetable_into(win, user_role, current_user, log_activity=log_activity)
    return win
