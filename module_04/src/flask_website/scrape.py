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

# Total number of entries desired, teh scraper pulls 20 entries
# Per page. The function runs until it hits the desired number
# Of pages.
desired_entries = 40000
MAX_PAGES = desired_entries // 20

REQUEST_TIMEOUT_SECONDS = 15
DEFAULT_DELAY_SECONDS = 3.0

MAX_SIBLING_ROW_HOPS = 3

_STOP_STATUS_CODES = {403, 429, 503}

_RESULT_ID_RE = re.compile(r"/result/(\d+)", re.IGNORECASE)
_NEXT_LINK_RE = re.compile(r"^next$", re.IGNORECASE)


class ScrapeStoppedError(RuntimeError):
    """Raised when scraping must stop because of blocking/rate limiting."""


# ============================================================================
# Small helpers
# ============================================================================


def _clean_text(value: object) -> str:
    """Collapse whitespace/newlines and strip the result."""
    if not value:
        return ""
    return re.sub(r"\s+", " ", str(value)).strip()


# ============================================================================
# Robots.txt
# ============================================================================


def check_robots_txt(
    user_agent: str = USER_AGENT,
) -> urllib.robotparser.RobotFileParser:
    parser = urllib.robotparser.RobotFileParser()
    parser.set_url(ROBOTS_URL)

    # RobotFileParser.read() fetches robots.txt with urllib's bare default
    # User-Agent ("Python-urllib/3.x"), which some sites' bot protection
    # blocks with a 403 -- and a blocked robots.txt fetch makes can_fetch()
    # return False for *every* URL afterward, even when robots.txt itself
    # would have allowed scraping. Fetch it manually with the same
    # User-Agent the rest of the scraper uses, and feed the content to the
    # parser directly instead.
    request = urllib.request.Request(
        ROBOTS_URL,
        headers={
            "User-Agent": user_agent,
            "Accept-Language": "en-US,en;q=0.9",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=REQUEST_TIMEOUT_SECONDS) as response:
            charset = response.headers.get_content_charset() or "utf-8"
            content = response.read().decode(charset, errors="replace")
        parser.parse(content.splitlines())
    except urllib.error.HTTPError as exc:
        if exc.code in (401, 403):
            parser.disallow_all = True
        elif 400 <= exc.code < 500:
            parser.allow_all = True
        else:
            raise

    return parser


def can_fetch(
    url: str,
    parser: urllib.robotparser.RobotFileParser,
    user_agent: str = USER_AGENT,
) -> bool:
    return parser.can_fetch(user_agent, url)


def confirm_scraping_permitted(
    user_agent: str = USER_AGENT,
) -> urllib.robotparser.RobotFileParser:
    survey_url = urllib.parse.urljoin(BASE_URL, SURVEY_PATH)

    print(
        f"[robots.txt] Checking {ROBOTS_URL} for permission to fetch "
        f"{survey_url} as '{user_agent}' ..."
    )

    try:
        parser = check_robots_txt(user_agent)
    except urllib.error.URLError as exc:
        raise RuntimeError(f"Could not retrieve robots.txt: {exc}") from exc

    if not can_fetch(survey_url, parser, user_agent):
        raise PermissionError(
            f"robots.txt at {ROBOTS_URL} disallows fetching "
            f"{survey_url} for this user agent. Refusing to scrape."
        )

    print(f"[robots.txt] Permission CONFIRMED for {survey_url}.")

    crawl_delay = parser.crawl_delay(user_agent)
    if crawl_delay:
        print(f"[robots.txt] Site requests a Crawl-delay of {crawl_delay}s.")

    return parser


# ============================================================================
# HTTP
# ============================================================================


def _fetch_html(url: str, timeout: int = REQUEST_TIMEOUT_SECONDS) -> str:
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": USER_AGENT,
            "Accept-Language": "en-US,en;q=0.9",
        },
    )

    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            charset = response.headers.get_content_charset() or "utf-8"
            return response.read().decode(charset, errors="replace")
    except urllib.error.HTTPError as exc:
        if exc.code in _STOP_STATUS_CODES:
            raise ScrapeStoppedError(
                f"Server returned HTTP {exc.code} for {url}; stopping."
            ) from exc
        raise


def _looks_blocked(html: str) -> bool:
    lowered = html.lower()
    block_markers = (
        "captcha",
        "access denied",
        "are you a robot",
        "unusual traffic",
    )
    return any(marker in lowered for marker in block_markers)


