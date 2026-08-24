import os
import shutil
import tkinter as tk
from tkinter import ttk, messagebox, filedialog
from datetime import datetime

import db
import rbac
import reports
import theme
import academic_year
import fee_cycles
import whatsapp_notify

GENDERS = ["Male", "Female", "Other"]
BLOOD_GROUPS = ["", "A+", "A-", "B+", "B-", "AB+", "AB-", "O+", "O-"]
ADMISSION_TYPES = ["New Admission", "Transfer", "Re-admission"]


def _ensure_extra_table():
    """Additive, idempotent — never touches db.py."""
    db.run("""
    CREATE TABLE IF NOT EXISTS student_admission_extra (
        student_id TEXT PRIMARY KEY,
        gender TEXT, blood_group TEXT, nationality TEXT, religion TEXT,
        mother_name TEXT, guardian_name TEXT, guardian_cnic TEXT, occupation TEXT,
        alt_phone TEXT, email TEXT,
        current_address TEXT, city TEXT, area TEXT,
        admission_date TEXT, academic_year TEXT, admission_type TEXT,
        previous_school TEXT, previous_class TEXT,
        emergency_contact_name TEXT, emergency_contact_phone TEXT,
        emergency_relationship TEXT, emergency_notes TEXT,
        admission_fee REAL DEFAULT 0,
        admission_fee_paid REAL DEFAULT 0,
        FOREIGN KEY(student_id) REFERENCES students(student_id)
    )""", commit=True)
    # Older installs created the table without these columns — add them safely.
    for col, typedef in (
        ("admission_fee", "REAL DEFAULT 0"),
        ("admission_fee_paid", "REAL DEFAULT 0"),
    ):
        try:
            db.run(
                f"ALTER TABLE student_admission_extra ADD COLUMN {col} {typedef}",
                commit=True,
            )
        except Exception:
            pass  # column already exists


def _ensure_admission_fee_ledger():
    """One-time admission fee ledger, separate from monthly fee_cycles.

    Tracks Charged / Paid / Pending for the one-time admission fee so
    Student Profile, Fee Management, and reports can show it apart from
    monthly dues. Idempotent.
    """
    db.run("""
    CREATE TABLE IF NOT EXISTS admission_fee_ledger (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        student_id TEXT NOT NULL,
        charged_amount REAL NOT NULL DEFAULT 0,
        paid_amount REAL NOT NULL DEFAULT 0,
        status TEXT NOT NULL DEFAULT 'Pending',
        charged_date TEXT,
        paid_date TEXT,
        payment_method TEXT,
        remarks TEXT,
        created_by TEXT,
        UNIQUE(student_id),
        FOREIGN KEY(student_id) REFERENCES students(student_id)
    )""", commit=True)


