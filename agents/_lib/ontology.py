"""Domain ontology as cognee DataPoints (Phase 3.7 / W3).

The knowledge entities of the brain, modeled as cognee `DataPoint` subclasses.
The ontology *emerges from the classes*: a typed field is a node property, and a
field that references another DataPoint is an edge. `metadata["index_fields"]`
picks which fields get embedded for semantic retrieval.

Entity ↔ operational boundary (target-state `25-target-state.md`, drawn here):

  KNOWLEDGE → the graph (these DataPoints): Organization, Person, Fact,
  Decision, Meeting, ICPSignal, ContentItem, InterestSignal.

  OPERATIONAL STATE → stays SQL (status machines, queues, ledgers, cadence):
  prospects, task_candidates, tasks, follow_ups, content_pipeline,
  approval_queue, buffer_posts, outcomes, agent_runs, dashboard, sources.

  CROSS-LINKS: when an operational row needs to reference a graph node, it holds
  the cognee node-id as a **TEXT column** (not a cross-store FK — the graph lives
  in `aiadaptive_cognee`, the operational tables in `aiadaptive_cos`, and Postgres
  can't FK across databases), joined in app code. The first such column
  (`outcomes.attributed_fact_node`) was dropped in 0006 — the operator chose not
  to link outcomes to fact nodes — so the pattern currently has no live use; the
  first will be a future channel/agent that pins an operational row to a graph
  entity.

cognee is optional (`uv sync --group cognee`). When it's absent a pydantic
`BaseModel` stand-in lets these classes import and their structure be
unit-tested; at runtime the real `cognee.low_level.DataPoint` is used and
`add_data_points([...])` walks the relationship fields into graph nodes/edges.
"""

from __future__ import annotations

try:
    from cognee.low_level import DataPoint

    HAVE_COGNEE = True
except ImportError:  # structural stand-in — lets the classes import + be tested
    from uuid import UUID, uuid4

    from pydantic import BaseModel, Field

    class DataPoint(BaseModel):  # type: ignore[no-redef]
        # Mirror cognee.low_level.DataPoint's identity field so a caller-set,
        # deterministic `id` (our entity-resolution key) survives without cognee.
        id: UUID = Field(default_factory=uuid4)

    HAVE_COGNEE = False


# Defined in topological order so each relationship target already exists.


class Organization(DataPoint):
    name: str
    segment: str | None = None            # law, accounting, advisory, …
    metadata: dict = {"index_fields": ["name"]}


class Person(DataPoint):
    name: str
    role: str | None = None
    relationship_type: str | None = None  # prospect, client, contact, partner
    context: str | None = None
    works_at: Organization | None = None  # edge → Organization
    metadata: dict = {"index_fields": ["name", "context"]}


class Fact(DataPoint):
    content: str
    domain: str | None = None
    confidence: float = 1.0
    source_type: str = "unknown"          # discord, meeting, email, web
    source_ref: str | None = None
    about_people: list[Person] = []       # edges → Person
    about_orgs: list[Organization] = []   # edges → Organization
    metadata: dict = {"index_fields": ["content"]}


class Decision(DataPoint):
    title: str
    rationale: str
    domain: str | None = None
    decided_at: str | None = None         # ISO date
    related_facts: list[Fact] = []        # edges → Fact
    metadata: dict = {"index_fields": ["title", "rationale"]}


class Meeting(DataPoint):
    title: str
    summary: str
    meeting_date: str | None = None       # ISO date
    participants: list[Person] = []       # edges → Person
    produced_facts: list[Fact] = []       # edges → Fact
    produced_decisions: list[Decision] = []
    metadata: dict = {"index_fields": ["title", "summary"]}


class ICPSignal(DataPoint):
    signal_text: str
    pain_category: str | None = None
    segment_hint: str | None = None
    about_org: Organization | None = None  # edge → Organization
    metadata: dict = {"index_fields": ["signal_text"]}


class ContentItem(DataPoint):
    url: str
    title: str
    summary: str | None = None
    mentions_signals: list[ICPSignal] = []  # edges → ICPSignal
    about_orgs: list[Organization] = []     # edges → Organization (Track O Part 1)
    metadata: dict = {"index_fields": ["title", "summary"]}


class InterestSignal(DataPoint):
    topic_label: str
    weight: float = 1.0
    metadata: dict = {"index_fields": ["topic_label"]}


# The knowledge-plane entity types, in dependency order.
ENTITIES: list[type[DataPoint]] = [
    Organization, Person, Fact, Decision, Meeting, ICPSignal, ContentItem, InterestSignal,
]


def index_fields(cls: type[DataPoint]) -> list[str]:
    """The `metadata['index_fields']` declared on a DataPoint subclass."""
    return cls.model_fields["metadata"].default["index_fields"]
