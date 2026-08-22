"""
theme.py — Central design system for AR School Management System.

Single place for the color palette, fonts, and small widget-factory
helpers so every screen looks consistent instead of each tab picking its
own colors. Existing screens already used a navy/blue/green/red palette
that's close to this; this module makes it official and reusable and
adds the sidebar/dashboard/card primitives the redesign needs.
"""

import tkinter as tk
from tkinter import ttk

# ---------------- Palette ----------------
NAVY = "#0f172a"          # Dark Navy / header / sidebar background
NAVY_LIGHT = "#1e293b"    # Sidebar hover / secondary panels
SLATE = "#334155"         # Secondary buttons / muted panels
SILVER = "#f1f5f9"        # App background
SILVER_BORDER = "#e2e8f0" # Card borders / dividers
WHITE = "#ffffff"

BRAND_BLUE = "#0284c7"    # AR Blue — primary actions, links, info
BRAND_BLUE_LIGHT = "#38bdf8"

SUCCESS = "#16a34a"       # Present / Paid / Success
DANGER = "#dc2626"        # Absent / Unpaid / Error
WARNING = "#d97706"       # Pending / Warning
INFO = "#0284c7"

TEXT_DARK = "#0f172a"
TEXT_MUTED = "#64748b"

FONT_BRAND = ("Segoe UI", 16, "bold")
FONT_TAGLINE = ("Segoe UI", 8)
FONT_H1 = ("Segoe UI", 14, "bold")
FONT_H2 = ("Segoe UI", 11, "bold")
FONT_BODY = ("Segoe UI", 10)
FONT_BODY_BOLD = ("Segoe UI", 10, "bold")
FONT_SMALL = ("Segoe UI", 9)
FONT_STAT = ("Segoe UI", 22, "bold")
FONT_SIDEBAR = ("Segoe UI", 10)


def apply_ttk_style():
    """Configure the global ttk.Style once, before any window is built.
    Only touches styling — never renames widget classes — so every
    existing ttk.Frame/ttk.Treeview/ttk.Combobox/ttk.Notebook in the
    codebase picks this up automatically with zero call-site changes."""
    style = ttk.Style()
    try:
        style.theme_use("clam")
    except tk.TclError:
        pass

    style.configure("Treeview", font=FONT_SMALL, rowheight=26,
                     background=WHITE, fieldbackground=WHITE, foreground=TEXT_DARK, borderwidth=0)
    style.configure("Treeview.Heading", font=FONT_BODY_BOLD, background=NAVY, foreground=WHITE, relief="flat")
    style.map("Treeview.Heading", background=[("active", NAVY_LIGHT)])
    style.map("Treeview", background=[("selected", BRAND_BLUE)], foreground=[("selected", WHITE)])

    style.configure("TNotebook", background=SILVER, borderwidth=0)
    style.configure("TNotebook.Tab", font=FONT_BODY_BOLD, padding=[14, 8])

    style.configure("TCombobox", font=FONT_BODY)


def sidebar_button(parent, text, icon, command):
    """A flat, full-width sidebar nav button. Returns the Button so the
    caller can restyle it (active/inactive) later."""
    btn = tk.Button(
        parent, text=f"  {icon}  {text}", command=command,
        anchor="w", bg=NAVY, fg="#cbd5e1", activebackground=NAVY_LIGHT,
        activeforeground=WHITE, font=FONT_SIDEBAR, bd=0, padx=14, pady=10,
        cursor="hand2", relief="flat",
    )
    return btn


def set_sidebar_active(btn, active):
    if active:
        btn.config(bg=BRAND_BLUE, fg=WHITE, activebackground=BRAND_BLUE)
    else:
        btn.config(bg=NAVY, fg="#cbd5e1", activebackground=NAVY_LIGHT)


def stat_card(parent, title, value, accent=BRAND_BLUE, subtitle=""):
    """A small dashboard metric card: colored left accent bar + big
    number + label. Pure tk primitives (Frame/Label) — the same widgets
    already proven to render correctly everywhere else in this app."""
    card = tk.Frame(parent, bg=WHITE, highlightbackground=SILVER_BORDER, highlightthickness=1)
    bar = tk.Frame(card, bg=accent, width=5)
    bar.pack(side=tk.LEFT, fill=tk.Y)
    inner = tk.Frame(card, bg=WHITE, padx=14, pady=10)
    inner.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
    tk.Label(inner, text=title, font=FONT_SMALL, bg=WHITE, fg=TEXT_MUTED).pack(anchor="w")
    tk.Label(inner, text=str(value), font=FONT_STAT, bg=WHITE, fg=TEXT_DARK).pack(anchor="w")
    if subtitle:
        tk.Label(inner, text=subtitle, font=FONT_SMALL, bg=WHITE, fg=TEXT_MUTED).pack(anchor="w")
    return card


def section_card(parent, title):
    """A titled white card panel for grouping content (recent activity
    lists, quick actions, etc.)."""
    card = tk.Frame(parent, bg=WHITE, highlightbackground=SILVER_BORDER, highlightthickness=1)
    header = tk.Frame(card, bg=WHITE, padx=12, pady=8)
    header.pack(fill=tk.X)
    tk.Label(header, text=title, font=FONT_H2, bg=WHITE, fg=TEXT_DARK).pack(anchor="w")
    body = tk.Frame(card, bg=WHITE, padx=12, pady=4)
    body.pack(fill=tk.BOTH, expand=True)
    return card, body


def primary_button(parent, text, command, bg=BRAND_BLUE, width=None):
    return tk.Button(parent, text=text, command=command, bg=bg, fg=WHITE,
                      font=FONT_BODY_BOLD, bd=0, padx=14, pady=8, cursor="hand2",
                      width=width if width else 0)


def status_badge(parent, text, kind="info"):
    colors = {"success": SUCCESS, "danger": DANGER, "warning": WARNING, "info": INFO}
    c = colors.get(kind, INFO)
    return tk.Label(parent, text=f" {text} ", bg=c, fg=WHITE, font=("Segoe UI", 8, "bold"))
