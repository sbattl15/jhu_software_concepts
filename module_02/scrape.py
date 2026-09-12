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


# Configuration

BASE_URL = "https://www.thegradcafe.com"
SURVEY_PATH = "/survey"
ROBOTS_URL = urllib.parse.urljoin(BASE_URL, "/robots.txt")
USER_AGENT = (
    "JHU-SoftwareConcepts-Module2-Scraper/1.0 "
    "(+mailto:sbattl15@jh.edu; educational coursework)"
)

REQUEST_TIMEOUT_SECONDS = 15
DEFAULT_DELAY_SECONDS = 3.0

_STOP_STATUS_CODES = {403, 429, 503}

_RESULT_ID_RE = re.compile(r"/result/(\d+)")
_DECISION_RE = re.compile(
    r"(Accepted|Rejected|Wait ?listed|Interview)\s+on\s+"
    r"([A-Za-z]{3}\s+\d{1,2})",
    re.IGNORECASE,
)

class ScrapeStoppedError(RuntimeError):
    """Raised when scraping must stop because of blocking/rate limiting."""


# robots.txt check

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
        raise RuntimeError(
            f"Could not retrieve robots.txt: {exc}"
        ) from exc

    if not can_fetch(survey_url, parser, user_agent):
        raise PermissionError(
            f"robots.txt at {ROBOTS_URL} disallows fetching "
            f"{survey_url} for this user agent. Refusing to scrape."
        )

    print(f"[robots.txt] Permission CONFIRMED for {survey_url}.")

    delay = parser.crawl_delay(user_agent)
    if delay:
        print(
            f"[robots.txt] Site requests a Crawl-delay of {delay}s; "
            "this will be honored automatically."
        )

    return parser


# ---------------------------------------------------------------------------
# HTTP fetch
# ---------------------------------------------------------------------------

# Downloads the HTML data from a single page
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
        with urllib.request.urlopen(request, timeout=timeout) as response:
            charset = response.headers.get_content_charset() or "utf-8"
            return response.read().decode(charset, errors="replace")

    except urllib.error.HTTPError as exc:
        if exc.code in _STOP_STATUS_CODES:
            raise ScrapeStoppedError(
                f"Server returned HTTP {exc.code} for {url}; "
                "stopping per polite-scraping requirements."
            ) from exc

        raise


def _looks_blocked(html: str) -> bool:
    """Best-effort detection of CAPTCHA/blocking pages."""
    lowered = html.lower()

    block_markers = (
        "captcha",
        "access denied",
        "are you a robot",
        "unusual traffic",
    )

    return any(marker in lowered for marker in block_markers)


def _fetch_html_selenium(
    url: str,
    wait_seconds: int = 10,
) -> Optional[str]:
    """Optional Selenium fallback for JavaScript-rendered pages."""
    try:
        from selenium import webdriver
        from selenium.webdriver.common.by import By
        from selenium.webdriver.support.ui import WebDriverWait
        from selenium.webdriver.support import expected_conditions as EC
    except ImportError:
        print("[selenium] not installed; skipping Selenium fallback.")
        return None

    options = webdriver.ChromeOptions()
    options.add_argument("--headless=new")
    options.add_argument(f"user-agent={USER_AGENT}")

    driver = webdriver.Chrome(options=options)

    try:
        driver.get(url)

        WebDriverWait(driver, wait_seconds).until(
            EC.presence_of_element_located(
                (By.CSS_SELECTOR, "a[href*='/result/']")
            )
        )

        return driver.page_source

    except Exception as exc:
        print(f"[selenium] rendering failed for {url}: {exc}")
        return None

    finally:
        driver.quit()


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------

