
from __future__ import annotations

from typing import Any, Iterable, List, Optional, Sequence, Tuple

import db


# ---------------------------------------------------------------------------
# Type aliases
# ---------------------------------------------------------------------------

PassingCriteria = dict[str, Any]
GradingBand = Tuple[str, float, float]
SubjectResult = dict[str, Any]
Result = dict[str, Any]


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _to_float(value: Any, default: float = 0.0) -> float:
    """Convert a DB value to float without allowing bad data to crash results."""
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _to_bool(value: Any) -> bool:
    """Convert SQLite-style boolean values to a real bool."""
    return bool(value)


def _validate_percent(value: Any, field_name: str) -> float:
    """Validate a percentage value used by grading/passing configuration."""
    percent = _to_float(value, float("nan"))

    if percent != percent:  # NaN check without importing math.
        raise ValueError(f"{field_name} must be a valid number.")

    if not 0 <= percent <= 100:
        raise ValueError(f"{field_name} must be between 0 and 100.")

    return percent


def _normalise_band(band: Sequence[Any]) -> GradingBand:
    """Validate and normalize one grading band."""
    if len(band) != 3:
        raise ValueError(
            "Each grading band must contain: grade, min_percent, max_percent."
        )

    grade = str(band[0]).strip()
    if not grade:
        raise ValueError("Grade name cannot be empty.")

    minimum = _validate_percent(band[1], "Minimum percentage")
    maximum = _validate_percent(band[2], "Maximum percentage")

    if minimum > maximum:
        raise ValueError(
            f"Invalid grading band '{grade}': minimum cannot exceed maximum."
        )

    return grade, minimum, maximum


def _validate_grading_bands(
    bands: Iterable[Sequence[Any]],
) -> List[GradingBand]:
    """Validate all grading bands before replacing the DB configuration."""
    normalised = [_normalise_band(band) for band in bands]

    if not normalised:
        raise ValueError("At least one grading band is required.")

    grades = [band[0].casefold() for band in normalised]
    if len(grades) != len(set(grades)):
        raise ValueError("Duplicate grade names are not allowed.")

    # Overlapping ranges make grade_for_percent() ambiguous.
    ordered = sorted(normalised, key=lambda item: item[1])
    for previous, current in zip(ordered, ordered[1:]):
        if current[1] <= previous[2]:
            raise ValueError(
                f"Overlapping grading bands: '{previous[0]}' and '{current[0]}'."
            )

    return normalised


def _fetch_marks(student_id: str, exam_type: Optional[str] = None) -> list[tuple]:
    """Fetch marks from the existing marks table."""
    if exam_type:
        return db.run(
            """
            SELECT subject_name, obtained_marks, total_marks
            FROM marks
            WHERE student_id=? AND exam_type=?
            ORDER BY id
            """,
            (student_id, exam_type),
            fetchall=True,
        ) or []

    return db.run(
        """
        SELECT subject_name, obtained_marks, total_marks
        FROM marks
        WHERE student_id=?
        ORDER BY id
        """,
        (student_id,),
        fetchall=True,
    ) or []


# ---------------------------------------------------------------------------
# Passing criteria
# ---------------------------------------------------------------------------

def get_passing_criteria() -> PassingCriteria:
    """Return the currently configured passing criteria.

    Returns
    -------
    dict
        ``min_overall_percent``
        ``require_pass_each_subject``
        ``min_subject_percent``

    Raises
    ------
    RuntimeError
        If the required configuration row does not exist.
    """
    row = db.run(
        """
        SELECT min_overall_percent,
               require_pass_each_subject,
               min_subject_percent
        FROM passing_criteria
        WHERE id=1
        """,
        fetchone=True,
    )

    if not row:
        raise RuntimeError(
            "Passing criteria configuration (id=1) was not found."
        )

    return {
        "min_overall_percent": _validate_percent(
            row[0], "Minimum overall percentage"
        ),
        "require_pass_each_subject": _to_bool(row[1]),
        "min_subject_percent": _validate_percent(
            row[2], "Minimum subject percentage"
        ),
    }


def set_passing_criteria(
    min_overall_percent: float,
    require_pass_each_subject: bool,
    min_subject_percent: float,
) -> None:
    """Update the configurable passing criteria."""
    overall = _validate_percent(
        min_overall_percent, "Minimum overall percentage"
    )
    subject = _validate_percent(
        min_subject_percent, "Minimum subject percentage"
    )

    db.run(
        """
        UPDATE passing_criteria
        SET min_overall_percent=?,
            require_pass_each_subject=?,
            min_subject_percent=?
        WHERE id=1
        """,
        (
            overall,
            1 if require_pass_each_subject else 0,
            subject,
        ),
        commit=True,
    )


# ---------------------------------------------------------------------------
# Grading bands
# ---------------------------------------------------------------------------

