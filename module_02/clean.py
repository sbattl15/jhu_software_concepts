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
# Parsing helpers
# ============================================================================

def _split_program_university(
    program_text: str,
    school_text: str = "",
) -> tuple[str, str]:

    program_text = _clean_text(
        program_text
    )

    school_text = _clean_text(
        school_text
    )

    if program_text and school_text:

        if (
            program_text.lower()
            != school_text.lower()
        ):
            return (
                program_text,
                school_text,
            )

    combined = (
        program_text
        or school_text
    )

    if not combined:
        return "", ""

    if "," in combined:

        program, university = combined.rsplit(
            ",",
            1,
        )

        return (
            _clean_text(program),
            _clean_text(university),
        )

    return combined, ""


def _parse_status(
    text: str,
) -> tuple[str, str, str]:

    text = _clean_text(text)

    status = ""
    accepted_date = ""
    rejected_date = ""

    accepted_match = re.search(
        r"\bAccepted\b"
        r"(?:\s+on\s+)?"
        r"([A-Za-z]{3,9}\s+\d{1,2})?",
        text,
        re.IGNORECASE,
    )

    if accepted_match:

        status = "Accepted"

        if accepted_match.group(1):
            accepted_date = _clean_text(
                accepted_match.group(1)
            )

    rejected_match = re.search(
        r"\bRejected\b"
        r"(?:\s+on\s+)?"
        r"([A-Za-z]{3,9}\s+\d{1,2})?",
        text,
        re.IGNORECASE,
    )

    if rejected_match:

        status = "Rejected"

        if rejected_match.group(1):
            rejected_date = _clean_text(
                rejected_match.group(1)
            )

    if re.search(
        r"\bWait\s*listed\b",
        text,
        re.IGNORECASE,
    ):
        status = "Wait listed"

    if re.search(
        r"\bInterview\b",
        text,
        re.IGNORECASE,
    ):
        status = "Interview"

    return (
        status,
        accepted_date,
        rejected_date,
    )


def _parse_semester_year(
    text: str,
) -> str:

    match = re.search(
        r"\b(Spring|Summer|Fall|Winter)"
        r"\s+(\d{4})\b",
        text,
        re.IGNORECASE,
    )

    if not match:
        return ""

    return (
        f"{match.group(1).capitalize()} "
        f"{match.group(2)}"
    )


def _parse_student_type(
    text: str,
) -> str:

    if re.search(
        r"\bInternational\b",
        text,
        re.IGNORECASE,
    ):
        return "International"

    if re.search(
        r"\bAmerican\b",
        text,
        re.IGNORECASE,
    ):
        return "American"

    if re.search(
        r"\bUS\b",
        text,
        re.IGNORECASE,
    ):
        return "American"

    return ""


def _parse_degree(
    text: str,
) -> str:

    if re.search(
        r"\b(Master'?s|Masters|MS|MSc|MA|MBA|MEng|MFA)\b",
        text,
        re.IGNORECASE,
    ):
        return "Masters"

    if re.search(
        r"\b(Ph\.?D\.?|Doctorate)\b",
        text,
        re.IGNORECASE,
    ):
        return "PhD"

    return ""


def _parse_gpa(
    text: str,
) -> str:

    match = re.search(
        r"\bGPA\s*[:\-]?\s*"
        r"([0-4](?:\.\d{1,3})?)\b",
        text,
        re.IGNORECASE,
    )

    return (
        match.group(1)
        if match
        else ""
    )


def _parse_gre(
    text: str,
) -> tuple[str, str, str]:

    gre_score = ""
    gre_v_score = ""
    gre_aw = ""

    match = re.search(
        r"\bGRE\s*[:\-]?\s*(\d{3})\b",
        text,
        re.IGNORECASE,
    )

    if match:
        gre_score = match.group(1)

    match = re.search(
        r"\b(?:GRE\s*)?"
        r"(?:V|Verbal)"
        r"\s*[:\-]?\s*"
        r"(\d{2,3})\b",
        text,
        re.IGNORECASE,
    )

    if match:
        gre_v_score = match.group(1)

    match = re.search(
        r"\b(?:GRE\s*)?"
        r"(?:AW|AWA|Analytical\s+Writing)"
        r"\s*[:\-]?\s*"
        r"(\d(?:\.\d)?)\b",
        text,
        re.IGNORECASE,
    )

    if match:
        gre_aw = match.group(1)

    return (
        gre_score,
        gre_v_score,
        gre_aw,
    )


# ============================================================================
# Clean data
# ============================================================================

def clean_data(
    raw_entries: list[dict],
) -> list[dict]:
    """
    Turn a list of raw scraped entries (raw_school_text, raw_program_text,
    raw_comment_text, raw_added_on_text, raw_decision_text, raw_meta_text,
    url) into a list of cleaned, structured entries.
    """

    cleaned: list[dict] = []

    for entry in raw_entries:

        school_text = _clean_text(
            entry.get(
                "raw_school_text"
            )
        )

        program_text = _clean_text(
            entry.get(
                "raw_program_text"
            )
        )

        program_name, university = (
            _split_program_university(
                program_text,
                school_text,
            )
        )

        comments = _clean_text(
            entry.get(
                "raw_comment_text"
            )
        )

        date_added = _clean_text(
            entry.get(
                "raw_added_on_text"
            )
        )

        url = _clean_text(
            entry.get(
                "url"
            )
        )

        decision_text = _clean_text(
            entry.get(
                "raw_decision_text"
            )
        )

        meta_text = _clean_text(
            entry.get(
                "raw_meta_text"
            )
        )

        searchable_text = " ".join(
            [
                decision_text,
                meta_text,
            ]
        )

        (
            status,
            accepted_date,
            rejected_date,
        ) = _parse_status(
            searchable_text
        )

        semester_year = _parse_semester_year(
            searchable_text
        )

        student_type = _parse_student_type(
            searchable_text
        )

        degree = _parse_degree(
            searchable_text
        )

        gpa = _parse_gpa(
            searchable_text
        )

        (
            gre_score,
            gre_v_score,
            gre_aw,
        ) = _parse_gre(
            searchable_text
        )

        cleaned_entry: dict[str, str] = {}

        if program_name:
            cleaned_entry["program_name"] = program_name

        if university:
            cleaned_entry["university"] = university

        if comments:
            cleaned_entry["comments"] = comments

        if date_added:
            cleaned_entry["date_added"] = date_added

        if url:
            cleaned_entry["url"] = url

        if status:
            cleaned_entry["applicant_status"] = status

        if accepted_date:
            cleaned_entry["accepted_date"] = accepted_date

        if rejected_date:
            cleaned_entry["rejected_date"] = rejected_date

        if semester_year:
            cleaned_entry["semester_year"] = semester_year

        if student_type:
            cleaned_entry["international_american"] = student_type

        if gre_score:
            cleaned_entry["gre_score"] = gre_score

        if gre_v_score:
            cleaned_entry["gre_v_score"] = gre_v_score

        if degree:
            cleaned_entry["masters_phd"] = degree

        if gpa:
            cleaned_entry["gpa"] = gpa

        if gre_aw:
            cleaned_entry["gre_aw"] = gre_aw

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
            "(program_name, university, applicant_status, gpa, etc.)."
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


# use the two below commands to run app.py from the module_2 folder
# cd llm_hosting
# python app.py --file ../cleaned_applicant_data.json --out ../out.json