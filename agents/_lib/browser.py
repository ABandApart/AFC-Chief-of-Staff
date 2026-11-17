"""Read-only browser research through the operator's Chrome (PRD-notion-integration N10 Part B).

The research worker reads pages that Anthropic's server-side fetch cannot open:
pages behind a login (LinkedIn above all) and URLs that were not in an earlier
search result. Playwright attaches to the operator's dedicated "CoS research"
Chrome profile over CDP (`COS_BROWSER_CDP`, default `http://127.0.0.1:9222`),
opens one new tab per call, and closes it afterwards. It never launches a browser,
never signs in, and never handles a password.

**Read-only by construction.** This module only navigates (`goto`) and reads
(`content`, `inner_text`, `title`, and one read-only `evaluate`). It never
clicks, types, fills, presses, or submits; `tests/test_browser.py` greps for it.

**LinkedIn limits (N-D31).** At most 25 LinkedIn page loads a day and 3 per firm,
8-20 s apart. A sign-in wall, checkpoint, or CAPTCHA stops LinkedIn for the rest
of the day and the tool reports `linkedin_unavailable`; nothing retries around it
or solves anything. The day's counters live in a small JSON file
(`COS_BROWSER_STATE`, default `~/.local/state/aiadaptive-cos/linkedin-limiter.json`).

The pure parts - text extraction, block detection, the limiter, and the LinkedIn
text parsers - are testable without a browser. Page text is data, never
instructions; the worker's rules and the dossier checks still apply.
"""

from __future__ import annotations

import contextlib
import fcntl
import ipaddress
import json
import os
import random
import re
import threading
import time
import urllib.error
import urllib.request
from collections.abc import Callable, Iterator
from datetime import date, datetime
from html.parser import HTMLParser
from pathlib import Path
from typing import Any
from urllib.parse import quote, urljoin, urlsplit

CDP_ENV = "COS_BROWSER_CDP"
DEFAULT_CDP = "http://127.0.0.1:9222"
STATE_ENV = "COS_BROWSER_STATE"
DEFAULT_STATE = Path.home() / ".local" / "state" / "aiadaptive-cos" / "linkedin-limiter.json"

MAX_TEXT_CHARS = 20_000
MAX_LINKS = 100
MAX_PEOPLE = 15
NAV_TIMEOUT_MS = 30_000
CONNECT_TIMEOUT_MS = 5_000
# LinkedIn renders client-side; give it this long to settle after DOMContentLoaded.
SETTLE_TIMEOUT_MS = 8_000

LINKEDIN_DOMAIN = "linkedin.com"
# R14 minus LinkedIn: never opened by any tool, never cited.
REFUSED_DOMAINS = ("zoominfo.com", "glassdoor.com")

LINKEDIN_DAILY_LIMIT = 25
LINKEDIN_PER_FIRM = 3
LINKEDIN_PACE_SECONDS = (8.0, 20.0)
# LinkedIn answers suspected automation with this non-standard status.
LINKEDIN_BOT_STATUS = 999


class BrowserUnavailable(RuntimeError):  # noqa: N818 - the name the PRD uses
    """CDP did not answer (or Playwright is missing): treat as "no browser"."""


class LinkedInUnavailable(RuntimeError):  # noqa: N818 - matches `linkedin_unavailable`
    """LinkedIn is stopped for today, or a cap is reached. `reason` is the code."""

    def __init__(self, reason: str, detail: str = "") -> None:
        super().__init__(f"linkedin_unavailable: {reason}{f' ({detail})' if detail else ''}")
        self.reason = reason
        self.detail = detail


class RefusedURL(ValueError):  # noqa: N818
    """A URL this module will not open (blocked domain, non-web scheme, local host)."""


# --- URLs ------------------------------------------------------------------------


def host_of(url: str) -> str:
    return (urlsplit((url or "").strip()).hostname or "").lower().rstrip(".")


def _on_domain(host: str, domain: str) -> bool:
    return host == domain or host.endswith("." + domain)


def is_linkedin(url: str) -> bool:
    host = host_of(url)
    return _on_domain(host, LINKEDIN_DOMAIN) or host == "lnkd.in"


