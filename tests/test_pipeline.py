"""Lightweight unit tests that do not touch Notion or OpenAI."""

from knowledge_db import config
from knowledge_db.config import access_allows_ai
from knowledge_db.extract import (
    content_hash,
    detect_source_kind,
    extract_file,
    extract_text,
    guess_material_type,
)
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


def test_detect_source_kind(tmp_path):
    assert detect_source_kind("https://example.com/a") == "url"
    assert detect_source_kind("http://x.io") == "url"
    f = tmp_path / "note.txt"
    f.write_text("hi", encoding="utf-8")
    assert detect_source_kind(str(f)) == "file"
    assert detect_source_kind("그냥 텍스트 메모") == "text"


def test_guess_material_type():
    assert guess_material_type("https://x.com/a.pdf", "url") == config.Inbox.TYPE_PDF
    assert guess_material_type("https://x.com/a", "url") == config.Inbox.TYPE_WEB
    assert guess_material_type("https://x.com/pic.png?w=1", "url") == config.Inbox.TYPE_IMAGE
    assert guess_material_type("/tmp/a.pdf", "file") == config.Inbox.TYPE_PDF
    assert guess_material_type("/tmp/a.png", "file") == config.Inbox.TYPE_IMAGE
    assert guess_material_type("메모", "text") == config.Inbox.TYPE_TEXT


def test_extract_image_file_is_empty_text_with_byte_hash(tmp_path):
    img = tmp_path / "shot.png"
    img.write_bytes(b"\x89PNG\r\n\x1a\n fake-image-bytes")
    doc = extract_file(str(img))
    assert doc.material_type == config.Inbox.TYPE_IMAGE
    assert doc.text == ""              # OCR happens later in the vision call
    assert doc.local_path == str(img)
    assert len(doc.content_hash) == 64  # hashed from bytes, enables dedup


def test_access_policy_blocks_sensitive():
    assert access_allows_ai("일반") is True
    assert access_allows_ai("개인") is True
    assert access_allows_ai(None) is True
    assert access_allows_ai("민감") is False


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
