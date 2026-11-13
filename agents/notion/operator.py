"""Who may decide in Notion, and where N9's databases live (`PRD-notion-integration.md` N9).

**Operator check (N-D24).** A decision or input pulled from Notion is taken only
if the page was last edited by the operator or by the CoS's own integration.
The integration counts because its own pushes also set `last_edited_by`: a CoS
write landing after the operator's edit must not hide that edit. Any other
workspace member's edit is refused and reported.

Notion's page objects carry `last_edited_by: {"object": "user", "id": …}` only, no
type, so the check compares ids against the operator's (recorded once by
barry-admin as `notion_sync ('notion_schema', 'operator_user')`) and the
integration's (`GET /v1/users/me`).
"""

from __future__ import annotations

from typing import Any

OPERATOR_KEY = "operator_user"


def schema_id(conn: Any, key: str) -> str:
    """A `notion_sync` `notion_schema` row's Notion id, such as `outcomes_database`."""
    row = conn.execute(
        "SELECT notion_page_id FROM notion_sync WHERE entity_table = 'notion_schema' "
        "AND entity_id = %s", (key,)).fetchone()
    if row is None:
        raise RuntimeError(f"notion_sync has no notion_schema row {key!r}; "
                           "barry-admin records it at setup (N9)")
    return row[0]


def allowed_editors(client: Any, conn: Any) -> frozenset[str]:
    """The user ids whose edits the CoS takes: the operator and the integration."""
    return frozenset({schema_id(conn, OPERATOR_KEY), client.me()["id"]})


def editor(page: dict[str, Any]) -> str | None:
    return (page.get("last_edited_by") or {}).get("id")


def foreign_edit(page: dict[str, Any], allowed: frozenset[str]) -> bool:
    """Whether someone other than the operator or the CoS last edited the page (pure).
    A page with no `last_edited_by` (a test fixture) counts as allowed."""
    who = editor(page)
    return who is not None and who not in allowed