def refusal(url: str, *, allow_private: bool = False) -> str | None:
    """Why `url` must not be opened, or None (pure).

    Besides ZoomInfo and Glassdoor, this refuses anything that is not plain
    http(s) and any loopback or private host: a page could otherwise talk the
    worker into reading `chrome://`, `file://`, or the CDP endpoint itself.
    """
    parts = urlsplit((url or "").strip())
    if parts.scheme not in ("http", "https"):
        return "only http and https URLs can be opened"
    host = host_of(url)
    if not host:
        return "the URL has no host"
    if any(_on_domain(host, d) for d in REFUSED_DOMAINS):
        return f"{host} is a blocked source (ZoomInfo and Glassdoor are never used)"
    if not allow_private:
        if host == "localhost" or host.endswith((".localhost", ".local", ".internal")):
            return "local hosts are not opened"
        with contextlib.suppress(ValueError):
            ip = ipaddress.ip_address(host.strip("[]"))
            if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved:
                return "private and loopback addresses are not opened"
    return None


def company_slug(slug_or_url: str) -> str:
    """`acme-learning` from a slug or any linkedin.com/company/... URL (pure)."""
    text = (slug_or_url or "").strip()
    if "/" in text or "linkedin" in text:
        path = urlsplit(text if "://" in text else f"https://{text}").path
        match = re.search(r"/company/([^/?#]+)", path)
        text = match.group(1) if match else ""
    slug = text.strip().strip("/").lower()
    if not re.fullmatch(r"[a-z0-9][a-z0-9._%-]{0,150}", slug):
        raise ValueError(f"not a LinkedIn company slug or URL: {slug_or_url!r}")
    return slug


def company_about_url(slug: str) -> str:
    return f"https://www.linkedin.com/company/{slug}/about/"


def company_people_url(slug: str, title_keywords: list[str]) -> str:
    words = " ".join(k.strip() for k in title_keywords if k and k.strip())
    query = f"?keywords={quote(words)}" if words else ""
    return f"https://www.linkedin.com/company/{slug}/people/{query}"


# --- readable text from HTML (pure) ------------------------------------------------

_SKIP_TAGS = frozenset({"script", "style", "noscript", "template", "svg", "iframe",
                        "head", "canvas", "button", "select", "option"})
_BLOCK_TAGS = frozenset({"p", "div", "section", "article", "main", "header", "footer",
                         "aside", "nav", "li", "ul", "ol", "dl", "dt", "dd", "tr", "td",
                         "th", "table", "br", "hr", "h1", "h2", "h3", "h4", "h5", "h6",
                         "blockquote", "pre", "figure", "figcaption", "address"})
_VOID_TAGS = frozenset({"br", "hr", "img", "input", "meta", "link", "area", "base",
                        "col", "embed", "source", "track", "wbr"})


class _Extractor(HTMLParser):
    def __init__(self, base_url: str) -> None:
        super().__init__(convert_charrefs=True)
        self.base_url = base_url
        self.title = ""
        self._in_title = False
        self._skip = 0
        # Text inside <main>/<article> is kept separately: it is the readable part
        # when the page marks one, and the whole body otherwise.
        self._main_depth = 0
        self.body: list[str] = []
        self.main: list[str] = []
        self.links: list[dict[str, str]] = []
        self._link: dict[str, Any] | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "title":
            self._in_title = True
        if tag in _SKIP_TAGS and tag not in _VOID_TAGS:
            self._skip += 1
            return
        if self._skip:
            return
        if tag in ("main", "article"):
            self._main_depth += 1
        if tag in _BLOCK_TAGS:
            self._emit("\n")
        if tag == "a":
            href = dict(attrs).get("href") or ""
            self._link = {"href": href, "text": []}

    def handle_endtag(self, tag: str) -> None:
        if tag == "title":
            self._in_title = False
        if tag in _SKIP_TAGS and tag not in _VOID_TAGS:
            self._skip = max(0, self._skip - 1)
            return
        if self._skip:
            return
        if tag in _BLOCK_TAGS:
            self._emit("\n")
        if tag in ("main", "article"):
            self._main_depth = max(0, self._main_depth - 1)
        if tag == "a" and self._link is not None:
            self._add_link(self._link["href"], " ".join("".join(self._link["text"]).split()))
            self._link = None

    def handle_data(self, data: str) -> None:
        if self._in_title:
            self.title += data
            return
        if self._skip:
            return
        # Source line breaks are layout, not structure: only block tags break lines.
        data = re.sub(r"\s+", " ", data)
        self._emit(data)
        if self._link is not None:
            self._link["text"].append(data)

    def _emit(self, text: str) -> None:
        self.body.append(text)
        if self._main_depth:
            self.main.append(text)

    def _add_link(self, href: str, text: str) -> None:
        url = urljoin(self.base_url, href.strip()) if href else ""
        if urlsplit(url).scheme not in ("http", "https"):
            return
        url = url.split("#", 1)[0]
        if any(link["url"] == url for link in self.links):
            return
        self.links.append({"text": text[:200], "url": url})


