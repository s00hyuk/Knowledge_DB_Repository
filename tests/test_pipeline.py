"""Lightweight unit tests that do not touch Notion or OpenAI."""

from knowledge_db import config
from knowledge_db.extract import content_hash, extract_text
from knowledge_db.models import Classification, ConceptOut


def test_content_hash_is_stable_and_whitespace_insensitive():
    assert content_hash("  hello world  ") == content_hash("hello world")
    assert content_hash("a") != content_hash("b")
    assert len(content_hash("x")) == 64


def test_extract_text_infers_title_from_first_line():
    doc = extract_text("첫 줄 제목\n본문 내용입니다.")
    assert doc.title == "첫 줄 제목"
    assert doc.material_type == config.Inbox.TYPE_TEXT
    assert doc.content_hash


def test_domains_match_schema_and_model_enum():
    assert config.DOMAINS == ["AI·기술", "인문예술", "사회경제"]
    # The classifier enum must stay in lockstep with the config domains.
    domain_enum = Classification.model_json_schema()["properties"]["domain"]["enum"]
    assert set(domain_enum) == set(config.DOMAINS)


def test_classification_roundtrip():
    c = Classification(
        title="테스트",
        summary="요약",
        material_type="텍스트",
        domain="AI·기술",
        topics=["연구"],
        concepts=[ConceptOut(name="트랜스포머", definition="어텐션 기반 모델", aliases=["Transformer"])],
        entities=[],
        claims=[],
        confidence=0.9,
    )
    assert c.confidence >= config.settings.confidence_threshold
    assert c.concepts[0].name == "트랜스포머"
