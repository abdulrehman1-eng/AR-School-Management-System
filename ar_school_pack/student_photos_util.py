"""
student_photos_util.py — Shared helpers for student photo storage & display.

- Stores uploads under project_root/student_photos/{student_id}.{ext}
- Resolves a usable path with safe fallback to assets/default_avatar.png
- Provides Tk preview helpers that never crash on missing/corrupt files
"""

from __future__ import annotations

import os
import shutil
from typing import Optional, Tuple

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

def _project_root() -> str:
    """Best-effort project root (directory containing this module or cwd)."""
    try:
        here = os.path.dirname(os.path.abspath(__file__))
        if os.path.isdir(here):
            return here
    except Exception:
        pass
    return os.getcwd()


def photos_dir() -> str:
    path = os.path.join(_project_root(), "student_photos")
    try:
        os.makedirs(path, exist_ok=True)
    except Exception:
        pass
    return path


def default_avatar_path() -> str:
    """Return path to default avatar if it exists, else empty string."""
    candidates = [
        os.path.join(_project_root(), "assets", "default_avatar.png"),
        os.path.join(os.getcwd(), "assets", "default_avatar.png"),
        os.path.join(_project_root(), "default_avatar.png"),
    ]
    for p in candidates:
        if p and os.path.isfile(p):
            return p
    return ""


def resolve_photo_path(photo_path: Optional[str], student_id: Optional[str] = None) -> str:
    """
    Return a filesystem path suitable for display/PDF.

    Priority:
      1. Explicit photo_path if file exists
      2. student_photos/{student_id}.* if student_id given
      3. assets/default_avatar.png
      4. empty string (caller must handle placeholder)
    """
    if photo_path:
        p = str(photo_path).strip()
        if p and os.path.isfile(p):
            return p
        # Relative path relative to project root / cwd
        for base in (_project_root(), os.getcwd()):
            candidate = os.path.join(base, p)
            if os.path.isfile(candidate):
                return candidate

    if student_id:
        sid = str(student_id).strip()
        if sid:
            folder = photos_dir()
            for ext in (".jpg", ".jpeg", ".png", ".JPG", ".JPEG", ".PNG"):
                candidate = os.path.join(folder, f"{sid}{ext}")
                if os.path.isfile(candidate):
                    return candidate

    return default_avatar_path()


def save_student_photo(source_path: str, student_id: str) -> str:
    """
    Copy source image into student_photos/{student_id}.{ext}.

    Returns the destination path stored in DB, or empty string on failure.
    Never raises — callers can keep the original path as fallback.
    """
    if not source_path or not student_id:
        return ""
    src = str(source_path).strip()
    sid = str(student_id).strip()
    if not src or not os.path.isfile(src) or not sid:
        return ""

    # Sanitize student_id for filename (keep alphanumerics, dash, underscore)
    safe_id = "".join(ch for ch in sid if ch.isalnum() or ch in ("-", "_"))
    if not safe_id:
        safe_id = sid.replace("/", "_").replace("\\", "_").replace(" ", "_")

    ext = os.path.splitext(src)[1].lower()
    if ext not in (".jpg", ".jpeg", ".png", ".gif", ".bmp", ".webp"):
        ext = ".jpg"

    dest_dir = photos_dir()
    dest = os.path.join(dest_dir, f"{safe_id}{ext}")

    try:
        # Remove other extensions for the same student to avoid stale files
        for other_ext in (".jpg", ".jpeg", ".png", ".gif", ".bmp", ".webp",
                          ".JPG", ".JPEG", ".PNG"):
            other = os.path.join(dest_dir, f"{safe_id}{other_ext}")
            if other != dest and os.path.isfile(other):
                try:
                    os.remove(other)
                except Exception:
                    pass
        shutil.copy2(src, dest)
        return dest
    except Exception as exc:
        print(f"[student_photos] Could not save photo for {sid}: {exc}")
        return ""


# ---------------------------------------------------------------------------
# Tkinter preview helpers
# ---------------------------------------------------------------------------

def load_photo_for_tk(photo_path: Optional[str], size: Tuple[int, int] = (90, 100),
                      student_id: Optional[str] = None):
    """
    Return a tkinter.PhotoImage (or None) sized for a Label.

    Tries PIL first for resize; falls back to raw PhotoImage for GIF/PNG
    that Tk can load natively. Never raises.
    """
    path = resolve_photo_path(photo_path, student_id)
    if not path:
        return None

    w, h = size
    try:
        from PIL import Image, ImageTk
        img = Image.open(path)
        img = img.convert("RGB")
        # Fit inside box preserving aspect ratio
        img.thumbnail((w, h), Image.Resampling.LANCZOS if hasattr(Image, "Resampling") else Image.LANCZOS)
        # Center on solid background so Label size is stable
        canvas = Image.new("RGB", (w, h), (203, 213, 225))  # #cbd5e1
        ox = (w - img.width) // 2
        oy = (h - img.height) // 2
        canvas.paste(img, (ox, oy))
        return ImageTk.PhotoImage(canvas)
    except Exception:
        pass

    try:
        import tkinter as tk
        # Native PhotoImage only supports GIF/PGM/PPM (and PNG on some builds)
        return tk.PhotoImage(file=path)
    except Exception:
        return None


def apply_photo_to_label(label, photo_path: Optional[str], size: Tuple[int, int] = (90, 100),
                         student_id: Optional[str] = None, placeholder_text: str = "No\nPhoto"):
    """
    Set image on a tk.Label. Keeps a reference on the label to avoid GC.
    Falls back to placeholder text if image cannot be loaded.
    """
    try:
        photo = load_photo_for_tk(photo_path, size=size, student_id=student_id)
        if photo is not None:
            label.configure(image=photo, text="")
            label.image = photo  # prevent garbage collection
            return True
    except Exception as exc:
        print(f"[student_photos] Preview error: {exc}")

    try:
        label.configure(image="", text=placeholder_text)
        label.image = None
    except Exception:
        pass
    return False