def _record_admission_fee(role, student_id, charged, paid, actor, payment_method="Cash"):
    """Persist one-time admission fee (separate from monthly fee_cycles).

    - Writes/updates admission_fee_ledger
    - Posts paid portion to accounting_revenue as 'Admission Fee'
    - Best-effort row in fee_payments with fee_type='Admission Fee' when the
      table supports it (ignored if schema has no fee_type column)
    """
    _ensure_admission_fee_ledger()
    charged = float(charged or 0)
    paid = float(paid or 0)
    if charged < 0:
        charged = 0.0
    if paid < 0:
        paid = 0.0
    if paid > charged:
        paid = charged

    if charged <= 0 and paid <= 0:
        return None

    today = datetime.now().strftime("%Y-%m-%d")
    if charged <= 0:
        status = "Paid"
    elif paid <= 0:
        status = "Pending"
    elif paid >= charged:
        status = "Paid"
    else:
        status = "Partial"

    existing = db.run(
        "SELECT id, paid_amount FROM admission_fee_ledger WHERE student_id=?",
        (student_id,), fetchone=True,
    )
    if existing:
        db.run(
            """UPDATE admission_fee_ledger
               SET charged_amount=?, paid_amount=?, status=?,
                   paid_date=CASE WHEN ? > 0 THEN ? ELSE paid_date END,
                   payment_method=?, remarks=?, created_by=?
               WHERE student_id=?""",
            (charged, paid, status, paid, today, payment_method,
             "One-time admission fee", actor, student_id),
            commit=True,
        )
        ledger_id = existing[0]
    else:
        db.run(
            """INSERT INTO admission_fee_ledger
               (student_id, charged_amount, paid_amount, status, charged_date,
                paid_date, payment_method, remarks, created_by)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (student_id, charged, paid, status, today,
             today if paid > 0 else None, payment_method,
             "One-time admission fee", actor),
            commit=True,
        )
        row = db.run(
            "SELECT id FROM admission_fee_ledger WHERE student_id=?",
            (student_id,), fetchone=True,
        )
        ledger_id = row[0] if row else None

    # Accounting: only the paid slice, tagged as Admission Fee (not monthly).
    # Uses record_admission_fee_revenue so it does not require the stricter
    # accounting.revenue.add permission — same gate as regular fee collection.
    if paid > 0:
        try:
            import accounting
            accounting.record_admission_fee_revenue(
                role, student_id, paid, actor,
                description=f"One-time admission fee — {student_id}",
                payment_method=payment_method,
            )
        except Exception as exc:
            print(f"Admission fee accounting warning: {exc}")

    # Optional fee_payments row with fee_type so Fee Management can filter.
    if paid > 0:
        try:
            db.run(
                """INSERT INTO fee_payments
                   (student_id, amount, payment_date, payment_method, remarks,
                    received_by, fee_type)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (student_id, paid, today, payment_method,
                 "One-time Admission Fee", actor, "Admission Fee"),
                commit=True,
            )
        except Exception:
            # Table may lack fee_type or use different columns — non-fatal.
            try:
                db.run(
                    """INSERT INTO fee_payments
                       (student_id, amount, payment_date, payment_method, remarks, received_by)
                       VALUES (?, ?, ?, ?, ?, ?)""",
                    (student_id, paid, today, payment_method,
                     "One-time Admission Fee [fee_type=Admission Fee]", actor),
                    commit=True,
                )
            except Exception as exc:
                print(f"Admission fee_payments insert note: {exc}")

    return {"id": ledger_id, "charged": charged, "paid": paid, "status": status}


