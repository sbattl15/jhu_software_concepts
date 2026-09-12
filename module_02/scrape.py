from __future__ import annotations

import argparse
import json
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import urllib.robotparser
from concurrent.futures import (
    ThreadPoolExecutor,
    as_completed,
)
from typing import Optional

from bs4 import BeautifulSoup


# ============================================================================
# Configuration
# ============================================================================

BASE_URL = "https://www.thegradcafe.com"
SURVEY_PATH = "/survey"
ROBOTS_URL = urllib.parse.urljoin(BASE_URL, "/robots.txt")

USER_AGENT = (
    "JHU-SoftwareConcepts-Module2-Scraper/1.0 "
    "(+mailto:sbattl15@jh.edu; educational coursework)"
)

# Exactly 8 pages at a time by default
DEFAULT_WORKERS = 8

# Maximum entries to pull from website
MAX_ENTRIES = 200

REQUEST_TIMEOUT_SECONDS = 15
DEFAULT_DELAY_SECONDS = 3.0

_STOP_STATUS_CODES = {403, 429, 503}

_RESULT_ID_RE = re.compile(
    r"/result/(\d+)",
    re.IGNORECASE,
)


class ScrapeStoppedError(RuntimeError):
    """Raised when scraping must stop because of blocking/rate limiting."""


# ============================================================================
# Robots.txt
# ============================================================================

def check_robots_txt(
    user_agent: str = USER_AGENT,
) -> urllib.robotparser.RobotFileParser:

    parser = urllib.robotparser.RobotFileParser()
    parser.set_url(ROBOTS_URL)
    parser.read()

    return parser


def can_fetch(
    url: str,
    parser: urllib.robotparser.RobotFileParser,
    user_agent: str = USER_AGENT,
) -> bool:

    return parser.can_fetch(
        user_agent,
        url,
    )


def confirm_scraping_permitted(
    user_agent: str = USER_AGENT,
) -> urllib.robotparser.RobotFileParser:

    survey_url = urllib.parse.urljoin(
        BASE_URL,
        SURVEY_PATH,
    )

    print(
        f"[robots.txt] Checking {ROBOTS_URL} for permission to fetch "
        f"{survey_url} as '{user_agent}' ..."
    )

    try:
        parser = check_robots_txt(
            user_agent
        )

    except urllib.error.URLError as exc:
        raise RuntimeError(
            f"Could not retrieve robots.txt: {exc}"
        ) from exc

    if not can_fetch(
        survey_url,
        parser,
        user_agent,
    ):
        raise PermissionError(
            f"robots.txt at {ROBOTS_URL} disallows fetching "
            f"{survey_url} for this user agent. Refusing to scrape."
        )

    print(
        f"[robots.txt] Permission CONFIRMED for {survey_url}."
    )

    crawl_delay = parser.crawl_delay(
        user_agent
    )

    if crawl_delay:
        print(
            f"[robots.txt] Site requests a Crawl-delay of "
            f"{crawl_delay}s."
        )

    return parser


# ============================================================================
# HTTP
# ============================================================================

def _fetch_html(
    url: str,
    timeout: int = REQUEST_TIMEOUT_SECONDS,
) -> str:

    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": USER_AGENT,
            "Accept-Language": "en-US,en;q=0.9",
        },
    )

    try:

        with urllib.request.urlopen(
            request,
            timeout=timeout,
        ) as response:

            charset = (
                response.headers.get_content_charset()
                or "utf-8"
            )

            return response.read().decode(
                charset,
                errors="replace",
            )

    except urllib.error.HTTPError as exc:

        if exc.code in _STOP_STATUS_CODES:

            raise ScrapeStoppedError(
                f"Server returned HTTP {exc.code} for {url}; "
                "stopping."
            ) from exc

        raise


def _looks_blocked(
    html: str,
) -> bool:

    lowered = html.lower()

    block_markers = (
        "captcha",
        "access denied",
        "are you a robot",
        "unusual traffic",
    )

    return any(
        marker in lowered
        for marker in block_markers
    )


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
# Page parsing
# ============================================================================

