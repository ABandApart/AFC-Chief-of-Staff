"""Unit tests for the control-plane loader (loops + playbooks).

Pure parsing/validation over temp manifest files, plus one test that the real
seed content under the repo validates clean.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from agents._lib.control_plane import (
    ControlPlaneError,
    discover,
    load_loop,
    load_playbook,
    parse_frontmatter,
    repo_root,
)


def write(dir_: Path, name: str, text: str) -> Path:
    p = dir_ / name
    p.write_text(text, encoding="utf-8")
    return p


# --- parse_frontmatter ----------------------------------------------------


def test_parse_frontmatter_splits_meta_and_body():
    meta, body = parse_frontmatter("---\nname: x\ndescription: y\n---\n# Title\nbody\n")
    assert meta == {"name": "x", "description": "y"}
    assert body.startswith("# Title")


def test_parse_frontmatter_missing_fence_raises():
    with pytest.raises(ControlPlaneError):
        parse_frontmatter("no frontmatter here")


def test_parse_frontmatter_unterminated_raises():
    with pytest.raises(ControlPlaneError):
        parse_frontmatter("---\nname: x\n")


def test_parse_frontmatter_non_mapping_raises():
    with pytest.raises(ControlPlaneError):
        parse_frontmatter("---\n- just\n- a\n- list\n---\nbody")


# --- playbooks ------------------------------------------------------------


def test_load_playbook_valid(tmp_path):
    p = write(tmp_path, "qual.md",
              "---\nname: qual\ndescription: d\napplies_to: [prospect]\n"
              "publish_to_memory: true\ntags: [w1]\n---\nbody")
    pb = load_playbook(p)
    assert pb.name == "qual" and pb.publish_to_memory is True
    assert pb.applies_to == ["prospect"] and pb.tags == ["w1"]


def test_load_playbook_defaults(tmp_path):
    p = write(tmp_path, "min.md", "---\nname: min\ndescription: d\n---\nbody")
    pb = load_playbook(p)
    assert pb.publish_to_memory is False and pb.applies_to == [] and pb.tags == []


def test_load_playbook_missing_description_raises(tmp_path):
    p = write(tmp_path, "bad.md", "---\nname: bad\n---\nbody")
    with pytest.raises(ControlPlaneError):
        load_playbook(p)


def test_load_playbook_name_must_match_filename(tmp_path):
    p = write(tmp_path, "file.md", "---\nname: other\ndescription: d\n---\nbody")
    with pytest.raises(ControlPlaneError):
        load_playbook(p)


def test_load_playbook_publish_must_be_bool(tmp_path):
    p = write(tmp_path, "b.md",
              "---\nname: b\ndescription: d\npublish_to_memory: yes-please\n---\nx")
    with pytest.raises(ControlPlaneError):
        load_playbook(p)


# --- loops ----------------------------------------------------------------


def test_load_loop_agent_variant(tmp_path):
    p = write(tmp_path, "brief.md",
              "---\nname: brief\nschedule: \"0 6 * * *\"\nenabled: true\n"
              "agent: briefing\nplaybook: daily\n---\n")
    lp = load_loop(p, known_playbooks={"daily"})
    assert lp.agent == "briefing" and lp.playbook == "daily" and lp.enabled
    assert lp.target == "agent: briefing → daily"


def test_load_loop_command_variant(tmp_path):
    p = write(tmp_path, "bk.md",
              "---\nname: bk\nschedule: \"0 2 * * *\"\nenabled: true\n"
              "command: scripts/pg_backup.sh\n---\n")
    lp = load_loop(p)
    assert lp.command == "scripts/pg_backup.sh"
    assert lp.target.startswith("command:")


def test_load_loop_bad_cron_raises(tmp_path):
    p = write(tmp_path, "c.md",
              "---\nname: c\nschedule: \"every morning\"\nenabled: true\nagent: a\n---\n")
    with pytest.raises(ControlPlaneError):
        load_loop(p)


def test_load_loop_requires_exactly_one_target(tmp_path):
    both = write(tmp_path, "d.md",
                 "---\nname: d\nschedule: \"0 6 * * *\"\nenabled: true\n"
                 "agent: a\ncommand: x\n---\n")
    with pytest.raises(ControlPlaneError):
        load_loop(both)
    neither = write(tmp_path, "e.md",
                    "---\nname: e\nschedule: \"0 6 * * *\"\nenabled: true\n---\n")
    with pytest.raises(ControlPlaneError):
        load_loop(neither)


def test_load_loop_enabled_missing_playbook_raises(tmp_path):
    p = write(tmp_path, "f.md",
              "---\nname: f\nschedule: \"0 6 * * *\"\nenabled: true\n"
              "agent: a\nplaybook: nope\n---\n")
    with pytest.raises(ControlPlaneError):
        load_loop(p, known_playbooks=set())


def test_load_loop_disabled_missing_playbook_ok(tmp_path):
    p = write(tmp_path, "g.md",
              "---\nname: g\nschedule: \"0 5 * * *\"\nenabled: false\n"
              "agent: tartt\nplaybook: future\n---\n")
    lp = load_loop(p, known_playbooks=set())
    assert lp.enabled is False and lp.playbook == "future"


def test_load_loop_enabled_must_be_bool(tmp_path):
    p = write(tmp_path, "h.md",
              "---\nname: h\nschedule: \"0 6 * * *\"\nenabled: sometimes\nagent: a\n---\n")
    with pytest.raises(ControlPlaneError):
        load_loop(p)


# --- discover over the real repo seed content -----------------------------


def test_repo_control_plane_validates_clean():
    cp = discover(repo_root())
    assert cp.ok, f"control-plane errors: {cp.errors}"
    names = {lp.name for lp in cp.loops}
    assert {"morning-briefing", "nightly-backup"} <= names
    pb_names = {pb.name for pb in cp.playbooks}
    assert {"daily-briefing", "prospect-qualification", "discovery-call-to-proposal"} <= pb_names
    # the briefing loop resolves to a real playbook
    brief = next(lp for lp in cp.loops if lp.name == "morning-briefing")
    assert brief.playbook in pb_names
