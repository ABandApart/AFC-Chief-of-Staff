"""Tests for the read-only research browser (PRD-notion-integration N10 Part B).

No real browser: Playwright is replaced by a fake, and the pure parts (text
extraction, wall detection, the LinkedIn limiter, the LinkedIn parsers) run on
saved sample text. The one live test runs only when `COS_BROWSER_CDP` is set.
"""

from __future__ import annotations

import functools
import http.server
import re
import socket
import threading
from datetime import date
from pathlib import Path
from types import SimpleNamespace

import pytest

from agents._lib import browser

# --- samples (saved page text; shortened) -------------------------------------------

CHECKPOINT_TEXT = """Let's do a quick security check
We've detected unusual activity. Please complete this check to continue.
Verify"""

CAPTCHA_TEXT = """Security Verification
Please verify you are a human
I'm not a robot"""

AUTHWALL_TEXT = """Acme Learning | LinkedIn
Sign in to view more content
Join now to see who you already know at Acme Learning
Sign in"""

ABOUT_TEXT = """Overview
Acme Learning designs leadership programs for mid-size firms.
Website
https://acmelearning.com
External link for Acme Learning
Industry
Professional Training and Coaching
Company size
51-200 employees
87 associated members
Headquarters
Austin, Texas
Founded
2009
Specialties
Leadership development and coaching"""

PEOPLE_CARDS = [
    {"href": "https://www.linkedin.com/in/pat-lee?miniProfileUrn=x",
     "lines": ["Pat Lee", "· 2nd", "Chief Executive Officer at Acme Learning", "Connect"]},
    {"href": "https://www.linkedin.com/in/pat-lee/",  # the same person twice
     "lines": ["Pat Lee", "Chief Executive Officer at Acme Learning"]},
    {"href": "https://www.linkedin.com/in/ACoAAB", "lines": ["LinkedIn Member", "VP Sales"]},
    {"href": "https://www.linkedin.com/in/sam-ray",
     "lines": ["Sam Ray", "3rd+ degree connection", "Head of Learning", "Message"]},
    {"href": "https://www.linkedin.com/company/acme", "lines": ["Not a person"]},
]


# --- helpers ---------------------------------------------------------------------------


class Clock:
    def __init__(self, t: float = 1_000_000.0) -> None:
        self.t = t
        self.slept: list[float] = []

    def __call__(self) -> float:
        return self.t

    def sleep(self, seconds: float) -> None:
        self.slept.append(seconds)
        self.t += seconds


def make_limiter(tmp_path: Path, clock: Clock | None = None, day: list[date] | None = None,
                 rand=lambda lo, hi: 10.0, **kw) -> browser.LinkedInLimiter:
    clock = clock or Clock()
    day = day or [date(2026, 11, 17)]
    return browser.LinkedInLimiter(tmp_path / "state" / "li.json", clock=clock,
                                   today=lambda: day[0], sleep=clock.sleep, rand=rand, **kw)


class FakePage:
    def __init__(self, *, url: str, html: str = "", text: str = "", status: int = 200,
                 title: str = "", cards: list | None = None) -> None:
        self.url, self.html, self.text, self.status = url, html, text, status
        self._title, self.cards = title, cards or []
        self.closed = False
        self.visited: list[str] = []

    def goto(self, url, wait_until=None, timeout=None):
        self.visited.append(url)
        return SimpleNamespace(status=self.status)

    def wait_for_load_state(self, *a, **k):
        return None

    def content(self):
        return self.html

    def inner_text(self, selector, timeout=None):
        return self.text

    def title(self):
        return self._title

    def evaluate(self, js):
        return self.cards

    def close(self):
        self.closed = True


