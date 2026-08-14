"""Unit tests for the job-board adapters (Track O, `35-` §6).

Board detection, response parsing, and fact mapping are pure — the network call
is a thin wrapper around them, mocked here. What matters most:

  * `dedup_key` is **stable and provider-scoped**, because `first_seen_at` is
    only meaningful if the same req maps to the same key on every poll. A drifting
    key silently resets posting age, which is the datum T10 and S4 both rest on.
  * A failed fetch/parse returns `ok=False`, NEVER an empty-but-successful
    result — the caller uses `ok` to decide whether absent reqs may be closed.
"""

from __future__ import annotations

import pytest

from agents.outreach import adapters

# --- detect_board (pure) ------------------------------------------------------


@pytest.mark.parametrize("url,expected", [
    ("https://boards.greenhouse.io/cadencehealth", ("greenhouse", "cadencehealth")),
    ("https://job-boards.greenhouse.io/acme/", ("greenhouse", "acme")),
    ("boards.greenhouse.io/embed/job_board?for=acme", ("greenhouse", "acme")),
    ("https://jobs.lever.co/cadence", ("lever", "cadence")),
    ("http://www.jobs.lever.co/cadence/some-role", ("lever", "cadence")),
    ("https://jobs.ashbyhq.com/acme", ("ashby", "acme")),
    ("https://api.ashbyhq.com/posting-api/job-board/acme", ("ashby", "acme")),
])
def test_detect_board_recognises_hosted_and_api_urls(url, expected):
    assert adapters.detect_board(url) == expected


@pytest.mark.parametrize("url,expected", [
    ("https://apply.workable.com/aiir-consulting/", ("workable", "aiir-consulting")),
    ("https://apply.workable.com/unboxed-technology", ("workable", "unboxed-technology")),
    # Board id from the SUBDOMAIN, not a path segment.
    ("https://experiencepoint.bamboohr.com/careers", ("bamboohr", "experiencepoint")),
    ("https://workingvoices.bamboohr.com/careers", ("bamboohr", "workingvoices")),
    # TeamTailor accounts can contain their own dots — keep the whole prefix.
    ("https://salesgravy-1748472865.na.teamtailor.com",
     ("teamtailor", "salesgravy-1748472865.na")),
    ("https://ats.rippling.com/ttcinnovations/jobs", ("rippling", "ttcinnovations")),
])
def test_detect_board_recognises_the_increment_1b_platforms(url, expected):
    assert adapters.detect_board(url) == expected


def test_workable_single_job_link_is_not_a_board():
    # apply.workable.com/j/<shortcode> is ONE posting. Treating it as a board
    # would poll a nonexistent account and quietly accrue nothing.
    assert adapters.detect_board("https://apply.workable.com/j/EF0DC62338") is None


@pytest.mark.parametrize("url", [
    None, "", "   ",
    "https://acme.com/careers",              # generic page — no stable role ids
    "https://www.notion.so/acme/jobs",
    "https://boards.greenhouse.io",          # host with no board token
    # Probed 2026-08-14: no public JSON feed. BreatheHR's .json route is 401,
    # Hireology and SaaSHR serve HTML only. Unsupported is the honest answer.
    "https://careers.hireology.com/torrancelearning",
    "https://hr.breathehr.com/recruitment/vacancies?identifier=roffeypark",
    "https://secure7.saashr.com/ta/6208035.careers",
    "https://careers.maximus.com.au/",
])
def test_detect_board_returns_none_for_unsupported(url):
    assert adapters.detect_board(url) is None


def test_board_api_url_per_provider():
    gh = adapters.board_api_url("greenhouse", "acme")
    assert "boards-api.greenhouse.io/v1/boards/acme/jobs" in gh
    assert "api.lever.co/v0/postings/acme" in adapters.board_api_url("lever", "acme")
    assert "posting-api/job-board/acme" in adapters.board_api_url("ashby", "acme")


def test_board_api_url_rejects_unknown_provider():
    with pytest.raises(ValueError):
        adapters.board_api_url("workday", "acme")


# --- parsers (pure) -----------------------------------------------------------


def test_parse_greenhouse():
    payload = {"jobs": [{
        "id": 4567, "title": "VP Revenue",
        "absolute_url": "https://boards.greenhouse.io/acme/jobs/4567",
        "location": {"name": "Remote (US)"},
        "departments": [{"name": "Sales"}],
    }]}
    (role,) = adapters.parse_greenhouse(payload)
    assert role == {
        "external_id": "4567", "title": "VP Revenue",
        "url": "https://boards.greenhouse.io/acme/jobs/4567",
        "location": "Remote (US)", "team": "Sales",
        "posted_at": None,   # Greenhouse's board feed carries no publish date
    }


