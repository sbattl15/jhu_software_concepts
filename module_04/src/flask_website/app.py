from __future__ import annotations

import os
import subprocess
import sys
import threading
from datetime import datetime
from pathlib import Path

from flask import Flask, jsonify, render_template

import orm_queries as q
from models import get_session

app = Flask(__name__)
app.secret_key = os.environ.get("FLASK_SECRET_KEY", "dev-secret-change-me")

BASE_DIR = Path(__file__).resolve().parent
PULL_DATA_SCRIPT = BASE_DIR / "pull_data.py"

# --------------------------------------------------------------------------
# Part 9: Pull Data -- background subprocess + status tracking
# --------------------------------------------------------------------------

_pull_lock = threading.Lock()
_pull_state: dict = {
    "running": False,
    "started_at": None,
    "finished_at": None,
    "success": None,       # None until a run has finished; True/False after
    "message": "No data pull has been run yet.",
}


def _iso(dt) -> str | None:
    return dt.isoformat() if dt else None


def _pull_status_snapshot() -> dict:
    with _pull_lock:
        return {
            "running": _pull_state["running"],
            "started_at": _iso(_pull_state["started_at"]),
            "finished_at": _iso(_pull_state["finished_at"]),
            "success": _pull_state["success"],
            "message": _pull_state["message"],
        }


def _pull_is_running() -> bool:
    """Cheap busy check that /pull-data and /update-analysis gate on, so
    a click can't start a second concurrent pull and an analysis refresh
    can't race a pull that's mid-write."""
    with _pull_lock:
        return _pull_state["running"]


def _fmt_pct(value) -> str:
    """Formats a percentage with exactly two decimal places, regardless of
    whether the DB driver handed back a Decimal, a float, or an int (and
    regardless of how many/few decimal places that value happened to
    carry) -- so the page never shows "42%" or "42.5%", only "42.00%".
    A None (e.g. a percentage of zero rows -- NULLIF-guarded division by
    zero -- comes back NULL/None from Postgres) renders as "0.00" rather
    than raising."""
    if value is None:
        return "0.00"
    return f"{float(value):.2f}"


def _watch_pull_process(proc: subprocess.Popen) -> None:
    """Runs in a background thread: waits for the Pull Data subprocess to
    finish, parses its final PULL_DATA_SUMMARY::{...} line if present, and
    updates the shared status dict. Never touches the request/response
    cycle, so it can't block a page load."""
    summary_line = None
    tail_lines: list[str] = []
    try:
        assert proc.stdout is not None
        for line in proc.stdout:
            line = line.rstrip("\n")
            if not line:
                continue
            print(f"[pull-data subprocess] {line}")
            if line.startswith("PULL_DATA_SUMMARY::"):
                summary_line = line[len("PULL_DATA_SUMMARY::"):]
            else:
                tail_lines.append(line)
        returncode = proc.wait()
    except Exception as exc:  # the watcher itself failed, not the subprocess
        returncode = -1
        tail_lines.append(f"(status watcher error: {exc})")

    if returncode == 0 and summary_line:
        import json

        try:
            summary = json.loads(summary_line)
            message = (
                f"Pull finished at {datetime.now().strftime('%I:%M %p')}: "
                f"added {summary['inserted']:,} new record(s) "
                f"({summary['duplicates']:,} already in the database, "
                f"{summary['usable']:,} usable of {summary['raw']:,} scraped)."
            )
            success = True
        except (ValueError, KeyError):
            message = "Pull finished, but its summary output couldn't be parsed."
            success = True
    elif returncode == 0:
        message = "Pull finished."
        success = True
    else:
        last_output = tail_lines[-1] if tail_lines else "no output captured"
        message = f"Pull failed (exit code {returncode}): {last_output}"
        success = False

    with _pull_lock:
        _pull_state["running"] = False
        _pull_state["finished_at"] = datetime.now()
        _pull_state["success"] = success
        _pull_state["message"] = message


def start_pull_data() -> tuple[bool, str]:
    """Starts pull_data.py in a background subprocess if one isn't already
    running. Returns (started, message)."""
    with _pull_lock:
        if _pull_state["running"]:
            started_at = _pull_state["started_at"]
            when = started_at.strftime("%I:%M %p") if started_at else "recently"
            return False, f"A data pull is already running (started {when}). Please wait for it to finish."

        try:
            proc = subprocess.Popen(
                [sys.executable, str(PULL_DATA_SCRIPT)],
                cwd=str(BASE_DIR),
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
            )
        except OSError as exc:
            return False, f"Could not start Pull Data: {exc}"

        _pull_state["running"] = True
        _pull_state["started_at"] = datetime.now()
        _pull_state["finished_at"] = None
        _pull_state["success"] = None
        _pull_state["message"] = "Pull Data started..."

    threading.Thread(target=_watch_pull_process, args=(proc,), daemon=True).start()
    return True, "Started pulling new data from Grad Cafe. This may take a while depending on how much new data is available -- feel free to keep using the page."


# --------------------------------------------------------------------------
# Part 8: Analysis page (unchanged query logic)
# --------------------------------------------------------------------------