def _tidy(chunks: list[str]) -> str:
    lines = (" ".join(line.split()) for line in "".join(chunks).splitlines())
    return "\n".join(line for line in lines if line)


def extract_text(html: str, base_url: str = "", *, max_chars: int = MAX_TEXT_CHARS,
                 max_links: int = MAX_LINKS) -> dict[str, Any]:
    """Title, readable main text (at most `max_chars`), and links from HTML (pure)."""
    parser = _Extractor(base_url)
    parser.feed(html or "")
    parser.close()
    main = _tidy(parser.main)
    text = main if len(main) >= 200 else _tidy(parser.body)
    return {"title": " ".join(parser.title.split()), "text": text[:max_chars],
            "truncated": len(text) > max_chars, "links": parser.links[:max_links]}


# --- sign-in walls, checkpoints, CAPTCHAs (pure) ---------------------------------

_BLOCK_URL_PATTERNS = (
    ("checkpoint", re.compile(r"/checkpoint/")),
    ("captcha", re.compile(r"captcha|/challenge", re.I)),
    ("sign_in_wall", re.compile(r"/authwall|/uas/login|/login\b|/signup|/signin|"
                                r"session_redirect", re.I)),
)
_BLOCK_TEXT_PATTERNS = (
    ("captcha", re.compile(r"captcha|i'?m not a robot|verify (that )?you(\s+are|'re) "
                           r"(a )?human|are you a robot", re.I)),
    ("checkpoint", re.compile(r"quick security check|security verification|"
                              r"let'?s do a quick (security )?check|"
                              r"unusual activity (from|on) your account|"
                              r"your account (has been|is) (temporarily )?restricted", re.I)),
    ("sign_in_wall", re.compile(r"sign in to (view|see) (more|this|the full)|"
                                r"join (linkedin|now) to (see|view)|"
                                r"sign in to linkedin|new to linkedin\?\s*join now|"
                                r"welcome back\s*\n?\s*(email|sign in)", re.I)),
)


def detect_block(url: str, text: str, status: int | None = None) -> str | None:
    """`checkpoint`, `captcha`, `sign_in_wall`, `bot_status`, or None (pure).

    Only the first part of the text is read: a wall replaces the page, while a real
    page's body could mention "captcha" far down in a post.
    """
    if status == LINKEDIN_BOT_STATUS:
        return "bot_status"
    path = urlsplit(url or "").path + "?" + urlsplit(url or "").query
    for reason, pattern in _BLOCK_URL_PATTERNS:
        if pattern.search(path):
            return reason
    head = (text or "")[:4000]
    for reason, pattern in _BLOCK_TEXT_PATTERNS:
        if pattern.search(head):
            return reason
    return None


# --- the daily LinkedIn limiter (pure apart from its file) --------------------------