def _parse_page(html: str) -> list[dict]:
    """Extract raw applicant entries from one survey page."""
    soup = BeautifulSoup(html, "html.parser")
    entries: list[dict] = []

    for link in soup.find_all("a", href=_RESULT_ID_RE):
        main_row = link.find_parent("tr")

        if main_row is None:
            continue

        cells = main_row.find_all("td")

        def cell_text(index: int) -> str:
            if len(cells) > index:
                return cells[index].get_text(" ", strip=True)
            return ""

        id_match = _RESULT_ID_RE.search(link.get("href", ""))
        result_id = id_match.group(1) if id_match else ""

        meta_line = ""
        comment_parts: list[str] = []

        sibling = main_row.find_next_sibling("tr")
        hops = 0

        while sibling is not None and hops < 2:
            # A link usually indicates that we have reached another
            # entry or navigation.
            if sibling.find("a"):
                break

            text = sibling.get_text(" ", strip=True)

            if text:
                if _DECISION_RE.search(text) or "GPA" in text or "GRE" in text:
                    meta_line = text
                else:
                    comment_parts.append(text)

            sibling = sibling.find_next_sibling("tr")
            hops += 1

        entries.append(
            {
                "raw_school_text": cell_text(0),
                "raw_program_text": cell_text(1),
                "raw_added_on_text": cell_text(2),
                "raw_decision_text": cell_text(3),
                "raw_meta_text": meta_line,
                "raw_comment_text": " ".join(comment_parts).strip(),
                "entry_id": result_id,
                "url": (
                    urllib.parse.urljoin(
                        BASE_URL,
                        f"/result/{result_id}",
                    )
                    if result_id
                    else ""
                ),
            }
        )

    return entries


def _find_next_page_url(
    html: str,
    current_url: str,
) -> Optional[str]:
    """Find the site's Next pagination link."""

    soup = BeautifulSoup(html, "html.parser")

    # First look for a link whose complete visible text is "Next".
    for link in soup.find_all("a", href=True):
        text = link.get_text(" ", strip=True)

        if re.fullmatch(r"Next", text, re.IGNORECASE):
            href = link["href"]

            if href:
                return urllib.parse.urljoin(current_url, href)

    return None


# ---------------------------------------------------------------------------
# Data cleaning
# ---------------------------------------------------------------------------

def _clean_text(value: object) -> str:
    """Normalize whitespace without changing the meaning of the text."""
    if value is None:
        return ""

    return re.sub(r"\s+", " ", str(value)).strip()


def clean_data(raw_entries: list[dict]) -> list[dict]:
    """Clean scraped fields while preserving the original information."""
    cleaned: list[dict] = []

    for entry in raw_entries:
        cleaned_entry = {
            "school": _clean_text(entry.get("raw_school_text")),
            "program": _clean_text(entry.get("raw_program_text")),
            "added_on": _clean_text(entry.get("raw_added_on_text")),
            "decision": _clean_text(entry.get("raw_decision_text")),
            "meta": _clean_text(entry.get("raw_meta_text")),
            "comment": _clean_text(entry.get("raw_comment_text")),
            "entry_id": _clean_text(entry.get("entry_id")),
            "url": _clean_text(entry.get("url")),
        }

        cleaned.append(cleaned_entry)

    return cleaned


# ---------------------------------------------------------------------------
# JSON export
# ---------------------------------------------------------------------------

def save_data(
    entries: list[dict],
    out_path: str,
) -> None:
    """Write entries to a UTF-8 JSON file."""
    try:
        with open(out_path, "w", encoding="utf-8") as file:
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


# ---------------------------------------------------------------------------
# Scraper
# ---------------------------------------------------------------------------

