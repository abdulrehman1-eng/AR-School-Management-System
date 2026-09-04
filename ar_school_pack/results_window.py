"""
results_window.py — Professional Results & Academics UI module (redesigned).

Uses results_engine.py for all grade/percentage/pass-fail calculations.
Uses reports.py for marksheet PDF generation.
Does not modify db schema; marks upsert is done by SELECT-then-UPDATE/INSERT
on (student_id, exam_type, subject_name).

Redesign highlights:
  - Batch / spreadsheet-style Marks Entry (class + exam + subject → editable grid)
  - Live percentage / grade calculation on cell change
  - Save All Marks in a single transaction
  - Polished dark theme alignment + KPI cards in Class Analytics
  - Multi-select batch exam filter retained for consolidated PDF
"""

from __future__ import annotations

import os
import tkinter as tk
from tkinter import ttk, messagebox, filedialog
from datetime import datetime
from typing import Optional, Callable

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


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------

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
# Results policy — single source of truth: results_engine
# (passing_criteria + grading_config tables used by compute_result)
# ---------------------------------------------------------------------------

_DEFAULT_TOTAL_MARKS = 100.0
_DEFAULT_GRADE_BANDS = [
    ("A+", 90.0, 100.0),
    ("A", 80.0, 89.99),
    ("B", 70.0, 79.99),
    ("C", 60.0, 69.99),
    ("D", 50.0, 59.99),
    ("E", 33.0, 49.99),
    ("F", 0.0, 32.99),
]


def get_default_total_marks() -> float:
    """UI default for new mark rows (not part of grading engine)."""
    try:
        if hasattr(db, "get_setting"):
            raw = db.get_setting("results_default_total_marks", str(_DEFAULT_TOTAL_MARKS))
            return float(raw or _DEFAULT_TOTAL_MARKS)
    except Exception:
        pass
    return _DEFAULT_TOTAL_MARKS


def set_default_total_marks(value: float) -> None:
    try:
        if hasattr(db, "set_settings_bulk"):
            db.set_settings_bulk({"results_default_total_marks": str(value)})
        elif hasattr(db, "set_setting"):
            db.set_setting("results_default_total_marks", str(value))
    except Exception as e:
        print(f"[results_window] could not save default total: {e}")


def get_pass_percent() -> float:
    """Subject-level minimum % to pass (from results_engine)."""
    try:
        return float(results_engine.get_passing_criteria()["min_subject_percent"])
    except Exception:
        return 33.0


def get_grade_bands() -> list[tuple[str, float, float]]:
    try:
        bands = results_engine.get_grading_bands()
        if bands:
            return [(str(g), float(mn), float(mx)) for g, mn, mx in bands]
    except Exception:
        pass
    return list(_DEFAULT_GRADE_BANDS)


def _grade_for_percent(pct: float) -> str:
    try:
        return results_engine.grade_for_percent(pct)
    except Exception:
        for grade, min_p, max_p in get_grade_bands():
            if min_p <= pct <= max_p:
                return grade
        return "N/A"


def _pass_for_percent(pct: float) -> bool:
    return pct >= get_pass_percent()


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


def list_active_classes() -> list[str]:
    rows = db.run(
        "SELECT DISTINCT class_sec FROM students "
        "WHERE class_sec IS NOT NULL AND TRIM(class_sec) <> '' "
        "AND COALESCE(status,'Active')='Active' ORDER BY class_sec",
        fetchall=True,
    ) or []
    return [r[0] for r in rows if r[0]]


def students_in_class(class_sec: str) -> list[tuple[str, str]]:
    """Return list of (student_id, name) for active students in class."""
    rows = db.run(
        """
        SELECT student_id, name FROM students
        WHERE class_sec=? AND COALESCE(status,'Active')='Active'
        ORDER BY name
        """,
        (class_sec,),
        fetchall=True,
    ) or []
    return [(r[0], r[1] or "") for r in rows]