class LinkedInLimiter:
    """25 loads a day, 3 per firm, 8-20 s apart, and a stop for the rest of the day.

    State is one JSON file, reset when the local date changes:
    `{"date", "loads", "firms": {firm: n}, "stopped": {reason, url, at} | null,
    "last_load_at"}`. A thread lock and an `flock` on `<file>.lock` serialise
    acquisitions across the worker threads and across processes (a probe and the
    scheduled run share one budget). Holding the lock through the pacing sleep is
    deliberate: it is what keeps parallel workers' LinkedIn loads apart.
    """

    def __init__(self, path: str | Path, *, daily: int = LINKEDIN_DAILY_LIMIT,
                 per_firm: int = LINKEDIN_PER_FIRM,
                 pace: tuple[float, float] = LINKEDIN_PACE_SECONDS,
                 clock: Callable[[], float] = time.time,
                 today: Callable[[], date] = date.today,
                 sleep: Callable[[float], None] = time.sleep,
                 rand: Callable[[float, float], float] = random.uniform) -> None:
        self.path = Path(path)
        self.daily, self.per_firm, self.pace = daily, per_firm, pace
        self.clock, self.today, self.sleep, self.rand = clock, today, sleep, rand
        self._lock = threading.Lock()

    @contextlib.contextmanager
    def _locked(self) -> Iterator[None]:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._lock, open(f"{self.path}.lock", "a") as fh:
            fcntl.flock(fh, fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(fh, fcntl.LOCK_UN)

    def _read(self) -> dict[str, Any]:
        fresh = {"date": self.today().isoformat(), "loads": 0, "firms": {},
                 "stopped": None, "last_load_at": None}
        try:
            state = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return fresh
        if not isinstance(state, dict) or state.get("date") != fresh["date"]:
            return fresh
        return {**fresh, **state}

    def _write(self, state: dict[str, Any]) -> None:
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(state, indent=1, sort_keys=True), encoding="utf-8")
        os.replace(tmp, self.path)

    @staticmethod
    def firm_key(firm: str | None) -> str:
        return (firm or "unknown").strip().lower() or "unknown"

    def acquire(self, firm: str | None) -> None:
        """Wait out the pacing and count one load, or raise `LinkedInUnavailable`."""
        key = self.firm_key(firm)
        with self._locked():
            state = self._read()
            if state["stopped"]:
                raise LinkedInUnavailable("stopped_for_today", state["stopped"].get("reason", ""))
            if state["loads"] >= self.daily:
                raise LinkedInUnavailable("daily_limit", f"{self.daily} loads used today")
            if state["firms"].get(key, 0) >= self.per_firm:
                raise LinkedInUnavailable("firm_limit", f"{self.per_firm} loads used for {key}")
            last = state["last_load_at"]
            if last is not None:
                wait = float(last) + self.rand(*self.pace) - self.clock()
                if wait > 0:
                    self.sleep(wait)
            state["loads"] += 1
            state["firms"][key] = state["firms"].get(key, 0) + 1
            state["last_load_at"] = self.clock()
            self._write(state)

    def stop(self, reason: str, url: str = "") -> None:
        """Stop LinkedIn for the rest of the day. Never undone automatically."""
        with self._locked():
            state = self._read()
            if not state["stopped"]:
                state["stopped"] = {"reason": reason, "url": url[:300],
                                    "at": datetime.fromtimestamp(self.clock()).isoformat(
                                        timespec="seconds")}
                self._write(state)

    def status(self) -> dict[str, Any]:
        with self._locked():
            return self._read()


_limiters: dict[str, LinkedInLimiter] = {}
_limiters_lock = threading.Lock()


def state_path() -> Path:
    return Path(os.environ.get(STATE_ENV) or DEFAULT_STATE).expanduser()


def limiter() -> LinkedInLimiter:
    """One limiter per state file in this process (its thread lock must be shared)."""
    path = str(state_path())
    with _limiters_lock:
        if path not in _limiters:
            _limiters[path] = LinkedInLimiter(path)
        return _limiters[path]


def linkedin_note(state: dict[str, Any] | None = None) -> str:
    """One line for a run's notes on today's LinkedIn use (pure given `state`)."""
    state = state if state is not None else limiter().status()
    line = (f"LinkedIn today: {state.get('loads', 0)}/{LINKEDIN_DAILY_LIMIT} loads "
            f"across {len(state.get('firms') or {})} firm(s)")
    if stopped := state.get("stopped"):
        line += (f"; STOPPED for the day at {stopped.get('at')} ({stopped.get('reason')}). "
                 "Check the CoS research profile's LinkedIn session by hand")
    return line


# --- LinkedIn text parsers (pure) ----------------------------------------------------

_ABOUT_LABELS = {
    "website": "website", "industry": "industry", "company size": "headcount_band",
    "headquarters": "hq", "founded": "founded", "type": "company_type",
}