def _parse_page(
    html: str,
) -> list[dict]:

    soup = BeautifulSoup(
        html,
        "html.parser",
    )

    entries: list[dict] = []

    for link in soup.find_all(
        "a",
        href=_RESULT_ID_RE,
    ):

        main_row = link.find_parent("tr")

        if main_row is None:
            continue

        cells = main_row.find_all("td")

        def cell_text(
            index: int,
        ) -> str:

            if len(cells) > index:

                return _clean_text(
                    cells[index].get_text(
                        " ",
                        strip=True,
                    )
                )

            return ""

        href = link.get(
            "href",
            "",
        )

        id_match = _RESULT_ID_RE.search(
            href
        )

        result_id = (
            id_match.group(1)
            if id_match
            else ""
        )

        school_text = cell_text(0)
        program_text = cell_text(1)
        added_on_text = cell_text(2)
        decision_text = cell_text(3)

        additional_text: list[str] = []

        sibling = main_row.find_next_sibling(
            "tr"
        )

        hops = 0

        while (
            sibling is not None
            and hops < 3
        ):

            if sibling.find(
                "a",
                href=_RESULT_ID_RE,
            ):
                break

            text = _clean_text(
                sibling.get_text(
                    " ",
                    strip=True,
                )
            )

            if text:
                additional_text.append(text)

            sibling = sibling.find_next_sibling(
                "tr"
            )

            hops += 1

        combined_meta = " ".join(
            additional_text
        )

        result_url = ""

        if result_id:

            result_url = urllib.parse.urljoin(
                BASE_URL,
                f"/result/{result_id}",
            )

        entries.append(
            {
                "raw_school_text": school_text,
                "raw_program_text": program_text,
                "raw_added_on_text": added_on_text,
                "raw_decision_text": decision_text,
                "raw_meta_text": combined_meta,
                "raw_comment_text": combined_meta,
                "entry_id": result_id,
                "url": result_url,
            }
        )

    return entries


# ============================================================================
# Pagination
# ============================================================================

def _find_next_page_url(
    html: str,
    current_url: str,
) -> Optional[str]:

    soup = BeautifulSoup(
        html,
        "html.parser",
    )

    for link in soup.find_all(
        "a",
        href=True,
    ):

        text = _clean_text(
            link.get_text(
                " ",
                strip=True,
            )
        )

        if re.fullmatch(
            r"Next",
            text,
            re.IGNORECASE,
        ):

            return urllib.parse.urljoin(
                current_url,
                link["href"],
            )

    return None


def _get_page_number(
    url: str,
) -> int:

    parsed = urllib.parse.urlparse(
        url
    )

    params = urllib.parse.parse_qs(
        parsed.query
    )

    try:
        return int(
            params.get(
                "p",
                ["1"],
            )[0]
        )

    except ValueError:
        return 1


def _make_page_url(
    page_number: int,
) -> str:
    """
    Build a survey URL for a specific page.

    GradCafe's survey pagination uses the p parameter.
    Page 1 remains /survey/.
    """

    if page_number <= 1:
        return urllib.parse.urljoin(
            BASE_URL,
            SURVEY_PATH,
        )

    return urllib.parse.urljoin(
        BASE_URL,
        f"{SURVEY_PATH}?p={page_number}",
    )


# ============================================================================
# Thread worker
# ============================================================================

def _fetch_and_parse_page(
    page_number: int,
    delay_seconds: float,
    robots_parser: urllib.robotparser.RobotFileParser,
) -> tuple[int, str, list[dict]]:

    url = _make_page_url(
        page_number
    )

    if not can_fetch(
        url,
        robots_parser,
        USER_AGENT,
    ):
        raise PermissionError(
            f"robots.txt disallows {url}"
        )

    print(
        f"[thread] page {page_number}: "
        f"fetching {url}"
    )

    html = _fetch_html(
        url
    )

    if _looks_blocked(html):

        raise ScrapeStoppedError(
            f"Page {page_number} appears to be "
            "a CAPTCHA/block page."
        )

    entries = _parse_page(
        html
    )

    print(
        f"[thread] page {page_number}: "
        f"{len(entries)} entries"
    )

    # IMPORTANT:
    #
    # The delay belongs to each worker, so 8 workers can operate
    # concurrently rather than making the entire batch wait 8 * delay.
    #
    if delay_seconds > 0:
        time.sleep(
            delay_seconds
        )

    return (
        page_number,
        url,
        entries,
    )


# ============================================================================
# Clean data
# ============================================================================

def clean_data(
    raw_entries: list[dict],
) -> list[dict]:

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
# Output
# ============================================================================

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
# SCRAPER
# ============================================================================

