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

    if re.search(r"\bInternational\b", text, re.IGNORECASE):
        return "International"

    if re.search(r"\bAmerican\b", text, re.IGNORECASE):
        return "American"

    return ""


def _parse_gpa(
    text: str,
) -> str:

    match = re.search(
        r"\bGPA\s*[:\-]?\s*([0-4](?:\.\d{1,3})?)\b",
        text,
        re.IGNORECASE,
    )

    return f"GPA {match.group(1)}" if match else ""


def _extract_degree(
    program_text: str,
) -> tuple[str, str]:
    """Split the trailing degree token (Masters/PhD/...) off the raw
    program text. Returns (program_name_without_degree, degree)."""

    match = _DEGREE_SUFFIX_RE.search(program_text)

    if not match:
        return _clean_text(program_text), ""

    degree = _clean_text(match.group(1))
    program_name = _clean_text(program_text[: match.start()])

    return program_name, degree


def _extract_comment(
    meta_text: str,
) -> str:
    """Strip the leading structured block (status/date/term/type/GRE/GPA)
    off the raw meta text, leaving only the genuine free-text comment."""

    match = _META_PREFIX_RE.match(meta_text)
    residue = meta_text[match.end():] if match else meta_text

    return _clean_text(residue)


# ============================================================================
# Clean data
# ============================================================================

def clean_data(
    raw_entries: list[dict],
) -> list[dict]:
    """
    Turn a list of raw scraped entries (raw_school_text, raw_program_text,
    raw_comment_text, raw_added_on_text, raw_decision_text, raw_meta_text,
    url) into a list of cleaned, structured entries ready for the
    llm-generated-program / llm-generated-university tagging step.
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

    parser = argparse.ArgumentParser(
        description=(
            "Clean a raw GradCafe JSON export into structured fields "
            "(program, comments, date_added, url, status, term, "
            "US/International, GPA, Degree)."
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
        default="cleaned_applicant_data.json",
        help="Path to write regex-cleaned JSON (default: cleaned_applicant_data.json).",
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