def parse_company_about(text: str, title: str = "") -> dict[str, Any]:
    """Fields from the rendered text of a LinkedIn company About page (pure).

    Reads label lines ("Industry", "Company size", "Headquarters", ...) and takes the
    next line as the value, which is how the About section renders as text. This
    leans on visible labels rather than CSS classes, which LinkedIn renames often.
    """
    lines = [" ".join(line.split()) for line in (text or "").splitlines()]
    lines = [line for line in lines if line]
    out: dict[str, Any] = {"name": None, "website": None, "industry": None,
                           "headcount_band": None, "associated_members": None,
                           "hq": None, "founded": None, "company_type": None}
    for i, line in enumerate(lines[:-1]):
        key = _ABOUT_LABELS.get(line.lower())
        if key and out[key] is None:
            out[key] = lines[i + 1][:200]
    for line in lines:
        if m := re.search(r"([\d,]+)\s+associated members?", line, re.I):
            out["associated_members"] = int(m.group(1).replace(",", ""))
            break
    if out["headcount_band"]:
        out["headcount_band"] = re.sub(r"\s*employees?$", "", out["headcount_band"], flags=re.I)
    name = re.split(r"\s*[:|]\s*(About|LinkedIn)\b", title or "", maxsplit=1)[0].strip()
    out["name"] = name or None
    return out


_NOISE_LINE = re.compile(
    r"^(·\s*)?(1st|2nd|3rd\+?)$|degree connection|^connect$|^message$|^follow$|"
    r"^following$|^view .* profile|mutual connection|^linkedin member$|^status is|"
    r"^·$|^pending$|^more$", re.I)


def parse_people_cards(cards: list[dict[str, Any]], title_keywords: list[str],
                       *, limit: int = MAX_PEOPLE) -> list[dict[str, Any]]:
    """Names and titles from People-page cards `{href, lines}` (pure).

    "LinkedIn Member" cards (out of network, no name) are dropped. Every person is
    returned with `matches_keywords`, so a title the search matched loosely is
    still visible but flagged.
    """
    keys = [k.lower() for k in title_keywords if k and k.strip()]
    people: list[dict[str, Any]] = []
    seen: set[str] = set()
    for card in cards:
        href = str(card.get("href") or "").split("?", 1)[0].rstrip("/")
        if "/in/" not in href or href in seen:
            continue
        raw = [" ".join(str(x).split()) for x in card.get("lines") or []]
        if any(x.lower() == "linkedin member" for x in raw[:2]):
            continue
        lines = [x for x in raw if x and not _NOISE_LINE.search(x)]
        if not lines:
            continue
        seen.add(href)
        name, title = lines[0][:120], (lines[1][:200] if len(lines) > 1 else None)
        people.append({"name": name, "title": title, "profile_url": href,
                       "matches_keywords": bool(title and any(k in title.lower()
                                                              for k in keys))})
        if len(people) >= limit:
            break
    return people


# Read-only: collects each profile link in <main> and the text of its card.
# Assumed DOM (unverified against live LinkedIn): profile anchors contain "/in/",
# and each person's card is the anchor's closest <li>.
_PEOPLE_JS = """() => {
  const out = []; const seen = new Set();
  for (const a of document.querySelectorAll('main a[href*="/in/"]')) {
    const href = a.href.split('?')[0];
    if (seen.has(href)) continue; seen.add(href);
    const card = a.closest('li') || a.parentElement;
    const lines = ((card && card.innerText) || a.innerText || '')
      .split('\\n').map(s => s.trim()).filter(Boolean);
    out.push({href, lines});
  }
  return out;
}"""


# --- the live part ---------------------------------------------------------------------


def cdp_endpoint() -> str:
    return (os.environ.get(CDP_ENV) or DEFAULT_CDP).rstrip("/")


def available(timeout: float = 2.0) -> bool:
    """True if Chrome's CDP endpoint answers. Never raises."""
    try:
        with urllib.request.urlopen(f"{cdp_endpoint()}/json/version",  # noqa: S310
                                    timeout=timeout) as resp:
            return 200 <= resp.status < 300
    except (OSError, urllib.error.URLError, ValueError):
        return False


def _sync_playwright() -> Any:
    """Playwright's sync entry point, imported lazily (tests replace this)."""
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as exc:  # pragma: no cover - a dependency, but stay safe
        raise BrowserUnavailable("playwright is not installed") from exc
    return sync_playwright()