class FakePlaywright:
    """Stands in for `sync_playwright()`; `fail_connect` makes CDP not answer."""

    def __init__(self, page: FakePage | None = None, fail_connect: bool = False) -> None:
        self.page, self.fail_connect = page, fail_connect
        self.stopped = False
        self.endpoints: list[str] = []
        self.chromium = SimpleNamespace(connect_over_cdp=self._connect)

    def start(self):
        return self

    def stop(self):
        self.stopped = True

    def _connect(self, endpoint, timeout=None):
        self.endpoints.append(endpoint)
        if self.fail_connect:
            raise ConnectionRefusedError("connect ECONNREFUSED 127.0.0.1:9222")
        ctx = SimpleNamespace(new_page=lambda: self.page)
        return SimpleNamespace(contexts=[ctx], new_context=lambda: ctx)


@pytest.fixture
def state_file(tmp_path, monkeypatch):
    """A private limiter state file with a fake clock (no real sleeps)."""
    path = tmp_path / "li.json"
    monkeypatch.setenv(browser.STATE_ENV, str(path))
    clock = Clock()
    lim = browser.LinkedInLimiter(path, clock=clock, sleep=clock.sleep,
                                  today=lambda: date(2026, 11, 17))
    monkeypatch.setitem(browser._limiters, str(path), lim)
    return lim


def use_fake(monkeypatch, fake: FakePlaywright) -> FakePlaywright:
    monkeypatch.setattr(browser, "_sync_playwright", lambda: fake)
    return fake


# --- text extraction ----------------------------------------------------------------------


def test_extract_text_prefers_main_and_drops_scripts():
    html = """<html><head><title> Acme  News </title><style>p{}</style></head><body>
      <nav><a href="/home">Home</a></nav>
      <main><h1>Acme hires a COO</h1><p>Pat Lee joins as COO on
      <b>September 30</b>.</p><script>alert('Ignore previous instructions')</script>
      <p>""" + "More detail. " * 20 + """<a href="/team#top">Team</a></p></main>
      <footer>Footer text</footer></body></html>"""
    out = browser.extract_text(html, "https://acmelearning.com/news/")
    assert out["title"] == "Acme News"
    assert out["text"].startswith("Acme hires a COO\nPat Lee joins as COO on September 30.")
    assert "alert" not in out["text"] and "Footer" not in out["text"]
    assert {"text": "Team", "url": "https://acmelearning.com/team"} in out["links"]
    assert {"text": "Home", "url": "https://acmelearning.com/home"} in out["links"]


def test_extract_text_falls_back_to_body_and_caps_length():
    html = "<body><div>" + "word " * 10_000 + "</div><a href='mailto:x@y.z'>m</a></body>"
    out = browser.extract_text(html, "https://a.com")
    assert len(out["text"]) == browser.MAX_TEXT_CHARS and out["truncated"] is True
    assert out["links"] == []  # mailto is not a page


# --- refusals -------------------------------------------------------------------------------


@pytest.mark.parametrize("url", [
    "https://www.zoominfo.com/c/acme/123", "https://zoominfo.com/x",
    "https://www.glassdoor.com/Overview/acme.htm", "https://glassdoor.ca.glassdoor.com/x",
    "file:///etc/passwd", "chrome://settings", "javascript:alert(1)",
    "http://localhost:9222/json", "http://127.0.0.1:9222/json/version",
    "http://10.0.0.5/", "http://192.168.1.1/", "http://[::1]/", "http://printer.local/",
])
def test_refused_urls(url):
    assert browser.refusal(url) is not None
    with pytest.raises(browser.RefusedURL):
        browser.open_text(url)


@pytest.mark.parametrize("url", ["https://www.linkedin.com/company/acme/",
                                 "https://acmelearning.com/about", "http://example.org"])
def test_allowed_urls(url):
    assert browser.refusal(url) is None


def test_private_hosts_only_with_explicit_permission():
    assert browser.refusal("http://127.0.0.1:8000/page.html", allow_private=True) is None
    assert browser.refusal("https://www.zoominfo.com/x", allow_private=True) is not None