def scrape_data(
    target_count = 30, # temp set to 30 instead of 40k
    max_pages: Optional[int] = None,
    delay_seconds: float = DEFAULT_DELAY_SECONDS,
    use_selenium_fallback: bool = False,
) -> list[dict]:

    robots_parser = confirm_scraping_permitted()

    start_url = urllib.parse.urljoin(
        BASE_URL,
        SURVEY_PATH,
    )


    all_entries: list[dict] = []
    url: Optional[str] = start_url
    page_num = 0

    while url and len(all_entries) < target_count:

        if max_pages is not None and page_num >= max_pages:
            print(
                f"[scrape] reached max_pages={max_pages}; stopping."
            )
            break

        if not can_fetch(url, robots_parser, USER_AGENT):
            print(
                f"[robots.txt] {url} is disallowed. "
                "Stopping pagination."
            )
            break

        page_num += 1

        print(f"[scrape] page {page_num}: {url}")

        try:
            html = _fetch_html(url)

        except ScrapeStoppedError as exc:
            print(f"[scrape] {exc}")
            break

        except urllib.error.URLError as exc:
            print(
                f"[scrape] network error on {url}: {exc}; "
                "stopping."
            )
            break

        if _looks_blocked(html):
            print(
                "[scrape] response looks like a block/CAPTCHA page; "
                "stopping."
            )
            break

        page_entries = _parse_page(html)

        if not page_entries and use_selenium_fallback:
            print(
                "[scrape] no rows found statically; "
                "trying Selenium fallback."
            )

            rendered_html = _fetch_html_selenium(url)

            if rendered_html:
                if _looks_blocked(rendered_html):
                    print(
                        "[scrape] Selenium response looks blocked; "
                        "stopping."
                    )
                    break

                page_entries = _parse_page(rendered_html)

        print(
            f"[scrape] found {len(page_entries)} entries on this page "
            f"(running total: {len(all_entries) + len(page_entries)})"
        )

        if not page_entries:
            print(
                "[scrape] no entries found; assuming end of results."
            )
            break

        all_entries.extend(page_entries)

        next_url = _find_next_page_url(
            html,
            url,
        )

        if not next_url:
            print("[scrape] no further pages found.")
            break

        url = next_url

        if url and len(all_entries) < target_count:
            time.sleep(delay_seconds)

    return all_entries[:target_count]


# ---------------------------------------------------------------------------
# Pipeline
# ---------------------------------------------------------------------------

def run(
    target_count: int = 30, # temp set to 30 instead of 50k
    max_pages: Optional[int] = None,
    delay_seconds: float = DEFAULT_DELAY_SECONDS,
    use_selenium_fallback: bool = False,
    out_path: str = "applicant_data.json",
    raw: bool = False,
) -> list[dict]:
    """Run the full scrape -> clean -> export pipeline."""


    raw_entries = scrape_data(
        target_count=target_count,
        max_pages=max_pages,
        delay_seconds=delay_seconds,
        use_selenium_fallback=use_selenium_fallback,
    )

    if not raw_entries:
        print(
            "No entries were scraped. Nothing was written."
        )
        return []

    entries = (
        [dict(entry) for entry in raw_entries]
        if raw
        else clean_data(raw_entries)
    )

    save_data(entries, out_path)

    if len(entries) < target_count:
        print(
            f"Warning: collected {len(entries)} entries, short of the "
            f"requested {target_count}."
        )

    return entries


# ---------------------------------------------------------------------------
# Command-line interface
# ---------------------------------------------------------------------------

def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Scrape publicly accessible GradCafe survey data "
            "while respecting robots.txt and crawl delays."
        )
    )

    parser.add_argument(
        "--target-count",
        type=int,
        default=30, # temp set to 30 instead of 50k
        help="Number of entries to collect (default: 30000).",
    )

    parser.add_argument(
        "--max-pages",
        type=int,
        default=None,
        help="Maximum number of pages to fetch.",
    )

    parser.add_argument(
        "--delay",
        type=float,
        default=DEFAULT_DELAY_SECONDS,
        help=(
            "Delay in seconds between requests "
            f"(default: {DEFAULT_DELAY_SECONDS})."
        ),
    )

    parser.add_argument(
        "--selenium",
        action="store_true",
        help="Use Selenium if static HTML contains no results.",
    )

    parser.add_argument(
        "--output",
        default="applicant_data.json",
        help="Output JSON filename.",
    )

    parser.add_argument(
        "--raw",
        action="store_true",
        help="Export raw scraped fields instead of cleaned fields.",
    )

    args = parser.parse_args()

    try:
        run(
            target_count=args.target_count,
            max_pages=args.max_pages,
            delay_seconds=args.delay,
            use_selenium_fallback=args.selenium,
            out_path=args.output,
            raw=args.raw,
        )

    except PermissionError as exc:
        print(f"[error] {exc}", file=sys.stderr)
        return 1

    except RuntimeError as exc:
        print(f"[error] {exc}", file=sys.stderr)
        return 1

    except KeyboardInterrupt:
        print("\n[error] Interrupted by user.", file=sys.stderr)
        return 130

    return 0


if __name__ == "__main__":
    sys.exit(main())