class AdmissionWindow:
    def __init__(self, parent, user_role, current_user):
        self.parent = parent
        self.user_role = user_role
        self.current_user = current_user
        self._submitting = False
        self._just_created_id = None  # set once insert succeeds — protects against re-creating on ID-card retry

        if not rbac.can(self.user_role, "student.add"):
            messagebox.showerror("Permission Denied",
                                  f"Role '{self.user_role}' is not permitted to admit students.", parent=parent)
            return

        _ensure_extra_table()
        _ensure_admission_fee_ledger()

        # Imported here (not at module load) so this file has no hard
        # dependency on app.py existing in every context, but reuses the
        # SAME id generator / audit logger app.py already defines instead
        # of building a second, incompatible one.
        import app as _app
        self._gen_id = _app.generate_next_student_id
        self._log = _app.log_activity

        self.photo_path_var = tk.StringVar(value="")

        self.win = tk.Toplevel(parent)
        self.win.title("New Student Admission")
        self.win.geometry("920x760")
        self.win.config(bg=theme.SILVER)
        self.win.transient(parent)

        self._build_ui()

    # ------------------------------------------------------------------
    def _build_ui(self):
        header = tk.Frame(self.win, bg=theme.NAVY, padx=20, pady=14)
        header.pack(fill=tk.X)
        tk.Label(header, text="📝  NEW STUDENT ADMISSION", font=theme.FONT_H1,
                 bg=theme.NAVY, fg="white").pack(anchor="w")

        canvas = tk.Canvas(self.win, bg=theme.SILVER, highlightthickness=0)
        vscroll = ttk.Scrollbar(self.win, orient="vertical", command=canvas.yview)
        self.scroll_body = tk.Frame(canvas, bg=theme.SILVER)
        self.scroll_body.bind("<Configure>", lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.create_window((0, 0), window=self.scroll_body, anchor="nw", width=880)
        canvas.configure(yscrollcommand=vscroll.set)
        canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=(16, 0), pady=10)
        vscroll.pack(side=tk.RIGHT, fill=tk.Y)

        body = self.scroll_body
        self.vars = {}

        def section(title):
            card, cbody = theme.section_card(body, title)
            card.pack(fill=tk.X, pady=6, padx=(0, 16))
            return cbody

        def entry_row(parent, label, key, width=28, required=False):
            row = tk.Frame(parent, bg=theme.WHITE)
            row.pack(fill=tk.X, pady=3)
            tk.Label(row, text=label + (" *" if required else ""), font=theme.FONT_SMALL,
                     bg=theme.WHITE, width=22, anchor="w",
                     fg=theme.DANGER if required else theme.TEXT_MUTED).pack(side=tk.LEFT)
            e = tk.Entry(row, font=theme.FONT_BODY, width=width)
            e.pack(side=tk.LEFT, padx=6, ipady=2)
            self.vars[key] = e
            return e

        def combo_row(parent, label, key, values, width=25):
            row = tk.Frame(parent, bg=theme.WHITE)
            row.pack(fill=tk.X, pady=3)
            tk.Label(row, text=label, font=theme.FONT_SMALL, bg=theme.WHITE,
                     width=22, anchor="w", fg=theme.TEXT_MUTED).pack(side=tk.LEFT)
            c = ttk.Combobox(row, values=values, width=width - 2, state="readonly")
            c.pack(side=tk.LEFT, padx=6)
            self.vars[key] = c
            return c

        # ---- A. Student Information ----
        s = section("A. Student Information")
        top = tk.Frame(s, bg=theme.WHITE)
        top.pack(fill=tk.X)
        photo_box = tk.Frame(top, bg="#cbd5e1", width=90, height=100)
        photo_box.pack(side=tk.LEFT, padx=(0, 12), pady=4)
        photo_box.pack_propagate(False)
        self.lbl_photo = tk.Label(photo_box, text="No\nPhoto", bg="#cbd5e1", fg="black", font=theme.FONT_SMALL)
        self.lbl_photo.pack(expand=True)
        pcol = tk.Frame(top, bg=theme.WHITE)
        pcol.pack(side=tk.LEFT)
        theme.primary_button(pcol, "📷 Upload Photo", self.browse_photo, bg=theme.SLATE).pack(anchor="w")
        self.lbl_auto_id = tk.Label(pcol, text="Student ID (auto): —", font=theme.FONT_BODY_BOLD,
                                     bg=theme.WHITE, fg=theme.BRAND_BLUE)
        self.lbl_auto_id.pack(anchor="w", pady=(6, 0))

        entry_row(s, "Full Name", "name", required=True)
        entry_row(s, "Date of Birth (YYYY-MM-DD)", "dob")
        combo_row(s, "Gender", "gender", GENDERS)
        combo_row(s, "Blood Group", "blood_group", BLOOD_GROUPS)
        entry_row(s, "Nationality", "nationality")
        entry_row(s, "Religion", "religion")
        entry_row(s, "Previous School", "previous_school")
        entry_row(s, "Previous Class/Grade", "previous_class")

        # ---- B. Parent / Guardian ----
        g = section("B. Parent / Guardian Information")
        entry_row(g, "Father's Name", "father_name")
        entry_row(g, "Mother's Name", "mother_name")
        entry_row(g, "Guardian Name (if different)", "guardian_name")
        entry_row(g, "Guardian CNIC / ID No.", "guardian_cnic")
        entry_row(g, "Occupation", "occupation")
        entry_row(g, "Contact Number", "phone", required=True)
        entry_row(g, "Alternate Contact", "alt_phone")
        entry_row(g, "Email", "email")

        # ---- C. Address ----
        a = section("C. Address Information")
        entry_row(a, "Current Address", "current_address", width=45)
        entry_row(a, "Permanent Address", "address", width=45)
        entry_row(a, "City", "city")
        entry_row(a, "Area / Locality", "area")

        # ---- D. Academic / Admission ----
        ac = section("D. Academic / Admission Information")
        entry_row(ac, "Admission Date (YYYY-MM-DD)", "admission_date")
        entry_row(ac, "Academic Year", "academic_year")
        entry_row(ac, "Class / Section", "class_sec", required=True)
        combo_row(ac, "Admission Type", "admission_type", ADMISSION_TYPES)
        entry_row(ac, "Monthly Fee (Rs.)", "total_fee")
        entry_row(ac, "Admission Fee — One-Time (Rs.)", "admission_fee")
        entry_row(ac, "Paid Monthly Fee at Admission (Rs.)", "paid_fee")
        entry_row(ac, "Paid Admission Fee at Admission (Rs.)", "admission_fee_paid")
        self.vars["admission_date"].insert(0, datetime.now().strftime("%Y-%m-%d"))
        self.vars["academic_year"].insert(0, academic_year.get_current_year_label())
        self.vars["total_fee"].insert(0, "0")
        self.vars["admission_fee"].insert(0, "0")
        self.vars["paid_fee"].insert(0, "0")
        self.vars["admission_fee_paid"].insert(0, "0")

        # ---- E. Emergency ----
        em = section("E. Emergency Information")
        entry_row(em, "Emergency Contact Name", "emergency_contact_name")
        entry_row(em, "Emergency Contact Phone", "emergency_contact_phone")
        entry_row(em, "Relationship", "emergency_relationship")
        entry_row(em, "Notes", "emergency_notes", width=45)

        # ---- Actions ----
        actions = tk.Frame(body, bg=theme.SILVER)
        actions.pack(fill=tk.X, pady=14, padx=(0, 16))
        self.btn_save = theme.primary_button(actions, "💾 SAVE ADMISSION", self.submit, bg=theme.SUCCESS)
        self.btn_save.pack(side=tk.LEFT, ipady=6, padx=4)
        theme.primary_button(actions, "🧹 CLEAR", self.clear_form, bg=theme.SLATE).pack(side=tk.LEFT, ipady=6, padx=4)
        theme.primary_button(actions, "✖ CANCEL", self.win.destroy, bg=theme.DANGER).pack(side=tk.LEFT, ipady=6, padx=4)

        self.result_frame = tk.Frame(body, bg=theme.SILVER)
        self.result_frame.pack(fill=tk.X, padx=(0, 16), pady=(0, 20))

        self._refresh_auto_id()

    def _refresh_auto_id(self):
        self.lbl_auto_id.config(text=f"Student ID (auto): {self._gen_id()}")

    def browse_photo(self):
        path = filedialog.askopenfilename(filetypes=[("Image Files", "*.jpg *.jpeg *.png")], parent=self.win)
        if path:
            self.photo_path_var.set(path)
            self.lbl_photo.config(text="Photo\nSelected", bg=theme.SUCCESS, fg="white")

    def clear_form(self):
        for key, w in self.vars.items():
            if isinstance(w, ttk.Combobox):
                w.set("")
            else:
                w.delete(0, tk.END)
        self.vars["admission_date"].insert(0, datetime.now().strftime("%Y-%m-%d"))
        self.vars["academic_year"].insert(0, academic_year.get_current_year_label())
        self.vars["total_fee"].insert(0, "0")
        if "admission_fee" in self.vars:
            self.vars["admission_fee"].insert(0, "0")
        self.vars["paid_fee"].insert(0, "0")
        if "admission_fee_paid" in self.vars:
            self.vars["admission_fee_paid"].insert(0, "0")
        self.photo_path_var.set("")
        self.lbl_photo.config(text="No\nPhoto", bg="#cbd5e1", fg="black")
        self._refresh_auto_id()
        for w in self.result_frame.winfo_children():
            w.destroy()

    # ------------------------------------------------------------------
    def _find_possible_duplicate(self, name, dob, guardian_cnic, phone):
        if guardian_cnic:
            row = db.run(
                "SELECT s.student_id, s.name FROM students s "
                "JOIN student_admission_extra e ON e.student_id = s.student_id "
                "WHERE e.guardian_cnic = ? AND s.status='Active' LIMIT 1", (guardian_cnic,), fetchone=True)
            if row:
                return row
        if name and dob:
            row = db.run("SELECT student_id, name FROM students WHERE name=? AND dob=? AND status='Active' LIMIT 1",
                          (name, dob), fetchone=True)
            if row:
                return row
        if name and phone:
            row = db.run("SELECT student_id, name FROM students WHERE name=? AND phone=? AND status='Active' LIMIT 1",
                          (name, phone), fetchone=True)
            if row:
                return row
        return None

    def submit(self):
        if self._submitting:
            return
        if not rbac.can(self.user_role, "student.add"):
            messagebox.showerror("Permission Denied", "You are not allowed to admit students.", parent=self.win)
            return

        name = self.vars["name"].get().strip()
        cls = self.vars["class_sec"].get().strip()
        phone = self.vars["phone"].get().strip()
        dob = self.vars["dob"].get().strip()

        missing = []
        if not name:
            missing.append("Full Name")
        if not cls:
            missing.append("Class/Section")
        if not phone:
            missing.append("Contact Number")
        if missing:
            messagebox.showerror("Missing Required Fields", "Please fill in:\n- " + "\n- ".join(missing),
                                  parent=self.win)
            return

        if dob:
            try:
                datetime.strptime(dob, "%Y-%m-%d")
            except ValueError:
                messagebox.showerror("Invalid Date", "Date of Birth must be YYYY-MM-DD.", parent=self.win)
                return
        adm_date = self.vars["admission_date"].get().strip()
        if adm_date:
            try:
                datetime.strptime(adm_date, "%Y-%m-%d")
            except ValueError:
                messagebox.showerror("Invalid Date", "Admission Date must be YYYY-MM-DD.", parent=self.win)
                return

        try:
            total_fee = float(self.vars["total_fee"].get().strip() or 0)
            paid_fee = float(self.vars["paid_fee"].get().strip() or 0)
            admission_fee = float(
                (self.vars["admission_fee"].get().strip() if "admission_fee" in self.vars else "0") or 0
            )
            admission_fee_paid = float(
                (self.vars["admission_fee_paid"].get().strip() if "admission_fee_paid" in self.vars else "0") or 0
            )
        except ValueError:
            messagebox.showerror(
                "Invalid Fee",
                "Monthly Fee, Admission Fee, and Paid amounts must be numbers.",
                parent=self.win,
            )
            return
        if total_fee < 0 or paid_fee < 0 or admission_fee < 0 or admission_fee_paid < 0:
            messagebox.showerror("Invalid Fee", "Fee amounts cannot be negative.", parent=self.win)
            return
        if admission_fee_paid > admission_fee:
            messagebox.showerror(
                "Invalid Fee",
                "Paid Admission Fee cannot exceed Admission Fee (One-Time).",
                parent=self.win,
            )
            return
        if paid_fee > total_fee and total_fee > 0:
            messagebox.showerror(
                "Invalid Fee",
                "Paid Monthly Fee cannot exceed Monthly Fee amount.",
                parent=self.win,
            )
            return

        guardian_cnic = self.vars["guardian_cnic"].get().strip()
        dup = self._find_possible_duplicate(name, dob, guardian_cnic, phone)
        if dup and self._just_created_id != dup[0]:
            proceed = messagebox.askyesnocancel(
                "Possible Duplicate Student",
                f"A student with similar information already exists:\n\n"
                f"{dup[1]}  (ID: {dup[0]})\n\n"
                "Yes = Continue Anyway (create new record)\n"
                "No = Cancel\n",
                parent=self.win,
            )
            if proceed is not True:
                return

        s_id = self.lbl_auto_id.cget("text").replace("Student ID (auto): ", "")

        due_now = (admission_fee - admission_fee_paid) + max(0.0, total_fee - paid_fee)
        summary = (
            f"Name: {name}\nClass: {cls}\nStudent ID: {s_id}\n\n"
            f"Monthly Fee: Rs. {total_fee:,.2f}   (Paid now: Rs. {paid_fee:,.2f})\n"
            f"Admission Fee (One-Time): Rs. {admission_fee:,.2f}   "
            f"(Paid now: Rs. {admission_fee_paid:,.2f})\n"
            f"Remaining at admission: Rs. {due_now:,.2f}\n\n"
            "Confirm this admission?"
        )
        if not messagebox.askyesno("Confirm Admission", summary, parent=self.win):
            return

        self._submitting = True
        self.btn_save.config(state="disabled", text="Processing...")
        self.win.update_idletasks()
        try:
            existing = db.run("SELECT 1 FROM students WHERE student_id=?", (s_id,), fetchone=True)
            if existing:
                # Extremely rare race (two admissions same millisecond) —
                # re-generate rather than silently overwriting someone.
                s_id = self._gen_id()

            # Insert with paid_fee=0; the initial payment (if any) is applied
            # through fee_cycles.record_payment below so the monthly ledger,
            # students.paid_fee cache, and accounting_revenue stay in sync.
            db.run("""INSERT INTO students
                      (student_id, name, father_name, dob, phone, address, class_sec, photo_path,
                       prev_education, total_fee, paid_fee, status)
                      VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'Active')""",
                   (s_id, name, self.vars["father_name"].get().strip(), dob, phone,
                    self.vars["address"].get().strip(), cls, self.photo_path_var.get(),
                    self.vars["previous_school"].get().strip(), total_fee, 0.0), commit=True)
            self._just_created_id = s_id

            db.run("""INSERT INTO student_admission_extra
                      (student_id, gender, blood_group, nationality, religion, mother_name, guardian_name,
                       guardian_cnic, occupation, alt_phone, email, current_address, city, area,
                       admission_date, academic_year, admission_type, previous_school, previous_class,
                       emergency_contact_name, emergency_contact_phone, emergency_relationship, emergency_notes,
                       admission_fee, admission_fee_paid)
                      VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                      ON CONFLICT(student_id) DO UPDATE SET
                        gender=excluded.gender, blood_group=excluded.blood_group,
                        admission_fee=excluded.admission_fee,
                        admission_fee_paid=excluded.admission_fee_paid""",
                   (s_id, self.vars["gender"].get(), self.vars["blood_group"].get(),
                    self.vars["nationality"].get().strip(), self.vars["religion"].get().strip(),
                    self.vars["mother_name"].get().strip(), self.vars["guardian_name"].get().strip(),
                    guardian_cnic, self.vars["occupation"].get().strip(), self.vars["alt_phone"].get().strip(),
                    self.vars["email"].get().strip(), self.vars["current_address"].get().strip(),
                    self.vars["city"].get().strip(), self.vars["area"].get().strip(),
                    adm_date, self.vars["academic_year"].get().strip(), self.vars["admission_type"].get(),
                    self.vars["previous_school"].get().strip(), self.vars["previous_class"].get().strip(),
                    self.vars["emergency_contact_name"].get().strip(),
                    self.vars["emergency_contact_phone"].get().strip(),
                    self.vars["emergency_relationship"].get().strip(),
                    self.vars["emergency_notes"].get().strip(),
                    admission_fee, admission_fee_paid), commit=True)

            # Record this student's enrollment for the chosen academic
            # year (defaults to the current session). Non-fatal if it
            # fails for any reason -- the admission itself already
            # succeeded above and should not be rolled back for this.
            try:
                academic_year.enroll_student(
                    s_id,
                    self.vars["academic_year"].get().strip() or academic_year.get_current_year_label(),
                    cls,
                )
            except Exception as exc:
                print(f"Academic year enrollment warning: {exc}")

            # --- Monthly fee (fee_type conceptually = 'Monthly Fee') ---
            # Generate the regular monthly cycle only for the recurring fee.
            # Admission Fee is tracked separately and must NEVER mix into this cycle.
            try:
                now_dt = datetime.now()
                cycle = None
                if total_fee > 0:
                    try:
                        cycle = fee_cycles.generate_cycle(
                            role=self.user_role,
                            student_id=s_id,
                            billing_month=now_dt.month,
                            billing_year=now_dt.year,
                            fee_amount=total_fee,
                            actor=self.current_user,
                            fee_type="Monthly Fee",
                        )
                    except TypeError:
                        # Older fee_cycles.generate_cycle without fee_type kwarg
                        cycle = fee_cycles.generate_cycle(
                            role=self.user_role,
                            student_id=s_id,
                            billing_month=now_dt.month,
                            billing_year=now_dt.year,
                            fee_amount=total_fee,
                            actor=self.current_user,
                        )
                if paid_fee > 0 and cycle and rbac.can(self.user_role, "student.fee.edit"):
                    try:
                        try:
                            fee_cycles.record_payment(
                                self.user_role,
                                cycle["id"],
                                paid_fee,
                                "Cash",
                                self.current_user,
                                remarks="Initial monthly fee payment at admission",
                                fee_type="Monthly Fee",
                            )
                        except TypeError:
                            fee_cycles.record_payment(
                                self.user_role,
                                cycle["id"],
                                paid_fee,
                                "Cash",
                                self.current_user,
                                remarks="Initial monthly fee payment at admission [fee_type=Monthly Fee]",
                            )
                    except Exception as pay_exc:
                        print(f"Monthly fee payment posting warning: {pay_exc}")
            except ValueError as ve:
                print(f"Fee cycle generation note: {ve}")
            except Exception as exc:
                print(f"Fee cycle auto-generation warning: {exc}")

            # --- One-time Admission Fee (fee_type = 'Admission Fee') ---
            # Completely separate from monthly fee_cycles / students.total_fee.
            try:
                if admission_fee > 0 or admission_fee_paid > 0:
                    _record_admission_fee(
                        self.user_role,
                        s_id,
                        admission_fee,
                        admission_fee_paid,
                        self.current_user,
                        payment_method="Cash",
                    )
            except Exception as af_exc:
                print(f"Admission fee ledger warning: {af_exc}")

            self._log(
                self.current_user,
                f"Admitted new student {name} ({s_id}) via Admission window "
                f"[monthly={total_fee}, admission_fee={admission_fee}, "
                f"paid_monthly={paid_fee}, paid_admission={admission_fee_paid}]",
            )

            # WhatsApp Integration — template from system_settings (user presses Send)
            try:
                parent_name = (
                    self.vars["father_name"].get().strip()
                    or self.vars["guardian_name"].get().strip()
                    or "Wali"
                )
                msg = db.render_msg_template(
                    "msg_template_admission",
                    parent_name=parent_name,
                    student_name=name,
                    class_sec=cls,
                    school_name=db.get_setting("school_name", "AR Academy"),
                )
                whatsapp_notify.open_whatsapp(phone, msg)
            except Exception:
                pass  # never block admission if WhatsApp fails
        except Exception as e:
            messagebox.showerror("Database Error", f"Admission could not be saved:\n{e}", parent=self.win)
            self._submitting = False
            self.btn_save.config(state="normal", text="💾 SAVE ADMISSION")
            return

        self._submitting = False
        self.btn_save.config(state="normal", text="💾 SAVE ADMISSION")
        self._show_success(
            s_id, name, cls,
            monthly_fee=total_fee, paid_monthly=paid_fee,
            admission_fee=admission_fee, paid_admission=admission_fee_paid,
        )

    # ------------------------------------------------------------------
    def _show_success(self, s_id, name, cls, monthly_fee=0, paid_monthly=0,
                      admission_fee=0, paid_admission=0):
        for w in self.result_frame.winfo_children():
            w.destroy()
        card, cbody = theme.section_card(self.result_frame, "✅ Admission Successful")
        card.pack(fill=tk.X)
        tk.Label(cbody, text=f"{name}   |   Student ID: {s_id}   |   Class: {cls}",
                 font=theme.FONT_BODY_BOLD, bg=theme.WHITE).pack(anchor="w", pady=(0, 4))
        fee_lines = (
            f"Monthly Fee: Rs. {float(monthly_fee):,.2f}  "
            f"(Paid: Rs. {float(paid_monthly):,.2f})   ·   "
            f"Admission Fee (One-Time): Rs. {float(admission_fee):,.2f}  "
            f"(Paid: Rs. {float(paid_admission):,.2f})"
        )
        tk.Label(cbody, text=fee_lines, font=theme.FONT_SMALL, bg=theme.WHITE,
                 fg=theme.TEXT_MUTED).pack(anchor="w", pady=(0, 8))

        def gen_card(path):
            row = db.run("SELECT name, father_name, class_sec, phone, photo_path FROM students WHERE student_id=?",
                          (s_id,), fetchone=True)
            nm, fname, cls2, phone2, photo = row
            emer = ""
            try:
                er = db.run(
                    "SELECT emergency_contact_phone FROM student_admission_extra WHERE student_id=?",
                    (s_id,), fetchone=True,
                )
                if er and er[0]:
                    emer = er[0]
            except Exception:
                pass
            reports.generate_id_card(
                s_id, nm, cls2, path, father_name=fname, phone=phone2,
                photo_path=photo, emergency_phone=emer,
            )

        def preview_print():
            out_path = os.path.join(os.getcwd(), f"ID_Card_{s_id}.pdf")
            try:
                gen_card(out_path)
            except Exception as e:
                messagebox.showerror("ID Card Error", f"Student was saved, but ID card generation failed:\n{e}\n"
                                                        "Use 'Generate Again' to retry.", parent=self.win)
                return
            try:
                if os.name == "nt":
                    os.startfile(out_path)
                elif shutil.which("xdg-open"):
                    os.system(f'xdg-open "{out_path}"')
                elif shutil.which("open"):
                    os.system(f'open "{out_path}"')
            except Exception:
                pass
            self._log(self.current_user, f"Generated ID card for new student {s_id}")
            messagebox.showinfo("ID Card Ready", f"ID Card generated:\n{out_path}", parent=self.win)

        def save_as():
            path = filedialog.asksaveasfilename(defaultextension=".pdf", initialfile=f"ID_Card_{s_id}.pdf",
                                                 filetypes=[("PDF Files", "*.pdf")], parent=self.win)
            if not path:
                return
            try:
                gen_card(path)
            except Exception as e:
                messagebox.showerror("ID Card Error", f"Could not generate ID card:\n{e}", parent=self.win)
                return
            messagebox.showinfo("Saved", f"ID card saved:\n{path}", parent=self.win)

        row = tk.Frame(cbody, bg=theme.WHITE)
        row.pack(anchor="w")
        theme.primary_button(row, "🪪 Preview / Print ID Card", preview_print).pack(side=tk.LEFT, padx=(0, 6))
        theme.primary_button(row, "💾 Save ID Card As...", save_as, bg=theme.SLATE).pack(side=tk.LEFT, padx=(0, 6))
        theme.primary_button(row, "➕ New Admission", self.clear_form, bg=theme.BRAND_BLUE).pack(side=tk.LEFT)


def launch_admission_window(parent, user_role, current_user):
    return AdmissionWindow(parent, user_role, current_user)