# ============================================================================
# Page parsing
# ============================================================================


def _row_cell_text(cells: list, index: int) -> str:
    if len(cells) > index:
        return _clean_text(cells[index].get_text(" ", strip=True))
    return ""


def _collect_additional_row_text(main_row) -> str:
    """Grab trailing metadata/comment rows that follow a result row."""
    additional_text: list[str] = []
    sibling = main_row.find_next_sibling("tr")
    hops = 0

    while sibling is not None and hops < MAX_SIBLING_ROW_HOPS:
        if sibling.find("a", href=_RESULT_ID_RE):
            break

        text = _clean_text(sibling.get_text(" ", strip=True))
        if text:
            additional_text.append(text)

        sibling = sibling.find_next_sibling("tr")
        hops += 1

    return " ".join(additional_text)


def _parse_page(html: str) -> list[dict]:
    soup = BeautifulSoup(html, "html.parser")
    entries: list[dict] = []

    for link in soup.find_all("a", href=_RESULT_ID_RE):
        main_row = link.find_parent("tr")
        if main_row is None:
            continue

        cells = main_row.find_all("td")

        href = link.get("href", "")
        id_match = _RESULT_ID_RE.search(href)
        result_id = id_match.group(1) if id_match else ""

        school_text = _row_cell_text(cells, 0)
        program_text = _row_cell_text(cells, 1)
        added_on_text = _row_cell_text(cells, 2)
        decision_text = _row_cell_text(cells, 3)
        combined_meta = _collect_additional_row_text(main_row)

        result_url = (
            urllib.parse.urljoin(BASE_URL, f"/result/{result_id}") if result_id else ""
        )

        entries.append(
            {
                "entry_id": result_id,
                "raw_school_text": school_text,
                "raw_program_text": program_text,
                "raw_added_on_text": added_on_text,
                "raw_decision_text": decision_text,
                "raw_meta_text": combined_meta,
                "raw_comment_text": combined_meta,
                "url": result_url,
            }
        )

    return entries


def _find_next_page_url(html: str, current_url: str) -> Optional[str]:
    """
    Find the URL of the next page of results.

    GradCafe paginates with an opaque ?cursor=... token rather than a
    plain page number -- the only way to know page N+1's URL is to read
    it out of the "Next" link in page N's HTML. Returns None once there
    is no "Next" link (i.e. this is the last page).
    """
    soup = BeautifulSoup(html, "html.parser")

    for link in soup.find_all("a", href=True):
        text = _clean_text(link.get_text(" ", strip=True))
        if _NEXT_LINK_RE.fullmatch(text):
            return urllib.parse.urljoin(current_url, link["href"])

    return None


# ============================================================================
# Fetching a single page
# ============================================================================


def _fetch_and_parse_page(
    url: str,
    delay_seconds: float,
    robots_parser: urllib.robotparser.RobotFileParser,
) -> tuple[list[dict], Optional[str]]:
    if not can_fetch(url, robots_parser, USER_AGENT):
        raise PermissionError(f"robots.txt disallows {url}")

    html = _fetch_html(url)

    if _looks_blocked(html):
        raise ScrapeStoppedError(f"{url} appears to be a CAPTCHA/block page.")

    entries = _parse_page(html)
    next_url = _find_next_page_url(html, url)

    print(f"[fetch] {url}: {len(entries)} entries")

    if delay_seconds > 0:
        time.sleep(delay_seconds)

    return entries, next_url


# ============================================================================
# SCRAPER
# ============================================================================