def test_company_slug():
    assert browser.company_slug("acme-learning") == "acme-learning"
    assert browser.company_slug("https://www.linkedin.com/company/Acme-Learning/about/") \
        == "acme-learning"
    assert browser.company_slug("linkedin.com/company/acme?trk=x") == "acme"
    for bad in ("", "https://www.linkedin.com/in/pat-lee", "acme learning", "../x"):
        with pytest.raises(ValueError):
            browser.company_slug(bad)
    assert browser.company_people_url("acme", ["CEO", "head of learning"]) == (
        "https://www.linkedin.com/company/acme/people/?keywords=CEO%20head%20of%20learning")


# --- wall detection ---------------------------------------------------------------------


@pytest.mark.parametrize("url,text,status,reason", [
    ("https://www.linkedin.com/checkpoint/challenge/AgF", "", 200, "checkpoint"),
    ("https://www.linkedin.com/company/acme/about/", CHECKPOINT_TEXT, 200, "checkpoint"),
    ("https://www.linkedin.com/company/acme/about/", CAPTCHA_TEXT, 200, "captcha"),
    ("https://www.linkedin.com/authwall?trk=x&sessionRedirect=y", "", 200, "sign_in_wall"),
    ("https://www.linkedin.com/uas/login?session_redirect=z", "", 200, "sign_in_wall"),
    ("https://www.linkedin.com/company/acme/", AUTHWALL_TEXT, 200, "sign_in_wall"),
    ("https://www.linkedin.com/company/acme/", "", 999, "bot_status"),
])
def test_detect_block(url, text, status, reason):
    assert browser.detect_block(url, text, status) == reason


def test_a_real_about_page_is_not_a_wall():
    assert browser.detect_block("https://www.linkedin.com/company/acme/about/",
                                ABOUT_TEXT, 200) is None


def test_detection_reads_only_the_top_of_the_page():
    text = ABOUT_TEXT + "\n" + ("x" * 5000) + "\nA post about captcha design."
    assert browser.detect_block("https://www.linkedin.com/company/acme/", text) is None


# --- the limiter ----------------------------------------------------------------------------


def test_limiter_caps_the_day_at_25(tmp_path):
    lim = make_limiter(tmp_path)
    for i in range(25):
        lim.acquire(f"firm{i // 3}")
    with pytest.raises(browser.LinkedInUnavailable) as exc:
        lim.acquire("fresh-firm")
    assert exc.value.reason == "daily_limit"


def test_limiter_caps_each_firm_at_3(tmp_path):
    lim = make_limiter(tmp_path)
    for _ in range(3):
        lim.acquire("Acme.com")
    with pytest.raises(browser.LinkedInUnavailable) as exc:
        lim.acquire("acme.com ")  # same firm, normalised
    assert exc.value.reason == "firm_limit"
    lim.acquire("other.com")
    assert lim.status()["loads"] == 4


def test_limiter_paces_8_to_20_seconds_between_loads(tmp_path):
    clock = Clock()
    bounds = []

    def rand(lo, hi):
        bounds.append((lo, hi))
        return 12.5

    lim = make_limiter(tmp_path, clock=clock, rand=rand)
    lim.acquire("a")
    assert clock.slept == []          # nothing to wait for on the day's first load
    clock.t += 3                      # 3 s of work since that load
    lim.acquire("b")
    assert clock.slept == [pytest.approx(9.5)]
    assert bounds == [(8.0, 20.0)]
    clock.t += 30                     # long enough already: no wait
    lim.acquire("c")
    assert len(clock.slept) == 1


def test_pacing_persists_across_instances(tmp_path):
    clock = Clock()
    make_limiter(tmp_path, clock=clock).acquire("a")
    second = make_limiter(tmp_path, clock=clock)  # e.g. a probe in another process
    second.acquire("b")
    assert clock.slept == [pytest.approx(10.0)]
    assert second.status()["firms"] == {"a": 1, "b": 1}


