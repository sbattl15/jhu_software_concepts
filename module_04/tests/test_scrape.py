"""Unit tests for scrape.py (the Grad Cafe scraper behind "Pull Data").
All HTTP is faked by patching urllib.request.urlopen / the page fetcher,
so no request ever leaves the machine and no delay is ever slept."""

from __future__ import annotations

import json
import runpy
import urllib.error
import urllib.request
import urllib.robotparser

import pytest

import scrape

pytestmark = pytest.mark.buttons


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------


class _Headers:
    def __init__(self, charset):
        self._charset = charset

    def get_content_charset(self):
        return self._charset


class _Response:
    def __init__(self, body: str, charset="utf-8"):
        self._body = body.encode(charset or "utf-8")
        self.headers = _Headers(charset)

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def _http_error(code):
    return urllib.error.HTTPError("https://example.invalid", code, "err", None, None)


def _urlopen_returning(body, charset="utf-8"):
    def fake(request, timeout=None):
        return _Response(body, charset)

    return fake


def _urlopen_raising(exc):
    def fake(request, timeout=None):
        raise exc

    return fake


def _allow_all_parser():
    parser = urllib.robotparser.RobotFileParser()
    parser.parse(["User-agent: *", "Allow: /"])
    return parser


def _deny_all_parser():
    parser = urllib.robotparser.RobotFileParser()
    parser.parse(["User-agent: *", "Disallow: /"])
    return parser


SURVEY_HTML = """
<html><body>
<a href="/result/999">orphan link outside any table row</a>
<table>
  <tr>
    <td>MIT</td><td>Computer Science PhD</td><td>Feb 1, 2026</td>
    <td>Accepted on 1 Feb</td><td><a href="/result/1">See more</a></td>
  </tr>
  <tr><td>Fall 2026 International GPA 3.90</td></tr>
  <tr><td>   </td></tr>
  <tr><td>Great program</td></tr>
  <tr><td>beyond the sibling-hop limit</td></tr>
  <tr>
    <td>Stanford</td><td>EE Masters</td><td>Feb 2, 2026</td>
    <td>Rejected on 2 Feb</td><td><a href="/result/2">See more</a></td>
  </tr>
  <tr><td>Fall 2025</td></tr>
  <tr><td><a href="/result/3">only a link</a></td></tr>
</table>
<a href="/survey?page=1">Previous</a>
<a href="/survey?cursor=abc">Next</a>
</body></html>
"""


# ---------------------------------------------------------------------------
# Helpers + robots.txt
# ---------------------------------------------------------------------------


class TestCleanText:
    @pytest.mark.parametrize(
        "value,expected", [(None, ""), ("", ""), ("  a \n\t b  ", "a b"), (42, "42")]
    )
    def test_clean_text(self, value, expected):
        assert scrape._clean_text(value) == expected


class TestRobots:
    @pytest.mark.parametrize("charset", ["utf-8", None])
    def test_parses_fetched_robots_txt(self, monkeypatch, charset):
        monkeypatch.setattr(
            urllib.request,
            "urlopen",
            _urlopen_returning("User-agent: *\nDisallow: /private\nCrawl-delay: 2\n", charset),
        )
        parser = scrape.check_robots_txt()
        assert scrape.can_fetch("https://www.thegradcafe.com/survey", parser)
        assert not scrape.can_fetch("https://www.thegradcafe.com/private/x", parser)

    @pytest.mark.parametrize("code,allowed", [(401, False), (403, False), (404, True)])
    def test_http_errors_map_to_allow_or_disallow_all(self, monkeypatch, code, allowed):
        monkeypatch.setattr(urllib.request, "urlopen", _urlopen_raising(_http_error(code)))
        parser = scrape.check_robots_txt()
        assert scrape.can_fetch("https://www.thegradcafe.com/survey", parser) is allowed

    def test_server_error_is_reraised(self, monkeypatch):
        monkeypatch.setattr(urllib.request, "urlopen", _urlopen_raising(_http_error(500)))
        with pytest.raises(urllib.error.HTTPError):
            scrape.check_robots_txt()

    def test_confirm_wraps_network_errors(self, monkeypatch):
        monkeypatch.setattr(
            urllib.request, "urlopen", _urlopen_raising(urllib.error.URLError("dns"))
        )
        with pytest.raises(RuntimeError, match="Could not retrieve robots.txt"):
            scrape.confirm_scraping_permitted()

    def test_confirm_refuses_when_disallowed(self, monkeypatch):
        monkeypatch.setattr(scrape, "check_robots_txt", lambda ua: _deny_all_parser())
        with pytest.raises(PermissionError, match="Refusing to scrape"):
            scrape.confirm_scraping_permitted()

    def test_confirm_reports_crawl_delay(self, monkeypatch, capsys):
        monkeypatch.setattr(
            urllib.request, "urlopen", _urlopen_returning("User-agent: *\nCrawl-delay: 5\n")
        )
        parser = scrape.confirm_scraping_permitted()
        assert isinstance(parser, urllib.robotparser.RobotFileParser)
        assert "Crawl-delay of 5s" in capsys.readouterr().out

    def test_confirm_without_crawl_delay(self, monkeypatch, capsys):
        monkeypatch.setattr(scrape, "check_robots_txt", lambda ua: _allow_all_parser())
        scrape.confirm_scraping_permitted()
        out = capsys.readouterr().out
        assert "Permission CONFIRMED" in out
        assert "Crawl-delay" not in out


