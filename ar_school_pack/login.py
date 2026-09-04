"""
login.py — Dedicated Login Window & Authentication for AR School Management System.

UI: modern card with rounded look, icons, placeholders, Remember Me, Forgot Password.
Auth logic is isolated here so app.py stays focused on the main application.
"""

from __future__ import annotations

import os
import json
import tkinter as tk
from tkinter import messagebox
from datetime import datetime

import db
import branding
import theme
from security import hash_password, verify_password


# ---------------------------------------------------------------------------
# Simple persistent "Remember Me" helper (username only — never the password)
# ---------------------------------------------------------------------------
_REMEMBER_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".login_remember.json")


def _load_remembered_username() -> str:
    try:
        if os.path.isfile(_REMEMBER_FILE):
            with open(_REMEMBER_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            return str(data.get("username", "") or "")
    except Exception:
        pass
    return ""


def _save_remembered_username(username: str) -> None:
    try:
        with open(_REMEMBER_FILE, "w", encoding="utf-8") as f:
            json.dump({"username": username}, f)
    except Exception:
        pass


def _clear_remembered_username() -> None:
    try:
        if os.path.isfile(_REMEMBER_FILE):
            os.remove(_REMEMBER_FILE)
    except Exception:
        pass


def log_activity(username: str, action: str) -> None:
    """Lightweight audit helper used only by the login flow.
    (app.py has its own identical helper for the rest of the system.)"""
    try:
        db.run(
            "INSERT INTO audit_logs (username, action, timestamp) VALUES (?, ?, ?)",
            (username, action, datetime.now().strftime("%Y-%m-%d %H:%M:%S")),
            commit=True,
        )
    except Exception as e:
        print(f"Audit Log Error: {e}")


# ---------------------------------------------------------------------------
# Rounded-looking card helper (pure Tkinter — no external deps)
# ---------------------------------------------------------------------------
def _draw_rounded_rect(canvas: tk.Canvas, x1, y1, x2, y2, radius=16, **kwargs):
    """Draw a rounded rectangle on a Canvas."""
    points = [
        x1 + radius, y1,
        x1 + radius, y1,
        x2 - radius, y1,
        x2 - radius, y1,
        x2, y1,
        x2, y1 + radius,
        x2, y1 + radius,
        x2, y2 - radius,
        x2, y2 - radius,
        x2, y2,
        x2 - radius, y2,
        x2 - radius, y2,
        x1 + radius, y2,
        x1 + radius, y2,
        x1, y2,
        x1, y2 - radius,
        x1, y2 - radius,
        x1, y1 + radius,
        x1, y1 + radius,
        x1, y1,
    ]
    return canvas.create_polygon(points, smooth=True, **kwargs)


# ---------------------------------------------------------------------------
# Placeholder Entry helper
# ---------------------------------------------------------------------------
class PlaceholderEntry(tk.Entry):
    def __init__(self, master, placeholder="", **kwargs):
        super().__init__(master, **kwargs)
        self.placeholder = placeholder
        self.placeholder_color = "#94a3b8"
        self.default_fg = kwargs.get("fg", "white")
        self._has_placeholder = False
        self.bind("<FocusIn>", self._on_focus_in)
        self.bind("<FocusOut>", self._on_focus_out)
        self._show_placeholder()

    def _show_placeholder(self):
        if not self.get():
            self._has_placeholder = True
            self.config(fg=self.placeholder_color)
            self.insert(0, self.placeholder)

    def _on_focus_in(self, _event=None):
        if self._has_placeholder:
            self.delete(0, tk.END)
            self.config(fg=self.default_fg)
            self._has_placeholder = False

    def _on_focus_out(self, _event=None):
        if not self.get():
            self._show_placeholder()

    def get_real(self) -> str:
        """Return the actual value, ignoring the placeholder text."""
        if self._has_placeholder:
            return ""
        return self.get().strip()


# ---------------------------------------------------------------------------
# LOGIN WINDOW
# ---------------------------------------------------------------------------
class LoginWindow:
    """Modern login screen for AR School Management System."""

    def __init__(self, root, on_success=None):
        """
        Parameters
        ----------
        root : tk.Tk
            The root window that hosts the login UI.
        on_success : callable(role: str, username: str) | None
            Called after a successful login (after the login window is destroyed).
            If None, the default behaviour launches StudentManagementApp.
        """
        self.root = root
        self.on_success = on_success

        self.root.title("AR School Management System — Login")
        self.root.geometry("480x580")
        self.root.config(bg=theme.SILVER)
        self.root.resizable(False, False)

        # Centre on screen
        self.root.update_idletasks()
        w, h = 480, 580
        x = (self.root.winfo_screenwidth() // 2) - (w // 2)
        y = (self.root.winfo_screenheight() // 2) - (h // 2)
        self.root.geometry(f"{w}x{h}+{x}+{y}")

        b = branding.get_branding()
        org_name = (b.get("org_name") or "MY SCHOOL / ACADEMY").upper()

        # ---- Outer background ----
        outer = tk.Frame(self.root, bg=theme.SILVER)
        outer.pack(fill=tk.BOTH, expand=True)

        # ---- Card via Canvas (rounded + subtle shadow feel) ----
        card_w, card_h = 400, 520
        canvas = tk.Canvas(outer, width=card_w + 20, height=card_h + 20,
                           bg=theme.SILVER, highlightthickness=0)
        canvas.place(relx=0.5, rely=0.5, anchor="center")

        # Soft shadow layer
        _draw_rounded_rect(canvas, 8, 8, card_w + 12, card_h + 12,
                           radius=18, fill="#0f172a", outline="")
        # Main card
        _draw_rounded_rect(canvas, 4, 4, card_w + 4, card_h + 4,
                           radius=16, fill=theme.NAVY, outline="#1e293b")

        # Content frame sitting on top of the canvas card
        card = tk.Frame(canvas, bg=theme.NAVY, padx=32, pady=28)
        canvas.create_window(card_w // 2 + 4, card_h // 2 + 4, window=card, width=card_w - 16)

        # ---- Branding header ----
        tk.Label(card, text="AR SOFTWARE SOLUTIONS",
                 font=("Segoe UI", 10, "bold"),
                 bg=theme.NAVY, fg=theme.BRAND_BLUE_LIGHT).pack(pady=(2, 0))

        tk.Label(card, text="AR School Management\nSystem",
                 font=("Segoe UI", 18, "bold"),
                 bg=theme.NAVY, fg="white",
                 justify="center").pack(pady=(4, 2))

        tk.Label(card, text="Smart Software. Simple Solutions.",
                 font=("Segoe UI", 8, "italic"),
                 bg=theme.NAVY, fg="#94a3b8").pack(pady=(0, 4))

        tk.Label(card, text=org_name,
                 font=("Segoe UI", 9, "bold"),
                 bg=theme.NAVY, fg="#38bdf8",
                 wraplength=320, justify="center").pack(pady=(0, 20))

        # ---- Username field with icon ----
        tk.Label(card, text="Username", font=("Segoe UI", 9, "bold"),
                 bg=theme.NAVY, fg="#cbd5e1").pack(anchor="w")

        user_row = tk.Frame(card, bg=theme.NAVY_LIGHT, highlightthickness=1,
                            highlightbackground="#334155", highlightcolor=theme.BRAND_BLUE)
        user_row.pack(fill=tk.X, pady=(4, 14), ipady=2)

        tk.Label(user_row, text="👤", font=("Segoe UI", 12),
                 bg=theme.NAVY_LIGHT, fg="#94a3b8", padx=8).pack(side=tk.LEFT)

        self.ent_user = PlaceholderEntry(
            user_row,
            placeholder="Enter username",
            font=("Segoe UI", 11),
            bg=theme.NAVY_LIGHT,
            fg="white",
            insertbackground="white",
            relief="flat",
            bd=0,
        )
        self.ent_user.pack(side=tk.LEFT, fill=tk.X, expand=True, ipady=6, padx=(0, 8))

        # ---- Password field with icon ----
        tk.Label(card, text="Password", font=("Segoe UI", 9, "bold"),
                 bg=theme.NAVY, fg="#cbd5e1").pack(anchor="w")

        pass_row = tk.Frame(card, bg=theme.NAVY_LIGHT, highlightthickness=1,
                            highlightbackground="#334155", highlightcolor=theme.BRAND_BLUE)
        pass_row.pack(fill=tk.X, pady=(4, 10), ipady=2)

        tk.Label(pass_row, text="🔒", font=("Segoe UI", 12),
                 bg=theme.NAVY_LIGHT, fg="#94a3b8", padx=8).pack(side=tk.LEFT)

        self.ent_pass = PlaceholderEntry(
            pass_row,
            placeholder="Enter password",
            font=("Segoe UI", 11),
            bg=theme.NAVY_LIGHT,
            fg="white",
            insertbackground="white",
            relief="flat",
            bd=0,
        )
        # Password needs special handling: plain placeholder when empty,
        # switch to masked input (show="*") as soon as the user types.
        self.ent_pass.config(show="")
        self.ent_pass.bind("<FocusIn>", self._pass_on_focus_in, add="+")
        self.ent_pass.bind("<FocusOut>", self._pass_on_focus_out, add="+")
        self.ent_pass.bind("<Key>", self._pass_on_key, add="+")
        self.ent_pass.pack(side=tk.LEFT, fill=tk.X, expand=True, ipady=6, padx=(0, 8))
        self.ent_pass.bind("<Return>", lambda e: self.check_login())

        # ---- Remember Me  +  Forgot Password ----
        opts_row = tk.Frame(card, bg=theme.NAVY)
        opts_row.pack(fill=tk.X, pady=(2, 18))

        self.remember_var = tk.BooleanVar(value=False)
        remember_cb = tk.Checkbutton(
            opts_row,
            text="Remember Me",
            variable=self.remember_var,
            bg=theme.NAVY,
            fg="#94a3b8",
            activebackground=theme.NAVY,
            activeforeground="#cbd5e1",
            selectcolor=theme.NAVY,
            font=("Segoe UI", 9),
            cursor="hand2",
            bd=0,
            highlightthickness=0,
        )
        remember_cb.pack(side=tk.LEFT)

        forgot_lbl = tk.Label(
            opts_row,
            text="Forgot Password?",
            font=("Segoe UI", 9),
            bg=theme.NAVY,
            fg=theme.BRAND_BLUE_LIGHT,
            cursor="hand2",
        )
        forgot_lbl.pack(side=tk.RIGHT)
        forgot_lbl.bind("<Button-1>", lambda e: self._forgot_password())
        forgot_lbl.bind("<Enter>", lambda e: forgot_lbl.config(fg="#7dd3fc"))
        forgot_lbl.bind("<Leave>", lambda e: forgot_lbl.config(fg=theme.BRAND_BLUE_LIGHT))

        # ---- LOGIN button with hover ----
        self.login_btn = tk.Button(
            card,
            text="LOGIN",
            command=self.check_login,
            bg=theme.BRAND_BLUE,
            fg="white",
            font=("Segoe UI", 12, "bold"),
            bd=0,
            pady=12,
            cursor="hand2",
            activebackground="#0284c7",
            activeforeground="white",
            relief="flat",
        )
        self.login_btn.pack(fill=tk.X, pady=(0, 8))
        self.login_btn.bind("<Enter>", lambda e: self.login_btn.config(bg="#0284c7"))
        self.login_btn.bind("<Leave>", lambda e: self.login_btn.config(bg=theme.BRAND_BLUE))

        # ---- Default credentials hint ----
        tk.Label(
            card,
            text="Default: admin/admin123  ·  teacher/teacher123  ·  reception/reception123",
            font=("Segoe UI", 7),
            bg=theme.NAVY,
            fg="#475569",
            wraplength=320,
            justify="center",
        ).pack(side=tk.BOTTOM, pady=(16, 0))

        # Pre-fill remembered username
        remembered = _load_remembered_username()
        if remembered:
            self.ent_user._on_focus_in()
            self.ent_user.insert(0, remembered)
            self.remember_var.set(True)
            self.ent_pass.focus_set()
        else:
            self.ent_user.focus_set()

    # ------------------------------------------------------------------
    # Password placeholder helpers (plain text placeholder → masked input)
    # ------------------------------------------------------------------
    def _pass_show_placeholder(self):
        e = self.ent_pass
        e._has_placeholder = True
        e.config(fg=e.placeholder_color, show="")
        e.delete(0, tk.END)
        e.insert(0, e.placeholder)

    def _pass_on_focus_in(self, _event=None):
        e = self.ent_pass
        if e._has_placeholder:
            e.delete(0, tk.END)
            e.config(fg=e.default_fg, show="*")
            e._has_placeholder = False

    def _pass_on_focus_out(self, _event=None):
        e = self.ent_pass
        if not e.get() or e._has_placeholder:
            self._pass_show_placeholder()

    def _pass_on_key(self, _event=None):
        e = self.ent_pass
        if e._has_placeholder:
            e.delete(0, tk.END)
            e.config(fg=e.default_fg, show="*")
            e._has_placeholder = False

    # ------------------------------------------------------------------
    # Forgot Password
    # ------------------------------------------------------------------
    def _forgot_password(self):
        messagebox.showinfo(
            "Forgot Password",
            "Please contact your system administrator to reset your password.\n\n"
            "For security reasons, password resets are handled only by authorised staff.",
            parent=self.root,
        )

    # ------------------------------------------------------------------
    # Authentication
    # ------------------------------------------------------------------
    def check_login(self):
        user = self.ent_user.get_real()
        pwd = self.ent_pass.get_real()

        if not user or not pwd:
            messagebox.showerror("Login Error", "Please enter both username and password.", parent=self.root)
            return

        row = db.run(
            "SELECT password, role, is_hashed, COALESCE(is_active,1) FROM users WHERE username=?",
            (user,),
            fetchone=True,
        )

        if row and not row[3]:
            log_activity(user, "Login attempt on deactivated account")
            messagebox.showerror(
                "Account Deactivated",
                "This account has been deactivated. Contact your administrator.",
                parent=self.root,
            )
            return

        if row and verify_password(pwd, row[0]):
            role = row[1]
            # Transparent migration of legacy plain-text passwords → hashed
            if not row[2]:
                db.run(
                    "UPDATE users SET password=?, is_hashed=1 WHERE username=?",
                    (hash_password(pwd), user),
                    commit=True,
                )
            log_activity(user, f"User logged in successfully as {role}")

            # Remember Me
            if self.remember_var.get():
                _save_remembered_username(user)
            else:
                _clear_remembered_username()

            self.root.destroy()

            if self.on_success is not None:
                self.on_success(role, user)
            else:
                # Default launch path (late import avoids circular dependency)
                import theme as _theme
                from app import StudentManagementApp
                main_root = tk.Tk()
                _theme.apply_ttk_style()
                StudentManagementApp(main_root, role, user)
                main_root.mainloop()
        else:
            log_activity(user or "(unknown)", "Failed login attempt")
            messagebox.showerror("Login Error", "Invalid Username or Password!", parent=self.root)
