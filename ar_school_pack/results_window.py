"""
results_window.py — Professional Results & Academics UI module.

Uses results_engine.py for all grade/percentage/pass-fail calculations.
Uses reports.py for marksheet PDF generation.
Does not modify db schema; marks upsert is done by SELECT-then-UPDATE/INSERT
on (student_id, exam_type, subject_name).
"""

from __future__ import annotations

import os
import tkinter as tk
from tkinter import ttk, messagebox, filedialog
from datetime import datetime
from typing import Any, Optional

import db
import rbac
import theme
import results_engine
import reports

try:
    import matplotlib.pyplot as plt
    HAS_MATPLOTLIB = True
except ImportError:
    HAS_MATPLOTLIB = False


def _safe_float(raw_text, field_label, default=None):
    """Parse a numeric form field. Returns (value, ok)."""
    text = (raw_text or "").strip()
    if not text:
        if default is not None:
            return default, True
        messagebox.showerror("Invalid Input", f"{field_label} is required.")
        return None, False
    try:
        return float(text), True
    except ValueError:
        messagebox.showerror(
            "Invalid Input",
            f"{field_label} must be a valid number (e.g. 75 or 75.5).",
        )
        return None, False


def _log_activity(username, action):
    try:
        db.run(
            "INSERT INTO audit_logs (username, action, timestamp) VALUES (?, ?, ?)",
            (username, action, datetime.now().strftime("%Y-%m-%d %H:%M:%S")),
            commit=True,
        )
    except Exception as e:
        print(f"Audit Log Error: {e}")


# ---------------------------------------------------------------------------
# Data helpers (thin layer over db + results_engine)
# ---------------------------------------------------------------------------

def fetch_student_info(student_id: str) -> Optional[dict]:
    """Return student display fields used by the Results UI."""
    student_id = (student_id or "").strip()
    if not student_id:
        return None
    row = db.run(
        """
        SELECT student_id, name, father_name, class_sec, status,
               current_academic_year, photo_path
        FROM students WHERE student_id=?
        """,
        (student_id,),
        fetchone=True,
    )
    if not row:
        return None
    extra = db.run(
        "SELECT academic_year FROM student_admission_extra WHERE student_id=?",
        (student_id,),
        fetchone=True,
    )
    session = (extra[0] if extra and extra[0] else None) or row[5] or ""
    return {
        "student_id": row[0],
        "name": row[1] or "",
        "father_name": row[2] or "",
        "class_sec": row[3] or "",
        "status": row[4] or "Active",
        "academic_session": session,
        "photo_path": row[6] or "",
        "roll_number": row[0],  # no separate roll column in schema; ID used
    }


def subjects_for_class(class_sec: str) -> list[str]:
    if not class_sec:
        return []
    rows = db.run(
        """
        SELECT subject_name FROM subjects
        WHERE class_name=? AND COALESCE(is_active,1)=1
        ORDER BY subject_name
        """,
        (class_sec,),
        fetchall=True,
    ) or []
    return [r[0] for r in rows]