# ---------------------------------------------------------------------------
# HTTP
# ---------------------------------------------------------------------------


class TestFetchHtml:
    @pytest.mark.parametrize("charset", ["utf-8", None])
    def test_returns_decoded_body(self, monkeypatch, charset):
        monkeypatch.setattr(urllib.request, "urlopen", _urlopen_returning("<p>hi</p>", charset))
        assert scrape._fetch_html("https://x.invalid") == "<p>hi</p>"

    @pytest.mark.parametrize("code", sorted(scrape._STOP_STATUS_CODES))
    def test_blocking_status_codes_stop_the_scrape(self, monkeypatch, code):
        monkeypatch.setattr(urllib.request, "urlopen", _urlopen_raising(_http_error(code)))
        with pytest.raises(scrape.ScrapeStoppedError):
            scrape._fetch_html("https://x.invalid")

    def test_other_http_errors_are_reraised(self, monkeypatch):
        monkeypatch.setattr(urllib.request, "urlopen", _urlopen_raising(_http_error(404)))
        with pytest.raises(urllib.error.HTTPError):
            scrape._fetch_html("https://x.invalid")

    @pytest.mark.parametrize(
        "html,blocked",
        [("<p>Please complete the CAPTCHA</p>", True), ("<p>results</p>", False)],
    )
    def test_looks_blocked(self, html, blocked):
        assert scrape._looks_blocked(html) is blocked


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------


class TestParsing:
    def test_parse_page_extracts_rows_and_trailing_metadata(self):
        entries = scrape._parse_page(SURVEY_HTML)
        assert [e["entry_id"] for e in entries] == ["1", "2", "3"]

        first = entries[0]
        assert first["raw_school_text"] == "MIT"
        assert first["raw_program_text"] == "Computer Science PhD"
        assert first["raw_added_on_text"] == "Feb 1, 2026"
        assert first["raw_decision_text"] == "Accepted on 1 Feb"
        # blank row skipped; stops after MAX_SIBLING_ROW_HOPS rows
        assert first["raw_meta_text"] == "Fall 2026 International GPA 3.90 Great program"
        assert first["raw_comment_text"] == first["raw_meta_text"]
        assert first["url"] == "https://www.thegradcafe.com/result/1"

        # stops at the next result row
        assert entries[1]["raw_meta_text"] == "Fall 2025"
        # a one-cell row: missing cells come back as ""
        assert entries[2]["raw_program_text"] == ""
        assert entries[2]["raw_meta_text"] == ""

    def test_find_next_page_url(self):
        assert (
            scrape._find_next_page_url(SURVEY_HTML, "https://www.thegradcafe.com/survey")
            == "https://www.thegradcafe.com/survey?cursor=abc"
        )

    def test_no_next_link_means_last_page(self):
        assert scrape._find_next_page_url("<a href='/x'>Previous</a>", "https://a.b/") is None


class TestFetchAndParsePage:
    def test_disallowed_url_raises(self):
        with pytest.raises(PermissionError):
            scrape._fetch_and_parse_page("https://www.thegradcafe.com/survey", 0, _deny_all_parser())

    def test_block_page_raises(self, monkeypatch):
        monkeypatch.setattr(scrape, "_fetch_html", lambda url: "Are you a robot?")
        with pytest.raises(scrape.ScrapeStoppedError):
            scrape._fetch_and_parse_page("https://www.thegradcafe.com/survey", 0, _allow_all_parser())

    @pytest.mark.parametrize("delay,expected_sleeps", [(0, []), (1.5, [1.5])])
    def test_returns_entries_and_next_url_and_honors_delay(
        self, monkeypatch, delay, expected_sleeps
    ):
        sleeps = []
        monkeypatch.setattr(scrape, "_fetch_html", lambda url: SURVEY_HTML)
        monkeypatch.setattr(scrape.time, "sleep", sleeps.append)
        entries, next_url = scrape._fetch_and_parse_page(
            "https://www.thegradcafe.com/survey", delay, _allow_all_parser()
        )
        assert len(entries) == 3
        assert next_url.endswith("cursor=abc")
        assert sleeps == expected_sleeps


# ---------------------------------------------------------------------------
# scrape_data
# ---------------------------------------------------------------------------


def _fake_pages(monkeypatch, pages):
    """`pages` is a list of (entries, next_url) tuples or exceptions, served
    in order to successive _fetch_and_parse_page calls."""
    queue = list(pages)
    fetched = []

    def fake_fetch(url, delay, parser):
        fetched.append(url)
        item = queue.pop(0)
        if isinstance(item, Exception):
            raise item
        return item

    monkeypatch.setattr(scrape, "confirm_scraping_permitted", lambda: _allow_all_parser())
    monkeypatch.setattr(scrape, "_fetch_and_parse_page", fake_fetch)
    return fetched


