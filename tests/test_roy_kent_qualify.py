"""Unit tests for Roy Kent — inbound prospect qualification (Phase 6).

Pure helpers (pain-point extraction, prompt assembly) are tested directly.
`qualify_fit`/`embed_pain_points` wiring is checked with `agent_run` mocked
(no ceiling, DB, or network) — same pattern as `test_tartt_summarize.py`.
`process_lead` orchestration is checked with `db.connection` and every DB/LLM
helper mocked, so the branching (duplicate/qualified/failed/low-fit) is
exercised without a real database.
"""

from __future__ import annotations

from agents.roy_kent import qualify

# --- extract_pain_points (pure) ---------------------------------------------


def test_extract_pain_points_filters_short_answers():
    raw = {"answers": {"q1": "yes", "q2": "We can't keep up with lead follow-up at all"}}
    assert qualify.extract_pain_points(raw) == ["We can't keep up with lead follow-up at all"]


def test_extract_pain_points_ignores_non_string_values():
    raw = {"answers": {"q1": 42, "q2": None, "q3": "A genuinely long pain point statement"}}
    assert qualify.extract_pain_points(raw) == ["A genuinely long pain point statement"]


def test_extract_pain_points_missing_answers_key_is_empty():
    assert qualify.extract_pain_points({}) == []
    assert qualify.extract_pain_points({"answers": "not-a-dict"}) == []


# --- build_qualification_prompt (pure) --------------------------------------


def test_prompt_delimits_prospect_data_as_data_not_instructions():
    payload = {"company": "Acme", "role": "Founder", "source_form": "scorecard"}
    p = qualify.build_qualification_prompt(payload, ["pain one"], criteria="the rubric")
    assert "PROSPECT-SUBMITTED DATA START" in p
    assert "PROSPECT-SUBMITTED DATA END" in p
    assert "not instructions to you" in p
    assert "the rubric" in p
    assert "pain one" in p


def test_prompt_handles_missing_company_and_role():
    payload = {"source_form": "contact"}
    p = qualify.build_qualification_prompt(payload, [], criteria="x")
    assert "(not given)" in p


def test_prompt_truncates_long_profile():
    payload = {"company": "A" * 20_000, "source_form": "scorecard"}
    p = qualify.build_qualification_prompt(payload, [], criteria="x")
    start = p.index("PROSPECT-SUBMITTED DATA START")
    end = p.index("PROSPECT-SUBMITTED DATA END")
    assert end - start <= qualify.MAX_PROFILE_CHARS + 100


# --- qualify_fit / embed_pain_points wiring ---------------------------------


def test_qualify_fit_runs_under_roy_kent_label_and_forces_the_qualify_tool(mocker):
    fake_run = mocker.MagicMock()
    fake_run.call_anthropic_structured.return_value = {
        "icp_fit_score": 0.8, "icp_segment": "solo consultant", "fit_reasoning": "good fit",
    }
    cm = mocker.MagicMock()
    cm.__enter__.return_value = fake_run
    cm.__exit__.return_value = False
    ar = mocker.patch.object(qualify, "agent_run", return_value=cm)

    out = qualify.qualify_fit(42, "the prompt")

    assert out["icp_fit_score"] == 0.8
    assert ar.call_args.args[:2] == ("roy-kent", "customer_discovery")
    assert ar.call_args.kwargs["correlation_id"] == "42"
    assert ar.call_args.kwargs["correlation_kind"] == "prospect"
    kw = fake_run.call_anthropic_structured.call_args.kwargs
    assert kw["model"] == qualify.QUALIFY_MODEL
    assert kw["max_output_tokens"] == qualify.QUALIFY_MAX_OUTPUT_TOKENS
    assert kw["tool_name"] == "qualify"
    assert kw["input_schema"] == qualify.QUALIFY_SCHEMA


def test_embed_pain_points_runs_under_roy_kent_label(mocker):
    fake_run = mocker.MagicMock()
    fake_run.call_embedding.return_value = [[0.1, 0.2]]
    cm = mocker.MagicMock()
    cm.__enter__.return_value = fake_run
    cm.__exit__.return_value = False
    ar = mocker.patch.object(qualify, "agent_run", return_value=cm)

    out = qualify.embed_pain_points(7, ["a pain point"])

    assert out == [[0.1, 0.2]]
    assert ar.call_args.args[:2] == ("roy-kent", "customer_discovery")
    fake_run.call_embedding.assert_called_once_with(["a pain point"])


# --- fetch_icp_criteria -------------------------------------------------


def test_fetch_icp_criteria_falls_back_when_no_decisions(mocker):
    cur = mocker.MagicMock()
    cur.fetchall.return_value = []
    conn = mocker.MagicMock()
    conn.cursor.return_value.__enter__.return_value = cur

    assert qualify.fetch_icp_criteria(conn) == qualify.DEFAULT_ICP_CRITERIA


def test_fetch_icp_criteria_joins_recorded_decisions(mocker):
    cur = mocker.MagicMock()
    cur.fetchall.return_value = [("Target segment", "solo consultants only")]
    conn = mocker.MagicMock()
    conn.cursor.return_value.__enter__.return_value = cur

    out = qualify.fetch_icp_criteria(conn)
    assert "Target segment" in out
    assert "solo consultants only" in out


# --- process_lead orchestration ---------------------------------------------

