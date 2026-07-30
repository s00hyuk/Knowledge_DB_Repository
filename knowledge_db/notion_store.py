"""Storage stage: all reads/writes against the four Notion databases.

Responsibilities
- queue mechanics: lease-claim a 대기 Inbox item, reclaim stale leases, and
  finalize items (완료/검토/실패);
- upsert Concepts/Entities (dedupe by title) and create Claims;
- wire the relations (개념/엔터티/주장 on Inbox, 관련 자료/원본 자료 on the
  children) so the graph is linked from both sides regardless of relation sync;
- attach the original file (uploaded for local files, external link for URLs).

Uses ``notion-client`` for page CRUD and the raw File Upload REST endpoint for
attachments (not yet wrapped by the SDK).
"""

from __future__ import annotations

import logging
import mimetypes
import os
from datetime import datetime, timedelta, timezone

import requests
from notion_client import Client

from .config import PROCESSING_VERSION, Claims, Concepts, Entities, Inbox, settings
from .models import Classification, RawDocument

log = logging.getLogger("knowledge_db.notion")

NOTION_VERSION = "2022-06-28"
_RICH_TEXT_LIMIT = 2000
_UPLOAD_LIMIT_BYTES = 20 * 1024 * 1024  # single-part file upload cap


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat()


# --------------------------------------------------------------------------- #
# Property builders
# --------------------------------------------------------------------------- #
def _rich_text(value: str | None) -> dict:
    value = (value or "").strip()
    if not value:
        return {"rich_text": []}
    chunks = [value[i : i + _RICH_TEXT_LIMIT] for i in range(0, len(value), _RICH_TEXT_LIMIT)]
    return {"rich_text": [{"type": "text", "text": {"content": c}} for c in chunks]}


def _title(value: str) -> dict:
    return {"title": [{"type": "text", "text": {"content": (value or "제목 없음")[:2000]}}]}


def _select(value: str | None) -> dict:
    return {"select": {"name": value} if value else None}


def _multi_select(values: list[str]) -> dict:
    return {"multi_select": [{"name": v} for v in dict.fromkeys(v for v in values if v)]}


def _relation(ids: list[str]) -> dict:
    return {"relation": [{"id": pid} for pid in dict.fromkeys(ids)]}


def _plain(prop: dict | None) -> str:
    """Read a title/rich_text property value back to a plain string."""
    if not prop:
        return ""
    parts = prop.get("title") or prop.get("rich_text") or []
    return "".join(p.get("plain_text", "") for p in parts)


