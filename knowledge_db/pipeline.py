"""Orchestration: run extract -> classify -> store for a single Inbox item.

This is deliberately storage-aware but stateless — the worker owns the queue
loop; the pipeline owns turning one leased item into a fully linked, classified
record and returning the outcome.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from .classify import classify
from .extract import extract_from_inbox
from .models import Classification, RawDocument
from .notion_store import NotionStore

log = logging.getLogger("knowledge_db.pipeline")


@dataclass
class Outcome:
    inbox_id: str
    status: str
    confidence: float
    n_concepts: int
    n_entities: int
    n_claims: int
    title: str


def process_item(store: NotionStore, item: dict) -> Outcome:
    """Process one leased Inbox item end to end.

    ``item`` is a ``NotionStore._inbox_view`` dict. Raises on failure so the
    worker can record the error and manage retries/leases.
    """
    inbox_id = item["id"]
    log.info("Processing %s — %s", inbox_id, item.get("title") or "(무제)")

    # Extract from whichever input the row carries (URL > file > text).
    doc: RawDocument = extract_from_inbox(
        source_url=item.get("source_url"),
        local_path=item.get("local_path"),
        inline_text=item.get("summary_text"),
    )
    return process_doc(store, inbox_id, doc)


def process_doc(store: NotionStore, inbox_id: str, doc: RawDocument) -> Outcome:
    """Classify an already-extracted document and store the full result."""
    # 2. Classify with Structured Outputs.
    result: Classification = classify(doc)

    # 3. Store: upsert concepts/entities, create claims, link everything.
    concept_ids = [
        store.upsert_concept(c, result.domain, inbox_id) for c in result.concepts
    ]
    entity_ids = [store.upsert_entity(e, inbox_id) for e in result.entities]
    claim_ids = [store.create_claim(cl, inbox_id) for cl in result.claims]

    # 4. Attach the original file / source.
    store.attach_original(inbox_id, doc)

    # 5. Finalize the Inbox row (완료 vs 검토 by confidence threshold).
    status = store.finalize_success(
        inbox_id, result, doc, concept_ids, entity_ids, claim_ids
    )

    outcome = Outcome(
        inbox_id=inbox_id,
        status=status,
        confidence=result.confidence,
        n_concepts=len(concept_ids),
        n_entities=len(entity_ids),
        n_claims=len(claim_ids),
        title=result.title,
    )
    log.info(
        "Done %s — 상태=%s 신뢰도=%.2f 개념=%d 엔터티=%d 주장=%d",
        inbox_id,
        outcome.status,
        outcome.confidence,
        outcome.n_concepts,
        outcome.n_entities,
        outcome.n_claims,
    )
    return outcome