def existing_mark(student_id: str, exam_type: str, subject_name: str) -> Optional[tuple[float, float]]:
    row = db.run(
        """
        SELECT obtained_marks, total_marks FROM marks
        WHERE student_id=? AND exam_type=? AND subject_name=?
        ORDER BY id DESC LIMIT 1
        """,
        (student_id, exam_type, subject_name),
        fetchone=True,
    )
    if not row:
        return None
    return float(row[0] or 0), float(row[1] or 100)


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
    """Return (rank, class_size, percentage) for this student within class."""
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

        # Batch entry state
        self._batch_rows: list[dict] = []  # {sid, name, obt_var, tot_var, pct_lbl, grade_lbl, status_lbl, dirty}
        self._batch_class = ""
        self._batch_exam = ""
        self._batch_subject = ""

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
    # Modern chrome helpers (buttons / fields / tabs — same functions, better UX)
    # ------------------------------------------------------------------
    def _apply_modern_chrome(self):
        """Ttk + shared field look: taller inputs, quieter tabs, consistent spacing."""
        try:
            style = ttk.Style()
            # Prefer a theme that respects custom colors when available
            try:
                style.theme_use("clam")
            except Exception:
                pass

            style.configure(
                "TNotebook",
                background=theme.SILVER,
                borderwidth=0,
            )
            style.configure(
                "TNotebook.Tab",
                padding=(14, 8),
                font=theme.FONT_BODY,
            )
            style.map(
                "TNotebook.Tab",
                background=[("selected", theme.WHITE), ("!selected", theme.SILVER)],
                foreground=[("selected", theme.NAVY), ("!selected", theme.TEXT_MUTED)],
            )
            style.configure(
                "Treeview",
                rowheight=28,
                font=theme.FONT_BODY,
                background=theme.WHITE,
                fieldbackground=theme.WHITE,
            )
            style.configure(
                "Treeview.Heading",
                font=theme.FONT_BODY_BOLD,
                background=theme.NAVY,
                foreground="white",
            )
            style.configure(
                "TCombobox",
                padding=4,
                font=theme.FONT_BODY,
            )
        except Exception as e:
            print(f"[results_window] chrome style skipped: {e}")

    @staticmethod
    def _modern_entry(parent, **kwargs):
        """Standard entry: readable height, light border feel via ipady."""
        kwargs.setdefault("font", theme.FONT_BODY)
        kwargs.setdefault("relief", "solid")
        kwargs.setdefault("bd", 1)
        kwargs.setdefault("highlightthickness", 0)
        ent = tk.Entry(parent, **kwargs)
        return ent

    # ------------------------------------------------------------------
    def _build(self):
        for child in self.container.winfo_children():
            child.destroy()

        self._apply_modern_chrome()

        outer = tk.Frame(self.container, bg=theme.SILVER)
        outer.pack(fill=tk.BOTH, expand=True)

        # Header
        header = tk.Frame(outer, bg=theme.NAVY, padx=18, pady=12)
        header.pack(fill=tk.X, padx=8, pady=(8, 4))

        left = tk.Frame(header, bg=theme.NAVY)
        left.pack(side=tk.LEFT, fill=tk.X, expand=True)
        tk.Label(
            left, text="Results & Academics",
            font=theme.FONT_H1, bg=theme.NAVY, fg="white",
        ).pack(anchor="w")
        tk.Label(
            left,
            text="Marks  ·  Student  ·  Class  ·  Reports  ·  History  ·  Setup",
            font=theme.FONT_SMALL, bg=theme.NAVY, fg="#94a3b8",
        ).pack(anchor="w", pady=(2, 0))

        right = tk.Frame(header, bg=theme.NAVY)
        right.pack(side=tk.RIGHT)
        tk.Label(
            right,
            text=f"  {self.current_user}  ·  {self.user_role}  ",
            font=theme.FONT_SMALL, bg="#1e293b", fg="#e2e8f0",
            padx=10, pady=4,
        ).pack(side=tk.RIGHT)

        # Primary tabs — History is its own tab (admin checks often)
        self.nb = ttk.Notebook(outer)
        self.nb.pack(fill=tk.BOTH, expand=True, padx=8, pady=(0, 8))

        self.tab_entry = tk.Frame(self.nb, bg=theme.SILVER)
        self.tab_overview = tk.Frame(self.nb, bg=theme.SILVER)
        self.tab_analytics = tk.Frame(self.nb, bg=theme.SILVER)
        self.tab_reports = tk.Frame(self.nb, bg=theme.SILVER)
        self.tab_history = tk.Frame(self.nb, bg=theme.SILVER)
        self.tab_setup = tk.Frame(self.nb, bg=theme.SILVER)

        self.nb.add(self.tab_entry, text="  Marks  ")
        self.nb.add(self.tab_overview, text="  Student  ")
        if self.can_view:
            self.nb.add(self.tab_analytics, text="  Class  ")
        self.nb.add(self.tab_reports, text="  Reports  ")
        self.nb.add(self.tab_history, text="  History  ")
        self.nb.add(self.tab_setup, text="  Setup  ")

        self._build_entry_tab()
        self._build_overview_tab()
        if self.can_view:
            self._build_analytics_tab()
        self._build_reports_tab()
        self._build_history_tab()
        self._build_setup_hub()

        self.nb.bind("<<NotebookTabChanged>>", self._on_tab_changed)

    def _build_setup_hub(self):
        """Setup only: Subjects + Policy (History is a main tab)."""
        f = self.tab_setup
        for w in f.winfo_children():
            w.destroy()

        intro = tk.Frame(f, bg=theme.SILVER, padx=12, pady=8)
        intro.pack(fill=tk.X)
        tk.Label(
            intro, text="Setup",
            font=theme.FONT_BODY_BOLD, bg=theme.SILVER, fg=theme.TEXT_DARK,
        ).pack(side=tk.LEFT)
        tk.Label(
            intro,
            text="  Subjects configuration  ·  Pass / grade policy",
            font=theme.FONT_SMALL, bg=theme.SILVER, fg=theme.TEXT_MUTED,
        ).pack(side=tk.LEFT)

        self.setup_nb = ttk.Notebook(f)
        self.setup_nb.pack(fill=tk.BOTH, expand=True, padx=8, pady=(0, 8))

        # IMPORTANT: tab frames must be children of the Notebook
        self.tab_subjects = tk.Frame(self.setup_nb, bg=theme.SILVER)
        self.tab_settings = tk.Frame(self.setup_nb, bg=theme.SILVER)

        has_any = False
        if self.can_manage_subjects:
            self.setup_nb.add(self.tab_subjects, text="  Subjects  ")
            self._build_subjects_tab()
            has_any = True

        if self.can_manage_subjects or self.can_edit_marks or self.can_view:
            self.setup_nb.add(self.tab_settings, text="  Policy  ")
            self._build_settings_tab()
            has_any = True

        if not has_any:
            empty = tk.Frame(self.setup_nb, bg=theme.SILVER)
            self.setup_nb.add(empty, text="  Info  ")
            tk.Label(
                empty,
                text="Is role ke liye Setup options available nahi hain.",
                font=theme.FONT_BODY, bg=theme.SILVER, fg=theme.TEXT_MUTED,
            ).pack(padx=20, pady=30)

    def _build_entry_tab(self):
        f = self.tab_entry
        pad = tk.Frame(f, bg=theme.SILVER, padx=12, pady=10)
        pad.pack(fill=tk.BOTH, expand=True)

        # ---- Filter header ----
        card, body = theme.section_card(pad, "Select class, exam & subject")
        card.pack(fill=tk.X, pady=(0, 8))

        row = tk.Frame(body, bg=theme.WHITE)
        row.pack(fill=tk.X, pady=4)

        tk.Label(row, text="Class / Section", font=theme.FONT_SMALL, bg=theme.WHITE, fg=theme.TEXT_MUTED).grid(
            row=0, column=0, sticky="w", padx=(0, 8)
        )
        self.cmb_batch_class = ttk.Combobox(row, values=[], state="readonly", width=16, font=theme.FONT_BODY)
        self.cmb_batch_class.grid(row=1, column=0, sticky="w", padx=(0, 14), pady=(0, 4))

        tk.Label(row, text="Examination", font=theme.FONT_SMALL, bg=theme.WHITE, fg=theme.TEXT_MUTED).grid(
            row=0, column=1, sticky="w", padx=(0, 8)
        )
        self.cmb_batch_exam = ttk.Combobox(
            row, values=self.EXAM_TYPES, state="readonly", width=16, font=theme.FONT_BODY
        )
        self.cmb_batch_exam.current(0)
        self.cmb_batch_exam.grid(row=1, column=1, sticky="w", padx=(0, 14), pady=(0, 4))

        tk.Label(row, text="Subject", font=theme.FONT_SMALL, bg=theme.WHITE, fg=theme.TEXT_MUTED).grid(
            row=0, column=2, sticky="w", padx=(0, 8)
        )
        self.cmb_batch_subject = ttk.Combobox(row, values=[], state="readonly", width=18, font=theme.FONT_BODY)
        self.cmb_batch_subject.grid(row=1, column=2, sticky="w", padx=(0, 14), pady=(0, 4))

        btn_frame = tk.Frame(row, bg=theme.WHITE)
        btn_frame.grid(row=1, column=3, sticky="w", padx=(4, 0), pady=(0, 4))
        theme.primary_button(btn_frame, "Load class", self.load_batch_class, bg=theme.SLATE).pack(
            side=tk.LEFT, padx=(0, 8)
        )
        self.btn_save_all = theme.primary_button(
            btn_frame, "Save all marks", self.save_all_batch_marks, bg=theme.SUCCESS
        )
        self.btn_save_all.pack(side=tk.LEFT)
        if not self.can_edit_marks:
            self.btn_save_all.config(state="disabled")

        self.lbl_batch_hint = tk.Label(
            body,
            text="Select Class, Exam and Subject, then press Load Class List.",
            font=theme.FONT_SMALL, bg=theme.WHITE, fg=theme.TEXT_MUTED, anchor="w",
        )
        self.lbl_batch_hint.pack(fill=tk.X, pady=(6, 2))

        # Wire class → subjects
        self.cmb_batch_class.bind("<<ComboboxSelected>>", lambda e: self._on_batch_class_changed())
        self._refresh_batch_class_options()

        # ---- Spreadsheet grid ----
        card2, body2 = theme.section_card(pad, "Class marks grid")
        card2.pack(fill=tk.BOTH, expand=True)

        # Header row
        hdr = tk.Frame(body2, bg=theme.NAVY, padx=4, pady=6)
        hdr.pack(fill=tk.X)
        col_specs = [
            ("#", 4),
            ("Student ID", 12),
            ("Student Name", 22),
            ("Obtained", 10),
            ("Total", 10),
            ("Percent", 9),
            ("Grade", 8),
            ("Status", 8),
        ]
        for text, width in col_specs:
            tk.Label(
                hdr, text=text, font=theme.FONT_BODY_BOLD, bg=theme.NAVY, fg="white",
                width=width, anchor="center",
            ).pack(side=tk.LEFT, padx=2)

        # Scrollable body
        canvas_frame = tk.Frame(body2, bg=theme.WHITE)
        canvas_frame.pack(fill=tk.BOTH, expand=True)

        self.batch_canvas = tk.Canvas(canvas_frame, bg=theme.WHITE, highlightthickness=0)
        self.batch_vsb = ttk.Scrollbar(canvas_frame, orient="vertical", command=self.batch_canvas.yview)
        self.batch_inner = tk.Frame(self.batch_canvas, bg=theme.WHITE)

        self.batch_inner.bind(
            "<Configure>",
            lambda e: self.batch_canvas.configure(scrollregion=self.batch_canvas.bbox("all")),
        )
        self.batch_canvas.create_window((0, 0), window=self.batch_inner, anchor="nw")
        self.batch_canvas.configure(yscrollcommand=self.batch_vsb.set)

        self.batch_canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        self.batch_vsb.pack(side=tk.RIGHT, fill=tk.Y)

        # Mousewheel only while pointer is over the batch grid (avoid global lag)
        def _on_mousewheel(event):
            self.batch_canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")
            return "break"

        def _bind_wheel(_e=None):
            self.batch_canvas.bind_all("<MouseWheel>", _on_mousewheel)

        def _unbind_wheel(_e=None):
            self.batch_canvas.unbind_all("<MouseWheel>")

        self.batch_canvas.bind("<Enter>", _bind_wheel)
        self.batch_canvas.bind("<Leave>", _unbind_wheel)
        self.batch_inner.bind("<Enter>", _bind_wheel)
        self.batch_inner.bind("<Leave>", _unbind_wheel)

        # Student lookup lives on Student tab (cleaner Marks screen)

    def _build_legacy_lookup_collapsed(self, parent):
        """Quick lookup so Result Overview / marksheet can load a single student."""
        card, body = theme.section_card(
            parent,
            "Result Overview ke liye student load karein (ya batch grid par double-click)",
        )
        card.pack(fill=tk.X, pady=(8, 0))

        row = tk.Frame(body, bg=theme.WHITE)
        row.pack(fill=tk.X, pady=2)
        tk.Label(row, text="Student ID:", font=theme.FONT_BODY_BOLD, bg=theme.WHITE).pack(side=tk.LEFT)
        self.ent_sid = tk.Entry(row, font=theme.FONT_BODY, width=16)
        self.ent_sid.pack(side=tk.LEFT, padx=8, ipady=3)
        self.ent_sid.bind("<Return>", lambda e: self.load_student())
        theme.primary_button(row, "🔍 Load + Open Overview", self.load_student_and_open_overview, bg=theme.BRAND_BLUE).pack(
            side=tk.LEFT, padx=4
        )
        theme.primary_button(row, "Load only", self.load_student, bg=theme.SLATE).pack(side=tk.LEFT, padx=4)

        self.lbl_student_info = tk.Label(
            body,
            text="Student ID daalein OR batch grid mein kisi student par DOUBLE-CLICK karein → Result Overview khulega.",
            font=theme.FONT_SMALL, bg=theme.WHITE, fg=theme.TEXT_MUTED, anchor="w",
        )
        self.lbl_student_info.pack(fill=tk.X, pady=(4, 0))

    def _on_tab_changed(self, _event=None):
        """Refresh Student Result when that primary tab is selected."""
        try:
            selected = self.nb.nametowidget(self.nb.select())
            if selected is self.tab_overview and self.student:
                self.refresh_result_overview()
        except Exception:
            pass

    def _refresh_batch_class_options(self):
        classes = list_active_classes()
        self.cmb_batch_class.config(values=classes)
        if classes and not self.cmb_batch_class.get():
            self.cmb_batch_class.set(classes[0])
            self._on_batch_class_changed()

    def _on_batch_class_changed(self):
        cls = self.cmb_batch_class.get().strip()
        subs = subjects_for_class(cls)
        self.cmb_batch_subject.config(values=subs)
        if subs:
            self.cmb_batch_subject.set(subs[0])
            self.lbl_batch_hint.config(
                text=f"Class {cls}: {len(subs)} active subject(s). Select exam & subject, then Load.",
                fg=theme.SUCCESS,
            )
        else:
            self.cmb_batch_subject.set("")
            self.lbl_batch_hint.config(
                text=f"No subjects configured for class '{cls}'. Add them under Subjects Setup.",
                fg=theme.WARNING,
            )

    def load_batch_class(self):
        cls = self.cmb_batch_class.get().strip()
        exam = self.cmb_batch_exam.get().strip()
        subject = self.cmb_batch_subject.get().strip()

        if not cls:
            messagebox.showerror("Error", "Select a Class / Section.", parent=self.root)
            return
        if not exam:
            messagebox.showerror("Error", "Select an Examination.", parent=self.root)
            return
        if not subject:
            messagebox.showerror("Error", "Select a Subject.", parent=self.root)
            return

        students = students_in_class(cls)
        if not students:
            messagebox.showinfo(
                "No Students",
                f"No active students found in class '{cls}'.",
                parent=self.root,
            )
            return

        self._batch_class = cls
        self._batch_exam = exam
        self._batch_subject = subject
        self._clear_batch_grid()
        self._batch_rows = []

        # Freeze UI updates while building many rows (reduces lag)
        try:
            self.batch_canvas.config(cursor="watch")
            self.root.config(cursor="watch")
            self.root.update_idletasks()
        except Exception:
            pass

        for idx, (sid, name) in enumerate(students, start=1):
            existing = existing_mark(sid, exam, subject)
            obt_val = "" if existing is None else str(existing[0])
            tot_val = str(get_default_total_marks()) if existing is None else str(existing[1])

            row_bg = theme.WHITE if idx % 2 else "#f8fafc"
            row_frame = tk.Frame(self.batch_inner, bg=row_bg, padx=2, pady=3)
            row_frame.pack(fill=tk.X)

            def _make_dbl(sid_val=sid):
                def _handler(_event=None):
                    self._load_student_by_id(sid_val, open_overview=True)
                return _handler

            dbl = _make_dbl()
            lbl_idx = tk.Label(row_frame, text=str(idx), font=theme.FONT_SMALL, bg=row_bg, width=4, anchor="center")
            lbl_idx.pack(side=tk.LEFT, padx=2)
            lbl_sid = tk.Label(row_frame, text=sid, font=theme.FONT_SMALL, bg=row_bg, width=12, anchor="w")
            lbl_sid.pack(side=tk.LEFT, padx=2)
            lbl_name = tk.Label(row_frame, text=name[:24], font=theme.FONT_SMALL, bg=row_bg, width=22, anchor="w")
            lbl_name.pack(side=tk.LEFT, padx=2)
            for w in (lbl_idx, lbl_sid, lbl_name, row_frame):
                w.bind("<Double-Button-1>", dbl)
                w.configure(cursor="hand2")

            obt_var = tk.StringVar(value=obt_val)
            tot_var = tk.StringVar(value=tot_val)

            ent_obt = tk.Entry(row_frame, textvariable=obt_var, font=theme.FONT_BODY, width=10, justify="center")
            ent_obt.pack(side=tk.LEFT, padx=2, ipady=2)
            ent_tot = tk.Entry(row_frame, textvariable=tot_var, font=theme.FONT_BODY, width=10, justify="center")
            ent_tot.pack(side=tk.LEFT, padx=2, ipady=2)

            pct_lbl = tk.Label(row_frame, text="—", font=theme.FONT_SMALL, bg=row_frame["bg"], width=9, anchor="center")
            pct_lbl.pack(side=tk.LEFT, padx=2)
            grade_lbl = tk.Label(row_frame, text="—", font=theme.FONT_SMALL, bg=row_frame["bg"], width=8, anchor="center")
            grade_lbl.pack(side=tk.LEFT, padx=2)
            status_lbl = tk.Label(
                row_frame, text="—", font=theme.FONT_BODY_BOLD, bg=row_frame["bg"], width=8, anchor="center"
            )
            status_lbl.pack(side=tk.LEFT, padx=2)

            row_data = {
                "sid": sid,
                "name": name,
                "obt_var": obt_var,
                "tot_var": tot_var,
                "ent_obt": ent_obt,
                "ent_tot": ent_tot,
                "pct_lbl": pct_lbl,
                "grade_lbl": grade_lbl,
                "status_lbl": status_lbl,
                "dirty": False,
                "original_obt": obt_val,
                "original_tot": tot_val,
            }
            self._batch_rows.append(row_data)

            # Live calc on key release / focus out (cheaper than trace on every keystroke)
            def _on_change(_event=None, rd=row_data):
                rd["dirty"] = True
                self._recalc_row(rd)

            ent_obt.bind("<KeyRelease>", _on_change)
            ent_tot.bind("<KeyRelease>", _on_change)
            ent_obt.bind("<FocusOut>", _on_change)
            ent_tot.bind("<FocusOut>", _on_change)

            # Initial calc
            self._recalc_row(row_data)

        # Wire sequential navigation after all rows exist
        for i, rd in enumerate(self._batch_rows):
            next_rd = self._batch_rows[(i + 1) % len(self._batch_rows)]
            rd["ent_obt"].bind(
                "<Return>",
                lambda e, t=next_rd["ent_obt"]: (t.focus_set(), t.select_range(0, tk.END), "break")[-1],
            )
            rd["ent_obt"].bind(
                "<Down>",
                lambda e, t=next_rd["ent_obt"]: (t.focus_set(), t.select_range(0, tk.END), "break")[-1],
            )
            rd["ent_tot"].bind(
                "<Return>",
                lambda e, t=next_rd["ent_obt"]: (t.focus_set(), t.select_range(0, tk.END), "break")[-1],
            )

        try:
            self.batch_canvas.config(cursor="")
            self.root.config(cursor="")
        except Exception:
            pass

        self.lbl_batch_hint.config(
            text=f"Loaded {len(students)} student(s) · {cls} · {exam} · {subject}. "
                 f"Edit marks live. DOUBLE-CLICK student name/ID → Result Overview. Save All when done.",
            fg=theme.BRAND_BLUE,
        )
        if self._batch_rows:
            self._batch_rows[0]["ent_obt"].focus_set()

    def _clear_batch_grid(self):
        for child in self.batch_inner.winfo_children():
            child.destroy()
        self._batch_rows = []

    def _recalc_row(self, rd: dict):
        try:
            obt_txt = (rd["obt_var"].get() or "").strip()
            tot_txt = (rd["tot_var"].get() or "").strip()
            if not obt_txt:
                rd["pct_lbl"].config(text="—", fg=theme.TEXT_MUTED)
                rd["grade_lbl"].config(text="—", fg=theme.TEXT_MUTED)
                rd["status_lbl"].config(text="—", fg=theme.TEXT_MUTED)
                return
            obt = float(obt_txt)
            tot = float(tot_txt) if tot_txt else 100.0
            if tot <= 0:
                rd["pct_lbl"].config(text="ERR", fg=theme.DANGER)
                rd["grade_lbl"].config(text="—", fg=theme.DANGER)
                rd["status_lbl"].config(text="—", fg=theme.DANGER)
                return
            pct = (obt / tot) * 100.0
            grade = _grade_for_percent(pct)
            passed = _pass_for_percent(pct)
            rd["pct_lbl"].config(text=f"{pct:.1f}%", fg=theme.TEXT_DARK)
            rd["grade_lbl"].config(text=grade, fg=theme.TEXT_DARK)
            rd["status_lbl"].config(
                text="PASS" if passed else "FAIL",
                fg=theme.SUCCESS if passed else theme.DANGER,
            )
        except ValueError:
            rd["pct_lbl"].config(text="—", fg=theme.WARNING)
            rd["grade_lbl"].config(text="—", fg=theme.WARNING)
            rd["status_lbl"].config(text="—", fg=theme.WARNING)

    def save_all_batch_marks(self):
        if not self.can_edit_marks:
            messagebox.showerror(
                "Permission Denied",
                "You are not allowed to edit marks.",
                parent=self.root,
            )
            return
        if not self._batch_rows:
            messagebox.showinfo("Nothing to Save", "Load a class list first.", parent=self.root)
            return

        exam = self._batch_exam
        subject = self._batch_subject
        if not (exam and subject):
            messagebox.showerror("Error", "Exam / Subject missing. Reload the class list.", parent=self.root)
            return

        to_save: list[tuple[str, float, float]] = []
        errors: list[str] = []

        for rd in self._batch_rows:
            obt_txt = (rd["obt_var"].get() or "").strip()
            if not obt_txt:
                continue  # skip empty rows
            try:
                obt = float(obt_txt)
                tot = float((rd["tot_var"].get() or "100").strip() or 100)
            except ValueError:
                errors.append(f"{rd['sid']} ({rd['name']}): invalid number")
                continue
            if obt < 0 or tot <= 0:
                errors.append(f"{rd['sid']}: obtained ≥ 0 and total > 0 required")
                continue
            if obt > tot:
                errors.append(f"{rd['sid']}: obtained ({obt}) > total ({tot})")
                continue
            to_save.append((rd["sid"], obt, tot))

        if errors:
            messagebox.showerror(
                "Validation Errors",
                "Fix these rows before saving:\n\n" + "\n".join(errors[:12]),
                parent=self.root,
            )
            return
        if not to_save:
            messagebox.showinfo("Nothing to Save", "No marks entered.", parent=self.root)
            return

        saved = 0
        updated = 0
        try:
            for sid, obt, tot in to_save:
                action = upsert_marks(sid, exam, subject, obt, tot, self.current_user)
                if action == "updated":
                    updated += 1
                else:
                    saved += 1
        except Exception as e:
            messagebox.showerror("Database Error", f"Save failed:\n{e}", parent=self.root)
            return

        _log_activity(
            self.current_user,
            f"Batch saved marks for {self._batch_class} / {exam} / {subject}: "
            f"{saved} inserted, {updated} updated ({len(to_save)} total)",
        )

        # Reset dirty flags & originals
        for rd in self._batch_rows:
            rd["dirty"] = False
            rd["original_obt"] = rd["obt_var"].get()
            rd["original_tot"] = rd["tot_var"].get()

        self.load_history_table()
        # If a student is currently loaded in Overview, refresh their result
        if self.student:
            self.refresh_result_overview()
        messagebox.showinfo(
            "Success",
            f"Saved {len(to_save)} mark(s) for {subject} ({exam}).\n"
            f"Inserted: {saved}  ·  Updated: {updated}",
            parent=self.root,
        )

    # ------------------------------------------------------------------
    # Single-student helpers (used by Result Overview / marksheet)
    # ------------------------------------------------------------------
    def _load_student_by_id(self, sid: str, *, open_overview: bool = False) -> bool:
        """Load student into self.student. Returns True on success."""
        sid = (sid or "").strip()
        if not sid:
            self.lbl_student_info.config(
                text="Student ID daalein ya batch grid par double-click karein.",
                fg=theme.TEXT_MUTED,
            )
            self.student = None
            return False

        self.student = fetch_student_info(sid)
        if not self.student:
            self.lbl_student_info.config(
                text=f"⚠ No student found with ID '{sid}'.",
                fg=theme.DANGER,
            )
            return False

        if hasattr(self, "ent_sid"):
            self.ent_sid.delete(0, tk.END)
            self.ent_sid.insert(0, sid)

        s = self.student
        info = (
            f"ID: {s['student_id']}   ·   Name: {s['name']}   ·   "
            f"Father: {s['father_name'] or '—'}   ·   Class: {s['class_sec'] or '—'}   ·   "
            f"Session: {s['academic_session'] or '—'}   ·   Status: {s['status']}"
        )
        self.lbl_student_info.config(text=info, fg=theme.TEXT_DARK)
        self.refresh_result_overview()

        if open_overview:
            try:
                self.nb.select(self.tab_overview)
            except Exception:
                pass
        return True

    def load_student(self):
        sid = self.ent_sid.get().strip() if hasattr(self, "ent_sid") else ""
        self._load_student_by_id(sid, open_overview=False)

    def load_student_and_open_overview(self):
        sid = self.ent_sid.get().strip() if hasattr(self, "ent_sid") else ""
        ok = self._load_student_by_id(sid, open_overview=True)
        if not ok and sid:
            messagebox.showerror("Not Found", f"Student ID '{sid}' nahi mila.", parent=self.root)

    # ==================================================================
    # Result Overview
    # ==================================================================
    def _build_overview_tab(self):
        f = self.tab_overview
        pad = tk.Frame(f, bg=theme.SILVER, padx=12, pady=10)
        pad.pack(fill=tk.BOTH, expand=True)

        # —— Load student (primary action on this tab) ——
        card_l, body_l = theme.section_card(pad, "Load student")
        card_l.pack(fill=tk.X, pady=(0, 8))
        lr = tk.Frame(body_l, bg=theme.WHITE)
        lr.pack(fill=tk.X, pady=2)
        tk.Label(lr, text="Student ID", font=theme.FONT_BODY_BOLD, bg=theme.WHITE).pack(side=tk.LEFT)
        self.ent_sid = tk.Entry(lr, font=theme.FONT_BODY, width=16)
        self.ent_sid.pack(side=tk.LEFT, padx=8, ipady=3)
        self.ent_sid.bind("<Return>", lambda e: self.load_student())
        theme.primary_button(lr, "Load student", self.load_student, bg=theme.BRAND_BLUE).pack(side=tk.LEFT, padx=4)
        theme.primary_button(lr, "Recalculate", self.refresh_result_overview, bg=theme.SLATE).pack(
            side=tk.LEFT, padx=4
        )
        self.lbl_student_info = tk.Label(
            body_l,
            text="ID enter karein — ya Marks tab ke grid par student DOUBLE-CLICK karein.",
            font=theme.FONT_SMALL, bg=theme.WHITE, fg=theme.TEXT_MUTED, anchor="w",
        )
        self.lbl_student_info.pack(fill=tk.X, pady=(4, 0))

        # Exam filter row
        ctrl = tk.Frame(pad, bg=theme.SILVER)
        ctrl.pack(fill=tk.X, pady=(0, 8))
        tk.Label(ctrl, text="Exam filter", font=theme.FONT_BODY_BOLD, bg=theme.SILVER).pack(side=tk.LEFT)
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

        self.lbl_ov_student = tk.Label(
            pad,
            text="Student load hone ke baad yahan summary, rank aur marksheet actions dikhenge.",
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
                text="Pehle student load karein: Marks Entry tab → neeche Student ID daal kar "
                     "'Load + Open Overview' dabayein, YA batch list mein student name/ID par DOUBLE-CLICK karein.",
                fg=theme.WARNING,
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
                text="Student load hone ke baad class rank yahan dikhega.",
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
    # Report Center — Personal / Class / Exam / School-wide PDFs
    # ==================================================================
    def _build_reports_tab(self):
        f = self.tab_reports

        # Scrollable shell so bottom cards never get clipped
        shell = tk.Frame(f, bg=theme.SILVER)
        shell.pack(fill=tk.BOTH, expand=True)

        canvas = tk.Canvas(shell, bg=theme.SILVER, highlightthickness=0)
        vsb = ttk.Scrollbar(shell, orient="vertical", command=canvas.yview)
        pad = tk.Frame(canvas, bg=theme.SILVER)

        pad.bind(
            "<Configure>",
            lambda e: canvas.configure(scrollregion=canvas.bbox("all")),
        )
        win_id = canvas.create_window((0, 0), window=pad, anchor="nw")

        def _sync_width(event):
            canvas.itemconfigure(win_id, width=event.width)

        canvas.bind("<Configure>", _sync_width)
        canvas.configure(yscrollcommand=vsb.set)
        canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        vsb.pack(side=tk.RIGHT, fill=tk.Y)

        def _wheel(event):
            canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")
            return "break"

        def _bind_w(_e=None):
            canvas.bind_all("<MouseWheel>", _wheel)

        def _unbind_w(_e=None):
            canvas.unbind_all("<MouseWheel>")

        canvas.bind("<Enter>", _bind_w)
        canvas.bind("<Leave>", _unbind_w)
        pad.bind("<Enter>", _bind_w)
        pad.bind("<Leave>", _unbind_w)

        inner_pad = tk.Frame(pad, bg=theme.SILVER, padx=12, pady=10)
        inner_pad.pack(fill=tk.BOTH, expand=True)

        # Compact banner
        banner = tk.Frame(inner_pad, bg=theme.NAVY, padx=14, pady=8)
        banner.pack(fill=tk.X, pady=(0, 8))
        tk.Label(
            banner, text="Reports",
            font=theme.FONT_H1, bg=theme.NAVY, fg="white",
        ).pack(anchor="w")
        tk.Label(
            banner,
            text="Personal · Class record · Individual files (bhejne ke liye) · Exam sheet · Whole school",
            font=theme.FONT_SMALL, bg=theme.NAVY, fg="#cbd5e1",
        ).pack(anchor="w", pady=(2, 0))

        # Shared filters (compact)
        card_f, body_f = theme.section_card(inner_pad, "Common Filters")
        card_f.pack(fill=tk.X, pady=(0, 8))

        filt = tk.Frame(body_f, bg=theme.WHITE)
        filt.pack(fill=tk.X, pady=2)

        tk.Label(filt, text="Class", font=theme.FONT_SMALL, bg=theme.WHITE, fg=theme.TEXT_MUTED).grid(
            row=0, column=0, sticky="w", padx=(0, 8)
        )
        self.cmb_rpt_class = ttk.Combobox(filt, values=[], state="readonly", width=14, font=theme.FONT_BODY)
        self.cmb_rpt_class.grid(row=1, column=0, sticky="w", padx=(0, 12), pady=(0, 2))

        tk.Label(filt, text="Examination", font=theme.FONT_SMALL, bg=theme.WHITE, fg=theme.TEXT_MUTED).grid(
            row=0, column=1, sticky="w", padx=(0, 8)
        )
        self.cmb_rpt_exam = ttk.Combobox(
            filt, values=["All Exams"] + self.EXAM_TYPES, state="readonly", width=14, font=theme.FONT_BODY
        )
        self.cmb_rpt_exam.set("All Exams")
        self.cmb_rpt_exam.grid(row=1, column=1, sticky="w", padx=(0, 12), pady=(0, 2))

        tk.Label(filt, text="Student ID (personal)", font=theme.FONT_SMALL, bg=theme.WHITE, fg=theme.TEXT_MUTED).grid(
            row=0, column=2, sticky="w", padx=(0, 8)
        )
        self.ent_rpt_sid = tk.Entry(filt, font=theme.FONT_BODY, width=14)
        self.ent_rpt_sid.grid(row=1, column=2, sticky="w", padx=(0, 8), pady=(0, 2), ipady=2)

        if self.student:
            self.ent_rpt_sid.insert(0, self.student["student_id"])
        classes = list_active_classes()
        self.cmb_rpt_class.config(values=classes)
        if classes:
            self.cmb_rpt_class.set(classes[0])

        # Compact action cards — 2 columns, short text so nothing clips
        actions_wrap = tk.Frame(inner_pad, bg=theme.SILVER)
        actions_wrap.pack(fill=tk.X, pady=(0, 6))

        def _action_card(parent, title, desc, btn_text, cmd, accent="#7c3aed"):
            outer = tk.Frame(parent, bg=theme.WHITE, highlightbackground="#e2e8f0", highlightthickness=1)
            outer.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=4, pady=4)
            bar = tk.Frame(outer, bg=accent, height=3)
            bar.pack(fill=tk.X)
            inner = tk.Frame(outer, bg=theme.WHITE, padx=10, pady=8)
            inner.pack(fill=tk.BOTH, expand=True)
            tk.Label(
                inner, text=title, font=theme.FONT_BODY_BOLD, bg=theme.WHITE, fg=theme.TEXT_DARK, anchor="w",
            ).pack(fill=tk.X)
            tk.Label(
                inner, text=desc, font=theme.FONT_SMALL, bg=theme.WHITE, fg=theme.TEXT_MUTED,
                wraplength=280, justify="left", anchor="w",
            ).pack(fill=tk.X, pady=(2, 6))
            theme.primary_button(inner, btn_text, cmd, bg=accent).pack(anchor="w")
            return outer

        r1 = tk.Frame(actions_wrap, bg=theme.SILVER)
        r1.pack(fill=tk.X)
        _action_card(
            r1, "① Personal Report Card",
            "Ek student — dena / WhatsApp. Student ID daalein.",
            "🧾 Personal PDF", self.rpt_generate_personal, "#7c3aed",
        )
        _action_card(
            r1, "② Class multi-page PDF",
            "Poori class ek PDF (har student alag page) — office record.",
            "📚 Class PDF (record)", self.rpt_generate_class, "#2563eb",
        )

        r2 = tk.Frame(actions_wrap, bg=theme.SILVER)
        r2.pack(fill=tk.X)
        _action_card(
            r2, "③ Alag PDF har student",
            "Folder mein har student ki alag file — bhejne / print ke liye.",
            "📂 Individual PDFs → folder", self.rpt_generate_class_individual_files, "#db2777",
        )
        _action_card(
            r2, "④ Exam consolidated sheet",
            "Selected exam ki class sheet — meeting / proceedings.",
            "📝 Exam sheet", self.rpt_generate_exam_sheet, "#0891b2",
        )

        r3 = tk.Frame(actions_wrap, bg=theme.SILVER)
        r3.pack(fill=tk.X)
        _action_card(
            r3, "⑤ Whole School (Principal)",
            "Har class ka PDF → folder. Ek click school pack.",
            "🏫 All classes → folder", self.rpt_generate_school, "#16a34a",
        )
        # empty spacer so left card doesn't stretch full width alone
        tk.Frame(r3, bg=theme.SILVER).pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=4, pady=4)

        # Status log (fixed small height)
        card_s, body_s = theme.section_card(inner_pad, "Generation Status")
        card_s.pack(fill=tk.X, pady=(4, 8))
        self.txt_rpt_log = tk.Text(
            body_s, height=5, wrap="word", font=theme.FONT_SMALL,
            bg="#f8fafc", relief="solid", bd=1, padx=6, pady=4,
        )
        self.txt_rpt_log.pack(fill=tk.X)
        self.txt_rpt_log.insert(
            "1.0",
            "Ready. Filters set karein, phir option choose karein. "
            "Neeche tak scroll karke saari options dekh sakte ho.\n",
        )
        self.txt_rpt_log.config(state="disabled")

    def _rpt_log(self, msg: str):
        try:
            self.txt_rpt_log.config(state="normal")
            self.txt_rpt_log.insert("end", msg + "\n")
            self.txt_rpt_log.see("end")
            self.txt_rpt_log.config(state="disabled")
            # Lightweight refresh — avoid heavy update_idletasks every line
            self.txt_rpt_log.update_idletasks()
        except Exception:
            pass

    def _rpt_exam_filter(self) -> Optional[str]:
        sel = self.cmb_rpt_exam.get()
        return None if sel == "All Exams" else sel

    def _rpt_exam_label(self) -> str:
        return self.cmb_rpt_exam.get() or "All Exams"

    def rpt_generate_personal(self):
        """Single student full report card PDF."""
        sid = (self.ent_rpt_sid.get() or "").strip()
        if not sid and self.student:
            sid = self.student["student_id"]
        if not sid:
            messagebox.showerror(
                "Student Required",
                "Personal report ke liye Student ID daalein (ya pehle student load karein).",
                parent=self.root,
            )
            return

        info = fetch_student_info(sid)
        if not info:
            messagebox.showerror("Not Found", f"Student '{sid}' nahi mila.", parent=self.root)
            return

        exam = self._rpt_exam_filter()
        result = results_engine.compute_result(sid, exam)
        if not result:
            messagebox.showinfo(
                "No Marks",
                f"'{sid}' ke liye is exam filter par marks nahi mile.",
                parent=self.root,
            )
            return

        path = filedialog.asksaveasfilename(
            title="Save Personal Report Card",
            defaultextension=".pdf",
            initialfile=f"ReportCard_{sid}_{self._rpt_exam_label().replace(' ', '_')}.pdf",
            filetypes=[("PDF Files", "*.pdf")],
            parent=self.root,
        )
        if not path:
            return

        self._rpt_log(f"Generating personal report: {sid} ({self._rpt_exam_label()})…")
        try:
            _generate_marksheet_extended(
                sid, info["name"], info["class_sec"], result, path,
                exam_label=self._rpt_exam_label(),
                father_name=info.get("father_name") or "",
                session=info.get("academic_session") or "",
                remarks="",
                rank_text="",
            )
        except Exception as e:
            self._rpt_log(f"ERROR: {e}")
            messagebox.showerror("PDF Error", str(e), parent=self.root)
            return

        _log_activity(self.current_user, f"Report Center: personal report {sid} ({self._rpt_exam_label()})")
        self._rpt_log(f"✓ Saved: {path}")
        opened = _try_open_file(path)
        messagebox.showinfo(
            "Report Ready",
            f"Personal report card {'opened' if opened else 'saved'}:\n{path}",
            parent=self.root,
        )

    def rpt_generate_class(self):
        """Multi-page PDF: one report card page per student in the class."""
        cls = self.cmb_rpt_class.get().strip()
        if not cls:
            messagebox.showerror("Class Required", "Class / Section select karein.", parent=self.root)
            return

        exam = self._rpt_exam_filter()
        students = students_in_class(cls)
        if not students:
            messagebox.showinfo("No Students", f"Class '{cls}' mein active students nahi.", parent=self.root)
            return

        pages = []
        for sid, name in students:
            result = results_engine.compute_result(sid, exam)
            if not result:
                continue
            info = fetch_student_info(sid) or {
                "student_id": sid, "name": name, "class_sec": cls,
                "father_name": "", "academic_session": "",
            }
            pages.append((info, result))

        if not pages:
            messagebox.showinfo(
                "No Marks",
                f"Class '{cls}' ke liye is exam filter par koi evaluated student nahi.",
                parent=self.root,
            )
            return

        path = filedialog.asksaveasfilename(
            title="Save Class Report Cards PDF",
            defaultextension=".pdf",
            initialfile=f"ClassReports_{cls.replace(' ', '_').replace('/', '-')}_{self._rpt_exam_label().replace(' ', '_')}.pdf",
            filetypes=[("PDF Files", "*.pdf")],
            parent=self.root,
        )
        if not path:
            return

        self._rpt_log(f"Class reports: {cls} · {self._rpt_exam_label()} · {len(pages)} student(s)…")
        try:
            _generate_multi_student_report_cards(pages, path, exam_label=self._rpt_exam_label())
        except Exception as e:
            self._rpt_log(f"ERROR: {e}")
            messagebox.showerror("PDF Error", str(e), parent=self.root)
            return

        _log_activity(
            self.current_user,
            f"Report Center: class reports {cls} ({self._rpt_exam_label()}), {len(pages)} pages",
        )
        self._rpt_log(f"✓ Saved {len(pages)} page(s): {path}")
        opened = _try_open_file(path)
        messagebox.showinfo(
            "Class Reports Ready",
            f"{len(pages)} report card(s) {'opened' if opened else 'saved'}:\n{path}",
            parent=self.root,
        )

    def rpt_generate_class_individual_files(self):
        """One separate PDF per student in a folder — for WhatsApp / hand-over."""
        cls = self.cmb_rpt_class.get().strip()
        if not cls:
            messagebox.showerror("Class Required", "Class / Section select karein.", parent=self.root)
            return

        exam = self._rpt_exam_filter()
        exam_label = self._rpt_exam_label()
        students = students_in_class(cls)
        if not students:
            messagebox.showinfo("No Students", f"Class '{cls}' mein active students nahi.", parent=self.root)
            return

        folder = filedialog.askdirectory(
            title="Folder choose karein (har student ki alag PDF yahan save hogi)",
            parent=self.root,
        )
        if not folder:
            return

        # Subfolder for this class + exam
        safe_cls = str(cls).replace(" ", "_").replace("/", "-")
        safe_exam = exam_label.replace(" ", "_")
        out_dir = os.path.join(folder, f"ReportCards_{safe_cls}_{safe_exam}")
        os.makedirs(out_dir, exist_ok=True)

        self._rpt_log(f"Individual PDFs: {cls} · {exam_label} → {out_dir}")
        made = 0
        skipped = 0

        for sid, name in students:
            result = results_engine.compute_result(sid, exam)
            if not result:
                skipped += 1
                continue
            info = fetch_student_info(sid) or {
                "student_id": sid, "name": name, "class_sec": cls,
                "father_name": "", "academic_session": "",
            }
            safe_name = "".join(ch if ch.isalnum() or ch in "._-" else "_" for ch in (name or sid))[:40]
            out_path = os.path.join(out_dir, f"{sid}_{safe_name}.pdf")
            try:
                _generate_multi_student_report_cards(
                    [(info, result)], out_path, exam_label=exam_label
                )
                made += 1
            except Exception as e:
                self._rpt_log(f"  ✗ {sid}: {e}")

        _log_activity(
            self.current_user,
            f"Report Center: individual PDFs {cls} ({exam_label}) — {made} files",
        )
        self._rpt_log(f"✓ {made} PDF(s) saved, {skipped} skipped (no marks)")
        messagebox.showinfo(
            "Individual Report Cards Ready",
            f"Class: {cls}\nExam: {exam_label}\n\n"
            f"PDFs created: {made}\nSkipped (no marks): {skipped}\n\n"
            f"Folder:\n{out_dir}\n\n"
            "In files ko print karke de sakte ho ya student/parent ke personal number pe bhej sakte ho.",
            parent=self.root,
        )

    def rpt_generate_exam_sheet(self):
        """Consolidated class mark sheet for the selected exam (like Analytics PDF)."""
        cls = self.cmb_rpt_class.get().strip()
        if not cls:
            messagebox.showerror("Class Required", "Class / Section select karein.", parent=self.root)
            return

        exam = self._rpt_exam_filter()
        exam_label = self._rpt_exam_label()
        students = students_in_class(cls)
        rows = []
        for sid, name in students:
            result = results_engine.compute_result(sid, exam)
            if result:
                rows.append((sid, name, result))

        if not rows:
            messagebox.showinfo(
                "No Data",
                f"Class '{cls}' / {exam_label} par evaluated students nahi mile.",
                parent=self.root,
            )
            return

        path = filedialog.asksaveasfilename(
            title="Save Exam Report Sheet",
            defaultextension=".pdf",
            initialfile=f"ExamReport_{cls.replace(' ', '_').replace('/', '-')}_{exam_label.replace(' ', '_')}.pdf",
            filetypes=[("PDF Files", "*.pdf")],
            parent=self.root,
        )
        if not path:
            return

        self._rpt_log(f"Exam sheet: {cls} · {exam_label} · {len(rows)} student(s)…")
        try:
            _generate_class_marksheet_pdf(cls, exam_label, rows, path)
        except Exception as e:
            self._rpt_log(f"ERROR: {e}")
            messagebox.showerror("PDF Error", str(e), parent=self.root)
            return

        _log_activity(
            self.current_user,
            f"Report Center: exam sheet {cls} ({exam_label}), {len(rows)} students",
        )
        self._rpt_log(f"✓ Saved: {path}")
        opened = _try_open_file(path)
        messagebox.showinfo(
            "Exam Report Ready",
            f"Exam report {'opened' if opened else 'saved'}:\n{path}",
            parent=self.root,
        )

    def rpt_generate_school(self):
        """Principal one-click: one multi-page report PDF per active class → folder."""
        classes = list_active_classes()
        if not classes:
            messagebox.showinfo("No Classes", "Koi active class nahi mili.", parent=self.root)
            return

        folder = filedialog.askdirectory(
            title="Select folder for school-wide report cards",
            parent=self.root,
        )
        if not folder:
            return

        exam = self._rpt_exam_filter()
        exam_label = self._rpt_exam_label()
        if not messagebox.askyesno(
            "Confirm School-wide Generation",
            f"{len(classes)} class(es) ke liye report cards generate hongi.\n"
            f"Exam filter: {exam_label}\n"
            f"Output folder:\n{folder}\n\nContinue?",
            parent=self.root,
        ):
            return

        self._rpt_log(f"School-wide start · {len(classes)} classes · exam={exam_label}")
        total_pages = 0
        files_made = 0
        skipped = []

        for cls in classes:
            students = students_in_class(cls)
            pages = []
            for sid, name in students:
                result = results_engine.compute_result(sid, exam)
                if not result:
                    continue
                info = fetch_student_info(sid) or {
                    "student_id": sid, "name": name, "class_sec": cls,
                    "father_name": "", "academic_session": "",
                }
                pages.append((info, result))

            if not pages:
                skipped.append(cls)
                self._rpt_log(f"  · skip {cls} (no marks)")
                continue

            safe = str(cls).replace(" ", "_").replace("/", "-")
            out = os.path.join(
                folder,
                f"ClassReports_{safe}_{exam_label.replace(' ', '_')}.pdf",
            )
            try:
                _generate_multi_student_report_cards(pages, out, exam_label=exam_label)
                files_made += 1
                total_pages += len(pages)
                self._rpt_log(f"  ✓ {cls}: {len(pages)} pages → {os.path.basename(out)}")
            except Exception as e:
                self._rpt_log(f"  ✗ {cls}: {e}")

        _log_activity(
            self.current_user,
            f"Report Center: school-wide ({exam_label}) — {files_made} files, {total_pages} pages",
        )
        summary = (
            f"Done.\n\nPDFs created: {files_made}\nTotal student pages: {total_pages}\n"
            f"Skipped classes (no marks): {len(skipped)}"
            + (f"\n  {', '.join(skipped[:8])}" if skipped else "")
            + f"\n\nFolder:\n{folder}"
        )
        self._rpt_log(f"Finished · {files_made} file(s), {total_pages} page(s)")
        messagebox.showinfo("School Reports Ready", summary, parent=self.root)

    # ==================================================================
    # Class Analytics
    # ==================================================================
    def _build_analytics_tab(self):
        f = self.tab_analytics
        pad = tk.Frame(f, bg=theme.SILVER, padx=12, pady=10)
        pad.pack(fill=tk.BOTH, expand=True)

        self._analytics_rows: list[dict] = []
        self._analytics_class = ""
        self._analytics_exam_label = "All Exams"

        # ---- Filters ----
        card_f, body_f = theme.section_card(pad, "Class-Wise Filter")
        card_f.pack(fill=tk.X, pady=(0, 8))

        row = tk.Frame(body_f, bg=theme.WHITE)
        row.pack(fill=tk.X, pady=4)

        tk.Label(row, text="Class / Section:", font=theme.FONT_BODY_BOLD, bg=theme.WHITE).pack(side=tk.LEFT)
        self.cmb_analytics_class = ttk.Combobox(row, values=[], state="readonly", width=16, font=theme.FONT_BODY)
        self.cmb_analytics_class.pack(side=tk.LEFT, padx=(6, 16))

        tk.Label(row, text="Exam filter:", font=theme.FONT_BODY_BOLD, bg=theme.WHITE).pack(side=tk.LEFT)
        self.cmb_analytics_exam = ttk.Combobox(
            row, values=["All Exams"] + self.EXAM_TYPES, state="readonly", width=16, font=theme.FONT_BODY,
        )
        self.cmb_analytics_exam.set("All Exams")
        self.cmb_analytics_exam.pack(side=tk.LEFT, padx=(6, 16))

        theme.primary_button(
            row, "↻ Load Analytics", self.refresh_class_analytics, bg=theme.SLATE,
        ).pack(side=tk.LEFT)

        row2 = tk.Frame(body_f, bg=theme.WHITE)
        row2.pack(fill=tk.X, pady=(10, 4))
        tk.Label(
            row2,
            text="Batch test groups (multi-select — used for consolidated mark sheet):",
            font=theme.FONT_SMALL, bg=theme.WHITE, fg=theme.TEXT_MUTED,
        ).pack(anchor="w")
        self.lst_batch_exams = tk.Listbox(
            row2, selectmode=tk.EXTENDED, font=theme.FONT_SMALL, height=4,
            bg="#f8fafc", relief="solid", bd=1, selectbackground=theme.BRAND_BLUE,
            exportselection=False,
        )
        for et in self.EXAM_TYPES:
            self.lst_batch_exams.insert(tk.END, et)
        self.lst_batch_exams.pack(fill=tk.X, pady=(4, 0))

        # ---- KPI cards ----
        tk.Label(
            pad, text="Class Summary", font=theme.FONT_BODY_BOLD, bg=theme.SILVER, fg=theme.TEXT_DARK,
        ).pack(anchor="w", pady=(4, 4))
        self.analytics_cards_row = tk.Frame(pad, bg=theme.SILVER)
        self.analytics_cards_row.pack(fill=tk.X, pady=(0, 6))

        tk.Label(
            pad, text="Grade Breakdown", font=theme.FONT_BODY_BOLD, bg=theme.SILVER, fg=theme.TEXT_DARK,
        ).pack(anchor="w", pady=(0, 4))
        self.analytics_grade_row = tk.Frame(pad, bg=theme.SILVER)
        self.analytics_grade_row.pack(fill=tk.X, pady=(0, 8))

        # ---- Chart placeholder area ----
        card_ch, body_ch = theme.section_card(pad, "Visual Analytics (charts)")
        card_ch.pack(fill=tk.X, pady=(0, 8))
        chart_row = tk.Frame(body_ch, bg=theme.WHITE)
        chart_row.pack(fill=tk.X, pady=4)
        theme.primary_button(
            chart_row, "📊 Show Grade Distribution", self._show_analytics_grade_chart, bg=theme.BRAND_BLUE
        ).pack(side=tk.LEFT, padx=(0, 8))
        theme.primary_button(
            chart_row, "📈 Show Subject Averages", self._show_analytics_subject_chart, bg=theme.SLATE
        ).pack(side=tk.LEFT)
        self.lbl_chart_hint = tk.Label(
            body_ch,
            text="Load analytics first, then open a chart. Requires matplotlib.",
            font=theme.FONT_SMALL, bg=theme.WHITE, fg=theme.TEXT_MUTED,
        )
        self.lbl_chart_hint.pack(anchor="w", pady=(4, 0))

        # ---- Data table ----
        card_t, body_t = theme.section_card(pad, "Class Result Sheet")
        card_t.pack(fill=tk.BOTH, expand=True, pady=(0, 8))

        cols = ("id", "name", "obtained", "total", "percent", "grade", "result")
        style = ttk.Style()
        style.configure("Analytics.Treeview", rowheight=26)
        self.tree_analytics = ttk.Treeview(
            body_t, columns=cols, show="headings", height=12, style="Analytics.Treeview",
        )
        headings = [
            ("id", "Student ID", 100), ("name", "Name", 170), ("obtained", "Obtained", 90),
            ("total", "Total", 80), ("percent", "Percentage", 100), ("grade", "Grade", 70),
            ("result", "Result", 90),
        ]
        for c, h, w in headings:
            self.tree_analytics.heading(c, text=h)
            self.tree_analytics.column(c, width=w, anchor="center")
        self.tree_analytics.tag_configure("PASS", foreground=theme.SUCCESS)
        self.tree_analytics.tag_configure("FAIL", foreground=theme.DANGER)
        self.tree_analytics.pack(fill=tk.BOTH, expand=True, side=tk.LEFT)
        sb3 = ttk.Scrollbar(body_t, orient="vertical", command=self.tree_analytics.yview)
        self.tree_analytics.configure(yscrollcommand=sb3.set)
        sb3.pack(side=tk.RIGHT, fill=tk.Y)

        # ---- Actions ----
        actions = tk.Frame(pad, bg=theme.SILVER)
        actions.pack(fill=tk.X, pady=(4, 0))
        theme.primary_button(
            actions, "🧾 Generate Consolidated Mark Sheet PDF",
            self.generate_class_marksheet, bg="#7c3aed",
        ).pack(side=tk.LEFT, padx=(0, 8))

        self._refresh_analytics_class_options()
        self.cmb_analytics_class.bind("<<ComboboxSelected>>", lambda e: self.refresh_class_analytics())
        self.cmb_analytics_exam.bind("<<ComboboxSelected>>", lambda e: self.refresh_class_analytics())

    def _refresh_analytics_class_options(self):
        classes = list_active_classes()
        self.cmb_analytics_class.config(values=classes)
        if classes and not self.cmb_analytics_class.get():
            self.cmb_analytics_class.set(classes[0])
            self.refresh_class_analytics()

    def _clear_analytics_cards(self):
        for w in self.analytics_cards_row.winfo_children():
            w.destroy()
        for w in self.analytics_grade_row.winfo_children():
            w.destroy()

    def refresh_class_analytics(self):
        self.tree_analytics.delete(*self.tree_analytics.get_children())
        self._clear_analytics_cards()
        self._analytics_rows = []

        cls = self.cmb_analytics_class.get().strip()
        if not cls:
            theme.stat_card(
                self.analytics_cards_row, "Class", "Select a class", accent=theme.TEXT_MUTED,
            ).pack(side=tk.LEFT, fill=tk.X, expand=True)
            return

        exam_sel = self.cmb_analytics_exam.get()
        exam = None if exam_sel == "All Exams" else exam_sel
        self._analytics_class = cls
        self._analytics_exam_label = "All Exams" if exam is None else exam

        students = db.run(
            "SELECT student_id, name FROM students "
            "WHERE class_sec=? AND COALESCE(status,'Active')='Active' ORDER BY name",
            (cls,), fetchall=True,
        ) or []

        grade_counts: dict[str, int] = {}
        pass_count = 0
        fail_count = 0

        for sid, name in students:
            result = results_engine.compute_result(sid, exam)
            if not result:
                continue
            grade_counts[result["grade"]] = grade_counts.get(result["grade"], 0) + 1
            passed = result["passed"]
            if passed:
                pass_count += 1
            else:
                fail_count += 1
            tag = "PASS" if passed else "FAIL"
            self.tree_analytics.insert(
                "", tk.END, tags=(tag,),
                values=(
                    sid, name, f"{result['total_obtained']:.1f}", f"{result['total_marks']:.0f}",
                    f"{result['percentage']:.1f}%", result["grade"], tag,
                ),
            )
            self._analytics_rows.append({"student_id": sid, "name": name, "result": result})

        evaluated = pass_count + fail_count
        for label, value, accent in [
            ("Class Size", str(len(students)), theme.NAVY),
            ("Evaluated", str(evaluated), theme.BRAND_BLUE),
            ("Pass", str(pass_count), theme.SUCCESS),
            ("Fail", str(fail_count), theme.DANGER),
        ]:
            theme.stat_card(self.analytics_cards_row, label, value, accent=accent).pack(
                side=tk.LEFT, fill=tk.X, expand=True, padx=6,
            )

        bands = results_engine.get_grading_bands()
        if bands:
            for grade, _min_p, _max_p in bands:
                count = grade_counts.get(grade, 0)
                theme.stat_card(
                    self.analytics_grade_row, f"Grade {grade}", str(count), accent=theme.WARNING,
                ).pack(side=tk.LEFT, fill=tk.X, expand=True, padx=6)
        else:
            theme.stat_card(
                self.analytics_grade_row, "Grades", "No grading bands configured", accent=theme.TEXT_MUTED,
            ).pack(side=tk.LEFT, fill=tk.X, expand=True, padx=6)

        self.lbl_chart_hint.config(
            text=f"Ready · {evaluated} evaluated student(s) in {cls}.",
            fg=theme.SUCCESS,
        )

    def _show_analytics_grade_chart(self):
        if not HAS_MATPLOTLIB:
            messagebox.showerror("Unavailable", "Matplotlib is not installed.", parent=self.root)
            return
        if not self._analytics_rows:
            messagebox.showinfo("No Data", "Load class analytics first.", parent=self.root)
            return
        counts: dict[str, int] = {}
        for entry in self._analytics_rows:
            g = entry["result"]["grade"]
            counts[g] = counts.get(g, 0) + 1
        if not counts:
            return
        labels = list(counts.keys())
        values = list(counts.values())
        fig, ax = plt.subplots(figsize=(6, 4))
        ax.pie(values, labels=labels, autopct="%1.0f%%", startangle=90)
        ax.set_title(f"Grade Distribution — {self._analytics_class}")
        fig.tight_layout()
        plt.show()

    def _show_analytics_subject_chart(self):
        if not HAS_MATPLOTLIB:
            messagebox.showerror("Unavailable", "Matplotlib is not installed.", parent=self.root)
            return
        if not self._analytics_rows:
            messagebox.showinfo("No Data", "Load class analytics first.", parent=self.root)
            return
        # Average percent per subject across evaluated students
        sub_totals: dict[str, list[float]] = {}
        for entry in self._analytics_rows:
            for s in entry["result"].get("subjects") or []:
                sub_totals.setdefault(s["subject"], []).append(float(s["percent"]))
        if not sub_totals:
            messagebox.showinfo("No Data", "No subject-level data available.", parent=self.root)
            return
        subjects = sorted(sub_totals.keys())
        avgs = [sum(sub_totals[s]) / len(sub_totals[s]) for s in subjects]
        fig, ax = plt.subplots(figsize=(8, 4))
        ax.bar(subjects, avgs, color="#2563eb", width=0.5)
        ax.set_ylabel("Average %")
        ax.set_title(f"Subject Averages — {self._analytics_class}")
        ax.tick_params(axis="x", rotation=30)
        ax.axhline(y=33, color="#dc2626", linestyle=":", linewidth=1)
        ax.grid(axis="y", linestyle="--", alpha=0.4)
        fig.tight_layout()
        plt.show()

    def generate_class_marksheet(self):
        if not self._analytics_rows:
            messagebox.showinfo(
                "No Data",
                "Load class analytics first (pick a class with at least one evaluated student).",
                parent=self.root,
            )
            return

        cls = self._analytics_class or self.cmb_analytics_class.get()
        batch_sel = [self.lst_batch_exams.get(i) for i in self.lst_batch_exams.curselection()]

        if batch_sel:
            rows_for_pdf = []
            for entry in self._analytics_rows:
                combined = results_engine.compute_combined_result(entry["student_id"], batch_sel)
                if combined:
                    rows_for_pdf.append((entry["student_id"], entry["name"], combined))
            exam_label = "Batch: " + " + ".join(batch_sel)
        else:
            rows_for_pdf = [
                (entry["student_id"], entry["name"], entry["result"]) for entry in self._analytics_rows
            ]
            exam_label = self._analytics_exam_label

        if not rows_for_pdf:
            messagebox.showinfo(
                "No Data", "No marks found for the selected batch test group(s).", parent=self.root,
            )
            return

        safe_cls = str(cls).replace(" ", "_").replace("/", "-")
        default_name = f"Class_{safe_cls}_Consolidated_{datetime.now().strftime('%Y%m%d_%H%M%S')}.pdf"
        path = filedialog.asksaveasfilename(
            title="Save Consolidated Mark Sheet",
            defaultextension=".pdf",
            initialfile=default_name,
            filetypes=[("PDF Files", "*.pdf")],
            parent=self.root,
        )
        if not path:
            return

        try:
            _generate_class_marksheet_pdf(cls, exam_label, rows_for_pdf, path)
        except Exception as e:
            messagebox.showerror(
                "Report Error", f"Could not generate consolidated mark sheet:\n{e}", parent=self.root,
            )
            return

        _log_activity(
            self.current_user,
            f"Generated consolidated class mark sheet for {cls} ({exam_label}), "
            f"{len(rows_for_pdf)} student(s)",
        )
        opened = _try_open_file(path)
        messagebox.showinfo(
            "Mark Sheet Ready",
            (f"Consolidated mark sheet opened:\n{path}" if opened else f"Consolidated mark sheet saved:\n{path}"),
            parent=self.root,
        )

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
        self._refresh_batch_class_options()  # keep batch dropdown in sync
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
        self._refresh_batch_class_options()

    # ==================================================================
    # Settings — pass %, grades, policy
    # ==================================================================
    def _build_settings_tab(self):
        f = self.tab_settings

        # Scrollable shell — grade buttons / Save kabhi cut na hon
        shell = tk.Frame(f, bg=theme.SILVER)
        shell.pack(fill=tk.BOTH, expand=True)

        canvas = tk.Canvas(shell, bg=theme.SILVER, highlightthickness=0)
        vsb = ttk.Scrollbar(shell, orient="vertical", command=canvas.yview)
        pad = tk.Frame(canvas, bg=theme.SILVER)

        pad.bind(
            "<Configure>",
            lambda e: canvas.configure(scrollregion=canvas.bbox("all")),
        )
        win_id = canvas.create_window((0, 0), window=pad, anchor="nw")

        def _sync_width(event):
            canvas.itemconfigure(win_id, width=event.width)

        canvas.bind("<Configure>", _sync_width)
        canvas.configure(yscrollcommand=vsb.set)
        canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        vsb.pack(side=tk.RIGHT, fill=tk.Y)

        def _wheel(event):
            canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")
            return "break"

        def _bind_w(_e=None):
            canvas.bind_all("<MouseWheel>", _wheel)

        def _unbind_w(_e=None):
            canvas.unbind_all("<MouseWheel>")

        canvas.bind("<Enter>", _bind_w)
        canvas.bind("<Leave>", _unbind_w)
        pad.bind("<Enter>", _bind_w)
        pad.bind("<Leave>", _unbind_w)

        inner = tk.Frame(pad, bg=theme.SILVER, padx=12, pady=10)
        inner.pack(fill=tk.BOTH, expand=True)

        banner = tk.Frame(inner, bg=theme.NAVY, padx=14, pady=8)
        banner.pack(fill=tk.X, pady=(0, 8))
        tk.Label(
            banner, text="RESULTS SETTINGS",
            font=theme.FONT_H1, bg=theme.NAVY, fg="white",
        ).pack(anchor="w")
        tk.Label(
            banner,
            text="Pass rules · grade bands · default total — results_engine (scroll for grade buttons)",
            font=theme.FONT_SMALL, bg=theme.NAVY, fg="#cbd5e1",
        ).pack(anchor="w", pady=(2, 0))

        try:
            criteria = results_engine.get_passing_criteria()
        except Exception:
            criteria = {
                "min_overall_percent": 33.0,
                "require_pass_each_subject": True,
                "min_subject_percent": 33.0,
            }

        card1, body1 = theme.section_card(inner, "Pass / Fail criteria")
        card1.pack(fill=tk.X, pady=(0, 8))

        row = tk.Frame(body1, bg=theme.WHITE)
        row.pack(fill=tk.X, pady=4)

        tk.Label(row, text="Min overall % to pass", font=theme.FONT_BODY_BOLD, bg=theme.WHITE).grid(
            row=0, column=0, sticky="w", padx=(0, 8)
        )
        self.ent_set_overall = tk.Entry(row, font=theme.FONT_BODY, width=10, justify="center")
        self.ent_set_overall.grid(row=1, column=0, sticky="w", padx=(0, 16), pady=(0, 4), ipady=2)
        self.ent_set_overall.insert(0, str(criteria.get("min_overall_percent", 33)))

        tk.Label(row, text="Min % per subject", font=theme.FONT_BODY_BOLD, bg=theme.WHITE).grid(
            row=0, column=1, sticky="w", padx=(0, 8)
        )
        self.ent_set_subject = tk.Entry(row, font=theme.FONT_BODY, width=10, justify="center")
        self.ent_set_subject.grid(row=1, column=1, sticky="w", padx=(0, 16), pady=(0, 4), ipady=2)
        self.ent_set_subject.insert(0, str(criteria.get("min_subject_percent", 33)))

        tk.Label(row, text="Default total marks", font=theme.FONT_BODY_BOLD, bg=theme.WHITE).grid(
            row=0, column=2, sticky="w", padx=(0, 8)
        )
        self.ent_set_total = tk.Entry(row, font=theme.FONT_BODY, width=10, justify="center")
        self.ent_set_total.grid(row=1, column=2, sticky="w", padx=(0, 16), pady=(0, 4), ipady=2)
        self.ent_set_total.insert(0, str(get_default_total_marks()))

        self.var_require_each = tk.BooleanVar(
            value=bool(criteria.get("require_pass_each_subject", True))
        )
        tk.Checkbutton(
            body1,
            text="Require passing each individual subject (overall pass ke saath)",
            variable=self.var_require_each,
            bg=theme.WHITE,
            font=theme.FONT_BODY,
            activebackground=theme.WHITE,
        ).pack(anchor="w", pady=(4, 2))

        # ---- Grade bands + ALWAYS-VISIBLE edit buttons (inside same card) ----
        card2, body2 = theme.section_card(inner, "Grade bands — edit / add / remove")
        card2.pack(fill=tk.X, pady=(0, 8))

        tk.Label(
            body2,
            text="List se grade select karein → fields bhar jayenge → Add/Update. Overlap mat rakhein.",
            font=theme.FONT_SMALL, bg=theme.WHITE, fg=theme.TEXT_MUTED,
        ).pack(anchor="w", pady=(0, 4))

        tree_wrap = tk.Frame(body2, bg=theme.WHITE)
        tree_wrap.pack(fill=tk.X, pady=(0, 6))

        cols = ("grade", "min_p", "max_p")
        self.tree_grades = ttk.Treeview(tree_wrap, columns=cols, show="headings", height=6)
        for c, h, w in [
            ("grade", "Grade", 100),
            ("min_p", "Min %", 100),
            ("max_p", "Max %", 100),
        ]:
            self.tree_grades.heading(c, text=h)
            self.tree_grades.column(c, width=w, anchor="center")
        self.tree_grades.pack(fill=tk.X, side=tk.LEFT, expand=True)
        sb = ttk.Scrollbar(tree_wrap, orient="vertical", command=self.tree_grades.yview)
        self.tree_grades.configure(yscrollcommand=sb.set)
        sb.pack(side=tk.RIGHT, fill=tk.Y)

        # Edit fields row
        edit = tk.Frame(body2, bg=theme.WHITE)
        edit.pack(fill=tk.X, pady=(4, 4))

        tk.Label(edit, text="Grade", font=theme.FONT_SMALL, bg=theme.WHITE).pack(side=tk.LEFT)
        self.ent_grade_name = tk.Entry(edit, font=theme.FONT_BODY, width=8)
        self.ent_grade_name.pack(side=tk.LEFT, padx=4, ipady=3)

        tk.Label(edit, text="Min %", font=theme.FONT_SMALL, bg=theme.WHITE).pack(side=tk.LEFT, padx=(10, 0))
        self.ent_grade_min = tk.Entry(edit, font=theme.FONT_BODY, width=8)
        self.ent_grade_min.pack(side=tk.LEFT, padx=4, ipady=3)

        tk.Label(edit, text="Max %", font=theme.FONT_SMALL, bg=theme.WHITE).pack(side=tk.LEFT, padx=(10, 0))
        self.ent_grade_max = tk.Entry(edit, font=theme.FONT_BODY, width=8)
        self.ent_grade_max.pack(side=tk.LEFT, padx=4, ipady=3)

        # Buttons row — clearly visible under fields
        btn_row = tk.Frame(body2, bg=theme.WHITE)
        btn_row.pack(fill=tk.X, pady=(6, 4))

        theme.primary_button(
            btn_row, "＋ Add / Update Grade", self._settings_add_grade, bg=theme.BRAND_BLUE
        ).pack(side=tk.LEFT, padx=(0, 8))
        theme.primary_button(
            btn_row, "🗑 Remove selected", self._settings_remove_grade, bg=theme.DANGER
        ).pack(side=tk.LEFT, padx=(0, 8))
        theme.primary_button(
            btn_row, "↺ Reset defaults", self._settings_reset_grades, bg=theme.SLATE
        ).pack(side=tk.LEFT, padx=(0, 8))

        self.tree_grades.bind("<<TreeviewSelect>>", self._settings_on_grade_select)

        # Save bar
        actions = tk.Frame(inner, bg=theme.SILVER)
        actions.pack(fill=tk.X, pady=(8, 16))
        theme.primary_button(
            actions, "✓ Save All Settings", self._settings_save_all, bg=theme.SUCCESS
        ).pack(side=tk.LEFT, padx=(0, 8))
        self.lbl_settings_status = tk.Label(
            actions, text="Grade change ke baad Save All zaroor dabayein.",
            font=theme.FONT_SMALL, bg=theme.SILVER, fg=theme.TEXT_MUTED,
        )
        self.lbl_settings_status.pack(side=tk.LEFT)

        self._settings_reload_grades_tree()

    def _settings_reload_grades_tree(self):
        self.tree_grades.delete(*self.tree_grades.get_children())
        for grade, min_p, max_p in get_grade_bands():
            self.tree_grades.insert(
                "", tk.END, values=(grade, f"{min_p:g}", f"{max_p:g}")
            )

    def _settings_on_grade_select(self, _event=None):
        sel = self.tree_grades.selection()
        if not sel:
            return
        vals = self.tree_grades.item(sel[0], "values")
        if not vals:
            return
        self.ent_grade_name.delete(0, tk.END)
        self.ent_grade_name.insert(0, vals[0])
        self.ent_grade_min.delete(0, tk.END)
        self.ent_grade_min.insert(0, vals[1])
        self.ent_grade_max.delete(0, tk.END)
        self.ent_grade_max.insert(0, vals[2])

    def _settings_add_grade(self):
        name = (self.ent_grade_name.get() or "").strip()
        try:
            min_p = float((self.ent_grade_min.get() or "").strip())
            max_p = float((self.ent_grade_max.get() or "").strip())
        except ValueError:
            messagebox.showerror("Invalid", "Min % aur Max % valid numbers hone chahiye.", parent=self.root)
            return
        if not name:
            messagebox.showerror("Invalid", "Grade name required.", parent=self.root)
            return
        if min_p > max_p:
            messagebox.showerror("Invalid", "Min % Max % se bada nahi ho sakta.", parent=self.root)
            return

        found = None
        for iid in self.tree_grades.get_children():
            if self.tree_grades.item(iid, "values")[0] == name:
                found = iid
                break
        if found:
            self.tree_grades.item(found, values=(name, f"{min_p:g}", f"{max_p:g}"))
        else:
            self.tree_grades.insert("", tk.END, values=(name, f"{min_p:g}", f"{max_p:g}"))
        self.ent_grade_name.delete(0, tk.END)
        self.ent_grade_min.delete(0, tk.END)
        self.ent_grade_max.delete(0, tk.END)

    def _settings_remove_grade(self):
        sel = self.tree_grades.selection()
        if not sel:
            messagebox.showinfo("Select", "Pehle list se grade select karein.", parent=self.root)
            return
        for iid in sel:
            self.tree_grades.delete(iid)

    def _settings_reset_grades(self):
        if not messagebox.askyesno(
            "Reset",
            "Grade bands default policy par reset ho jayengi (save ke baad apply).",
            parent=self.root,
        ):
            return
        self.tree_grades.delete(*self.tree_grades.get_children())
        for grade, min_p, max_p in _DEFAULT_GRADE_BANDS:
            self.tree_grades.insert("", tk.END, values=(grade, f"{min_p:g}", f"{max_p:g}"))
        self.ent_set_overall.delete(0, tk.END)
        self.ent_set_overall.insert(0, "33")
        self.ent_set_subject.delete(0, tk.END)
        self.ent_set_subject.insert(0, "33")
        self.var_require_each.set(True)
        self.ent_set_total.delete(0, tk.END)
        self.ent_set_total.insert(0, str(_DEFAULT_TOTAL_MARKS))

    def _settings_save_all(self):
        try:
            overall = float((self.ent_set_overall.get() or "").strip())
            subject = float((self.ent_set_subject.get() or "").strip())
            tot = float((self.ent_set_total.get() or "").strip())
        except ValueError:
            messagebox.showerror(
                "Invalid",
                "Overall %, subject % aur default total valid numbers hone chahiye.",
                parent=self.root,
            )
            return
        if tot <= 0:
            messagebox.showerror("Invalid", "Default total marks > 0 hona chahiye.", parent=self.root)
            return

        bands: list[tuple[str, float, float]] = []
        for iid in self.tree_grades.get_children():
            g, mn, mx = self.tree_grades.item(iid, "values")
            try:
                bands.append((str(g).strip(), float(mn), float(mx)))
            except ValueError:
                messagebox.showerror("Invalid", f"Grade '{g}' ki range invalid hai.", parent=self.root)
                return

        if not bands:
            messagebox.showerror("Invalid", "Kam az kam ek grade band zaroori hai.", parent=self.root)
            return

        try:
            results_engine.set_passing_criteria(
                overall, self.var_require_each.get(), subject
            )
            results_engine.set_grading_bands(bands)
            set_default_total_marks(tot)
        except Exception as e:
            messagebox.showerror("Save Error", str(e), parent=self.root)
            return

        _log_activity(
            self.current_user,
            f"Results settings saved via results_engine: overall={overall}, "
            f"subject={subject}, require_each={self.var_require_each.get()}, "
            f"grades={len(bands)}, default_total={tot}",
        )
        self.lbl_settings_status.config(
            text=f"Saved · overall {overall:g}% · subject {subject:g}% · {len(bands)} bands",
            fg=theme.SUCCESS,
        )
        messagebox.showinfo(
            "Settings Saved",
            f"Min overall %: {overall:g}\n"
            f"Min subject %: {subject:g}\n"
            f"Require each subject: {self.var_require_each.get()}\n"
            f"Grade bands: {len(bands)}\n"
            f"Default total marks: {tot:g}\n\n"
            "Ab Result Overview, Analytics, PDFs aur batch entry sab isi policy ko follow karenge.",
            parent=self.root,
        )
        self._settings_reload_grades_tree()

    # ==================================================================
    # Marks History
    # ==================================================================
    def _build_history_tab(self):
        f = self.tab_history
        pad = tk.Frame(f, bg=theme.SILVER, padx=12, pady=10)
        pad.pack(fill=tk.BOTH, expand=True)

        # ---- Filters card ----
        card, body = theme.section_card(pad, "Filters — Class · Exam · Subject · Student")
        card.pack(fill=tk.X, pady=(0, 8))

        row = tk.Frame(body, bg=theme.WHITE)
        row.pack(fill=tk.X, pady=4)

        tk.Label(row, text="Class", font=theme.FONT_SMALL, bg=theme.WHITE, fg=theme.TEXT_MUTED).grid(
            row=0, column=0, sticky="w", padx=(0, 6)
        )
        self.cmb_hist_class = ttk.Combobox(
            row, values=["All Classes"], state="readonly", width=14, font=theme.FONT_BODY
        )
        self.cmb_hist_class.set("All Classes")
        self.cmb_hist_class.grid(row=1, column=0, sticky="w", padx=(0, 10), pady=(0, 2))

        tk.Label(row, text="Examination", font=theme.FONT_SMALL, bg=theme.WHITE, fg=theme.TEXT_MUTED).grid(
            row=0, column=1, sticky="w", padx=(0, 6)
        )
        self.cmb_hist_exam = ttk.Combobox(
            row, values=["All Exams"] + self.EXAM_TYPES, state="readonly", width=14, font=theme.FONT_BODY
        )
        self.cmb_hist_exam.set("All Exams")
        self.cmb_hist_exam.grid(row=1, column=1, sticky="w", padx=(0, 10), pady=(0, 2))

        tk.Label(row, text="Subject", font=theme.FONT_SMALL, bg=theme.WHITE, fg=theme.TEXT_MUTED).grid(
            row=0, column=2, sticky="w", padx=(0, 6)
        )
        self.cmb_hist_subject = ttk.Combobox(
            row, values=["All Subjects"], state="readonly", width=16, font=theme.FONT_BODY
        )
        self.cmb_hist_subject.set("All Subjects")
        self.cmb_hist_subject.grid(row=1, column=2, sticky="w", padx=(0, 10), pady=(0, 2))

        tk.Label(row, text="Student ID", font=theme.FONT_SMALL, bg=theme.WHITE, fg=theme.TEXT_MUTED).grid(
            row=0, column=3, sticky="w", padx=(0, 6)
        )
        self.ent_hist_sid = tk.Entry(row, font=theme.FONT_BODY, width=12)
        self.ent_hist_sid.grid(row=1, column=3, sticky="w", padx=(0, 10), pady=(0, 2), ipady=2)

        btn_row = tk.Frame(body, bg=theme.WHITE)
        btn_row.pack(fill=tk.X, pady=(4, 2))
        theme.primary_button(btn_row, "↻ Apply Filters", self.load_history_table, bg=theme.SLATE).pack(
            side=tk.LEFT, padx=(0, 6)
        )
        theme.primary_button(btn_row, "Clear Filters", self._clear_history_filters, bg=theme.NAVY).pack(
            side=tk.LEFT, padx=(0, 6)
        )
        self.lbl_hist_count = tk.Label(
            btn_row, text="", font=theme.FONT_SMALL, bg=theme.WHITE, fg=theme.TEXT_MUTED,
        )
        self.lbl_hist_count.pack(side=tk.LEFT, padx=8)

        # Class change → refresh subject list for that class
        self.cmb_hist_class.bind("<<ComboboxSelected>>", lambda e: self._on_hist_class_changed())
        self.cmb_hist_exam.bind("<<ComboboxSelected>>", lambda e: self.load_history_table())
        self.cmb_hist_subject.bind("<<ComboboxSelected>>", lambda e: self.load_history_table())
        self.ent_hist_sid.bind("<Return>", lambda e: self.load_history_table())

        self._refresh_hist_filter_options()

        # ---- Table ----
        cols = ("id", "student_id", "class", "name", "exam", "subject", "obtained", "total", "percent", "by", "at")
        self.tree_hist = ttk.Treeview(pad, columns=cols, show="headings", height=16)
        headings = [
            ("id", "#", 45),
            ("student_id", "Student ID", 95),
            ("class", "Class", 80),
            ("name", "Name", 120),
            ("exam", "Examination", 100),
            ("subject", "Subject", 120),
            ("obtained", "Obtained", 70),
            ("total", "Total", 60),
            ("percent", "%", 60),
            ("by", "Entered By", 90),
            ("at", "Entered At", 130),
        ]
        for c, h, w in headings:
            self.tree_hist.heading(c, text=h)
            self.tree_hist.column(c, width=w, anchor="center")
        self.tree_hist.pack(fill=tk.BOTH, expand=True, side=tk.LEFT)
        sb = ttk.Scrollbar(pad, orient="vertical", command=self.tree_hist.yview)
        self.tree_hist.configure(yscrollcommand=sb.set)
        sb.pack(side=tk.RIGHT, fill=tk.Y)

        self.load_history_table()

    def _refresh_hist_filter_options(self):
        classes = list_active_classes()
        self.cmb_hist_class.config(values=["All Classes"] + classes)
        # Distinct subjects from marks (or subjects table)
        sub_rows = db.run(
            "SELECT DISTINCT subject_name FROM marks WHERE subject_name IS NOT NULL "
            "AND TRIM(subject_name) <> '' ORDER BY subject_name",
            fetchall=True,
        ) or []
        subjects = [r[0] for r in sub_rows if r[0]]
        self.cmb_hist_subject.config(values=["All Subjects"] + subjects)

    def _on_hist_class_changed(self):
        cls = self.cmb_hist_class.get().strip()
        if cls and cls != "All Classes":
            subs = subjects_for_class(cls)
            # Also include subjects that appear in marks for this class
            extra = db.run(
                """
                SELECT DISTINCT m.subject_name
                FROM marks m
                JOIN students s ON s.student_id = m.student_id
                WHERE s.class_sec=? AND m.subject_name IS NOT NULL
                ORDER BY m.subject_name
                """,
                (cls,),
                fetchall=True,
            ) or []
            for (sn,) in extra:
                if sn and sn not in subs:
                    subs.append(sn)
            self.cmb_hist_subject.config(values=["All Subjects"] + subs)
            self.cmb_hist_subject.set("All Subjects")
        else:
            self._refresh_hist_filter_options()
            self.cmb_hist_subject.set("All Subjects")
        self.load_history_table()

    def _clear_history_filters(self):
        self.cmb_hist_class.set("All Classes")
        self.cmb_hist_exam.set("All Exams")
        self.cmb_hist_subject.set("All Subjects")
        self.ent_hist_sid.delete(0, tk.END)
        self._refresh_hist_filter_options()
        self.load_history_table()

    def load_history_table(self):
        self.tree_hist.delete(*self.tree_hist.get_children())

        cls = ""
        exam = ""
        subject = ""
        sid_filter = ""
        if hasattr(self, "cmb_hist_class"):
            cls = self.cmb_hist_class.get().strip()
            if cls == "All Classes":
                cls = ""
        if hasattr(self, "cmb_hist_exam"):
            exam = self.cmb_hist_exam.get().strip()
            if exam == "All Exams":
                exam = ""
        if hasattr(self, "cmb_hist_subject"):
            subject = self.cmb_hist_subject.get().strip()
            if subject == "All Subjects":
                subject = ""
        if hasattr(self, "ent_hist_sid"):
            sid_filter = self.ent_hist_sid.get().strip()

        # Build dynamic WHERE with JOIN for class / name
        sql = """
            SELECT m.id, m.student_id, COALESCE(s.class_sec, ''), COALESCE(s.name, ''),
                   m.exam_type, m.subject_name, m.obtained_marks, m.total_marks,
                   m.entered_by, m.entered_at
            FROM marks m
            LEFT JOIN students s ON s.student_id = m.student_id
            WHERE 1=1
        """
        params: list = []

        if sid_filter:
            sql += " AND m.student_id = ?"
            params.append(sid_filter)
        if cls:
            sql += " AND s.class_sec = ?"
            params.append(cls)
        if exam:
            sql += " AND m.exam_type = ?"
            params.append(exam)
        if subject:
            sql += " AND m.subject_name = ?"
            params.append(subject)

        sql += " ORDER BY m.id DESC LIMIT 500"

        if params:
            rows = db.run(sql, tuple(params), fetchall=True) or []
        else:
            rows = db.run(sql, fetchall=True) or []

        for r in rows:
            m_id, s_id, class_sec, name, exam_t, sub, obt, tot, by, at = r
            pct = (obt / tot * 100) if tot and tot > 0 else 0
            self.tree_hist.insert(
                "",
                tk.END,
                values=(
                    m_id,
                    s_id,
                    class_sec or "—",
                    (name or "—")[:22],
                    exam_t or "—",
                    sub or "—",
                    f"{obt:.1f}" if obt is not None else "",
                    f"{tot:.1f}" if tot is not None else "",
                    f"{pct:.1f}%",
                    by or "—",
                    at or "—",
                ),
            )

        if hasattr(self, "lbl_hist_count"):
            parts = []
            if cls:
                parts.append(f"Class={cls}")
            if exam:
                parts.append(f"Exam={exam}")
            if subject:
                parts.append(f"Subject={subject}")
            if sid_filter:
                parts.append(f"ID={sid_filter}")
            filt_txt = " · ".join(parts) if parts else "All records"
            self.lbl_hist_count.config(text=f"{len(rows)} row(s)  ·  {filt_txt}")