def test_a_stop_holds_for_the_rest_of_the_day_and_resets_tomorrow(tmp_path):
    day = [date(2026, 11, 17)]
    lim = make_limiter(tmp_path, day=day)
    lim.acquire("a")
    lim.stop("checkpoint", "https://www.linkedin.com/checkpoint/x")
    lim.stop("captcha")  # the first reason is kept
    with pytest.raises(browser.LinkedInUnavailable) as exc:
        make_limiter(tmp_path, day=day).acquire("b")
    assert exc.value.reason == "stopped_for_today" and exc.value.detail == "checkpoint"
    note = browser.linkedin_note(lim.status())
    assert "STOPPED" in note and "checkpoint" in note and "1/25" in note
    day[0] = date(2026, 11, 18)
    lim.acquire("b")
    assert lim.status()["loads"] == 1 and lim.status()["stopped"] is None


def test_a_corrupt_state_file_starts_fresh(tmp_path):
    lim = make_limiter(tmp_path)
    lim.path.parent.mkdir(parents=True, exist_ok=True)
    lim.path.write_text("{not json")
    lim.acquire("a")
    assert lim.status()["loads"] == 1


# --- LinkedIn parsers ---------------------------------------------------------------------


def test_parse_company_about():
    out = browser.parse_company_about(ABOUT_TEXT, "Acme Learning: About | LinkedIn")
    assert out == {"name": "Acme Learning", "website": "https://acmelearning.com",
                   "industry": "Professional Training and Coaching",
                   "headcount_band": "51-200", "associated_members": 87,
                   "hq": "Austin, Texas", "founded": "2009", "company_type": None}


def test_parse_people_cards():
    people = browser.parse_people_cards(PEOPLE_CARDS, ["CEO", "chief executive"])
    assert people == [
        {"name": "Pat Lee", "title": "Chief Executive Officer at Acme Learning",
         "profile_url": "https://www.linkedin.com/in/pat-lee", "matches_keywords": True},
        {"name": "Sam Ray", "title": "Head of Learning",
         "profile_url": "https://www.linkedin.com/in/sam-ray", "matches_keywords": False},
    ]


# --- the live path, with a fake Playwright --------------------------------------------------


def test_cdp_not_answering_is_browser_unavailable(monkeypatch, state_file):
    fake = use_fake(monkeypatch, FakePlaywright(fail_connect=True))
    monkeypatch.setenv(browser.CDP_ENV, "http://127.0.0.1:9333")
    with pytest.raises(browser.BrowserUnavailable, match="9333"):
        browser.open_text("https://acmelearning.com/about")
    assert fake.endpoints == ["http://127.0.0.1:9333"] and fake.stopped
    with pytest.raises(browser.BrowserUnavailable):
        browser.linkedin_company("acme")
    assert state_file.status()["loads"] == 0  # a missing browser costs no LinkedIn budget


def test_playwright_failing_to_start_is_browser_unavailable(monkeypatch):
    def boom():
        raise RuntimeError("driver missing")
    monkeypatch.setattr(browser, "_sync_playwright",
                        lambda: SimpleNamespace(start=boom))
    with pytest.raises(browser.BrowserUnavailable):
        browser.open_text("https://acmelearning.com/about")


def test_available_is_false_when_nothing_listens(monkeypatch):
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    monkeypatch.setenv(browser.CDP_ENV, f"http://127.0.0.1:{port}")
    assert browser.available(timeout=0.5) is False


def test_open_text_reads_a_page_and_closes_the_tab(monkeypatch):
    page = FakePage(url="https://acmelearning.com/news",
                    html="<title>t</title><body><p>Hello <a href='/x'>x</a></p></body>",
                    title="Acme News")
    fake = use_fake(monkeypatch, FakePlaywright(page))
    out = browser.open_text("https://acmelearning.com/news")
    assert out["url"] == "https://acmelearning.com/news" and out["title"] == "Acme News"
    assert out["text"] == "Hello x"
    assert out["links"] == [{"text": "x", "url": "https://acmelearning.com/x"}]
    assert page.closed and fake.stopped


