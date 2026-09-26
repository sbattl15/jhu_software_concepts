# -*- coding: utf-8 -*-
"""Flask + tiny local LLM standardizer with incremental JSON-array CLI output.

The CLI path (--file) parallelizes row processing across multiple worker
*processes* (not threads) via ProcessPoolExecutor. Each worker loads its
own private llama.cpp model instance, since a single llama.cpp context
cannot safely serve concurrent generate() calls from multiple threads. With
NUM_WORKERS worker processes each running WORKER_N_THREADS llama.cpp
threads internally, the product of the two should roughly match your
machine's logical CPU count (defaults: 12 workers x 1 thread = 12 cores).

pull_data.py imports `_call_llm` and `_clean_row` from this module directly
(in-process) to add llm_generated_program / llm_generated_university (plus
the other typed fields _clean_row derives -- date_added, status, gpa, gre,
us_or_international, degree) to each row clean_new.py produced, before
they're loaded into Postgres. This file's own Flask app / CLI entry point
below are unused by that path and are left exactly as provided so `python
llm_standardize.py --serve` or `--file ...` still work standalone.
"""

from __future__ import annotations

import json
import os
import re
import sys
import difflib
from concurrent.futures import ProcessPoolExecutor
from datetime import datetime
from typing import Any, Dict, List, Tuple

from flask import Flask, jsonify, request
from huggingface_hub import hf_hub_download
from llama_cpp import Llama  # CPU-only by default if N_GPU_LAYERS=0

app = Flask(__name__)

# ---------------- Model config ----------------
MODEL_REPO = os.getenv(
    "MODEL_REPO",
    "TheBloke/TinyLlama-1.1B-Chat-v1.0-GGUF",
)
MODEL_FILE = os.getenv(
    "MODEL_FILE",
    "tinyllama-1.1b-chat-v1.0.Q4_K_M.gguf",
)

# Threads a single Llama instance uses (the Flask /standardize path, and
# the CLI path when --workers 1). Defaults to all logical CPUs.
N_THREADS = int(os.getenv("N_THREADS", str(os.cpu_count() or 2)))
N_CTX = int(os.getenv("N_CTX", "2048"))
N_GPU_LAYERS = int(os.getenv("N_GPU_LAYERS", "0"))  # 0 -> CPU-only

# ---------------- CLI multiprocessing config ----------------
# How many worker *processes* the CLI (--file) path fans out across.
# Each process loads its own independent Llama instance/context, since
# llama.cpp contexts aren't safe to share across concurrent generations.
NUM_WORKERS = int(os.getenv("NUM_WORKERS", "12"))
# llama.cpp threads *within* each worker process. With NUM_WORKERS
# processes already splitting the CPU budget, each one typically only
# needs a sliver of the total core count (default 1 thread/worker so
# NUM_WORKERS x WORKER_N_THREADS lines up with a 12-core machine).
WORKER_N_THREADS = int(os.getenv("WORKER_N_THREADS", "1"))

CANON_UNIS_PATH = os.getenv("CANON_UNIS_PATH", "canon_universities.txt")
CANON_PROGS_PATH = os.getenv("CANON_PROGS_PATH", "canon_programs.txt")

# Precompiled, non-greedy JSON object matcher to tolerate chatter around JSON
JSON_OBJ_RE = re.compile(r"\{.*?\}", re.DOTALL)

# ---------------- Canonical lists + abbrev maps ----------------
def _read_lines(path: str) -> List[str]:
    """Read non-empty, stripped lines from a file (UTF-8)."""
    try:
        with open(path, "r", encoding="utf-8") as f:
            return [ln.strip() for ln in f if ln.strip()]
    except FileNotFoundError:
        return []


CANON_UNIS = _read_lines(CANON_UNIS_PATH)
CANON_PROGS = _read_lines(CANON_PROGS_PATH)

ABBREV_UNI: Dict[str, str] = {
    r"(?i)^mcg(\.|ill)?$": "McGill University",
    r"(?i)^(ubc|u\.?b\.?c\.?)$": "University of British Columbia",
    r"(?i)^uoft$": "University of Toronto",
}