class TestScrapeData:
    @pytest.mark.parametrize("kwargs", [{"max_pages": 0}, {"delay_seconds": -1}])
    def test_rejects_bad_arguments(self, kwargs):
        with pytest.raises(ValueError):
            scrape.scrape_data(**kwargs)

    def test_follows_next_links_and_dedupes_by_entry_id(self, monkeypatch, capsys):
        _fake_pages(
            monkeypatch,
            [
                ([{"entry_id": "1"}, {"entry_id": "2"}, {"entry_id": ""}], "page2"),
                ([{"entry_id": "2"}, {"entry_id": "3"}], None),
            ],
        )
        entries = scrape.scrape_data(max_pages=5, delay_seconds=0)
        assert [e["entry_id"] for e in entries] == ["1", "2", "", "3"]
        assert "No further pages after 2 page(s)" in capsys.readouterr().out

    def test_page_limit_reached(self, monkeypatch, capsys):
        _fake_pages(monkeypatch, [([{"entry_id": "1"}], "page2")])
        assert len(scrape.scrape_data(max_pages=1, delay_seconds=0)) == 1
        assert "PAGE LIMIT REACHED: 1 pages" in capsys.readouterr().out

    def test_default_max_pages_is_the_hard_cap(self, monkeypatch, capsys):
        monkeypatch.setattr(scrape, "MAX_PAGES", 2)
        fetched = _fake_pages(monkeypatch, [([], "p2"), ([], "p3")])
        scrape.scrape_data(delay_seconds=0)
        assert len(fetched) == 2
        assert "MAX_PAGES HARD CAP REACHED: 2 pages" in capsys.readouterr().out

    def test_requests_above_the_hard_cap_are_capped(self, monkeypatch, capsys):
        monkeypatch.setattr(scrape, "MAX_PAGES", 1)
        fetched = _fake_pages(monkeypatch, [([], "p2")])
        scrape.scrape_data(max_pages=10, delay_seconds=0)
        assert len(fetched) == 1
        assert "capping to 1" in capsys.readouterr().out

    @pytest.mark.parametrize(
        "exc,message",
        [
            (scrape.ScrapeStoppedError("blocked"), "[scrape] STOP: blocked"),
            (PermissionError("nope"), "[robots.txt] nope"),
            (urllib.error.URLError("dns"), "network error fetching"),
            (ValueError("weird"), "failed: weird"),
        ],
    )
    def test_fetch_errors_stop_the_loop_but_keep_earlier_pages(
        self, monkeypatch, capsys, exc, message
    ):
        _fake_pages(monkeypatch, [([{"entry_id": "1"}], "page2"), exc])
        entries = scrape.scrape_data(max_pages=5, delay_seconds=0)
        assert [e["entry_id"] for e in entries] == ["1"]
        out = capsys.readouterr().out
        assert message in out
        assert "Finished after fetching 1 page(s)" in out


# ---------------------------------------------------------------------------
# run / main
# ---------------------------------------------------------------------------


class TestRunAndMain:
    def test_run_writes_json(self, monkeypatch, tmp_path):
        monkeypatch.setattr(scrape, "scrape_data", lambda **kw: [{"entry_id": "1"}])
        out = tmp_path / "out.json"
        assert scrape.run(out_path=str(out)) == [{"entry_id": "1"}]
        assert json.loads(out.read_text(encoding="utf-8")) == [{"entry_id": "1"}]

    def test_run_with_nothing_scraped_writes_nothing(self, monkeypatch, tmp_path, capsys):
        monkeypatch.setattr(scrape, "scrape_data", lambda **kw: [])
        out = tmp_path / "out.json"
        assert scrape.run(out_path=str(out)) == []
        assert not out.exists()
        assert "Nothing was written" in capsys.readouterr().out

    def test_main_passes_cli_args_to_run(self, monkeypatch):
        seen = {}
        monkeypatch.setattr(scrape, "run", lambda **kw: seen.update(kw))
        monkeypatch.setattr(
            "sys.argv", ["scrape.py", "--max-pages", "3", "--delay", "0", "--output", "o.json"]
        )
        assert scrape.main() == 0
        assert seen == {"max_pages": 3, "delay_seconds": 0.0, "out_path": "o.json"}

    @pytest.mark.parametrize(
        "exc,code", [(PermissionError("x"), 1), (RuntimeError("x"), 1), (KeyboardInterrupt(), 130)]
    )
    def test_main_error_exit_codes(self, monkeypatch, exc, code):
        def boom(**kw):
            raise exc

        monkeypatch.setattr(scrape, "run", boom)
        monkeypatch.setattr("sys.argv", ["scrape.py"])
        assert scrape.main() == code

    def test_module_main_block(self, monkeypatch):
        monkeypatch.setattr(
            urllib.request, "urlopen", _urlopen_raising(urllib.error.URLError("offline"))
        )
        monkeypatch.setattr("sys.argv", ["scrape.py", "--max-pages", "1"])
        with pytest.raises(SystemExit) as exc_info:
            runpy.run_path(scrape.__file__, run_name="__main__")
        assert exc_info.value.code == 1