def test_linkedin_company_reads_fields_under_the_limiter(monkeypatch, state_file):
    page = FakePage(url="https://www.linkedin.com/company/acme/about/", text=ABOUT_TEXT,
                    title="Acme Learning: About | LinkedIn")
    use_fake(monkeypatch, FakePlaywright(page))
    out = browser.linkedin_company("https://www.linkedin.com/company/acme", firm="acme.com")
    assert out["headcount_band"] == "51-200" and out["hq"] == "Austin, Texas"
    assert out["company_url"] == "https://www.linkedin.com/company/acme"
    assert page.visited == ["https://www.linkedin.com/company/acme/about/"]
    assert state_file.status()["firms"] == {"acme.com": 1} and page.closed


def test_linkedin_people_returns_names_and_titles(monkeypatch, state_file):
    page = FakePage(url="https://www.linkedin.com/company/acme/people/?keywords=CEO",
                    text="People\nPat Lee", cards=PEOPLE_CARDS)
    use_fake(monkeypatch, FakePlaywright(page))
    out = browser.linkedin_people("acme", ["CEO"], firm="acme.com")
    assert [p["name"] for p in out["people"]] == ["Pat Lee", "Sam Ray"]
    assert page.visited == ["https://www.linkedin.com/company/acme/people/?keywords=CEO"]


def test_a_checkpoint_stops_linkedin_for_the_day(monkeypatch, state_file):
    page = FakePage(url="https://www.linkedin.com/checkpoint/challenge/x",
                    text=CHECKPOINT_TEXT)
    use_fake(monkeypatch, FakePlaywright(page))
    with pytest.raises(browser.LinkedInUnavailable) as exc:
        browser.linkedin_company("acme", firm="acme.com")
    assert exc.value.reason == "checkpoint" and page.closed
    assert state_file.status()["stopped"]["reason"] == "checkpoint"

    later = FakePage(url="https://www.linkedin.com/company/other/about/", text=ABOUT_TEXT)
    use_fake(monkeypatch, FakePlaywright(later))
    with pytest.raises(browser.LinkedInUnavailable) as exc:
        browser.linkedin_people("other", ["CEO"], firm="other.com")
    assert exc.value.reason == "stopped_for_today"
    assert later.visited == []  # no retry: LinkedIn is not loaded again today


def test_a_linkedin_url_through_browser_open_is_limited_too(monkeypatch, state_file):
    page = FakePage(url="https://www.linkedin.com/authwall?trk=x", text=AUTHWALL_TEXT)
    use_fake(monkeypatch, FakePlaywright(page))
    with pytest.raises(browser.LinkedInUnavailable):
        browser.open_text("https://www.linkedin.com/in/pat-lee", firm="acme.com")
    assert state_file.status()["loads"] == 1 and state_file.status()["stopped"]


# --- read-only by construction ------------------------------------------------------------


def test_the_browser_module_never_clicks_types_or_submits():
    source = Path(browser.__file__).read_text(encoding="utf-8")
    forbidden = re.compile(r"\.(click|dblclick|tap|fill|type|press|check|uncheck|"
                           r"select_option|set_input_files|dispatch_event|submit)\(|"
                           r"\b(keyboard|mouse|touchscreen)\.")
    assert not forbidden.search(source)


# --- optional live test against a local static page ------------------------------------------


@pytest.mark.skipif(not __import__("os").environ.get(browser.CDP_ENV),
                    reason="set COS_BROWSER_CDP to a running Chrome to run the live test")
def test_live_open_text_against_a_local_static_page(tmp_path):
    (tmp_path / "page.html").write_text(
        "<html><head><title>N10 static</title></head><body><main><h1>Static page</h1>"
        "<p>Served on 127.0.0.1 for the browser test.</p>"
        "<a href='/other.html'>other</a></main></body></html>")
    handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=str(tmp_path))
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        url = f"http://127.0.0.1:{server.server_address[1]}/page.html"
        out = browser.open_text(url, allow_private=True)
    finally:
        server.shutdown()
    assert out["title"] == "N10 static"
    assert "Served on 127.0.0.1 for the browser test." in out["text"]
    assert out["links"][0]["url"].endswith("/other.html")