COMMON_UNI_FIXES: Dict[str, str] = {
    "McGiill University": "McGill University",
    "Mcgill University": "McGill University",
    # Normalize 'Of' -> 'of'
    "University Of British Columbia": "University of British Columbia",
}

COMMON_PROG_FIXES: Dict[str, str] = {
    "Mathematic": "Mathematics",
    "Info Studies": "Information Studies",
}

# ---------------- Field cleaning (beyond program/university) ----------------
# The LLM step above only splits + standardizes the combined "program"
# string into program name + university. Everything below cleans the
# *other* raw, semi-structured fields scraped from thegradcafe.com into the
# explicit fields this cleaning step is expected to produce:
#   Program Name, University, Comments, Date of Information Added to Grad
#   Cafe, URL, Applicant Status, Accepted/Rejected Date, Semester and Year
#   of Program Start, International/American Student, GRE Score, GRE V
#   Score, GRE AW, Masters or PhD, GPA.

DATE_ADDED_RE = re.compile(r"^Added on (?P<date>.+)$")
DATE_ADDED_FMT = "%b %d, %Y"  # e.g. "Feb 17, 2026"

STATUS_RE = re.compile(
    r"^(?P<type>Accepted|Rejected|Interview|Wait listed) on (?P<date>.+)$"
)

GPA_RE = re.compile(r"^GPA\s+(?P<value>[\d.]+)$")

# GRE fields aren't present at all in the currently-scraped data (only GPA
# is -- confirmed across the full 40k-row dataset this was built against),
# but if a future scrape adds "GRE" / "GRE V" / "GRE AW" keys following the
# same "<Label> <value>" shape as GPA (e.g. "GRE 166"), this pulls the
# trailing number out regardless of the exact label text.
SCORE_RE = re.compile(r"(?P<value>\d+(?:\.\d+)?)\s*$")


def _parse_date_added(value: str | None) -> str | None:
    """"Added on Feb 17, 2026" -> "2026-02-17" (ISO date string -- JSON has
    no native date type). None if missing or unparseable."""
    if not value:
        return None
    m = DATE_ADDED_RE.match(value)
    if not m:
        return None
    try:
        return datetime.strptime(m.group("date"), DATE_ADDED_FMT).date().isoformat()
    except ValueError:
        return None


def _parse_status(value: str | None) -> Tuple[str | None, str | None]:
    """"Rejected on Feb 17" -> ("Rejected", "Feb 17"). No year is present in
    the source status string, and it isn't safe to infer one from
    date_added (decisions are sometimes logged well before or after the
    post date), so the acceptance/rejection date is kept as month/day text
    rather than guessing a full date."""
    if not value:
        return None, None
    m = STATUS_RE.match(value)
    if not m:
        return None, None
    return m.group("type"), m.group("date")


def _parse_gpa(value: str | None) -> float | None:
    """"GPA 3.85" -> 3.85. Deliberately does NOT clamp/reject values above
    4.0 -- some entries report a weighted/non-4.0-scale GPA (up to ~4.9 in
    this data), and silently dropping those would lose real data."""
    if not value:
        return None
    m = GPA_RE.match(value.strip())
    if not m:
        return None
    try:
        return float(m.group("value"))
    except ValueError:
        return None


def _parse_score(value: str | None) -> float | None:
    """Pulls a trailing numeric GRE-style score out of a free-form string
    (e.g. "GRE 166", "GRE AW 4.5"). Returns None for missing/unparseable
    input so a row with no GRE data never blocks cleaning."""
    if not value:
        return None
    m = SCORE_RE.search(value.strip())
    if not m:
        return None
    try:
        return float(m.group("value"))
    except ValueError:
        return None