def get_grading_bands() -> list[GradingBand]:
    """Return configured grading bands from highest minimum percentage first."""
    rows = db.run(
        """
        SELECT grade, min_percent, max_percent
        FROM grading_config
        ORDER BY min_percent DESC
        """,
        fetchall=True,
    ) or []

    return [
        (str(grade), _to_float(minimum), _to_float(maximum))
        for grade, minimum, maximum in rows
    ]


def set_grading_bands(bands: Iterable[Sequence[Any]]) -> None:
    """Replace the grading configuration after validating every band.

    ``bands`` must contain tuples/lists in this form::

        (grade, min_percent, max_percent)

    The existing table is only changed after validation succeeds.
    """
    validated = _validate_grading_bands(bands)

    db.run("DELETE FROM grading_config", commit=True)
    db.executemany(
        """
        INSERT INTO grading_config
            (grade, min_percent, max_percent)
        VALUES (?, ?, ?)
        """,
        validated,
    )


def grade_for_percent(percent: float) -> str:
    """Return the grade matching a percentage, or ``N/A`` if none matches."""
    value = _to_float(percent, float("nan"))

    if value != value:  # NaN
        return "N/A"

    # Keep the existing configurable DB behavior.
    for grade, minimum, maximum in get_grading_bands():
        if minimum <= value <= maximum:
            return grade

    return "N/A"


# ---------------------------------------------------------------------------
# Result calculation
# ---------------------------------------------------------------------------

def compute_result(
    student_id: str,
    exam_type: Optional[str] = None,
) -> Optional[Result]:
    """Calculate a student's result using configurable grading rules.

    Parameters
    ----------
    student_id:
        Student ID from the existing ``students``/``marks`` records.
    exam_type:
        Optional exam type. If omitted, all marks for the student are used.

    Returns
    -------
    dict | None
        A result dictionary containing totals, percentage, grade, pass/fail,
        and per-subject results. Returns ``None`` when no marks exist.

    Notes
    -----
    This function never deletes or modifies marks. Existing marks therefore
    remain available as result history.
    """
    student_id = str(student_id).strip()
    if not student_id:
        raise ValueError("Student ID cannot be empty.")

    exam_type = str(exam_type).strip() if exam_type else None
    rows = _fetch_marks(student_id, exam_type)

    if not rows:
        return None

    total_obtained = 0.0
    total_marks = 0.0
    subject_results: list[SubjectResult] = []

    criteria = get_passing_criteria()

    for subject_name, obtained, total in rows:
        obtained_value = _to_float(obtained)
        total_value = _to_float(total)

        # Negative marks/max marks are invalid data for result calculation.
        if total_value < 0:
            raise ValueError(
                f"Invalid total marks for subject '{subject_name}'."
            )
        if obtained_value < 0:
            raise ValueError(
                f"Invalid obtained marks for subject '{subject_name}'."
            )

        # Do not allow obtained marks to exceed the subject's maximum.
        # This keeps the calculation sane without modifying the DB record.
        if total_value > 0 and obtained_value > total_value:
            raise ValueError(
                f"Obtained marks exceed total marks for subject "
                f"'{subject_name}'."
            )

        subject_percent = (
            obtained_value / total_value * 100
            if total_value > 0
            else 0.0
        )

        subject_pass = (
            subject_percent >= criteria["min_subject_percent"]
        )

        subject_results.append(
            {
                "subject": subject_name,
                "obtained": obtained_value,
                "total": total_value,
                "percent": subject_percent,
                "pass": subject_pass,
            }
        )

        total_obtained += obtained_value
        total_marks += total_value

    percentage = (
        total_obtained / total_marks * 100
        if total_marks > 0
        else 0.0
    )

    grade = grade_for_percent(percentage)

    passed = percentage >= criteria["min_overall_percent"]

    if criteria["require_pass_each_subject"]:
        passed = passed and all(
            subject["pass"] for subject in subject_results
        )

    return {
        "student_id": student_id,
        "subjects": subject_results,
        "total_obtained": total_obtained,
        "total_marks": total_marks,
        "percentage": percentage,
        "grade": grade,
        "passed": passed,
    }


# ---------------------------------------------------------------------------
# Exam types
# ---------------------------------------------------------------------------

def exam_types_for_student(student_id: str) -> list[str]:
    """Return unique exam types recorded for a student in insertion order."""
    student_id = str(student_id).strip()
    if not student_id:
        return []

    rows = db.run(
        """
        SELECT DISTINCT exam_type
        FROM marks
        WHERE student_id=?
          AND exam_type IS NOT NULL
          AND TRIM(exam_type) <> ''
        ORDER BY id
        """,
        (student_id,),
        fetchall=True,
    ) or []

    return [str(row[0]) for row in rows]


__all__ = [
    "get_passing_criteria",
    "set_passing_criteria",
    "get_grading_bands",
    "set_grading_bands",
    "grade_for_percent",
    "compute_result",
    "exam_types_for_student",
]
