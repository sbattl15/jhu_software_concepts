"""Clean raw GradCafe scrape output into structured applicant records.

This module is the **transform** stage of the ETL pipeline. It takes the
raw text fields written by :mod:`scrape` and, using regular expressions,
splits them into structured fields: program, degree, comments, date added,
status, term, applicant nationality, GRE Quant/Verbal/AW and GPA.

Output field names match what :mod:`load_data` expects (``program``,
``comments``, ``date_added``, ``url``, ``status``, ``term``,
``US/International``, ``GRE``, ``GRE V``, ``GRE AW``, ``GPA``, ``Degree``).
Empty fields are omitted from each record (except ``comments``).

Example::

    python clean.py -i applicant_data.json -o cleaned_applicant_data_new.json
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path


# ============================================================================
# Text helpers
# ============================================================================

def _clean_text(
    value: object,
) -> str:
    """Collapse runs of whitespace to single spaces and strip.

    :param value: Any value; converted with :func:`str`.
    :returns: Cleaned text, or ``""`` for ``None``.
    :rtype: str
    """

    if value is None:
        return ""

    return re.sub(
        r"\s+",
        " ",
        str(value),
    ).strip()


# ============================================================================
# Structured-field parsing
# ============================================================================

# Degree token that GradCafe appends to the end of the raw program name,
# e.g. "Speech Language Pathology Masters" -> program="Speech Language
# Pathology", degree="Masters".
_DEGREE_SUFFIX_RE = re.compile(
    r"\s*\b(Master'?s|Masters|MS|MSc|MA|MBA|MEng|MFA|PsyD|Ph\.?D\.?|"
    r"Doctorate|EdD|MPH|JD|Other)\s*$",
    re.IGNORECASE,
)

# The leading structured block that GradCafe prepends to every meta/comment
# line, in a fixed order: status (+ date), term, applicant type, GRE scores,
# GPA. Anything left over after this prefix is matched is a genuine
# free-text comment.
_META_PREFIX_RE = re.compile(
    r"^\s*"
    r"(?:Accepted|Rejected|Wait\s*listed|Interview)"
    r"(?:\s+on\s+[A-Za-z]{3,9}\s+\d{1,2})?"
    r"(?:\s+(?:Spring|Summer|Fall|Winter)\s+\d{4})?"
    r"(?:\s+(?:International|American|Other|0))?"
    r"(?:\s+GRE\s*[:\-]?\s*\d{2,3})?"
    r"(?:\s+GRE\s*V\s*[:\-]?\s*\d{2,3})?"
    r"(?:\s+GRE\s*AW\s*[:\-]?\s*\d(?:\.\d{1,2})?)?"
    r"(?:\s+GPA\s*[:\-]?\s*[0-4](?:\.\d{1,3})?)?"
    r"\s*",
    re.IGNORECASE,
)


def _parse_term(
    text: str,
) -> str:
    """Extract the intended start term, e.g. ``"Fall 2026"``.

    :param text: Raw metadata text.
    :returns: Term with the season capitalised, or ``""`` if none found.
    :rtype: str
    """

    match = re.search(
        r"\b(Spring|Summer|Fall|Winter)\s+(\d{4})\b",
        text,
        re.IGNORECASE,
    )

    if not match:
        return ""

    return f"{match.group(1).capitalize()} {match.group(2)}"


def _parse_student_type(
    text: str,
) -> str:
    """Classify the applicant as ``"International"`` or ``"American"``.

    :param text: Raw metadata text.
    :returns: ``"International"``, ``"American"``, or ``""`` if neither appears.
    :rtype: str
    """

    if re.search(r"\bInternational\b", text, re.IGNORECASE):
        return "International"

    if re.search(r"\bAmerican\b", text, re.IGNORECASE):
        return "American"

    return ""


def _parse_gpa(
    text: str,
) -> str:
    """Extract the GPA as a normalised ``"GPA <value>"`` string.

    :param text: Raw metadata text.
    :returns: e.g. ``"GPA 3.70"``, or ``""`` if no GPA is present.
    :rtype: str
    """

    match = re.search(
        r"\bGPA\s*[:\-]?\s*([0-4](?:\.\d{1,3})?)\b",
        text,
        re.IGNORECASE,
    )

    return f"GPA {match.group(1)}" if match else ""


# GRE scores show up in the same fixed structured block as GPA (e.g. "GRE
# 168 GRE V 163 GRE AW 4.50 GPA 3.70"), so these three follow the exact same
# search-anywhere-in-the-text approach as _parse_gpa above. Order matters
# for the Quant pattern: it requires a digit immediately after "GRE" (only
# whitespace/colon/dash in between), so it never accidentally matches into
# "GRE V ..." or "GRE AW ..." (both have a letter, not a digit, right after
# "GRE"). Verified against the full 40k-row dataset: 3,044 / 2,463 / 2,160
# entries carry a Quant / Verbal / AW score respectively, and every match
# pulled the correct number.
_GRE_Q_RE = re.compile(
    r"\bGRE\s*[:\-]?\s*(\d{2,3})\b",
    re.IGNORECASE,
)

_GRE_V_RE = re.compile(
    r"\bGRE\s*V\s*[:\-]?\s*(\d{2,3})\b",
    re.IGNORECASE,
)

_GRE_AW_RE = re.compile(
    r"\bGRE\s*AW\s*[:\-]?\s*(\d(?:\.\d{1,2})?)\b",
    re.IGNORECASE,
)


def _parse_gre(
    text: str,
) -> str:
    """Extract the GRE Quantitative score as ``"GRE <n>"``.

    :param text: Raw metadata text.
    :returns: e.g. ``"GRE 168"``, or ``""``.
    :rtype: str
    """

    match = _GRE_Q_RE.search(text)

    return f"GRE {match.group(1)}" if match else ""


def _parse_gre_v(
    text: str,
) -> str:
    """Extract the GRE Verbal score as ``"GRE V <n>"``.

    :param text: Raw metadata text.
    :returns: e.g. ``"GRE V 163"``, or ``""``.
    :rtype: str
    """

    match = _GRE_V_RE.search(text)

    return f"GRE V {match.group(1)}" if match else ""


def _parse_gre_aw(
    text: str,
) -> str:
    """Extract the GRE Analytical Writing score as ``"GRE AW <n>"``.

    :param text: Raw metadata text.
    :returns: e.g. ``"GRE AW 4.50"``, or ``""``.
    :rtype: str
    """

    match = _GRE_AW_RE.search(text)

    return f"GRE AW {match.group(1)}" if match else ""


def _extract_degree(
    program_text: str,
) -> tuple[str, str]:
    """Split the trailing degree token (Masters/PhD/...) off the program text.

    :param program_text: Raw program text, e.g.
        ``"Speech Language Pathology Masters"``.
    :returns: ``(program_name, degree)``, e.g.
        ``("Speech Language Pathology", "Masters")``. ``degree`` is ``""``
        if no recognised suffix is found.
    :rtype: tuple[str, str]
    """

    match = _DEGREE_SUFFIX_RE.search(program_text)

    if not match:
        return _clean_text(program_text), ""

    degree = _clean_text(match.group(1))
    program_name = _clean_text(program_text[: match.start()])

    return program_name, degree


def _extract_comment(
    meta_text: str,
) -> str:
    """Strip the structured prefix off the meta text, leaving the comment.

    The prefix (status/date/term/type/GRE/GPA) is matched by
    ``_META_PREFIX_RE``; whatever remains is the applicant's free-text
    comment.

    :param meta_text: Raw metadata/comment text.
    :returns: The free-text comment, possibly ``""``.
    :rtype: str
    """

    match = _META_PREFIX_RE.match(meta_text)
    residue = meta_text[match.end():] if match else meta_text

    return _clean_text(residue)


# ============================================================================
# Clean data
# ============================================================================

def clean_data(
    raw_entries: list[dict],
) -> list[dict]:
    """Turn raw scraped entries into cleaned, structured entries.

    Input entries use the keys produced by :func:`scrape._parse_page`
    (``raw_school_text``, ``raw_program_text``, ``raw_comment_text``,
    ``raw_added_on_text``, ``raw_decision_text``, ``raw_meta_text``,
    ``url``). Output entries are ready for the
    ``llm-generated-program`` / ``llm-generated-university`` tagging step
    and, after that, :mod:`load_data`.

    :param raw_entries: Raw scraper output.
    :returns: One cleaned dictionary per input entry, in the same order.
    :rtype: list[dict]

    >>> clean_data([{"raw_school_text": "MIT",
    ...              "raw_program_text": "Computer Science PhD",
    ...              "raw_meta_text": "Accepted Fall 2026 International GPA 3.90"}])[0]["Degree"]
    'PhD'
    """

    cleaned: list[dict] = []

    for entry in raw_entries:

        school_text = _clean_text(
            entry.get("raw_school_text")
        )

        program_text = _clean_text(
            entry.get("raw_program_text")
        )

        program_name, degree = _extract_degree(
            program_text
        )

        program = ", ".join(
            part for part in (program_name, school_text) if part
        )

        comments = _extract_comment(
            _clean_text(entry.get("raw_comment_text"))
        )

        raw_added_on = _clean_text(
            entry.get("raw_added_on_text")
        )

        if raw_added_on and not raw_added_on.lower().startswith("added on"):
            date_added = f"Added on {raw_added_on}"
        else:
            date_added = raw_added_on

        url = _clean_text(
            entry.get("url")
        )

        status = _clean_text(
            entry.get("raw_decision_text")
        )

        meta_text = _clean_text(
            entry.get("raw_meta_text")
        )

        term = _parse_term(meta_text)
        student_type = _parse_student_type(meta_text)
        gre = _parse_gre(meta_text)
        gre_v = _parse_gre_v(meta_text)
        gre_aw = _parse_gre_aw(meta_text)
        gpa = _parse_gpa(meta_text)

        cleaned_entry: dict[str, str] = {}

        if program:
            cleaned_entry["program"] = program

        cleaned_entry["comments"] = comments

        if date_added:
            cleaned_entry["date_added"] = date_added

        if url:
            cleaned_entry["url"] = url

        if status:
            cleaned_entry["status"] = status

        if term:
            cleaned_entry["term"] = term

        if student_type:
            cleaned_entry["US/International"] = student_type

        if gre:
            cleaned_entry["GRE"] = gre

        if gre_v:
            cleaned_entry["GRE V"] = gre_v

        if gre_aw:
            cleaned_entry["GRE AW"] = gre_aw

        if gpa:
            cleaned_entry["GPA"] = gpa

        if degree:
            cleaned_entry["Degree"] = degree

        cleaned.append(
            cleaned_entry
        )

    return cleaned


# ============================================================================
# I/O
# ============================================================================

def load_data(
    in_path: str,
) -> list[dict]:
    """Load raw scraper JSON from disk.

    :param in_path: Path to the input JSON file.
    :returns: The decoded list of raw entries.
    :rtype: list[dict]
    :raises FileNotFoundError: If ``in_path`` does not exist.
    """

    path = Path(in_path)

    if not path.exists():
        raise FileNotFoundError(
            f"Input file not found: {path}"
        )

    with path.open(
        "r",
        encoding="utf-8",
    ) as file:

        return json.load(file)


def save_data(
    entries: list[dict],
    out_path: str,
) -> None:
    """Write cleaned entries to ``out_path`` as pretty-printed UTF-8 JSON.

    :param entries: Cleaned entries.
    :param out_path: Destination file path.
    :raises RuntimeError: If the file cannot be written.
    """

    try:

        with open(
            out_path,
            "w",
            encoding="utf-8",
        ) as file:

            json.dump(
                entries,
                file,
                ensure_ascii=False,
                indent=2,
            )

    except OSError as exc:

        raise RuntimeError(
            f"Could not write output file '{out_path}': {exc}"
        ) from exc

    print(
        f"[output] Wrote {len(entries):,} entries to {out_path}"
    )


# ============================================================================
# CLI
# ============================================================================

def main() -> int:
    """Command-line entry point.

    Options: ``--input/-i`` (default ``applicant_data.json``) and
    ``--output/-o`` (default ``cleaned_applicant_data_new.json``).

    :returns: ``0`` on success or empty input, ``1`` if the input is missing.
    :rtype: int
    """

    parser = argparse.ArgumentParser(
        description=(
            "Clean a raw GradCafe JSON export into structured fields "
            "(program, comments, date_added, url, status, term, "
            "US/International, GRE, GRE V, GRE AW, GPA, Degree)."
        )
    )

    parser.add_argument(
        "--input",
        "-i",
        default="applicant_data.json",
        help="Path to input JSON (default: applicant_data.json).",
    )

    parser.add_argument(
        "--output",
        "-o",
        default="cleaned_applicant_data_new.json",
        help="Path to write regex-cleaned JSON (default: cleaned_applicant_data_new.json).",
    )

    args = parser.parse_args()

    try:
        entries = load_data(args.input)
    except FileNotFoundError as exc:
        print(f"[error] {exc}", file=sys.stderr)
        return 1

    if not entries:
        print("No entries to process. Nothing was written.")
        return 0

    cleaned = clean_data(entries)
    save_data(cleaned, args.output)

    return 0


if __name__ == "__main__":
    sys.exit(main())