# ---------------------------------------------------------------------------
# PDF helpers
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
    reports.generate_marksheet(
        student_id, name, cls, result, out_path, exam_label=exam_label
    )

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


def _generate_multi_student_report_cards(pages, out_path, exam_label="All Exams"):
    """One multi-page PDF: each (student_info, result) pair gets its own report card page.

    ``pages`` is a list of (info_dict, result_dict) where info_dict has at least
    student_id, name, class_sec and optionally father_name, academic_session.
    """
    from reportlab.pdfgen import canvas as rl_canvas
    from reportlab.lib.pagesizes import letter
    from reportlab.lib import colors

    NAVY = colors.HexColor("#0f172a")
    SILVER = colors.HexColor("#f1f5f9")
    SUCCESS = colors.HexColor("#16a34a")
    DANGER = colors.HexColor("#dc2626")
    MUTED = colors.HexColor("#64748b")
    WHITE = colors.white
    BLACK = colors.black
    ACCENT = colors.HexColor("#2563eb")

    c = rl_canvas.Canvas(out_path, pagesize=letter)

    for page_idx, (info, result) in enumerate(pages):
        if page_idx > 0:
            c.showPage()

        y = reports._draw_page_header(c, "STUDENT REPORT CARD")
        y = reports._kv_row(c, 50, y, "Student ID", str(info.get("student_id", "")))
        y = reports._kv_row(c, 50, y, "Name", str(info.get("name", "")))
        if info.get("father_name"):
            y = reports._kv_row(c, 50, y, "Father / Guardian", str(info["father_name"]))
        y = reports._kv_row(c, 50, y, "Class", str(info.get("class_sec") or "-"))
        if info.get("academic_session"):
            y = reports._kv_row(c, 50, y, "Academic Session", str(info["academic_session"]))
        y = reports._kv_row(c, 50, y, "Examination", exam_label)
        y -= 10

        # Table header
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
            if y < 120:
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

        y -= 14
        c.setFillColor(NAVY)
        c.setFont("Helvetica-Bold", 11)
        c.drawString(50, y, f"Total:  {result['total_obtained']:.1f}  /  {result['total_marks']:.1f}")
        y -= 16
        c.drawString(50, y, f"Percentage:  {result['percentage']:.2f}%     Grade:  {result['grade']}")
        y -= 24

        passed = result.get("passed", False)
        c.setFillColor(SUCCESS if passed else DANGER)
        c.roundRect(50, y - 6, 160, 26, 6, fill=1, stroke=0)
        c.setFillColor(WHITE)
        c.setFont("Helvetica-Bold", 12)
        c.drawCentredString(130, y + 2, "PASS" if passed else "FAIL")

        # Accent line under badge
        c.setStrokeColor(ACCENT)
        c.setLineWidth(1.5)
        c.line(230, y + 6, 560, y + 6)

        c.setFillColor(MUTED)
        c.setFont("Helvetica", 8)
        c.drawString(230, y + 2, f"Generated {datetime.now().strftime('%Y-%m-%d %H:%M')}")

        reports._draw_page_footer(c)

    c.save()
    return out_path


