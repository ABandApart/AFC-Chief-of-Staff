"""Publish trusted playbooks from git into the cognee graph (W5 / B1).

Every playbook with `publish_to_memory: true` (see `playbooks/*.md`) is cognified
into a dedicated **`playbooks`** dataset. That dataset is the *trusted* memory
region: agent retrieval that must not be swayed by untrusted ingest is scoped to
it (trust boundary B1, architecture/25-target-state.md). Free-text capture goes
to the separate `capture` dataset, never here.

    uv run python -m cli.publish_playbooks            # publish changed/new ones
    uv run python -m cli.publish_playbooks --force    # re-publish all flagged
    uv run python -m cli.publish_playbooks --dry-run  # show what would publish

Idempotency: cognee does not dedup on re-ingest, so re-publishing an unchanged
playbook would duplicate graph nodes and burn LLM spend. We track the last
published content hash per playbook in `playbook_publications` (aiadaptive_cos)
and skip a playbook whose hash is unchanged. `--force` ignores the tracker.

⚠️ Known limitation: when a playbook *changes*, this re-cognifies the new content
but does not delete the previous version's nodes from the graph (cognee dataset
node-deletion API to be verified at runtime — W7). Publishing is git-driven and
playbooks change rarely, so stale-version accumulation is slow; revisit if it
matters. Runs under `labeled()`, so the cognify spend lands in the ledger.

Needs the keys + cognee in keychain/venv (barry-agent). `configure_cognee()`
runs at startup, so a CLI must call it before any cognee op.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import logging
import sys

from agents._lib import cognee_setup, db
from agents._lib.control_plane import Playbook, discover
from agents._lib.telemetry_context import labeled

logger = logging.getLogger(__name__)

PLAYBOOKS_DATASET = "playbooks"


def publish_content(pb: Playbook) -> str:
    """The text cognified for a playbook: a self-describing block (pure).

    Name + description head the body so the graph nodes carry enough context to
    be retrievable on their own, not just as an anonymous chunk.
    """
    return f"# {pb.name}\n\n{pb.description}\n\n{pb.body}".strip()


def content_hash(text: str) -> str:
    """sha256 hex of the normalized content (whitespace collapsed, casefolded) —
    the publish-dedup key, mirroring capture's message_hash."""
    normalized = " ".join(text.split()).casefold()
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def needs_publish(pb: Playbook, published: dict[str, str], *, force: bool) -> bool:
    """Should this playbook be (re)published? Pure — unit-tested.

    Only `publish_to_memory` playbooks are eligible. Beyond that: `force`
    republishes everything; otherwise publish when new or when the content hash
    differs from what was last pushed.
    """
    if not pb.publish_to_memory:
        return False
    if force:
        return True
    return published.get(pb.name) != content_hash(publish_content(pb))


def _published_hashes() -> dict[str, str]:
    with db.connection() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT name, content_hash FROM playbook_publications")
            return {name: h for name, h in cur.fetchall()}


def _record_publication(name: str, chash: str) -> None:
    with db.connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO playbook_publications (name, content_hash, published_at)
                VALUES (%s, %s, now())
                ON CONFLICT (name)
                DO UPDATE SET content_hash = EXCLUDED.content_hash,
                             published_at = now()
                """,
                (name, chash),
            )


async def _cognify_playbook(pb: Playbook) -> None:
    import cognee  # lazy — optional `cognee` dependency group

    text = publish_content(pb)
    with labeled(
        "playbook-publish", "infrastructure",
        trigger_kind="manual", correlation_id=pb.name,
    ):
        await cognee.add(text, dataset_name=PLAYBOOKS_DATASET)
        await cognee.cognify(datasets=[PLAYBOOKS_DATASET])


async def publish(*, force: bool = False, dry_run: bool = False) -> list[str]:
    """Publish changed/new trusted playbooks. Returns the names published.

    On `dry_run` nothing is cognified or recorded — it just reports what would
    publish (no cognee/keychain needed).
    """
    cp = discover()
    if cp.errors:
        for e in cp.errors:
            logger.error("control-plane error (skipping publish): %s", e)
        raise SystemExit("control plane invalid — fix the manifests before publishing")

    published = {} if dry_run else await asyncio.to_thread(_published_hashes)
    targets = [pb for pb in cp.playbooks if needs_publish(pb, published, force=force)]

    if dry_run:
        for pb in targets:
            print(f"would publish: {pb.name}")
        return [pb.name for pb in targets]

    cognee_setup.configure_cognee()
    done: list[str] = []
    for pb in targets:
        await _cognify_playbook(pb)
        await asyncio.to_thread(
            _record_publication, pb.name, content_hash(publish_content(pb))
        )
        logger.info("published playbook %r → %s dataset", pb.name, PLAYBOOKS_DATASET)
        print(f"published: {pb.name}")
        done.append(pb.name)
    return done


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    parser = argparse.ArgumentParser(
        description="Publish publish_to_memory playbooks into the trusted cognee dataset."
    )
    parser.add_argument("--force", action="store_true",
                        help="re-publish every flagged playbook, ignoring the hash tracker")
    parser.add_argument("--dry-run", action="store_true",
                        help="report what would publish; touch nothing")
    args = parser.parse_args()

    done = asyncio.run(publish(force=args.force, dry_run=args.dry_run))
    verb = "would publish" if args.dry_run else "published"
    print(f"\n{verb} {len(done)} playbook(s).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
