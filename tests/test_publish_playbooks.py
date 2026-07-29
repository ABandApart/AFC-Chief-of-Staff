"""Unit tests for the playbook-publish pure logic (W5).

The cognify/DB calls are exercised by runtime validation; here we pin the
content formatting, the hash-dedup key, and the `needs_publish` predicate (which
decides what gets re-cognified — the spend-control invariant).
"""

from __future__ import annotations

from agents._lib.control_plane import Playbook
from cli.publish_playbooks import content_hash, needs_publish, publish_content


def _pb(name="pb", desc="a description", body="the body", publish=True):
    return Playbook(name=name, description=desc, publish_to_memory=publish, body=body)


def test_publish_content_heads_with_name_and_description():
    text = publish_content(_pb(name="qual", desc="how to qualify", body="Step 1."))
    assert text.startswith("# qual\n\nhow to qualify")
    assert "Step 1." in text


def test_content_hash_ignores_whitespace_and_case():
    assert content_hash("Hello   World.") == content_hash("hello world.")


def test_content_hash_is_hex_sha256():
    h = content_hash("anything")
    assert len(h) == 64 and all(c in "0123456789abcdef" for c in h)


def test_needs_publish_skips_non_memory_playbooks():
    pb = _pb(publish=False)
    assert needs_publish(pb, {}, force=False) is False
    # even --force does not publish a local-only playbook
    assert needs_publish(pb, {}, force=True) is False


def test_needs_publish_true_when_new():
    pb = _pb()
    assert needs_publish(pb, {}, force=False) is True


def test_needs_publish_false_when_hash_unchanged():
    pb = _pb()
    published = {pb.name: content_hash(publish_content(pb))}
    assert needs_publish(pb, published, force=False) is False


def test_needs_publish_true_when_content_changed():
    pb = _pb(body="original")
    published = {pb.name: content_hash(publish_content(pb))}
    changed = _pb(body="revised")
    assert needs_publish(changed, published, force=False) is True


def test_force_republishes_unchanged():
    pb = _pb()
    published = {pb.name: content_hash(publish_content(pb))}
    assert needs_publish(pb, published, force=True) is True