def _clean_row(row: Dict[str, Any], standardized: Dict[str, str]) -> None:
    """Attaches the full set of cleaned fields to `row` in place, on top of
    the LLM-standardized program/university already computed in
    `standardized`. Raw source strings that get parsed into a cleaner form
    are kept too (under *_raw keys) so nothing scraped is lost."""
    # Program Name / University -- computed by the LLM step. Kept under
    # both the new plain names and the original "llm-generated-*" keys so
    # anything already reading the old keys (e.g. load_data.py) still works.
    row["program_name"] = standardized["standardized_program"]
    row["university"] = standardized["standardized_university"]
    row["llm-generated-program"] = standardized["standardized_program"]
    row["llm-generated-university"] = standardized["standardized_university"]

    # Comments / URL / Term are already clean as scraped -- pass through.
    row["comments"] = row.get("comments") or None
    row["url"] = row.get("url")
    row["term"] = row.get("term")

    # Date of Information Added to Grad Cafe.
    row["date_added_raw"] = row.get("date_added")
    row["date_added"] = _parse_date_added(row.get("date_added"))

    # Applicant Status, split from the combined "<Type> on <date>" string
    # into a clean status type plus separate acceptance/rejection dates.
    row["status_raw"] = row.get("status")
    status_type, status_date = _parse_status(row.get("status"))
    row["status"] = status_type
    row["accepted_date"] = status_date if status_type == "Accepted" else None
    row["rejected_date"] = status_date if status_type == "Rejected" else None

    # International / American Student.
    row["us_or_international"] = row.get("US/International")

    # Masters or PhD.
    row["degree"] = row.get("Degree")

    # GPA, parsed to a float.
    row["gpa"] = _parse_gpa(row.get("GPA"))

    # GRE Score / GRE V Score / GRE AW. NOTE: the currently-scraped data has
    # no GRE fields at all, so these come back None for every row today --
    # included so the output schema is complete and ready to populate the
    # moment a scrape actually includes them.
    row["gre"] = _parse_score(row.get("GRE"))
    row["gre_v"] = _parse_score(row.get("GRE V"))
    row["gre_aw"] = _parse_score(row.get("GRE AW"))


# ---------------- Few-shot prompt ----------------
SYSTEM_PROMPT = (
    "You are a data cleaning assistant. Standardize degree program and university "
    "names.\n\n"
    "Rules:\n"
    "- Input provides a single string under key `program` that may contain both "
    "program and university.\n"
    "- Split into (program name, university name).\n"
    "- Trim extra spaces and commas.\n"
    '- Expand obvious abbreviations (e.g., "McG" -> "McGill University", '
    '"UBC" -> "University of British Columbia").\n'
    "- Use Title Case for program; use official capitalization for university "
    "names (e.g., \"University of X\").\n"
    '- Ensure correct spelling (e.g., "McGill", not "McGiill").\n'
    '- If university cannot be inferred, return "Unknown".\n\n'
    "Return JSON ONLY with keys:\n"
    "  standardized_program, standardized_university\n"
)

FEW_SHOTS: List[Tuple[Dict[str, str], Dict[str, str]]] = [
    (
        {"program": "Information Studies, McGill University"},
        {
            "standardized_program": "Information Studies",
            "standardized_university": "McGill University",
        },
    ),
    (
        {"program": "Information, McG"},
        {
            "standardized_program": "Information Studies",
            "standardized_university": "McGill University",
        },
    ),
    (
        {"program": "Mathematics, University Of British Columbia"},
        {
            "standardized_program": "Mathematics",
            "standardized_university": "University of British Columbia",
        },
    ),
]

_LLM: Llama | None = None


def _ensure_model_downloaded() -> str:
    """Download (or reuse) the GGUF file and return its local path.

    Called once up-front in the main process before spawning workers, so
    NUM_WORKERS processes don't all race to download the same file over
    the network the first time this is run.
    """
    return hf_hub_download(
        repo_id=MODEL_REPO,
        filename=MODEL_FILE,
        local_dir="models",
        local_dir_use_symlinks=False,
        force_filename=MODEL_FILE,
    )


def _load_llm(n_threads: int | None = None) -> Llama:
    """Load (or reuse) this process's private llama.cpp instance."""
    global _LLM
    if _LLM is not None:
        return _LLM

    model_path = _ensure_model_downloaded()

    _LLM = Llama(
        model_path=model_path,
        n_ctx=N_CTX,
        n_threads=n_threads if n_threads is not None else N_THREADS,
        n_gpu_layers=N_GPU_LAYERS,
        verbose=False,
    )
    return _LLM