def scrape_data(
    max_pages: Optional[int] = None,
    delay_seconds: float = DEFAULT_DELAY_SECONDS,
) -> list[dict]:
    """
    Scrape survey pages, following GradCafe's "Next" link from one page
    to the next.

    GradCafe paginates with an opaque cursor token, not a page number --
    page N+1's URL is only known after page N has been fetched and
    parsed, so pages are fetched strictly one at a time, in order (they
    cannot be fetched concurrently or out of sequence).

    Stops once `max_pages` pages have been fetched, or once a page has
    no "Next" link (there are no more results). MAX_PAGES is a hard
    limit: no more than MAX_PAGES pages are ever requested.
    """
    if max_pages is not None and max_pages < 1:
        raise ValueError("max_pages must be at least 1")
    if delay_seconds < 0:
        raise ValueError("delay_seconds cannot be negative")

    # Hard stop: never fetch more than MAX_PAGES pages, regardless of what
    # max_pages was requested.
    if max_pages is None:
        max_pages = MAX_PAGES
    elif max_pages > MAX_PAGES:
        print(
            f"[scrape] Requested max_pages of {max_pages:,} exceeds the "
            f"hard limit of {MAX_PAGES:,}; capping to {MAX_PAGES:,}."
        )
        max_pages = MAX_PAGES

    print(f"[scrape] Target: {max_pages:,} pages")

    robots_parser = confirm_scraping_permitted()

    all_entries: list[dict] = []
    seen_entry_ids: set[str] = set()

    pages_fetched = 0
    current_url: Optional[str] = urllib.parse.urljoin(BASE_URL, SURVEY_PATH)

    # ========================================================================
    # MAIN SCRAPING LOOP -- strictly sequential; see the docstring above.
    # ========================================================================
    while pages_fetched < max_pages and current_url is not None:
        try:
            page_entries, next_url = _fetch_and_parse_page(
                current_url, delay_seconds, robots_parser
            )
            pages_fetched += 1
        except ScrapeStoppedError as exc:
            print(f"[scrape] STOP: {exc}")
            break
        except PermissionError as exc:
            print(f"[robots.txt] {exc}")
            break
        except urllib.error.URLError as exc:
            print(f"[scrape] network error fetching {current_url}: {exc}")
            break
        except Exception as exc:
            print(f"[scrape] fetching {current_url} failed: {exc}")
            break

        # ====================================================================
        # ADD ENTRIES
        # ====================================================================
        for entry in page_entries:
            entry_id = _clean_text(entry.get("entry_id"))

            # Deduplicate by GradCafe result ID.
            if entry_id:
                if entry_id in seen_entry_ids:
                    continue
                seen_entry_ids.add(entry_id)

            all_entries.append(entry)

        current_url = next_url

    print()
    print("=" * 70)
    if pages_fetched >= max_pages:
        if max_pages >= MAX_PAGES:
            print(f"[scrape] MAX_PAGES HARD CAP REACHED: {pages_fetched:,} pages")
        else:
            print(f"[scrape] PAGE LIMIT REACHED: {pages_fetched:,} pages")
    elif current_url is None:
        print(f"[scrape] No further pages after {pages_fetched:,} page(s).")
    print("=" * 70)

    print(
        f"[scrape] Finished after fetching {pages_fetched:,} page(s); "
        f"collected {len(all_entries):,} unique entries."
    )

    return all_entries


# ============================================================================
# Persistence
# ============================================================================


def _save_json(entries: list[dict], out_path: str) -> None:
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(entries, f, indent=2, ensure_ascii=False)


# ============================================================================
# Pipeline
# ============================================================================


def run(
    max_pages: Optional[int] = None,
    delay_seconds: float = DEFAULT_DELAY_SECONDS,
    out_path: str = "applicant_data.json",
) -> list[dict]:
    raw_entries = scrape_data(
        max_pages=max_pages,
        delay_seconds=delay_seconds,
    )

    if not raw_entries:
        print("No entries were scraped. Nothing was written.")
        return []

    entries = [dict(entry) for entry in raw_entries]

    _save_json(entries, out_path)
    print(f"[scrape] Wrote {len(entries):,} entries to {out_path}")

    return entries


# ============================================================================
# CLI
# ============================================================================


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Scrape GradCafe survey data, following its Next-page links."
    )

    parser.add_argument(
        "--max-pages",
        type=int,
        default=MAX_PAGES,
        help=f"Number of pages to fetch (hard maximum: {MAX_PAGES:,}).",
    )
    parser.add_argument(
        "--delay",
        type=float,
        default=DEFAULT_DELAY_SECONDS,
        help=("Delay after each request " f"(default: {DEFAULT_DELAY_SECONDS})."),
    )
    parser.add_argument(
        "--output",
        default="applicant_data.json",
        help="Output JSON filename.",
    )

    args = parser.parse_args()

    try:
        run(
            max_pages=args.max_pages,
            delay_seconds=args.delay,
            out_path=args.output,
        )
    except (PermissionError, RuntimeError) as exc:
        print(f"[error] {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("\n[error] Interrupted by user.", file=sys.stderr)
        return 130

    return 0


if __name__ == "__main__":
    sys.exit(main())
