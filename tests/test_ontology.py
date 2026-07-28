"""Structural tests for the domain ontology (W3).

Exercises the DataPoint classes via the pydantic stand-in (cognee not required):
instantiation, relationship fields, nested/multi-hop construction, and that
every declared index_field is a real model field.
"""

from __future__ import annotations

from agents._lib import ontology as o


def test_entities_list_is_the_eight_knowledge_types():
    names = [c.__name__ for c in o.ENTITIES]
    assert names == [
        "Organization", "Person", "Fact", "Decision",
        "Meeting", "ICPSignal", "ContentItem", "InterestSignal",
    ]


def test_person_works_at_organization_edge():
    org = o.Organization(name="Two Rivers Advisory", segment="advisory")
    p = o.Person(name="Elena Ruiz", role="Managing Partner", works_at=org)
    assert p.works_at is org
    assert p.works_at.name == "Two Rivers Advisory"


def test_person_without_org_is_valid():
    p = o.Person(name="Nobody")
    assert p.works_at is None
    assert p.relationship_type is None


def test_fact_defaults_and_entity_edges():
    org = o.Organization(name="Beacon Legal")
    sarah = o.Person(name="Sarah")
    f = o.Fact(content="Beacon wants contract intake triage removed.",
               source_type="meeting", about_people=[sarah], about_orgs=[org])
    assert f.confidence == 1.0
    assert f.about_people[0].name == "Sarah"
    assert f.about_orgs[0].name == "Beacon Legal"


def test_meeting_two_hop_structure():
    """Meeting → participant → works_at is a real 2-hop chain in the model."""
    org = o.Organization(name="Two Rivers Advisory")
    elena = o.Person(name="Elena Ruiz", works_at=org)
    fact = o.Fact(content="Wants discovery notes turned into summaries.", source_type="meeting")
    m = o.Meeting(
        title="Discovery call — Two Rivers",
        summary="Two pains: slow client summaries, no cross-call memory.",
        participants=[elena], produced_facts=[fact],
    )
    assert m.participants[0].works_at.name == "Two Rivers Advisory"  # 2 hops
    assert m.produced_facts[0].content.startswith("Wants discovery")


def test_decision_relates_facts():
    f = o.Fact(content="Lead with one workflow, not the platform.", source_type="discord")
    d = o.Decision(title="Positioning: one workflow", rationale="Trust is earned per win.",
                   related_facts=[f])
    assert d.related_facts[0] is f


def test_icp_signal_and_content_item_edges():
    org = o.Organization(name="Harbor CPA")
    sig = o.ICPSignal(signal_text="Month-end reconciliation exceptions are manual.",
                      pain_category="reconciliation", about_org=org)
    item = o.ContentItem(url="https://x", title="AI for SMB advisories",
                         mentions_signals=[sig])
    assert sig.about_org.name == "Harbor CPA"
    assert item.mentions_signals[0].pain_category == "reconciliation"


def test_index_fields_are_real_model_fields():
    for cls in o.ENTITIES:
        for field in o.index_fields(cls):
            assert field in cls.model_fields, \
                f"{cls.__name__}.index_fields → unknown field {field!r}"


def test_index_fields_target_text_properties_not_edges():
    # index_fields should be embeddable text/props, never relationship fields.
    edge_names = {"works_at", "about_people", "about_orgs", "related_facts",
                  "participants", "produced_facts", "produced_decisions",
                  "about_org", "mentions_signals"}
    for cls in o.ENTITIES:
        assert not (set(o.index_fields(cls)) & edge_names)