def scrape_data(
    target_count: int = MAX_ENTRIES,
    max_pages: Optional[int] = None,
    delay_seconds: float = DEFAULT_DELAY_SECONDS,
    use_selenium_fallback: bool = False,
    workers: int = DEFAULT_WORKERS,
) -> list[dict]:
    """
    Scrape survey pages concurrently.

    Stops collecting once target_count entries are reached.

    MAX_ENTRIES is an absolute hard limit of 200.

    Up to `workers` requests may already be running when the target
    is reached. Those requests cannot always be cancelled because
    Python cannot safely terminate a running thread.

    However, once target_count is reached:
        - no new pages are submitted
        - pending futures are cancelled where possible
        - the returned list contains at most 200 entries
    """

    if workers < 1:
        raise ValueError("workers must be at least 1")

    if target_count < 1:
        raise ValueError("target_count must be at least 1")

    if max_pages is not None and max_pages < 1:
        raise ValueError("max_pages must be at least 1")

    if delay_seconds < 0:
        raise ValueError("delay_seconds cannot be negative")

    # Never allow more than 200 entries.
    target_count = min(
        target_count,
        MAX_ENTRIES,
    )

    print(
        f"[scrape] Target: {target_count:,} unique entries"
    )

    robots_parser = confirm_scraping_permitted()

    all_entries: list[dict] = []
    seen_entry_ids: set[str] = set()

    pages_fetched = 0
    next_page_number = 1

    executor = ThreadPoolExecutor(
        max_workers=workers,
        thread_name_prefix="gradcafe",
    )

    active_futures = {}

    try:

        # ================================================================
        # MAIN SCRAPING LOOP
        # ================================================================
        while len(all_entries) < target_count:

            # ------------------------------------------------------------
            # Submit pages until all workers are busy.
            #
            # IMPORTANT:
            # We check len(all_entries) BEFORE every submission.
            # This prevents submitting new work after reaching 200.
            # ------------------------------------------------------------
            while (
                len(active_futures) < workers
                and len(all_entries) < target_count
            ):

                # Respect --max-pages.
                if (
                    max_pages is not None
                    and next_page_number > max_pages
                ):
                    break

                page_number = next_page_number
                next_page_number += 1

                try:

                    future = executor.submit(
                        _fetch_and_parse_page,
                        page_number,
                        delay_seconds,
                        robots_parser,
                    )

                except RuntimeError as exc:

                    # This should not normally happen because the executor
                    # is only shut down in the finally block.
                    print(
                        f"[scrape] Could not submit page "
                        f"{page_number}: {exc}"
                    )

                    break

                active_futures[future] = page_number

                print(
                    f"[threads] submitted page {page_number} "
                    f"(active={len(active_futures)})"
                )

            # ------------------------------------------------------------
            # No requests are running.
            # ------------------------------------------------------------
            if not active_futures:

                print(
                    "[scrape] no active pages remaining; stopping."
                )

                break

            # ------------------------------------------------------------
            # Wait for one request to finish.
            # ------------------------------------------------------------
            completed_future = next(
                as_completed(active_futures)
            )

            page_number = active_futures.pop(
                completed_future
            )

            try:

                (
                    completed_page_number,
                    page_url,
                    page_entries,
                ) = completed_future.result()

                pages_fetched += 1

            except ScrapeStoppedError as exc:

                print(
                    f"[scrape] STOP: {exc}"
                )

                # Cancel requests that have not started yet.
                for future in active_futures:
                    future.cancel()

                break

            except PermissionError as exc:

                print(
                    f"[robots.txt] {exc}"
                )

                for future in active_futures:
                    future.cancel()

                break

            except urllib.error.URLError as exc:

                pages_fetched += 1

                print(
                    f"[scrape] network error on page "
                    f"{page_number}: {exc}"
                )

                continue

            except Exception as exc:

                pages_fetched += 1

                print(
                    f"[scrape] page {page_number} failed: {exc}"
                )

                continue

            # ============================================================
            # ADD ENTRIES
            # ============================================================

            before_count = len(all_entries)

            for entry in page_entries:

                # --------------------------------------------------------
                # HARD STOP.
                #
                # This guarantees that the list can never exceed 200.
                # --------------------------------------------------------
                if len(all_entries) >= target_count:
                    break

                entry_id = _clean_text(
                    entry.get("entry_id")
                )

                # --------------------------------------------------------
                # Deduplicate by GradCafe result ID.
                # --------------------------------------------------------
                if entry_id:

                    if entry_id in seen_entry_ids:
                        continue

                    seen_entry_ids.add(entry_id)

                # --------------------------------------------------------
                # Add entry.
                # --------------------------------------------------------
                all_entries.append(
                    entry
                )

            after_count = len(all_entries)

            new_entries = (
                after_count
                - before_count
            )

            print(
                f"[scrape] page {completed_page_number}: "
                f"{new_entries} new entries "
                f"(total={after_count:,}/{target_count:,})"
            )

            # ============================================================
            # TARGET REACHED
            # ============================================================

            if len(all_entries) >= target_count:

                # Absolute safety cap.
                all_entries = all_entries[
                    :MAX_ENTRIES
                ]

                all_entries = all_entries[
                    :target_count
                ]

                print()
                print("=" * 70)
                print(
                    f"[scrape] TARGET REACHED: "
                    f"{len(all_entries):,} entries"
                )
                print(
                    "[scrape] No additional pages will be submitted."
                )
                print("=" * 70)

                # --------------------------------------------------------
                # Cancel futures that haven't started.
                #
                # Running HTTP requests cannot reliably be cancelled,
                # but no new requests will be submitted.
                # --------------------------------------------------------
                cancelled = 0
                still_running = 0

                for future in active_futures:

                    if future.cancel():
                        cancelled += 1
                    else:
                        still_running += 1

                print(
                    f"[scrape] Cancelled {cancelled} "
                    f"pending request(s)."
                )

                if still_running:
                    print(
                        f"[scrape] {still_running} request(s) "
                        f"were already running."
                    )

                break

            # ============================================================
            # MAX PAGES
            # ============================================================

            if (
                max_pages is not None
                and next_page_number > max_pages
                and not active_futures
            ):

                print(
                    f"[scrape] reached max_pages={max_pages}."
                )

                break

    finally:

        # ================================================================
        # SHUTDOWN
        #
        # IMPORTANT:
        # This happens ONLY after the scraping loop has stopped.
        #
        # DO NOT call executor.shutdown() before executor.submit().
        # ================================================================

        for future in active_futures:
            future.cancel()

        executor.shutdown(
            wait=True,
            cancel_futures=True,
        )

    # ================================================================
    # FINAL HARD CAP
    # ================================================================

    all_entries = all_entries[
        :target_count
    ]

    all_entries = all_entries[
        :MAX_ENTRIES
    ]

    print(
        f"[scrape] Finished with "
        f"{len(all_entries):,} unique entries."
    )

    return all_entries