@contextlib.contextmanager
def _page() -> Iterator[Any]:
    """A new tab in the operator's Chrome, closed afterwards.

    Uses the profile's own context so its LinkedIn session applies. It never closes
    the browser: stopping Playwright only drops the CDP connection.
    """
    try:
        pw = _sync_playwright().start()
    except BrowserUnavailable:
        raise
    except Exception as exc:
        raise BrowserUnavailable(f"playwright did not start: {exc}") from exc
    page = None
    try:
        try:
            browser = pw.chromium.connect_over_cdp(cdp_endpoint(), timeout=CONNECT_TIMEOUT_MS)
        except Exception as exc:
            raise BrowserUnavailable(f"CDP did not answer at {cdp_endpoint()}: {exc}") from exc
        context = browser.contexts[0] if browser.contexts else browser.new_context()
        page = context.new_page()
        yield page
    finally:
        if page is not None:
            with contextlib.suppress(Exception):
                page.close()
        with contextlib.suppress(Exception):
            pw.stop()


def _goto(page: Any, url: str) -> int | None:
    response = page.goto(url, wait_until="domcontentloaded", timeout=NAV_TIMEOUT_MS)
    with contextlib.suppress(Exception):
        page.wait_for_load_state("networkidle", timeout=SETTLE_TIMEOUT_MS)
    return response.status if response is not None else None


def _visible_text(page: Any) -> str:
    for selector in ("main", "body"):
        with contextlib.suppress(Exception):
            text = page.inner_text(selector, timeout=3_000)
            if text and text.strip():
                return str(text)
    return ""


def _linkedin(url: str, firm: str | None, read: Callable[[Any, str], dict[str, Any]],
              lim: LinkedInLimiter | None = None) -> dict[str, Any]:
    """One LinkedIn page load under the limiter, with wall detection.

    The tab is open before the limiter counts the load, so a missing browser costs
    no budget. On a wall the day's LinkedIn browsing stops and this raises.
    """
    lim = lim or limiter()
    with _page() as page:
        lim.acquire(firm)
        status = _goto(page, url)
        final = str(page.url)
        text = _visible_text(page)
        if reason := detect_block(final, text, status):
            lim.stop(reason, final)
            raise LinkedInUnavailable(reason, "LinkedIn stopped for the rest of the day")
        return {"url": final, **read(page, text)}


def open_text(url: str, *, firm: str | None = None, allow_private: bool = False) -> dict[str, Any]:
    """Open `url` in a new tab and return `{url, title, text, truncated, links}`.

    Refuses ZoomInfo, Glassdoor, non-web schemes, and local hosts (`RefusedURL`).
    A LinkedIn URL goes through the daily limiter and wall detection.
    """
    if why := refusal(url, allow_private=allow_private):
        raise RefusedURL(why)

    def read(page: Any, _text: str) -> dict[str, Any]:
        found = extract_text(page.content(), str(page.url))
        with contextlib.suppress(Exception):
            found["title"] = page.title() or found["title"]
        return found

    if is_linkedin(url):
        return _linkedin(url, firm, read)
    with _page() as page:
        _goto(page, url)
        return {"url": str(page.url), **read(page, "")}


def linkedin_company(slug_or_url: str, *, firm: str | None = None) -> dict[str, Any]:
    """Headcount band, HQ, industry, and more from a company's About page."""
    slug = company_slug(slug_or_url)

    def read(page: Any, text: str) -> dict[str, Any]:
        title = ""
        with contextlib.suppress(Exception):
            title = page.title()
        return {"company_url": f"https://www.linkedin.com/company/{slug}",
                **parse_company_about(text, title)}

    return _linkedin(company_about_url(slug), firm or slug, read)


def linkedin_people(company: str, title_keywords: list[str], *,
                    firm: str | None = None) -> dict[str, Any]:
    """Names, titles, and profile URLs from a company's People page, filtered by
    LinkedIn's own keyword search."""
    slug = company_slug(company)
    keywords = [k for k in title_keywords if k and k.strip()][:5]

    def read(page: Any, _text: str) -> dict[str, Any]:
        cards = page.evaluate(_PEOPLE_JS) or []
        return {"people": parse_people_cards(cards, keywords)}

    return _linkedin(company_people_url(slug, keywords), firm or slug, read)