def _split_fallback(text: str) -> Tuple[str, str]:
    """Simple, rules-first parser if the model returns non-JSON."""
    s = re.sub(r"\s+", " ", (text or "")).strip().strip(",")
    parts = [p.strip() for p in re.split(r",| at | @ ", s) if p.strip()]
    prog = parts[0] if parts else ""
    uni = parts[1] if len(parts) > 1 else ""

    # High-signal expansions
    if re.fullmatch(r"(?i)mcg(ill)?(\.)?", uni or ""):
        uni = "McGill University"
    if re.fullmatch(
        r"(?i)(ubc|u\.?b\.?c\.?|university of british columbia)",
        uni or "",
    ):
        uni = "University of British Columbia"

    # Title-case program; normalize 'Of' -> 'of' for universities
    prog = prog.title()
    if uni:
        uni = re.sub(r"\bOf\b", "of", uni.title())
    else:
        uni = "Unknown"
    return prog, uni


def _best_match(name: str, candidates: List[str], cutoff: float = 0.86) -> str | None:
    """Fuzzy match via difflib (lightweight, Replit-friendly)."""
    if not name or not candidates:
        return None
    matches = difflib.get_close_matches(name, candidates, n=1, cutoff=cutoff)
    return matches[0] if matches else None


def _post_normalize_program(prog: str) -> str:
    """Apply common fixes, title case, then canonical/fuzzy mapping."""
    p = (prog or "").strip()
    p = COMMON_PROG_FIXES.get(p, p)
    p = p.title()
    if p in CANON_PROGS:
        return p
    match = _best_match(p, CANON_PROGS, cutoff=0.84)
    return match or p


def _post_normalize_university(uni: str) -> str:
    """Expand abbreviations, apply common fixes, capitalization, and canonical map."""
    u = (uni or "").strip()

    # Abbreviations
    for pat, full in ABBREV_UNI.items():
        if re.fullmatch(pat, u):
            u = full
            break

    # Common spelling fixes
    u = COMMON_UNI_FIXES.get(u, u)

    # Normalize 'Of' -> 'of'
    if u:
        u = re.sub(r"\bOf\b", "of", u.title())

    # Canonical or fuzzy map
    if u in CANON_UNIS:
        return u
    match = _best_match(u, CANON_UNIS, cutoff=0.86)
    return match or u or "Unknown"


def _call_llm(program_text: str) -> Dict[str, str]:
    """Query this process's tiny LLM and return standardized fields."""
    llm = _load_llm()

    messages = [{"role": "system", "content": SYSTEM_PROMPT}]
    for x_in, x_out in FEW_SHOTS:
        messages.append(
            {"role": "user", "content": json.dumps(x_in, ensure_ascii=False)}
        )
        messages.append(
            {
                "role": "assistant",
                "content": json.dumps(x_out, ensure_ascii=False),
            }
        )
    messages.append(
        {
            "role": "user",
            "content": json.dumps({"program": program_text}, ensure_ascii=False),
        }
    )

    out = llm.create_chat_completion(
        messages=messages,
        temperature=0.0,
        max_tokens=128,
        top_p=1.0,
    )

    text = (out["choices"][0]["message"]["content"] or "").strip()
    try:
        match = JSON_OBJ_RE.search(text)
        obj = json.loads(match.group(0) if match else text)
        std_prog = str(obj.get("standardized_program", "")).strip()
        std_uni = str(obj.get("standardized_university", "")).strip()
    except Exception:
        std_prog, std_uni = _split_fallback(program_text)

    std_prog = _post_normalize_program(std_prog)
    std_uni = _post_normalize_university(std_uni)
    return {
        "standardized_program": std_prog,
        "standardized_university": std_uni,
    }


def _normalize_input(payload: Any) -> List[Dict[str, Any]]:
    """Accept either a list of rows or {'rows': [...]}."""
    if isinstance(payload, list):
        return payload
    if isinstance(payload, dict) and isinstance(payload.get("rows"), list):
        return payload["rows"]
    return []


@app.get("/")
def health() -> Any:
    """Simple liveness check."""
    return jsonify({"ok": True})


@app.post("/standardize")
def standardize() -> Any:
    """Standardize rows from an HTTP request and return JSON."""
    payload = request.get_json(force=True, silent=True)
    rows = _normalize_input(payload)

    out: List[Dict[str, Any]] = []
    for row in rows:
        program_text = (row or {}).get("program") or ""
        result = _call_llm(program_text)
        _clean_row(row, result)
        out.append(row)

    return jsonify({"rows": out})


