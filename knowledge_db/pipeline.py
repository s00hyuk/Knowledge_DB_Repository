"""Orchestration: run extract -> classify -> store for a single Inbox item.

This is deliberately storage-aware but stateless — the worker owns the queue
loop; the pipeline owns turning one leased item into a fully linked, classified
record and returning the outcome.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from .classify import classify
from .config import access_allows_ai
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

    # Access policy: 민감 자료는 외부 모델로 보내지 않고 검토로 라우팅.
    access = item.get("access")
    if not access_allows_ai(access):
        note = f"접근 등급 '{access}' — AI 처리 제외, 검토로 이동."
        log.info("Skipping AI for %s (%s).", inbox_id, note)
        store.route_to_review(inbox_id, note)
        return Outcome(inbox_id, "검토", 0.0, 0, 0, 0, item.get("title") or "")

    # Extract from whichever input the row carries (URL > file > text).
    doc: RawDocument = extract_from_inbox(
        source_url=item.get("source_url"),
        local_path=item.get("local_path"),
        inline_text=item.get("summary_text"),
    )
    return process_doc(store, inbox_id, doc, original_title=item.get("title"))


def process_doc(
    store: NotionStore,
    inbox_id: str,
    doc: RawDocument,
    original_title: str | None = None,
) -> Outcome:
    """Classify an already-extracted document and store the full result."""
    # 1b. Dedup: if identical content was already processed, skip AI and route
    # the new row to 검토 with a pointer to the original.
    duplicate = store.find_duplicate(doc.content_hash, inbox_id)
    if duplicate:
        log.info(
            "Duplicate of %s (%s) — skipping AI.", duplicate.get("title"), duplicate.get("url")
        )
        status = store.finalize_duplicate(inbox_id, doc.content_hash, duplicate)
        return Outcome(inbox_id, status, 0.0, 0, 0, 0, doc.title)

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

    # Auto-title when the row had no meaningful title (blank or just the URL).
    ot = (original_title or "").strip()
    set_title = result.title if (not ot or ot == (doc.source_url or "")) else None

    # 5. Finalize the Inbox row (완료 vs 검토 by confidence threshold).
    status = store.finalize_success(
        inbox_id, result, doc, concept_ids, entity_ids, claim_ids, set_title=set_title
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