def test_parse_lever():
    payload = [{
        "id": "abc-123", "text": "Account Executive",
        "hostedUrl": "https://jobs.lever.co/acme/abc-123",
        "categories": {"location": "London", "team": "GTM"},
    }]
    (role,) = adapters.parse_lever(payload)
    assert role["external_id"] == "abc-123"
    assert role["title"] == "Account Executive"
    assert role["location"] == "London" and role["team"] == "GTM"


def test_parse_ashby():
    payload = {"jobs": [{
        "id": "u-9", "title": "Head of Growth",
        "jobUrl": "https://jobs.ashbyhq.com/acme/u-9",
        "location": "NYC", "department": "Marketing",
    }]}
    (role,) = adapters.parse_ashby(payload)
    assert role["external_id"] == "u-9" and role["title"] == "Head of Growth"


def test_parse_workable():
    # Shape captured from the live widget feed, 2026-08-14.
    payload = {"jobs": [{
        "title": "QA Engineering Lead", "shortcode": "EF0DC62338",
        "department": "Product", "url": "https://apply.workable.com/j/EF0DC62338",
        "published_on": "2026-07-29", "city": "Richmond",
        "state": "Virginia", "country": "United States",
    }]}
    (role,) = adapters.parse_workable(payload)
    assert role["external_id"] == "EF0DC62338"   # shortcode, not the title
    assert role["title"] == "QA Engineering Lead"
    assert role["location"] == "Richmond, Virginia, United States"
    assert role["team"] == "Product"
    assert role["posted_at"] == "2026-07-29"


def test_parse_bamboohr_reconstructs_the_job_url_from_the_token():
    # BambooHR's payload has no URL at all — without the token the packet would
    # show a role with nothing to click through to.
    payload = {"result": [{
        "id": "48", "jobOpeningName": "General Application ",
        "departmentLabel": "All Departments",
        "location": {"city": "Toronto", "state": "Ontario"},
    }]}
    (role,) = adapters.parse_bamboohr(payload, "experiencepoint")
    assert role["external_id"] == "48"
    assert role["title"] == "General Application"          # trailing space trimmed
    assert role["url"] == "https://experiencepoint.bamboohr.com/careers/48"
    assert role["location"] == "Toronto, Ontario"


def test_parse_bamboohr_without_a_token_yields_no_url_not_a_broken_one():
    payload = {"result": [{"id": "48", "jobOpeningName": "Role"}]}
    (role,) = adapters.parse_bamboohr(payload, "")
    assert role["url"] is None


def test_parse_teamtailor_ignores_the_description_blob():
    # H1: evidence carries short bounded fields. content_html is a whole job
    # description and must not reach the payload.
    payload = {"items": [{
        "id": "81bd2305-cf44-46f9-a2f8-8f105eef2dd0",
        "title": "Sales Consultant and Trainer",
        "url": "https://x.teamtailor.com/jobs/592669",
        "date_published": "2026-04-15T10:04:41-04:00",
        "content_html": "<p>" + "x" * 5000 + "</p>",
    }]}
    (role,) = adapters.parse_teamtailor(payload)
    assert role["external_id"] == "81bd2305-cf44-46f9-a2f8-8f105eef2dd0"
    assert "x" * 100 not in str(role)          # the blob is nowhere in the role
    assert role["posted_at"] == "2026-04-15"   # date only, time dropped


def test_parse_rippling():
    payload = [{
        "uuid": "d524c7fd-6205-4ddb-a861-4ef6e7272f24",
        "name": "Change Management Consultant",
        "department": {"label": "Performance Consulting"},
        "url": "https://ats.rippling.com/ttcinnovations/jobs/d524c7fd",
        "workLocation": {"label": "Charlotte, NC"},
    }]
    (role,) = adapters.parse_rippling(payload)
    assert role["external_id"] == "d524c7fd-6205-4ddb-a861-4ef6e7272f24"
    assert role["team"] == "Performance Consulting"
    assert role["location"] == "Charlotte, NC"


def test_posted_at_never_becomes_first_seen_at():
    # The board's claimed date rides in the payload for display; first_seen_at
    # stays OUR observation. A provider date can reset on an edit or repost, and
    # mixing the two would make "open 56 days" mean different things per row.
    role = {"external_id": "1", "title": "T", "url": None, "posted_at": "2020-01-01"}
    fact = adapters.role_to_fact(role, "workable")
    assert fact["payload"]["posted_at"] == "2020-01-01"
    assert "first_seen_at" not in fact         # the poller stamps it, from today


@pytest.mark.parametrize("provider", list(adapters._PARSERS))
def test_every_provider_has_an_api_url(provider):
    assert adapters.board_api_url(provider, "acme").startswith("https://")