# ============================================================================
# Pipeline
# ============================================================================

def run(
    target_count: int = MAX_ENTRIES,
    max_pages: Optional[int] = None,
    delay_seconds: float = DEFAULT_DELAY_SECONDS,
    use_selenium_fallback: bool = False,
    workers: int = DEFAULT_WORKERS,
    out_path: str = "applicant_data.json",
    raw: bool = False,
) -> list[dict]:

    raw_entries = scrape_data(
        target_count=target_count,
        max_pages=max_pages,
        delay_seconds=delay_seconds,
        use_selenium_fallback=use_selenium_fallback,
        workers=workers,
    )

    if not raw_entries:

        print(
            "No entries were scraped. "
            "Nothing was written."
        )

        return []

    if raw:

        entries = [
            dict(entry)
            for entry in raw_entries
        ]

    else:

        entries = clean_data(
            raw_entries
        )

    save_data(
        entries,
        out_path,
    )

    if len(entries) < target_count:

        print(
            f"Warning: collected "
            f"{len(entries)} entries, "
            f"short of requested "
            f"{target_count}."
        )

    return entries


# ============================================================================
# CLI
# ============================================================================

def main() -> int:

    parser = argparse.ArgumentParser(
        description=(
            "Scrape GradCafe survey data "
            "using 8 concurrent page workers."
        )
    )

    parser.add_argument(
        "--workers",
        type=int,
        default=DEFAULT_WORKERS,
        help=(
            f"Number of pages fetched concurrently "
            f"(default: {DEFAULT_WORKERS})."
        ),
    )

    parser.add_argument(
        "--target-count",
        type=int,
        default=MAX_ENTRIES,
        help=(
            f"Number of entries to collect "
            f"(hard maximum: {MAX_ENTRIES:,})."
        ),
    )

    parser.add_argument(
        "--max-pages",
        type=int,
        default=None,
        help=(
            "Maximum number of pages to fetch."
        ),
    )

    parser.add_argument(
        "--delay",
        type=float,
        default=DEFAULT_DELAY_SECONDS,
        help=(
            "Delay inside each worker after a request "
            f"(default: {DEFAULT_DELAY_SECONDS})."
        ),
    )

    parser.add_argument(
        "--selenium",
        action="store_true",
        help=(
            "Enable Selenium fallback."
        ),
    )

    parser.add_argument(
        "--output",
        default="applicant_data.json",
        help="Output JSON filename.",
    )

    parser.add_argument(
        "--raw",
        action="store_true",
        help="Export raw fields.",
    )

    args = parser.parse_args()

    try:

        run(
            target_count=args.target_count,
            max_pages=args.max_pages,
            delay_seconds=args.delay,
            use_selenium_fallback=args.selenium,
            workers=args.workers,
            out_path=args.output,
            raw=args.raw,
        )

    except PermissionError as exc:

        print(
            f"[error] {exc}",
            file=sys.stderr,
        )

        return 1

    except RuntimeError as exc:

        print(
            f"[error] {exc}",
            file=sys.stderr,
        )

        return 1

    except KeyboardInterrupt:

        print(
            "\n[error] Interrupted by user.",
            file=sys.stderr,
        )

        return 130

    return 0


if __name__ == "__main__":
    sys.exit(main())