def upsert_marks(
    student_id: str,
    exam_type: str,
    subject_name: str,
    obtained: float,
    total: float,
    entered_by: str,
) -> str:
    """Insert or update marks for the unique key (student, exam, subject).

    Returns 'updated' or 'inserted'.
    """
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    existing = db.run(
        """
        SELECT id FROM marks
        WHERE student_id=? AND exam_type=? AND subject_name=?
        ORDER BY id DESC LIMIT 1
        """,
        (student_id, exam_type, subject_name),
        fetchone=True,
    )
    if existing:
        db.run(
            """
            UPDATE marks
            SET obtained_marks=?, total_marks=?, entered_by=?, entered_at=?
            WHERE id=?
            """,
            (obtained, total, entered_by, now, existing[0]),
            commit=True,
        )
        return "updated"
    db.run(
        """
        INSERT INTO marks
            (student_id, exam_type, subject_name, obtained_marks, total_marks,
             entered_by, entered_at)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (student_id, exam_type, subject_name, obtained, total, entered_by, now),
        commit=True,
    )
    return "inserted"


def class_rank_for_student(
    student_id: str,
    class_sec: str,
    exam_type: Optional[str] = None,
) -> Optional[tuple[int, int, float]]:
    """Return (rank, class_size, percentage) for this student within class.

    Rank is by overall percentage (highest first) among classmates who have
    marks for the same exam filter. Returns None if ranking is not possible.
    """
    if not class_sec:
        return None
    peers = db.run(
        """
        SELECT student_id FROM students
        WHERE class_sec=? AND COALESCE(status,'Active')='Active'
        """,
        (class_sec,),
        fetchall=True,
    ) or []
    scored: list[tuple[str, float]] = []
    for (sid,) in peers:
        result = results_engine.compute_result(sid, exam_type)
        if result and result["total_marks"] > 0:
            scored.append((sid, float(result["percentage"])))
    if not scored:
        return None
    scored.sort(key=lambda x: (-x[1], x[0]))
    rank = None
    my_pct = None
    for i, (sid, pct) in enumerate(scored, start=1):
        if sid == student_id:
            rank = i
            my_pct = pct
            break
    if rank is None:
        return None
    return rank, len(scored), my_pct


# ---------------------------------------------------------------------------
# Main UI
# ---------------------------------------------------------------------------

class ResultsWorkspace:
    """Embeddable Results & Academics workspace (Frame content)."""

    EXAM_TYPES = [
        "Midterm",
        "Final Exam",
        "Monthly Test",
        "Unit Test",
        "Quiz",
        "Annual",
    ]

    def __init__(self, parent, user_role: str, current_user: str, *, as_toplevel: bool = False):
        self.parent = parent
        self.user_role = user_role
        self.current_user = current_user
        self.as_toplevel = as_toplevel
        self.student: Optional[dict] = None
        self._last_result: Optional[dict] = None
        self._last_exam_filter: Optional[str] = None

        self.can_manage_subjects = rbac.can(user_role, "results.subject.manage")
        self.can_edit_marks = rbac.can(user_role, "results.marks.edit")
        self.can_view = rbac.can(user_role, "results.view") or self.can_edit_marks

        if as_toplevel:
            self.root = parent  # Toplevel
            self.container = tk.Frame(parent, bg=theme.SILVER)
            self.container.pack(fill=tk.BOTH, expand=True)
        else:
            self.root = parent.winfo_toplevel()
            self.container = parent

        self._build()

    # ------------------------------------------------------------------
    def _build(self):
        for child in self.container.winfo_children():
            child.destroy()

        outer = tk.Frame(self.container, bg=theme.SILVER)
        outer.pack(fill=tk.BOTH, expand=True)

        # Header
        header = tk.Frame(outer, bg=theme.NAVY, padx=20, pady=14)
        header.pack(fill=tk.X, padx=10, pady=(10, 6))
        tk.Label(
            header, text="RESULTS & ACADEMICS",
            font=theme.FONT_H1, bg=theme.NAVY, fg="white",
        ).pack(anchor="w")
        tk.Label(
            header,
            text="Subjects · Marks entry · Exam-wise & overall results · Rank · Marksheet · Performance",
            font=theme.FONT_SMALL, bg=theme.NAVY, fg="#cbd5e1",
        ).pack(anchor="w", pady=(2, 0))

        # Notebook sections
        self.nb = ttk.Notebook(outer)
        self.nb.pack(fill=tk.BOTH, expand=True, padx=10, pady=(0, 10))

        self.tab_entry = tk.Frame(self.nb, bg=theme.SILVER)
        self.tab_overview = tk.Frame(self.nb, bg=theme.SILVER)
        self.tab_subjects = tk.Frame(self.nb, bg=theme.SILVER)
        self.tab_history = tk.Frame(self.nb, bg=theme.SILVER)

        self.nb.add(self.tab_entry, text="  Marks Entry  ")
        self.nb.add(self.tab_overview, text="  Result Overview  ")
        if self.can_manage_subjects:
            self.nb.add(self.tab_subjects, text="  Subjects Setup  ")
        self.nb.add(self.tab_history, text="  Marks History  ")

        self._build_entry_tab()
        self._build_overview_tab()
        if self.can_manage_subjects:
            self._build_subjects_tab()
        self._build_history_tab()

    # ==================================================================
    # Marks Entry
    # ==================================================================
    def _build_entry_tab(self):
        f = self.tab_entry
        pad = tk.Frame(f, bg=theme.SILVER, padx=12, pady=10)
        pad.pack(fill=tk.BOTH, expand=True)

        # Student lookup card
        card, body = theme.section_card(pad, "Student Lookup")
        card.pack(fill=tk.X, pady=(0, 8))

        row = tk.Frame(body, bg=theme.WHITE)
        row.pack(fill=tk.X, pady=4)
        tk.Label(row, text="Student ID:", font=theme.FONT_BODY_BOLD, bg=theme.WHITE).pack(side=tk.LEFT)
        self.ent_sid = tk.Entry(row, font=theme.FONT_BODY, width=18)
        self.ent_sid.pack(side=tk.LEFT, padx=8, ipady=3)
        self.ent_sid.bind("<Return>", lambda e: self.load_student())
        self.ent_sid.bind("<FocusOut>", lambda e: self.load_student())
        theme.primary_button(row, "🔍 Load Student", self.load_student).pack(side=tk.LEFT, padx=4)

        self.lbl_student_info = tk.Label(
            body,
            text="Enter a Student ID and press Load to begin.",
            font=theme.FONT_BODY, bg=theme.WHITE, fg=theme.TEXT_MUTED,
            justify="left", anchor="w",
        )
        self.lbl_student_info.pack(fill=tk.X, pady=(6, 2))

        # Marks form
        card2, body2 = theme.section_card(pad, "Enter / Update Marks")
        card2.pack(fill=tk.X, pady=(0, 8))

        form = tk.Frame(body2, bg=theme.WHITE)
        form.pack(fill=tk.X, pady=4)

        tk.Label(form, text="Examination", font=theme.FONT_SMALL, bg=theme.WHITE, fg=theme.TEXT_MUTED).grid(
            row=0, column=0, sticky="w", padx=(0, 8)
        )
        self.cmb_exam = ttk.Combobox(form, values=self.EXAM_TYPES, state="readonly", width=16, font=theme.FONT_BODY)
        self.cmb_exam.current(0)
        self.cmb_exam.grid(row=1, column=0, sticky="w", padx=(0, 12), pady=(0, 8))
        self.cmb_exam.bind("<<ComboboxSelected>>", lambda e: self._on_exam_changed())

        tk.Label(form, text="Subject", font=theme.FONT_SMALL, bg=theme.WHITE, fg=theme.TEXT_MUTED).grid(
            row=0, column=1, sticky="w", padx=(0, 8)
        )
        self.cmb_subject = ttk.Combobox(form, values=[], state="readonly", width=20, font=theme.FONT_BODY)
        self.cmb_subject.grid(row=1, column=1, sticky="w", padx=(0, 12), pady=(0, 8))
        self.cmb_subject.bind("<<ComboboxSelected>>", lambda e: self._prefill_existing_marks())

        tk.Label(form, text="Obtained", font=theme.FONT_SMALL, bg=theme.WHITE, fg=theme.TEXT_MUTED).grid(
            row=0, column=2, sticky="w", padx=(0, 8)
        )
        self.ent_obt = tk.Entry(form, font=theme.FONT_BODY, width=10)
        self.ent_obt.grid(row=1, column=2, sticky="w", padx=(0, 12), pady=(0, 8))

        tk.Label(form, text="Total", font=theme.FONT_SMALL, bg=theme.WHITE, fg=theme.TEXT_MUTED).grid(
            row=0, column=3, sticky="w", padx=(0, 8)
        )
        self.ent_tot = tk.Entry(form, font=theme.FONT_BODY, width=10)
        self.ent_tot.insert(0, "100")
        self.ent_tot.grid(row=1, column=3, sticky="w", padx=(0, 12), pady=(0, 8))

        self.lbl_subject_hint = tk.Label(
            body2, text="Load a student to see subjects for their class.",
            font=theme.FONT_SMALL, bg=theme.WHITE, fg=theme.TEXT_MUTED,
        )
        self.lbl_subject_hint.pack(anchor="w", pady=(0, 6))

        btn_row = tk.Frame(body2, bg=theme.WHITE)
        btn_row.pack(fill=tk.X, pady=(2, 4))
        self.btn_save = theme.primary_button(
            btn_row, "✓ Save Marks", self.save_marks, bg=theme.SUCCESS
        )
        self.btn_save.pack(side=tk.LEFT, padx=(0, 8))
        if not self.can_edit_marks:
            self.btn_save.config(state="disabled")

        theme.primary_button(
            btn_row, "↻ Refresh Result", self.refresh_result_overview, bg=theme.SLATE
        ).pack(side=tk.LEFT, padx=(0, 8))

        # Quick subject marks table for current student + exam
        card3, body3 = theme.section_card(pad, "Subject-wise Marks (selected exam)")
        card3.pack(fill=tk.BOTH, expand=True)

        cols = ("subject", "obtained", "total", "percent", "result")
        self.tree_subject = ttk.Treeview(body3, columns=cols, show="headings", height=8)
        for c, h, w in [
            ("subject", "Subject", 160),
            ("obtained", "Obtained", 90),
            ("total", "Total", 90),
            ("percent", "Percent", 90),
            ("result", "Result", 80),
        ]:
            self.tree_subject.heading(c, text=h)
            self.tree_subject.column(c, width=w, anchor="center")
        self.tree_subject.pack(fill=tk.BOTH, expand=True, side=tk.LEFT)
        sb = ttk.Scrollbar(body3, orient="vertical", command=self.tree_subject.yview)
        self.tree_subject.configure(yscrollcommand=sb.set)
        sb.pack(side=tk.RIGHT, fill=tk.Y)

        self.tree_subject.bind("<<TreeviewSelect>>", self._on_subject_row_select)

    def load_student(self):
        sid = self.ent_sid.get().strip()
        self.student = fetch_student_info(sid) if sid else None
        if not sid:
            self.lbl_student_info.config(
                text="Enter a Student ID and press Load to begin.",
                fg=theme.TEXT_MUTED,
            )
            self.cmb_subject.config(values=[])
            return
        if not self.student:
            self.lbl_student_info.config(
                text=f"⚠ No student found with ID '{sid}'.",
                fg=theme.DANGER,
            )
            self.cmb_subject.config(values=[])
            self.tree_subject.delete(*self.tree_subject.get_children())
            return

        s = self.student
        info = (
            f"ID: {s['student_id']}   ·   Name: {s['name']}   ·   "
            f"Father: {s['father_name'] or '—'}   ·   Class: {s['class_sec'] or '—'}   ·   "
            f"Session: {s['academic_session'] or '—'}   ·   Status: {s['status']}"
        )
        self.lbl_student_info.config(text=info, fg=theme.TEXT_DARK)

        subs = subjects_for_class(s["class_sec"])
        self.cmb_subject.config(values=subs)
        if subs:
            self.lbl_subject_hint.config(
                text=f"Class {s['class_sec']}: {len(subs)} active subject(s).",
                fg=theme.SUCCESS,
            )
            if not self.cmb_subject.get() and subs:
                self.cmb_subject.set(subs[0])
        else:
            self.lbl_subject_hint.config(
                text=f"No subjects configured for class '{s['class_sec']}'. "
                     f"Add them under Subjects Setup.",
                fg=theme.WARNING,
            )

        self._prefill_existing_marks()
        self.refresh_result_overview()
        self._fill_subject_tree()

    def _on_exam_changed(self):
        self._prefill_existing_marks()
        self._fill_subject_tree()
        self.refresh_result_overview()

    def _prefill_existing_marks(self):
        if not self.student:
            return
        exam = self.cmb_exam.get()
        sub = self.cmb_subject.get().strip()
        if not sub:
            return
        row = db.run(
            """
            SELECT obtained_marks, total_marks FROM marks
            WHERE student_id=? AND exam_type=? AND subject_name=?
            ORDER BY id DESC LIMIT 1
            """,
            (self.student["student_id"], exam, sub),
            fetchone=True,
        )
        self.ent_obt.delete(0, tk.END)
        self.ent_tot.delete(0, tk.END)
        if row:
            self.ent_obt.insert(0, str(row[0]))
            self.ent_tot.insert(0, str(row[1] if row[1] else 100))
            self.lbl_subject_hint.config(
                text=f"Existing marks loaded for {sub} / {exam} — Save will update.",
                fg=theme.BRAND_BLUE,
            )
        else:
            self.ent_tot.insert(0, "100")
            self.lbl_subject_hint.config(
                text=f"New entry for {sub} / {exam}.",
                fg=theme.TEXT_MUTED,
            )

    def _on_subject_row_select(self, _event=None):
        sel = self.tree_subject.selection()
        if not sel:
            return
        vals = self.tree_subject.item(sel[0], "values")
        if not vals:
            return
        sub = vals[0]
        self.cmb_subject.set(sub)
        self._prefill_existing_marks()

    def save_marks(self):
        if not self.can_edit_marks:
            messagebox.showerror(
                "Permission Denied",
                "You are not allowed to edit marks.",
                parent=self.root,
            )
            return
        if not self.student:
            messagebox.showerror("Error", "Load a student first.", parent=self.root)
            return

        s_id = self.student["student_id"]
        exam = self.cmb_exam.get()
        sub = self.cmb_subject.get().strip()
        obt, ok1 = _safe_float(self.ent_obt.get(), "Obtained Marks")
        tot, ok2 = _safe_float(self.ent_tot.get(), "Total Marks", default=100.0)
        if not (ok1 and ok2):
            return
        if not sub:
            messagebox.showerror("Error", "Select a subject.", parent=self.root)
            return
        if obt < 0 or tot <= 0:
            messagebox.showerror(
                "Error",
                "Obtained marks must be ≥ 0 and Total marks must be > 0.",
                parent=self.root,
            )
            return
        if obt > tot:
            messagebox.showerror(
                "Error",
                f"Obtained ({obt}) cannot exceed Total ({tot}).",
                parent=self.root,
            )
            return

        valid = db.run(
            """
            SELECT 1 FROM subjects
            WHERE class_name=? AND subject_name=? AND COALESCE(is_active,1)=1
            """,
            (self.student["class_sec"], sub),
            fetchone=True,
        )
        if not valid:
            messagebox.showerror(
                "Error",
                f"'{sub}' is not an active subject for class "
                f"'{self.student['class_sec']}'.",
                parent=self.root,
            )
            return

        action = upsert_marks(s_id, exam, sub, obt, tot, self.current_user)
        _log_activity(
            self.current_user,
            f"{'Updated' if action == 'updated' else 'Saved'} marks for {s_id} "
            f"({exam} / {sub}): {obt}/{tot}",
        )
        self._fill_subject_tree()
        self.refresh_result_overview()
        self.load_history_table()
        messagebox.showinfo(
            "Success",
            f"Marks {'updated' if action == 'updated' else 'saved'} successfully "
            f"for {sub} ({exam}).",
            parent=self.root,
        )

    def _fill_subject_tree(self):
        self.tree_subject.delete(*self.tree_subject.get_children())
        if not self.student:
            return
        exam = self.cmb_exam.get()
        result = results_engine.compute_result(self.student["student_id"], exam)
        if not result:
            return
        for sub in result["subjects"]:
            self.tree_subject.insert(
                "",
                tk.END,
                values=(
                    sub["subject"],
                    f"{sub['obtained']:.1f}",
                    f"{sub['total']:.1f}",
                    f"{sub['percent']:.1f}%",
                    "PASS" if sub["pass"] else "FAIL",
                ),
            )

    # ==================================================================
    # Result Overview
    # ==================================================================
    def _build_overview_tab(self):
        f = self.tab_overview
        pad = tk.Frame(f, bg=theme.SILVER, padx=12, pady=10)
        pad.pack(fill=tk.BOTH, expand=True)

        # Filter
        ctrl = tk.Frame(pad, bg=theme.SILVER)
        ctrl.pack(fill=tk.X, pady=(0, 8))
        tk.Label(ctrl, text="Exam filter:", font=theme.FONT_BODY_BOLD, bg=theme.SILVER).pack(side=tk.LEFT)
        self.cmb_overview_exam = ttk.Combobox(
            ctrl,
            values=["All Exams"] + self.EXAM_TYPES,
            state="readonly",
            width=18,
            font=theme.FONT_BODY,
        )
        self.cmb_overview_exam.set("All Exams")
        self.cmb_overview_exam.pack(side=tk.LEFT, padx=8)
        self.cmb_overview_exam.bind("<<ComboboxSelected>>", lambda e: self.refresh_result_overview())
        theme.primary_button(ctrl, "↻ Calculate", self.refresh_result_overview, bg=theme.SLATE).pack(
            side=tk.LEFT, padx=4
        )

        # Student banner
        self.lbl_ov_student = tk.Label(
            pad, text="Load a student from the Marks Entry tab first.",
            font=theme.FONT_BODY, bg=theme.SILVER, fg=theme.TEXT_MUTED, anchor="w",
        )
        self.lbl_ov_student.pack(fill=tk.X, pady=(0, 8))

        # Summary cards row
        cards_row = tk.Frame(pad, bg=theme.SILVER)
        cards_row.pack(fill=tk.X, pady=(0, 8))
        self._stat_total = theme.stat_card(cards_row, "Total Marks", "—", accent=theme.NAVY)
        self._stat_total.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 6))
        self._stat_obt = theme.stat_card(cards_row, "Obtained", "—", accent=theme.BRAND_BLUE)
        self._stat_obt.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=6)
        self._stat_pct = theme.stat_card(cards_row, "Percentage", "—", accent=theme.SUCCESS)
        self._stat_pct.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=6)
        self._stat_grade = theme.stat_card(cards_row, "Grade", "—", accent=theme.WARNING)
        self._stat_grade.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=6)
        self._stat_status = theme.stat_card(cards_row, "Result", "—", accent=theme.DANGER)
        self._stat_status.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(6, 0))

        # Rank + remarks
        mid = tk.Frame(pad, bg=theme.SILVER)
        mid.pack(fill=tk.X, pady=(0, 8))

        card_r, body_r = theme.section_card(mid, "Class Position")
        card_r.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=(0, 6))
        self.lbl_rank = tk.Label(
            body_r,
            text="Rank will appear when classmates have marks for the same exam.",
            font=theme.FONT_BODY, bg=theme.WHITE, fg=theme.TEXT_MUTED, wraplength=320, justify="left",
        )
        self.lbl_rank.pack(anchor="w", pady=4)

        card_m, body_m = theme.section_card(mid, "Teacher / Principal Remarks")
        card_m.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=(6, 0))
        self.txt_remarks = tk.Text(
            body_m, height=4, wrap="word", font=theme.FONT_BODY,
            bg="#f8fafc", relief="solid", bd=1, padx=6, pady=4,
        )
        self.txt_remarks.pack(fill=tk.BOTH, expand=True)

        # Subject table
        card_t, body_t = theme.section_card(pad, "Subject-wise Result")
        card_t.pack(fill=tk.BOTH, expand=True, pady=(0, 8))
        cols = ("subject", "obtained", "total", "percent", "result")
        self.tree_ov = ttk.Treeview(body_t, columns=cols, show="headings", height=8)
        for c, h, w in [
            ("subject", "Subject", 180),
            ("obtained", "Obtained", 90),
            ("total", "Total", 90),
            ("percent", "Percent", 90),
            ("result", "Result", 80),
        ]:
            self.tree_ov.heading(c, text=h)
            self.tree_ov.column(c, width=w, anchor="center")
        self.tree_ov.pack(fill=tk.BOTH, expand=True, side=tk.LEFT)
        sb2 = ttk.Scrollbar(body_t, orient="vertical", command=self.tree_ov.yview)
        self.tree_ov.configure(yscrollcommand=sb2.set)
        sb2.pack(side=tk.RIGHT, fill=tk.Y)

        # Actions
        actions = tk.Frame(pad, bg=theme.SILVER)
        actions.pack(fill=tk.X, pady=(4, 0))
        theme.primary_button(
            actions, "🧾 Generate Marksheet PDF", self.generate_marksheet, bg="#7c3aed"
        ).pack(side=tk.LEFT, padx=(0, 8))
        theme.primary_button(
            actions, "💾 Save Marksheet As…", self.save_marksheet_as, bg=theme.SLATE
        ).pack(side=tk.LEFT, padx=(0, 8))
        theme.primary_button(
            actions, "📊 Performance Graph", self.show_performance_graph, bg=theme.BRAND_BLUE
        ).pack(side=tk.LEFT, padx=(0, 8))

    def _set_stat_value(self, card, value: str):
        """Update the big number Label inside a theme.stat_card."""
        try:
            inner = card.winfo_children()[1]  # inner frame
            labels = [w for w in inner.winfo_children() if isinstance(w, tk.Label)]
            if len(labels) >= 2:
                labels[1].config(text=str(value))
        except Exception:
            pass

    def refresh_result_overview(self):
        self.tree_ov.delete(*self.tree_ov.get_children())
        self._last_result = None
        self._last_exam_filter = None

        if not self.student:
            self.lbl_ov_student.config(
                text="Load a student from the Marks Entry tab first.",
                fg=theme.TEXT_MUTED,
            )
            for card, val in [
                (self._stat_total, "—"),
                (self._stat_obt, "—"),
                (self._stat_pct, "—"),
                (self._stat_grade, "—"),
                (self._stat_status, "—"),
            ]:
                self._set_stat_value(card, val)
            self.lbl_rank.config(
                text="Rank will appear when classmates have marks for the same exam.",
                fg=theme.TEXT_MUTED,
            )
            return

        s = self.student
        exam_sel = self.cmb_overview_exam.get()
        exam = None if exam_sel == "All Exams" else exam_sel
        self._last_exam_filter = exam

        self.lbl_ov_student.config(
            text=(
                f"{s['name']}  ·  {s['student_id']}  ·  Class {s['class_sec'] or '—'}  ·  "
                f"Father: {s['father_name'] or '—'}  ·  Session: {s['academic_session'] or '—'}"
            ),
            fg=theme.TEXT_DARK,
        )

        result = results_engine.compute_result(s["student_id"], exam)
        if not result:
            for card, val in [
                (self._stat_total, "—"),
                (self._stat_obt, "—"),
                (self._stat_pct, "—"),
                (self._stat_grade, "—"),
                (self._stat_status, "—"),
            ]:
                self._set_stat_value(card, val)
            self.lbl_rank.config(text="No marks recorded for this filter.", fg=theme.WARNING)
            return

        self._last_result = result
        self._set_stat_value(self._stat_total, f"{result['total_marks']:.0f}")
        self._set_stat_value(self._stat_obt, f"{result['total_obtained']:.1f}")
        self._set_stat_value(self._stat_pct, f"{result['percentage']:.1f}%")
        self._set_stat_value(self._stat_grade, result["grade"])
        self._set_stat_value(self._stat_status, "PASS" if result["passed"] else "FAIL")

        for sub in result["subjects"]:
            self.tree_ov.insert(
                "",
                tk.END,
                values=(
                    sub["subject"],
                    f"{sub['obtained']:.1f}",
                    f"{sub['total']:.1f}",
                    f"{sub['percent']:.1f}%",
                    "PASS" if sub["pass"] else "FAIL",
                ),
            )

        rank_info = class_rank_for_student(s["student_id"], s["class_sec"], exam)
        if rank_info:
            rank, size, pct = rank_info
            self.lbl_rank.config(
                text=f"Position: {rank} of {size}  ·  Percentage: {pct:.1f}%\n"
                     f"(Based on {'All Exams' if exam is None else exam} among active classmates with marks.)",
                fg=theme.TEXT_DARK,
            )
        else:
            self.lbl_rank.config(
                text="Not enough peer data to compute class rank yet.",
                fg=theme.TEXT_MUTED,
            )

        # Keep subject tree on entry tab in sync when same exam
        if self.cmb_exam.get() == (exam or self.cmb_exam.get()):
            self._fill_subject_tree()

    def generate_marksheet(self, out_path: Optional[str] = None):
        if not self.student:
            messagebox.showerror("Error", "Load a student first.", parent=self.root)
            return
        if not self._last_result:
            self.refresh_result_overview()
        if not self._last_result:
            messagebox.showinfo("No Data", "No marks to export.", parent=self.root)
            return

        s = self.student
        exam_label = (
            "All Exams" if self._last_exam_filter is None else self._last_exam_filter
        )
        remarks = self.txt_remarks.get("1.0", "end").strip()
        if out_path is None:
            out_path = os.path.join(os.getcwd(), f"Marksheet_{s['student_id']}.pdf")

        try:
            _generate_marksheet_extended(
                s["student_id"],
                s["name"],
                s["class_sec"],
                self._last_result,
                out_path,
                exam_label=exam_label,
                father_name=s.get("father_name") or "",
                session=s.get("academic_session") or "",
                remarks=remarks,
                rank_text=self.lbl_rank.cget("text") if self.lbl_rank else "",
            )
        except Exception as e:
            messagebox.showerror(
                "Marksheet Error", f"Could not generate marksheet:\n{e}", parent=self.root
            )
            return

        _log_activity(self.current_user, f"Generated marksheet for {s['student_id']} ({exam_label})")
        opened = _try_open_file(out_path)
        messagebox.showinfo(
            "Marksheet Ready",
            (f"Marksheet opened:\n{out_path}" if opened else f"Marksheet saved:\n{out_path}"),
            parent=self.root,
        )

    def save_marksheet_as(self):
        if not self.student:
            messagebox.showerror("Error", "Load a student first.", parent=self.root)
            return
        path = filedialog.asksaveasfilename(
            defaultextension=".pdf",
            initialfile=f"Marksheet_{self.student['student_id']}.pdf",
            filetypes=[("PDF Files", "*.pdf")],
            parent=self.root,
        )
        if path:
            self.generate_marksheet(out_path=path)

    def show_performance_graph(self):
        if not HAS_MATPLOTLIB:
            messagebox.showerror(
                "Unavailable",
                "Matplotlib is not installed. Install it to view performance graphs.",
                parent=self.root,
            )
            return
        if not self.student:
            messagebox.showerror("Error", "Load a student first.", parent=self.root)
            return
        if not self._last_result:
            self.refresh_result_overview()
        if not self._last_result:
            messagebox.showinfo("No Data", "No marks to graph.", parent=self.root)
            return

        subjects = [s["subject"] for s in self._last_result["subjects"]]
        marks = [s["obtained"] for s in self._last_result["subjects"]]
        totals = [s["total"] for s in self._last_result["subjects"]]
        percents = [s["percent"] for s in self._last_result["subjects"]]

        fig, axes = plt.subplots(1, 2, figsize=(10, 4.2))
        axes[0].bar(subjects, marks, color="#0284c7", width=0.45, label="Obtained")
        axes[0].plot(subjects, totals, color="#dc2626", marker="o", linestyle="--", label="Total")
        axes[0].set_title(f"Marks — {self.student['student_id']}", fontweight="bold")
        axes[0].set_ylabel("Marks")
        axes[0].tick_params(axis="x", rotation=30)
        axes[0].legend(fontsize=8)
        axes[0].grid(axis="y", linestyle="--", alpha=0.5)

        axes[1].bar(subjects, percents, color="#16a34a", width=0.45)
        axes[1].axhline(y=33, color="#dc2626", linestyle=":", linewidth=1, label="Min subject %")
        axes[1].set_title("Percentage by Subject", fontweight="bold")
        axes[1].set_ylabel("%")
        axes[1].tick_params(axis="x", rotation=30)
        axes[1].legend(fontsize=8)
        axes[1].grid(axis="y", linestyle="--", alpha=0.5)

        fig.tight_layout()
        plt.show()

    # ==================================================================
    # Subjects Setup
    # ==================================================================
    def _build_subjects_tab(self):
        f = self.tab_subjects
        pad = tk.Frame(f, bg=theme.SILVER, padx=12, pady=10)
        pad.pack(fill=tk.BOTH, expand=True)

        card, body = theme.section_card(pad, "Configure subjects per class")
        card.pack(fill=tk.X, pady=(0, 8))

        row = tk.Frame(body, bg=theme.WHITE)
        row.pack(fill=tk.X, pady=4)
        tk.Label(row, text="Class:", font=theme.FONT_BODY_BOLD, bg=theme.WHITE).pack(side=tk.LEFT)
        self.ent_cls = tk.Entry(row, font=theme.FONT_BODY, width=14)
        self.ent_cls.pack(side=tk.LEFT, padx=6, ipady=2)
        self.ent_cls.bind("<KeyRelease>", lambda e: self.refresh_class_subjects())

        tk.Label(row, text="Subject:", font=theme.FONT_BODY_BOLD, bg=theme.WHITE).pack(side=tk.LEFT, padx=(12, 0))
        self.ent_sub = tk.Entry(row, font=theme.FONT_BODY, width=18)
        self.ent_sub.pack(side=tk.LEFT, padx=6, ipady=2)

        theme.primary_button(row, "＋ Add Subject", self.add_subject, bg=theme.BRAND_BLUE).pack(
            side=tk.LEFT, padx=8
        )
        theme.primary_button(
            row, "Deactivate Selected", self.deactivate_subject, bg=theme.DANGER
        ).pack(side=tk.LEFT, padx=4)

        self.lst_subjects = tk.Listbox(
            body, height=12, font=theme.FONT_BODY, bg="#f8fafc",
            relief="solid", bd=1, selectbackground=theme.BRAND_BLUE,
        )
        self.lst_subjects.pack(fill=tk.BOTH, expand=True, pady=(8, 4))

        tk.Label(
            body,
            text="Deactivating keeps historical marks; the subject is only hidden from new entries.",
            font=theme.FONT_SMALL, bg=theme.WHITE, fg=theme.TEXT_MUTED,
        ).pack(anchor="w")

    def refresh_class_subjects(self):
        self.lst_subjects.delete(0, tk.END)
        cls = self.ent_cls.get().strip()
        if not cls:
            return
        for sub in subjects_for_class(cls):
            self.lst_subjects.insert(tk.END, sub)

    def add_subject(self):
        if not self.can_manage_subjects:
            messagebox.showerror("Permission Denied", "Not allowed to manage subjects.", parent=self.root)
            return
        cls = self.ent_cls.get().strip()
        sub = self.ent_sub.get().strip()
        if not (cls and sub):
            messagebox.showerror("Error", "Enter both class and subject name.", parent=self.root)
            return
        existing = db.run(
            "SELECT id, is_active FROM subjects WHERE class_name=? AND subject_name=?",
            (cls, sub),
            fetchone=True,
        )
        if existing:
            if existing[1]:
                messagebox.showinfo(
                    "Already Exists",
                    f"'{sub}' is already active for class '{cls}'.",
                    parent=self.root,
                )
                return
            db.run("UPDATE subjects SET is_active=1 WHERE id=?", (existing[0],), commit=True)
        else:
            db.run(
                "INSERT INTO subjects (class_name, subject_name, is_active) VALUES (?, ?, 1)",
                (cls, sub),
                commit=True,
            )
        _log_activity(self.current_user, f"Added/reactivated subject '{sub}' for class '{cls}'")
        self.ent_sub.delete(0, tk.END)
        self.refresh_class_subjects()
        messagebox.showinfo("Success", f"Subject '{sub}' added to class '{cls}'.", parent=self.root)

    def deactivate_subject(self):
        if not self.can_manage_subjects:
            messagebox.showerror("Permission Denied", "Not allowed to manage subjects.", parent=self.root)
            return
        sel = self.lst_subjects.curselection()
        cls = self.ent_cls.get().strip()
        if not sel or not cls:
            messagebox.showerror("Error", "Select a subject from the list first.", parent=self.root)
            return
        sub = self.lst_subjects.get(sel[0])
        if not messagebox.askyesno(
            "Confirm",
            f"Deactivate '{sub}' for class '{cls}'?\nExisting marks history is kept.",
            parent=self.root,
        ):
            return
        db.run(
            "UPDATE subjects SET is_active=0 WHERE class_name=? AND subject_name=?",
            (cls, sub),
            commit=True,
        )
        _log_activity(self.current_user, f"Deactivated subject '{sub}' for class '{cls}'")
        self.refresh_class_subjects()

    # ==================================================================
    # Marks History
    # ==================================================================
    def _build_history_tab(self):
        f = self.tab_history
        pad = tk.Frame(f, bg=theme.SILVER, padx=12, pady=10)
        pad.pack(fill=tk.BOTH, expand=True)

        toolbar = tk.Frame(pad, bg=theme.SILVER)
        toolbar.pack(fill=tk.X, pady=(0, 6))
        tk.Label(toolbar, text="Filter Student ID (optional):", font=theme.FONT_SMALL, bg=theme.SILVER).pack(
            side=tk.LEFT
        )
        self.ent_hist_sid = tk.Entry(toolbar, font=theme.FONT_BODY, width=16)
        self.ent_hist_sid.pack(side=tk.LEFT, padx=6, ipady=2)
        theme.primary_button(toolbar, "↻ Refresh", self.load_history_table, bg=theme.SLATE).pack(
            side=tk.LEFT, padx=4
        )
        tk.Label(
            toolbar,
            text="All recorded marks are retained as academic history.",
            font=theme.FONT_SMALL, bg=theme.SILVER, fg=theme.TEXT_MUTED,
        ).pack(side=tk.RIGHT)

        cols = ("id", "student_id", "exam", "subject", "obtained", "total", "percent", "by", "at")
        self.tree_hist = ttk.Treeview(pad, columns=cols, show="headings", height=16)
        headings = [
            ("id", "#", 50),
            ("student_id", "Student ID", 110),
            ("exam", "Examination", 110),
            ("subject", "Subject", 140),
            ("obtained", "Obtained", 80),
            ("total", "Total", 70),
            ("percent", "%", 70),
            ("by", "Entered By", 100),
            ("at", "Entered At", 140),
        ]
        for c, h, w in headings:
            self.tree_hist.heading(c, text=h)
            self.tree_hist.column(c, width=w, anchor="center")
        self.tree_hist.pack(fill=tk.BOTH, expand=True, side=tk.LEFT)
        sb = ttk.Scrollbar(pad, orient="vertical", command=self.tree_hist.yview)
        self.tree_hist.configure(yscrollcommand=sb.set)
        sb.pack(side=tk.RIGHT, fill=tk.Y)

        self.load_history_table()

    def load_history_table(self):
        self.tree_hist.delete(*self.tree_hist.get_children())
        sid_filter = ""
        if hasattr(self, "ent_hist_sid"):
            sid_filter = self.ent_hist_sid.get().strip()
        if sid_filter:
            rows = db.run(
                """
                SELECT id, student_id, exam_type, subject_name, obtained_marks,
                       total_marks, entered_by, entered_at
                FROM marks WHERE student_id=? ORDER BY id DESC LIMIT 500
                """,
                (sid_filter,),
                fetchall=True,
            ) or []
        else:
            rows = db.run(
                """
                SELECT id, student_id, exam_type, subject_name, obtained_marks,
                       total_marks, entered_by, entered_at
                FROM marks ORDER BY id DESC LIMIT 500
                """,
                fetchall=True,
            ) or []
        for r in rows:
            m_id, s_id, exam, sub, obt, tot, by, at = r
            pct = (obt / tot * 100) if tot and tot > 0 else 0
            self.tree_hist.insert(
                "",
                tk.END,
                values=(
                    m_id, s_id, exam, sub,
                    f"{obt:.1f}" if obt is not None else "",
                    f"{tot:.1f}" if tot is not None else "",
                    f"{pct:.1f}%",
                    by or "—",
                    at or "—",
                ),
            )


# ---------------------------------------------------------------------------
# PDF helper — extends reports.generate_marksheet with optional extras
# without breaking the original function signature.
# ---------------------------------------------------------------------------

def _generate_marksheet_extended(
    student_id,
    name,
    cls,
    result,
    out_path,
    exam_label="All Exams",
    father_name="",
    session="",
    remarks="",
    rank_text="",
):
    """Generate marksheet via reports.py, then optionally annotate remarks."""
    # Primary generation (existing professional layout)
    reports.generate_marksheet(
        student_id, name, cls, result, out_path, exam_label=exam_label
    )

    # If remarks / father / session / rank requested, append a short
    # second-pass annotation by rewriting with reportlab on top of a
    # fresh full document that includes the extras. This keeps
    # reports.generate_marksheet intact for other callers.
    if not (remarks or father_name or session or rank_text):
        return out_path

    try:
        from reportlab.pdfgen import canvas as rl_canvas
        from reportlab.lib.pagesizes import letter
        from reportlab.lib import colors

        c = rl_canvas.Canvas(out_path, pagesize=letter)
        y = reports._draw_page_header(c, "STUDENT MARKSHEET")
        y = reports._kv_row(c, 50, y, "Student ID", student_id)
        y = reports._kv_row(c, 50, y, "Name", name)
        if father_name:
            y = reports._kv_row(c, 50, y, "Father / Guardian", father_name)
        y = reports._kv_row(c, 50, y, "Class", cls or "-")
        if session:
            y = reports._kv_row(c, 50, y, "Academic Session", session)
        y = reports._kv_row(c, 50, y, "Examination", exam_label)
        y -= 10

        NAVY = colors.HexColor("#0f172a")
        SILVER = colors.HexColor("#f1f5f9")
        SUCCESS = colors.HexColor("#16a34a")
        DANGER = colors.HexColor("#dc2626")
        MUTED = colors.HexColor("#64748b")
        WHITE = colors.white
        BLACK = colors.black
        SILVER_BORDER = colors.HexColor("#e2e8f0")

        c.setFillColor(NAVY)
        c.roundRect(50, y - 4, 510, 22, 4, fill=1, stroke=0)
        c.setFillColor(WHITE)
        c.setFont("Helvetica-Bold", 9)
        c.drawString(60, y + 2, "SUBJECT")
        c.drawString(280, y + 2, "OBTAINED")
        c.drawString(370, y + 2, "TOTAL")
        c.drawString(450, y + 2, "PERCENT")
        c.drawString(520, y + 2, "RESULT")
        y -= 24

        for i, s in enumerate(result.get("subjects") or []):
            if y < 160:
                c.showPage()
                y = 750
            if i % 2 == 0:
                c.setFillColor(SILVER)
                c.rect(50, y - 4, 510, 18, fill=1, stroke=0)
            c.setFillColor(BLACK)
            c.setFont("Helvetica", 9)
            c.drawString(60, y, str(s["subject"])[:28])
            c.drawString(280, y, f"{s['obtained']:.1f}")
            c.drawString(370, y, f"{s['total']:.1f}")
            c.drawString(450, y, f"{s['percent']:.1f}%")
            passed = s.get("pass", s.get("percent", 0) >= 33)
            c.setFillColor(SUCCESS if passed else DANGER)
            c.setFont("Helvetica-Bold", 9)
            c.drawString(520, y, "PASS" if passed else "FAIL")
            y -= 18

        y -= 12
        c.setStrokeColor(SILVER_BORDER)
        c.line(50, y, 560, y)
        y -= 22

        c.setFillColor(NAVY)
        c.setFont("Helvetica-Bold", 11)
        c.drawString(50, y, f"Total:  {result['total_obtained']:.1f}  /  {result['total_marks']:.1f}")
        y -= 18
        c.drawString(50, y, f"Percentage:  {result['percentage']:.2f}%")
        y -= 18
        c.drawString(50, y, f"Grade:  {result['grade']}")
        y -= 28

        passed = result.get("passed", False)
        c.setFillColor(SUCCESS if passed else DANGER)
        c.roundRect(50, y - 6, 200, 26, 6, fill=1, stroke=0)
        c.setFillColor(WHITE)
        c.setFont("Helvetica-Bold", 12)
        c.drawCentredString(150, y + 2, "PASS" if passed else "FAIL")

        if rank_text and "Position:" in rank_text:
            c.setFillColor(MUTED)
            c.setFont("Helvetica", 9)
            first_line = rank_text.split("\n")[0]
            c.drawString(270, y + 2, first_line[:50])

        y -= 40
        if remarks:
            c.setFillColor(NAVY)
            c.setFont("Helvetica-Bold", 10)
            c.drawString(50, y, "Teacher / Principal Remarks")
            y -= 14
            c.setFillColor(BLACK)
            c.setFont("Helvetica", 9)
            words = remarks.split()
            line = ""
            for w in words:
                trial = (line + " " + w).strip()
                if c.stringWidth(trial, "Helvetica", 9) > 500:
                    c.drawString(50, y, line)
                    y -= 12
                    line = w
                    if y < 70:
                        break
                else:
                    line = trial
            if line and y >= 70:
                c.drawString(50, y, line)

        reports._draw_page_footer(c)
        c.save()
    except Exception as e:
        print(f"[results_window] Extended marksheet annotation failed (base PDF kept): {e}")

    return out_path


def _try_open_file(path: str) -> bool:
    try:
        if os.name == "nt":
            os.startfile(path)  # type: ignore[attr-defined]
            return True
        import shutil
        if shutil.which("xdg-open"):
            os.system(f'xdg-open "{path}"')
            return True
        if shutil.which("open"):
            os.system(f'open "{path}"')
            return True
    except Exception:
        pass
    return False


# ---------------------------------------------------------------------------
# Public launchers
# ---------------------------------------------------------------------------

def build_results_into(parent_frame, user_role: str, current_user: str) -> ResultsWorkspace:
    """Embed the Results workspace into an existing frame (e.g. app tab)."""
    return ResultsWorkspace(parent_frame, user_role, current_user, as_toplevel=False)


def launch_results_window(parent, user_role: str, current_user: str) -> Optional[ResultsWorkspace]:
    """Open Results & Academics as a dedicated Toplevel window."""
    if not (rbac.can(user_role, "results.view") or rbac.can(user_role, "results.marks.edit")):
        messagebox.showerror(
            "Permission Denied",
            f"Role '{user_role}' cannot open Results & Academics.",
            parent=parent,
        )
        return None

    win = tk.Toplevel(parent)
    win.title("Results & Academics — AR School Management System")
    win.geometry("1100x720")
    win.minsize(900, 600)
    win.config(bg=theme.SILVER)
    win.transient(parent)
    workspace = ResultsWorkspace(win, user_role, current_user, as_toplevel=True)
    return workspace
