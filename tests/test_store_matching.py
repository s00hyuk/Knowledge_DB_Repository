"""Tests for the concept/entity match logic (stubbed Notion client, no network)."""

from knowledge_db.config import Concepts
from knowledge_db.notion_store import NotionStore


class _FakeDatabases:
    def __init__(self, responder):
        self._responder = responder

    def query(self, **kwargs):
        return self._responder(kwargs)


class _FakePages:
    def __init__(self):
        self.updates = []

    def retrieve(self, page_id):
        return {"properties": {}}

    def update(self, **kwargs):
        self.updates.append(kwargs)


class _FakeClient:
    def __init__(self, responder):
        self.databases = _FakeDatabases(responder)
        self.pages = _FakePages()


def _make_store(responder):
    store = NotionStore.__new__(NotionStore)  # bypass __init__ (no token needed)
    store.token = "test"
    store.client = _FakeClient(responder)
    return store


def _hit(page_id):
    return {"results": [{"id": page_id}]}


def _title_equals(kwargs):
    f = kwargs["filter"]
    return "title" in f and f["title"].get("equals")


def _alias_contains(kwargs):
    f = kwargs["filter"]
    return "multi_select" in f and f["multi_select"].get("contains")


def test_exact_title_match_is_not_a_merge_candidate():
    def responder(kwargs):
        if _title_equals(kwargs) == "트랜스포머":
            return _hit("concept-1")
        return {"results": []}

    store = _make_store(responder)
    page_id, via = store._match_page(
        "db", Concepts.TITLE, Concepts.ALIASES, "트랜스포머", ["Transformer"]
    )
    assert page_id == "concept-1"
    assert via == "title"


def test_alias_contains_name_flags_merge_candidate():
    def responder(kwargs):
        # No exact title, but an existing page lists the name as an alias.
        if _alias_contains(kwargs) == "어텐션":
            return _hit("concept-2")
        return {"results": []}

    store = _make_store(responder)
    page_id, via = store._match_page("db", Concepts.TITLE, Concepts.ALIASES, "어텐션", [])
    assert page_id == "concept-2"
    assert via == "alias"


def test_no_match_returns_none():
    store = _make_store(lambda kwargs: {"results": []})
    page_id, via = store._match_page("db", Concepts.TITLE, Concepts.ALIASES, "신규개념", ["별칭1"])
    assert page_id is None
    assert via is None