def _generate_class_marksheet_pdf(class_sec, exam_label, rows, out_path):
    """Build one consolidated PDF mark sheet listing every student's totals."""
    from reportlab.pdfgen import canvas as rl_canvas
    from reportlab.lib.pagesizes import letter
    from reportlab.lib import colors

    NAVY = colors.HexColor("#0f172a")
    SILVER = colors.HexColor("#f1f5f9")
    SUCCESS = colors.HexColor("#16a34a")
    DANGER = colors.HexColor("#dc2626")
    WHITE = colors.white
    BLACK = colors.black

    c = rl_canvas.Canvas(out_path, pagesize=letter)
    y = reports._draw_page_header(c, "CONSOLIDATED CLASS MARK SHEET")
    y = reports._kv_row(c, 50, y, "Class / Section", class_sec or "-")
    y = reports._kv_row(c, 50, y, "Examination", exam_label)
    y = reports._kv_row(c, 50, y, "Generated", datetime.now().strftime("%Y-%m-%d %H:%M"))
    y -= 10

    def draw_table_header(y_pos):
        c.setFillColor(NAVY)
        c.roundRect(50, y_pos - 4, 510, 20, 4, fill=1, stroke=0)
        c.setFillColor(WHITE)
        c.setFont("Helvetica-Bold", 8)
        c.drawString(56, y_pos + 1, "STUDENT ID")
        c.drawString(140, y_pos + 1, "NAME")
        c.drawString(320, y_pos + 1, "OBTAINED / TOTAL")
        c.drawString(440, y_pos + 1, "PERCENT")
        c.drawString(500, y_pos + 1, "GRADE")
        c.drawString(540, y_pos + 1, "RESULT")
        return y_pos - 22

    y = draw_table_header(y)

    for i, (sid, name, result) in enumerate(rows):
        if y < 90:
            c.showPage()
            y = 750
            y = draw_table_header(y)
        if i % 2 == 0:
            c.setFillColor(SILVER)
            c.rect(50, y - 4, 510, 18, fill=1, stroke=0)
        c.setFillColor(BLACK)
        c.setFont("Helvetica", 8)
        c.drawString(56, y, str(sid)[:14])
        c.drawString(140, y, str(name)[:26])
        c.drawString(320, y, f"{result['total_obtained']:.1f} / {result['total_marks']:.0f}")
        c.drawString(440, y, f"{result['percentage']:.1f}%")
        c.drawString(500, y, str(result["grade"]))
        passed = result.get("passed", False)
        c.setFillColor(SUCCESS if passed else DANGER)
        c.setFont("Helvetica-Bold", 8)
        c.drawString(540, y, "PASS" if passed else "FAIL")
        y -= 18

    y -= 14
    if y < 60:
        c.showPage()
        y = 750
    total = len(rows)
    passed_ct = sum(1 for _, _, r in rows if r.get("passed"))
    c.setFillColor(NAVY)
    c.setFont("Helvetica-Bold", 10)
    c.drawString(50, y, f"Total Students: {total}   ·   Passed: {passed_ct}   ·   Failed: {total - passed_ct}")

    reports._draw_page_footer(c)
    c.save()
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