def _build_analysis_context() -> dict:
    """Runs every analysis question against Postgres (through the
    SQLAlchemy Applicant model, via orm_queries.py) and returns the
    template context. Always re-queries -- this is what makes both the
    initial page load and the Update Analysis button "re-query and
    display the most current results"."""
    with get_session() as session:
        results = [
            {
                "number": 1,
                "question": "How many entries are from applicants who applied for Fall 2026?",
                "answer": f"{q.question_1(session):,}",
            },
            {
                "number": 2,
                "question": (
                    "Among entries that provide a nationality classification, "
                    "what percentage are international students?"
                ),
                "answer": f"{_fmt_pct(q.question_2(session))}%",
            },
        ]

        avg_gpa, avg_gre_q, avg_gre_v, avg_gre_aw = q.question_3(session)
        results.append(
            {
                "number": 3,
                "question": (
                    "What are the average GPA, GRE Quantitative, GRE Verbal, and GRE "
                    "Analytical Writing scores of applicants who provide each metric?"
                ),
                "answer": (
                    f"GPA: {avg_gpa} &nbsp;|&nbsp; GRE Quant: {avg_gre_q} &nbsp;|&nbsp; "
                    f"GRE Verbal: {avg_gre_v} &nbsp;|&nbsp; GRE AW: {avg_gre_aw}"
                ),
            }
        )

        results.append(
            {
                "number": 4,
                "question": "What is the average GPA of American applicants who applied for Fall 2026?",
                "answer": str(q.question_4(session)),
            }
        )
        results.append(
            {
                "number": 5,
                "question": "What percentage of Fall 2025 entries are acceptances?",
                "answer": f"{_fmt_pct(q.question_5(session))}%",
            }
        )
        results.append(
            {
                "number": 6,
                "question": "What is the average GPA of accepted applicants who applied for Fall 2025?",
                "answer": str(q.question_6(session)),
            }
        )
        results.append(
            {
                "number": 7,
                "question": (
                    "How many entries are from applicants who applied to Johns Hopkins "
                    "University for a master's degree in Computer Science?"
                ),
                "answer": f"{q.question_7(session):,}",
            }
        )

        original_field_count = q.question_8(session)
        results.append(
            {
                "number": 8,
                "question": (
                    "How many Fall 2026 entries are acceptances from applicants applying for a "
                    "PhD in Computer Science at Georgetown, MIT, Stanford, or Carnegie Mellon?"
                ),
                "answer": f"{original_field_count:,}",
            }
        )

        llm_field_count = q.question_9(session)
        diff = llm_field_count - original_field_count
        results.append(
            {
                "number": 9,
                "question": "Repeat Question 8, but identify university/program using the LLM-generated fields.",
                "answer": (
                    f"{llm_field_count:,} "
                    f"(original-field count was {original_field_count:,}, "
                    f"a difference of {diff:+,})"
                ),
            }
        )

        # -- Original question 1: acceptance rate by GPA bucket ------------
        gpa_bucket_rows = q.additional_question_1(session)
        gpa_buckets = [
            {
                "label": label,
                "total": total_in_bucket,
                "pct": _fmt_pct(acceptance_pct),
            }
            for label, total_in_bucket, acceptance_pct in gpa_bucket_rows
        ]

        # -- Original question 2: applicant volume by term, with trend -----
        term_count_rows = q.additional_question_2(session)
        term_counts = [{"term": term, "count": count} for term, count in term_count_rows]
        max_term_count = max((row["count"] for row in term_counts), default=0)
        for row in term_counts:
            row["pct_of_max"] = (
                _fmt_pct(row["count"] / max_term_count * 100) if max_term_count else "0.00"
            )

        trend = "no data"
        if len(term_count_rows) >= 2:
            first_count, last_count = term_count_rows[0][1], term_count_rows[-1][1]
            if last_count > first_count:
                trend = "trending up"
            elif last_count < first_count:
                trend = "trending down"
            else:
                trend = "no clear trend"

    return {
        "results": results,
        "gpa_buckets": gpa_buckets,
        "term_counts": term_counts,
        "trend": trend,
        "generated_at": datetime.now().strftime("%B %d, %Y at %I:%M %p"),
    }


@app.route("/")
def analysis() -> str:
    context = _build_analysis_context()
    context["pull_status"] = _pull_status_snapshot()
    return render_template("index.html", **context)


# --------------------------------------------------------------------------
# Part 9: Pull Data routes
# --------------------------------------------------------------------------


@app.route("/pull-data", methods=["POST"])
def pull_data():
    if _pull_is_running():
        return jsonify({"error": "A data pull is already in progress."}), 409

    started, message = start_pull_data()
    if not started:
        # Lost a race with another request between the check above and
        # start_pull_data()'s own lock -- still busy, so still a 409.
        return jsonify({"error": message}), 409

    return jsonify({"started": True, "message": message}), 200


@app.route("/pull-data/status")
def pull_data_status():
    return jsonify(_pull_status_snapshot())


# --------------------------------------------------------------------------
# Part 10: Update Analysis
#
# "/" already re-queries Postgres on every request, so refreshing the
# results on demand is just running _build_analysis_context() again.
# Gated on the same busy flag as Pull Data: while a pull is writing new
# rows, an update is refused outright (409, no query is run) rather than
# racing it.
# --------------------------------------------------------------------------


@app.route("/update-analysis", methods=["POST"])
def update_analysis():
    if _pull_is_running():
        return jsonify({"error": "A data pull is in progress; try again once it finishes."}), 409

    context = _build_analysis_context()
    return jsonify({"updated": True, "generated_at": context["generated_at"]}), 200


if __name__ == "__main__":
    app.run(debug=True)