# ============================================================================
# Worker-process pool for the CLI path
# ============================================================================

def _pool_worker_init(n_threads: int) -> None:
    """Runs once in each freshly-started worker process: loads a private
    Llama instance so concurrent workers never share one llama.cpp
    context/model (concurrent generate() calls on a shared context aren't
    safe)."""
    global N_THREADS
    N_THREADS = n_threads
    _load_llm(n_threads=n_threads)


def _worker_call_llm(program_text: str) -> Dict[str, str]:
    """Top-level (picklable) entry point ProcessPoolExecutor.map calls in
    each worker process."""
    return _call_llm(program_text)


# How often (in processed rows) the CLI rewrites the output file, so a long
# run stays resumable / viewable partway through without paying the cost of
# rewriting the whole array after every single row.
_CHECKPOINT_EVERY = 200


def _dump_json_array(entries: List[Dict[str, Any]], sink) -> None:
    """Write `entries` as one indented JSON array, matching the format
    produced by clean.py's save_data (indent=2, ensure_ascii=False)."""
    json.dump(entries, sink, ensure_ascii=False, indent=2)
    sink.write("\n")


def _cli_process_file(
    in_path: str,
    out_path: str | None,
    append: bool,
    to_stdout: bool,
    workers: int = NUM_WORKERS,
    worker_threads: int = WORKER_N_THREADS,
) -> None:
    """Process a JSON file and write a single JSON array (not JSON Lines),
    so the CLI output matches the array format `clean.py` produces.

    Rows are farmed out across `workers` worker *processes* (each with its
    own private llama.cpp instance running `worker_threads` internal
    threads), using ProcessPoolExecutor.map so results still land back in
    the original row order despite running concurrently.

    Periodically re-writes the full array to disk (every
    `_CHECKPOINT_EVERY` rows and always at the end) so a long run stays
    crash-safe and viewable partway through, without the O(n^2) cost of
    rewriting the file after every single row.

    With --append, if the output file already exists and is a valid JSON
    array, entries already present (matched by "url") are skipped so a run
    can be resumed instead of reprocessing everything from scratch.
    """
    with open(in_path, "r", encoding="utf-8") as f:
        rows = _normalize_input(json.load(f))

    out_path = None if to_stdout else (out_path or (in_path + ".out.json"))

    processed: List[Dict[str, Any]] = []
    seen_urls: set[str] = set()

    if append and out_path and os.path.exists(out_path) and os.path.getsize(out_path) > 0:
        try:
            with open(out_path, "r", encoding="utf-8") as f:
                existing = json.load(f)
            if isinstance(existing, list):
                processed = existing
                seen_urls = {
                    row.get("url") for row in processed if isinstance(row, dict) and row.get("url")
                }
                print(
                    f"[resume] {len(processed):,} entries already in "
                    f"{out_path}; skipping those."
                )
        except (json.JSONDecodeError, OSError):
            processed = []
            seen_urls = set()

    pending = (
        [row for row in rows if not (isinstance(row, dict) and row.get("url") in seen_urls)]
        if seen_urls
        else rows
    )

    def _checkpoint() -> None:
        if to_stdout:
            _dump_json_array(processed, sys.stdout)
        else:
            with open(out_path, "w", encoding="utf-8") as sink:
                _dump_json_array(processed, sink)

    total_pending = len(pending)

    if total_pending == 0:
        # Nothing new to process, but still make sure the destination
        # reflects what we already have.
        _checkpoint()
        return

    workers = max(1, workers)
    program_texts = [(row or {}).get("program") or "" for row in pending]

    # `_call_llm` runs at temperature=0.0, so it's deterministic: the same
    # `program` string always produces the same standardized fields. Real
    # GradCafe data is full of exact repeats (the same program/university
    # gets posted by many applicants), so we only ever run the model once
    # per *distinct* string and fan that cached result back out to every
    # row that shares it -- this alone often cuts the number of actual LLM
    # calls by more than half, with no change in output quality.
    unique_texts = list(dict.fromkeys(program_texts))
    total_unique = len(unique_texts)

    print(
        f"[dedupe] {total_pending:,} pending rows -> {total_unique:,} distinct "
        f"program strings to run through the model "
        f"({total_pending - total_unique:,} rows will reuse a cached result).",
        file=sys.stderr,
    )

    results_by_text: Dict[str, Dict[str, str]] = {}
    unique_done = 0

    def _record(text: str, result: Dict[str, str]) -> None:
        nonlocal unique_done
        results_by_text[text] = result
        unique_done += 1
        if unique_done % _CHECKPOINT_EVERY == 0 or unique_done == total_unique:
            print(
                f"[progress] {unique_done:,}/{total_unique:,} distinct "
                f"program strings resolved",
                file=sys.stderr,
            )

    if workers == 1:
        # Small jobs (or explicit --workers 1) skip process-pool overhead
        # entirely and just run in this process, like before.
        for text in unique_texts:
            _record(text, _call_llm(text))
    else:
        # Pre-download the model once here so the worker processes about
        # to start don't all race to fetch it over the network at once.
        _ensure_model_downloaded()

        print(
            f"[workers] Fanning out across {workers} worker processes "
            f"({worker_threads} llama.cpp thread(s) each).",
            file=sys.stderr,
        )

        with ProcessPoolExecutor(
            max_workers=workers,
            initializer=_pool_worker_init,
            initargs=(worker_threads,),
        ) as executor:
            # .map keeps results in input order even though the underlying
            # work runs concurrently across worker processes/CPUs.
            for text, result in zip(unique_texts, executor.map(_worker_call_llm, unique_texts)):
                _record(text, result)

    # Fan the (much smaller) set of cached results back out to every
    # pending row, including duplicates -- pure dict lookups, no LLM calls,
    # so this pass is essentially instant even for tens of thousands of
    # rows. Still checkpoints periodically at row granularity so --out
    # keeps updating the way it did before.
    completed = 0
    for row, program_text in zip(pending, program_texts):
        result = results_by_text[program_text]
        _clean_row(row, result)
        processed.append(row)
        completed += 1

        if completed % _CHECKPOINT_EVERY == 0 or completed == total_pending:
            _checkpoint()

    print(
        f"[progress] {len(processed):,}/{len(rows):,} entries processed",
        file=sys.stderr,
    )


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(
        description="Standardize program/university with a tiny local LLM.",
    )
    parser.add_argument(
        "--file",
        help="Path to JSON input (list of rows or {'rows': [...]})",
        default=None,
    )
    parser.add_argument(
        "--serve",
        action="store_true",
        help="Run the HTTP server instead of CLI.",
    )
    parser.add_argument(
        "--out",
        default=None,
        help="Output path for a single JSON array. "
        "Defaults to <input>.out.json when --file is set.",
    )
    parser.add_argument(
        "--append",
        action="store_true",
        help="Resume into an existing --out JSON array, skipping entries "
        "already present (matched by url) instead of reprocessing them.",
    )
    parser.add_argument(
        "--stdout",
        action="store_true",
        help="Write the JSON array to stdout instead of a file.",
    )
    parser.add_argument(
        "--workers",
        type=int,
        default=NUM_WORKERS,
        help=f"Worker processes to fan the CLI run across (default: {NUM_WORKERS}, "
        "i.e. NUM_WORKERS env var). Use --workers 1 to run single-process "
        "like before.",
    )
    parser.add_argument(
        "--worker-threads",
        type=int,
        default=WORKER_N_THREADS,
        help="llama.cpp threads per worker process (default: "
        f"{WORKER_N_THREADS}, i.e. WORKER_N_THREADS env var). Workers x "
        "worker-threads should roughly match your CPU core count.",
    )
    args = parser.parse_args()

    if args.serve or args.file is None:
        port = int(os.getenv("PORT", "8000"))
        app.run(host="0.0.0.0", port=port, debug=False)
    else:
        _cli_process_file(
            in_path=args.file,
            out_path=args.out,
            append=bool(args.append),
            to_stdout=bool(args.stdout),
            workers=args.workers,
            worker_threads=args.worker_threads,
        )