PAYLOAD = {
    "wordpress_profile_id": "wp-1",
    "name": "Jane Prospect",
    "email": "jane@ex.com",
    "company": "Acme",
    "role": "Founder",
    "source_form": "scorecard",
    "raw_profile": {"answers": {"q1": "We can't keep up with our own pipeline at all"}},
}

FIT_HIGH = {"icp_fit_score": 0.8, "icp_segment": "solo consultant", "fit_reasoning": "great fit"}
FIT_LOW = {"icp_fit_score": 0.2, "icp_segment": "enterprise", "fit_reasoning": "poor fit"}


def _patch_db_connection(mocker):
    conn = mocker.MagicMock()
    cm = mocker.MagicMock()
    cm.__enter__.return_value = conn
    cm.__exit__.return_value = False
    mocker.patch.object(qualify.db, "connection", return_value=cm)
    return conn


def test_process_lead_duplicate_short_circuits(mocker):
    _patch_db_connection(mocker)
    mocker.patch.object(qualify, "find_existing_prospect", return_value={"id": 99})
    qualify_mock = mocker.patch.object(qualify, "qualify_fit")

    result = qualify.process_lead(PAYLOAD)

    assert result == {"status": "duplicate", "prospect_id": 99}
    qualify_mock.assert_not_called()


def test_process_lead_happy_path_qualifies_embeds_and_proposes_task(mocker):
    _patch_db_connection(mocker)
    mocker.patch.object(qualify, "find_existing_prospect", return_value=None)
    mocker.patch.object(
        qualify, "insert_prospect",
        return_value={"id": 1, "name": "Jane Prospect", "company": "Acme",
                      "wordpress_profile_id": "wp-1"},
    )
    mocker.patch.object(qualify, "fetch_icp_criteria", return_value="rubric")
    mocker.patch.object(qualify, "qualify_fit", return_value=FIT_HIGH)
    apply_mock = mocker.patch.object(qualify, "apply_qualification")
    mocker.patch.object(qualify, "embed_pain_points", return_value=[[0.1] * 768])
    insert_signal_mock = mocker.patch.object(qualify, "insert_pain_signal")
    propose_mock = mocker.patch.object(qualify, "propose_task_candidate")

    result = qualify.process_lead(PAYLOAD)

    assert result["status"] == "processed"
    assert result["fit"] == FIT_HIGH
    apply_mock.assert_called_once()
    insert_signal_mock.assert_called_once()
    assert insert_signal_mock.call_args.kwargs["icp_segment_hint"] == "solo consultant"
    propose_mock.assert_called_once()


def test_process_lead_low_fit_no_task_candidate(mocker):
    _patch_db_connection(mocker)
    mocker.patch.object(qualify, "find_existing_prospect", return_value=None)
    mocker.patch.object(
        qualify, "insert_prospect",
        return_value={"id": 2, "name": "Jane", "company": None, "wordpress_profile_id": "wp-1"},
    )
    mocker.patch.object(qualify, "fetch_icp_criteria", return_value="rubric")
    mocker.patch.object(qualify, "qualify_fit", return_value=FIT_LOW)
    mocker.patch.object(qualify, "apply_qualification")
    mocker.patch.object(qualify, "embed_pain_points", return_value=[[0.1] * 768])
    mocker.patch.object(qualify, "insert_pain_signal")
    propose_mock = mocker.patch.object(qualify, "propose_task_candidate")

    result = qualify.process_lead(PAYLOAD)

    assert result["fit"]["icp_fit_score"] < qualify.TASK_CANDIDATE_THRESHOLD
    propose_mock.assert_not_called()


def test_process_lead_qualification_failure_leaves_prospect_unscored(mocker):
    _patch_db_connection(mocker)
    mocker.patch.object(qualify, "find_existing_prospect", return_value=None)
    mocker.patch.object(
        qualify, "insert_prospect",
        return_value={"id": 3, "name": "Jane", "company": None, "wordpress_profile_id": "wp-1"},
    )
    mocker.patch.object(qualify, "fetch_icp_criteria", return_value="rubric")
    mocker.patch.object(qualify, "qualify_fit", side_effect=RuntimeError("provider down"))
    apply_mock = mocker.patch.object(qualify, "apply_qualification")
    mocker.patch.object(qualify, "embed_pain_points", return_value=[[0.1] * 768])
    mocker.patch.object(qualify, "insert_pain_signal")
    propose_mock = mocker.patch.object(qualify, "propose_task_candidate")

    result = qualify.process_lead(PAYLOAD)

    assert result["fit"] is None
    apply_mock.assert_not_called()
    propose_mock.assert_not_called()


def test_process_lead_no_pain_points_skips_embedding(mocker):
    _patch_db_connection(mocker)
    mocker.patch.object(qualify, "find_existing_prospect", return_value=None)
    mocker.patch.object(
        qualify, "insert_prospect",
        return_value={"id": 4, "name": "Jane", "company": None, "wordpress_profile_id": "wp-1"},
    )
    mocker.patch.object(qualify, "fetch_icp_criteria", return_value="rubric")
    mocker.patch.object(qualify, "qualify_fit", return_value=FIT_LOW)
    mocker.patch.object(qualify, "apply_qualification")
    embed_mock = mocker.patch.object(qualify, "embed_pain_points")

    payload = {**PAYLOAD, "raw_profile": {}}
    qualify.process_lead(payload)

    embed_mock.assert_not_called()