class NotionStore:
    def __init__(self) -> None:
        self.token = settings.require_notion()
        self.client = Client(auth=self.token)
        # database_id -> data_source_id, for the newer data-sources query API.
        self._ds = {
            settings.inbox_db: settings.inbox_ds,
            settings.concepts_db: settings.concepts_ds,
            settings.entities_db: settings.entities_ds,
            settings.claims_db: settings.claims_ds,
        }

    def _query(self, db_id: str, **kwargs):
        """Query a table, compatible with both notion-client generations.

        Newer notion-client (Notion 2025-09-03) dropped ``databases.query`` in
        favor of ``data_sources.query``; older versions only have the former.
        """
        if hasattr(self.client, "data_sources"):
            return self.client.data_sources.query(self._ds[db_id], **kwargs)
        return self.client.databases.query(database_id=db_id, **kwargs)

    # ----------------------------------------------------------------- queue
    def claim_next(self) -> dict | None:
        """Lease the next processable Inbox item, or return None if idle.

        Preference order: oldest 대기 item, then a stale 처리중 item whose lease
        has expired (crash recovery). The claim stamps Worker ID + Lease Until
        and flips 상태 -> 처리중.
        """
        # Pending = 상태 '대기' OR 상태 미설정(빈 값). AI 처리 허용 체크박스는 더 이상
        # 클레임 조건이 아니며, 민감 자료는 파이프라인에서 자동 제외된다(process_item).
        page = self._query_first(
            filter={
                "or": [
                    {"property": Inbox.STATUS, "select": {"equals": Inbox.ST_PENDING}},
                    {"property": Inbox.STATUS, "select": {"is_empty": True}},
                ]
            },
            sorts=[{"timestamp": "created_time", "direction": "ascending"}],
        )
        if page is None:
            page = self._query_first(
                filter={
                    "and": [
                        {"property": Inbox.STATUS, "select": {"equals": Inbox.ST_PROCESSING}},
                        {"property": Inbox.LEASE_UNTIL, "date": {"on_or_before": _iso(_now())}},
                    ]
                },
            )
        if page is None:
            return None

        lease_until = _now() + timedelta(seconds=settings.lease_seconds)
        self.client.pages.update(
            page_id=page["id"],
            properties={
                Inbox.STATUS: _select(Inbox.ST_PROCESSING),
                Inbox.WORKER_ID: _rich_text(settings.worker_id),
                Inbox.LEASE_UNTIL: {"date": {"start": _iso(lease_until)}},
            },
        )
        return self._inbox_view(page)

    def _query_first(self, filter: dict, sorts: list | None = None) -> dict | None:
        kwargs: dict = {"filter": filter, "page_size": 1}
        if sorts:
            kwargs["sorts"] = sorts
        result = self._query(settings.inbox_db, **kwargs)
        results = result.get("results", [])
        return results[0] if results else None

    @staticmethod
    def _inbox_view(page: dict) -> dict:
        props = page.get("properties", {})

        def num(name: str) -> float | None:
            return (props.get(name) or {}).get("number")

        return {
            "id": page["id"],
            "url": page.get("url"),
            "title": _plain(props.get(Inbox.TITLE)),
            "summary_text": _plain(props.get(Inbox.SUMMARY)),
            "source_url": (props.get(Inbox.SOURCE_URL) or {}).get("url"),
            "access": ((props.get(Inbox.ACCESS) or {}).get("select") or {}).get("name"),
            "files": (props.get(Inbox.FILE) or {}).get("files", []),
            "retries": int(num(Inbox.RETRIES) or 0),
        }

    # -------------------------------------------------------------- finalize
    def finalize_success(
        self,
        inbox_id: str,
        classification: Classification,
        doc: RawDocument,
        concept_ids: list[str],
        entity_ids: list[str],
        claim_ids: list[str],
        set_title: str | None = None,
    ) -> str:
        status = (
            Inbox.ST_DONE
            if classification.confidence >= settings.confidence_threshold
            else Inbox.ST_REVIEW
        )
        props: dict = {
            Inbox.SUMMARY: _rich_text(classification.summary),
            Inbox.MATERIAL_TYPE: _select(classification.material_type),
            Inbox.TOPICS: _multi_select(list(classification.topics)),
            Inbox.STATUS: _select(status),
            Inbox.HASH: _rich_text(doc.content_hash),
            Inbox.VERSION: _rich_text(PROCESSING_VERSION),
            Inbox.ERROR: {"rich_text": []},
            Inbox.WORKER_ID: {"rich_text": []},
            Inbox.LEASE_UNTIL: {"date": None},
            Inbox.REL_CONCEPTS: _relation(concept_ids),
            Inbox.REL_ENTITIES: _relation(entity_ids),
            Inbox.REL_CLAIMS: _relation(claim_ids),
        }
        # Auto-title: only when the row had no meaningful title (empty or the URL).
        if set_title:
            props[Inbox.TITLE] = _title(set_title)
        if doc.source_url and not self._has_files(inbox_id):
            props[Inbox.SOURCE_URL] = {"url": doc.source_url}

        self.client.pages.update(page_id=inbox_id, properties=props)
        return status

    def route_to_review(self, inbox_id: str, note: str) -> str:
        """Clear the lease and route an item to 검토 without AI (e.g. 민감 자료)."""
        self.client.pages.update(
            page_id=inbox_id,
            properties={
                Inbox.STATUS: _select(Inbox.ST_REVIEW),
                Inbox.ERROR: _rich_text(note[:1900]),
                Inbox.VERSION: _rich_text(PROCESSING_VERSION),
                Inbox.WORKER_ID: {"rich_text": []},
                Inbox.LEASE_UNTIL: {"date": None},
            },
        )
        return Inbox.ST_REVIEW

    # ------------------------------------------------------------- dedup
    def find_duplicate(self, content_hash: str, exclude_id: str) -> dict | None:
        """Return an already-processed Inbox item sharing this content hash.

        Only processed items carry a 콘텐츠 해시, so a hash match inherently
        means the content was ingested before (not another pending row).
        """
        if not content_hash:
            return None
        result = self._query(
            settings.inbox_db,
            filter={"property": Inbox.HASH, "rich_text": {"equals": content_hash}},
            page_size=5,
        )
        for page in result.get("results", []):
            if page["id"] == exclude_id:
                continue
            props = page["properties"]
            return {
                "id": page["id"],
                "url": page.get("url"),
                "title": _plain(props.get(Inbox.TITLE)),
                "status": ((props.get(Inbox.STATUS) or {}).get("select") or {}).get("name"),
            }
        return None

    def finalize_duplicate(self, inbox_id: str, content_hash: str, original: dict) -> str:
        """Route a duplicate to 검토 without AI, noting the original item."""
        ref = " ".join(x for x in (original.get("title"), original.get("url")) if x)
        note = f"중복 자료 — 원본: {ref}".strip()
        self.client.pages.update(
            page_id=inbox_id,
            properties={
                Inbox.STATUS: _select(Inbox.ST_REVIEW),
                Inbox.HASH: _rich_text(content_hash),
                Inbox.ERROR: _rich_text(note[:1900]),
                Inbox.VERSION: _rich_text(PROCESSING_VERSION),
                Inbox.WORKER_ID: {"rich_text": []},
                Inbox.LEASE_UNTIL: {"date": None},
            },
        )
        return Inbox.ST_REVIEW

    def mark_failed(self, inbox_id: str, error: str, retries: int) -> str:
        """Bump retry count; back to 대기 if retries remain, else 실패."""
        next_retries = retries + 1
        status = Inbox.ST_PENDING if next_retries < settings.max_retries else Inbox.ST_FAILED
        self.client.pages.update(
            page_id=inbox_id,
            properties={
                Inbox.STATUS: _select(status),
                Inbox.ERROR: _rich_text(error[:1900]),
                Inbox.RETRIES: {"number": next_retries},
                Inbox.WORKER_ID: {"rich_text": []},
                Inbox.LEASE_UNTIL: {"date": None},
            },
        )
        return status

    def _has_files(self, inbox_id: str) -> bool:
        page = self.client.pages.retrieve(page_id=inbox_id)
        return bool((page["properties"].get(Inbox.FILE) or {}).get("files"))

    # --------------------------------------------------------------- upsert
    def _query_title_equals(self, database_id: str, title_prop: str, value: str) -> str | None:
        result = self._query(
            database_id,
            filter={"property": title_prop, "title": {"equals": value}},
            page_size=1,
        )
        results = result.get("results", [])
        return results[0]["id"] if results else None

    def _query_alias_contains(self, database_id: str, alias_prop: str, value: str) -> str | None:
        result = self._query(
            database_id,
            filter={"property": alias_prop, "multi_select": {"contains": value}},
            page_size=1,
        )
        results = result.get("results", [])
        return results[0]["id"] if results else None

    def _match_page(
        self, database_id: str, title_prop: str, alias_prop: str, name: str, aliases: list[str]
    ) -> tuple[str | None, str | None]:
        """Find an existing page for a concept/entity via bounded queries.

        Returns ``(page_id, via)`` where ``via`` is ``"title"`` (exact name
        match, a confident reuse) or ``"alias"`` (matched through an alias — a
        likely-but-uncertain duplicate, so the caller flags it 병합 후보).
        Order: exact title -> existing 별칭 contains name -> each new alias vs.
        existing titles/별칭.
        """
        exact = self._query_title_equals(database_id, title_prop, name)
        if exact:
            return exact, "title"
        hit = self._query_alias_contains(database_id, alias_prop, name)
        if hit:
            return hit, "alias"
        for alias in list(aliases)[:5]:
            if not alias:
                continue
            hit = self._query_title_equals(database_id, title_prop, alias)
            if hit:
                return hit, "alias"
            hit = self._query_alias_contains(database_id, alias_prop, alias)
            if hit:
                return hit, "alias"
        return None, None

    def _mark_merge_candidate(
        self, page_id: str, review_prop: str, merge_value: str, alias_prop: str, add_alias: str
    ) -> None:
        """Flag a reused page as 병합 후보 and record the new surface form as an alias."""
        props: dict = {review_prop: _select(merge_value)}
        try:
            page = self.client.pages.retrieve(page_id=page_id)
            current = [
                o["name"] for o in (page["properties"].get(alias_prop) or {}).get("multi_select", [])
            ]
            if add_alias and add_alias not in current:
                props[alias_prop] = _multi_select(current + [add_alias])
        except Exception as exc:  # noqa: BLE001 - still set the review flag
            log.warning("Could not read aliases for %s: %s", page_id, exc)
        self.client.pages.update(page_id=page_id, properties=props)

    def upsert_concept(self, concept, domain: str, inbox_id: str) -> str:
        match_id, via = self._match_page(
            settings.concepts_db, Concepts.TITLE, Concepts.ALIASES, concept.name, concept.aliases
        )
        if match_id:
            self._link_child_to_inbox(match_id, Concepts.REL_INBOX, inbox_id)
            if via == "alias":
                self._mark_merge_candidate(
                    match_id, Concepts.REVIEW, Concepts.RV_MERGE, Concepts.ALIASES, concept.name
                )
            return match_id
        page = self.client.pages.create(
            parent={"database_id": settings.concepts_db},
            properties={
                Concepts.TITLE: _title(concept.name),
                Concepts.DEFINITION: _rich_text(concept.definition),
                Concepts.FIELD: _multi_select([domain]),
                Concepts.ALIASES: _multi_select(list(concept.aliases)),
                Concepts.REVIEW: _select(Concepts.RV_PROPOSED),
                Concepts.REL_INBOX: _relation([inbox_id]),
            },
        )
        return page["id"]

    def upsert_entity(self, entity, inbox_id: str) -> str:
        match_id, via = self._match_page(
            settings.entities_db, Entities.TITLE, Entities.ALIASES, entity.name, entity.aliases
        )
        if match_id:
            self._link_child_to_inbox(match_id, Entities.REL_INBOX, inbox_id)
            if via == "alias":
                self._mark_merge_candidate(
                    match_id, Entities.REVIEW, Entities.RV_MERGE, Entities.ALIASES, entity.name
                )
            return match_id
        props = {
            Entities.TITLE: _title(entity.name),
            Entities.TYPE: _select(entity.type),
            Entities.DESCRIPTION: _rich_text(entity.description),
            Entities.ALIASES: _multi_select(list(entity.aliases)),
            Entities.REVIEW: _select(Entities.RV_PROPOSED),
            Entities.REL_INBOX: _relation([inbox_id]),
        }
        if entity.official_url:
            props[Entities.OFFICIAL_URL] = {"url": entity.official_url}
        page = self.client.pages.create(
            parent={"database_id": settings.entities_db}, properties=props
        )
        return page["id"]

    def create_claim(self, claim, inbox_id: str) -> str:
        verify = (
            Claims.VF_CONFIRMED
            if claim.confidence >= settings.confidence_threshold
            else Claims.VF_UNREVIEWED
        )
        page = self.client.pages.create(
            parent={"database_id": settings.claims_db},
            properties={
                Claims.TITLE: _title(claim.statement),
                Claims.EVIDENCE: _rich_text(claim.evidence),
                Claims.EVIDENCE_LOC: _rich_text(claim.evidence_location),
                Claims.RELATION: _select(claim.relation),
                Claims.CONFIDENCE: {"number": round(float(claim.confidence), 3)},
                Claims.VERIFY: _select(verify),
                Claims.REL_INBOX: _relation([inbox_id]),
            },
        )
        return page["id"]

    def _link_child_to_inbox(self, child_id: str, rel_prop: str, inbox_id: str) -> None:
        """Append the inbox id to a reused child's back-relation (idempotent)."""
        try:
            page = self.client.pages.retrieve(page_id=child_id)
            current = [r["id"] for r in (page["properties"].get(rel_prop) or {}).get("relation", [])]
            if inbox_id in current:
                return
            self.client.pages.update(
                page_id=child_id,
                properties={rel_prop: _relation(current + [inbox_id])},
            )
        except Exception as exc:  # noqa: BLE001 - relation sync may cover this already
            log.warning("Could not back-link child %s: %s", child_id, exc)

    # --------------------------------------------------------------- files
    def attach_original(self, inbox_id: str, doc: RawDocument) -> None:
        """Attach the original file to 원본 파일 (upload local, link external)."""
        try:
            if doc.local_path and os.path.exists(doc.local_path):
                file_value = self._upload_and_ref(doc.local_path)
            elif doc.source_url:
                name = os.path.basename(doc.source_url.split("?")[0]) or doc.title
                file_value = {
                    "type": "external",
                    "name": name[:100],
                    "external": {"url": doc.source_url},
                }
            else:
                return
            if file_value:
                self.client.pages.update(
                    page_id=inbox_id, properties={Inbox.FILE: {"files": [file_value]}}
                )
        except Exception as exc:  # noqa: BLE001 - attachment is best-effort
            log.warning("Could not attach original for %s: %s", inbox_id, exc)

    def _upload_and_ref(self, path: str) -> dict | None:
        size = os.path.getsize(path)
        if size > _UPLOAD_LIMIT_BYTES:
            log.warning("File %s (%d bytes) exceeds single-part upload cap; skipping.", path, size)
            return None
        name = os.path.basename(path)
        mime = mimetypes.guess_type(path)[0] or "application/octet-stream"
        headers = {
            "Authorization": f"Bearer {self.token}",
            "Notion-Version": NOTION_VERSION,
        }
        create = requests.post(
            "https://api.notion.com/v1/file_uploads",
            headers={**headers, "Content-Type": "application/json"},
            json={"filename": name, "content_type": mime},
            timeout=30,
        )
        create.raise_for_status()
        payload = create.json()
        upload_url, file_upload_id = payload["upload_url"], payload["id"]

        with open(path, "rb") as fh:
            send = requests.post(
                upload_url, headers=headers, files={"file": (name, fh, mime)}, timeout=120
            )
        send.raise_for_status()
        return {"type": "file_upload", "name": name[:100], "file_upload": {"id": file_upload_id}}

    # --------------------------------------------------------------- ingest
    def create_inbox_item(
        self,
        title: str,
        source_url: str | None = None,
        inline_text: str | None = None,
        local_path: str | None = None,
        material_type: str | None = None,
        access: str | None = None,
        topics: list[str] | None = None,
        ai_allowed: bool = True,
        status: str | None = None,
    ) -> dict:
        props: dict = {
            Inbox.TITLE: _title(title),
            Inbox.STATUS: _select(status or Inbox.ST_PENDING),
            Inbox.AI_ALLOWED: {"checkbox": ai_allowed},
            Inbox.RETRIES: {"number": 0},
        }
        if source_url:
            props[Inbox.SOURCE_URL] = {"url": source_url}
        if inline_text:
            props[Inbox.SUMMARY] = _rich_text(inline_text[:1900])
        if material_type:
            props[Inbox.MATERIAL_TYPE] = _select(material_type)
        if access:
            props[Inbox.ACCESS] = _select(access)
        if topics:
            props[Inbox.TOPICS] = _multi_select(list(topics))
        page = self.client.pages.create(
            parent={"database_id": settings.inbox_db}, properties=props
        )
        created = {"id": page["id"], "url": page.get("url")}

        # Preserve the original immediately at ingest, before any processing.
        if local_path or source_url:
            self.attach_original(
                created["id"],
                RawDocument(
                    title=title,
                    text="",
                    material_type=material_type or Inbox.TYPE_DOC,
                    source_url=source_url,
                    local_path=local_path,
                ),
            )
        return created