def test_all_seven_platforms_are_wired():
    assert set(adapters._PARSERS) == {
        "greenhouse", "lever", "ashby",
        "workable", "bamboohr", "teamtailor", "rippling",
    }


def test_parsers_skip_postings_with_no_usable_identity():
    # No id, or no title → no stable dedup key → not evidence.
    assert adapters.parse_greenhouse({"jobs": [{"title": "No id"}]}) == []
    assert adapters.parse_greenhouse({"jobs": [{"id": 1, "title": "  "}]}) == []
    assert adapters.parse_lever([{"id": "x"}]) == []


def test_parsers_tolerate_empty_and_missing_collections():
    assert adapters.parse_greenhouse({}) == []
    assert adapters.parse_lever([]) == []
    assert adapters.parse_ashby({"jobs": None}) == []


def test_parse_board_dispatches_and_rejects_unknown():
    assert adapters.parse_board("lever", [{"id": "1", "text": "T"}])[0]["external_id"] == "1"
    with pytest.raises(ValueError):
        adapters.parse_board("workday", {})


# --- role_to_fact (pure) ------------------------------------------------------


def test_role_to_fact_shape_and_provider_scoped_dedup_key():
    role = {"external_id": "4567", "title": "VP Revenue",
            "url": "https://x/4567", "location": "Remote", "team": "Sales"}
    fact = adapters.role_to_fact(role, "greenhouse")
    assert fact["fact_kind"] == "open_role"
    # Provider-scoped so an ATS migration starts fresh instead of colliding ids.
    assert fact["dedup_key"] == "greenhouse:4567"
    assert fact["source_kind"] == "careers_page"
    assert fact["source_excerpt"] == "VP Revenue"
    assert fact["payload"]["title"] == "VP Revenue"


def test_dedup_key_is_stable_across_polls_but_differs_across_providers():
    role = {"external_id": "4567", "title": "VP Revenue", "url": None}
    first = adapters.role_to_fact(role, "greenhouse")["dedup_key"]
    second = adapters.role_to_fact(dict(role), "greenhouse")["dedup_key"]
    assert first == second  # stable — this is what makes first_seen_at meaningful
    assert adapters.role_to_fact(role, "lever")["dedup_key"] != first


# --- fetch_open_roles (network mocked) ---------------------------------------


def test_fetch_unsupported_url_is_not_ok(mocker):
    get = mocker.patch.object(adapters, "_get_json")
    result = adapters.fetch_open_roles("https://acme.com/careers")
    assert result.ok is False and "unsupported" in (result.reason or "")
    get.assert_not_called()  # nothing to call — detection failed first


def test_fetch_network_error_is_not_ok_not_empty(mocker):
    # THE failure that matters: "we could not look" must never read as
    # "there is nothing there", or the caller closes every open req.
    mocker.patch.object(adapters, "_get_json", side_effect=OSError("connection reset"))
    result = adapters.fetch_open_roles("https://jobs.lever.co/acme")
    assert result.ok is False and result.roles == []
    assert "fetch failed" in (result.reason or "")


def test_fetch_malformed_payload_is_not_ok(mocker):
    mocker.patch.object(adapters, "_get_json", return_value={"unexpected": "shape"})
    mocker.patch.object(adapters, "parse_board", side_effect=KeyError("jobs"))
    result = adapters.fetch_open_roles("https://jobs.lever.co/acme")
    assert result.ok is False and "parse failed" in (result.reason or "")


def test_fetch_success_returns_roles_and_provider(mocker):
    mocker.patch.object(adapters, "_get_json", return_value=[
        {"id": "1", "text": "AE", "hostedUrl": "https://x/1", "categories": {}},
    ])
    result = adapters.fetch_open_roles("https://jobs.lever.co/acme")
    assert result.ok is True and result.provider == "lever"
    assert [r["external_id"] for r in result.roles] == ["1"]


def test_fetch_genuinely_empty_board_is_ok(mocker):
    # An empty board that parsed fine IS a legitimate zero — the company really
    # closed its reqs — so this one may close absent evidence.
    mocker.patch.object(adapters, "_get_json", return_value=[])
    result = adapters.fetch_open_roles("https://jobs.lever.co/acme")
    assert result.ok is True and result.roles == []


def test_fetch_caps_absurdly_large_boards(mocker):
    mocker.patch.object(adapters, "_get_json", return_value=[
        {"id": str(i), "text": f"Role {i}", "categories": {}} for i in range(500)
    ])
    result = adapters.fetch_open_roles("https://jobs.lever.co/acme")
    assert len(result.roles) == adapters.MAX_ROLES_PER_TARGET
