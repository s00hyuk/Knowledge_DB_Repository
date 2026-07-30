"""Central configuration and Notion schema constants.

Everything the pipeline needs to talk to the specific "Personal Knowledge OS"
workspace lives here: env-driven settings plus the exact property names of the
four databases (Inbox / Concepts / Entities / Claims). The property-name
constants mirror the live Notion schema so the rest of the code never hard-codes
Korean strings inline.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field

from dotenv import load_dotenv

load_dotenv()

# Processing version stamped onto every item this build touches.
PROCESSING_VERSION = "1.0"

# The three fixed knowledge domains, applied to Concepts.분야.
DOMAINS = ["AI·기술", "인문예술", "사회경제"]

# Access grades whose content must never be sent to an external model.
# 민감 자료는 AI 처리에서 제외한다(로컬에만 보관, 검토로 라우팅).
AI_BLOCKED_ACCESS = {"민감"}


def access_allows_ai(access: str | None) -> bool:
    """Whether an item with this 접근 등급 may be sent to the classifier."""
    return (access or "일반") not in AI_BLOCKED_ACCESS


def _get(name: str, default: str | None = None) -> str | None:
    value = os.getenv(name)
    return value if value not in (None, "") else default


def _get_float(name: str, default: float) -> float:
    raw = _get(name)
    try:
        return float(raw) if raw is not None else default
    except ValueError:
        return default


def _get_int(name: str, default: int) -> int:
    raw = _get(name)
    try:
        return int(raw) if raw is not None else default
    except ValueError:
        return default


@dataclass(frozen=True)
class Settings:
    notion_api_key: str | None = field(default_factory=lambda: _get("NOTION_API_KEY"))
    openai_api_key: str | None = field(default_factory=lambda: _get("OPENAI_API_KEY"))
    openai_model: str = field(default_factory=lambda: _get("OPENAI_MODEL", "gpt-4o"))

    inbox_db: str = field(
        default_factory=lambda: _get("NOTION_INBOX_DB", "977c9481-f47e-4892-8b84-24a8236831aa")
    )
    concepts_db: str = field(
        default_factory=lambda: _get("NOTION_CONCEPTS_DB", "3db62bfa-b693-4159-8bc9-211e719607c7")
    )
    entities_db: str = field(
        default_factory=lambda: _get("NOTION_ENTITIES_DB", "9f4b16da-8894-4c95-86a9-28dae22a1806")
    )
    claims_db: str = field(
        default_factory=lambda: _get("NOTION_CLAIMS_DB", "3e54f8ff-cebe-45e4-a5ed-90c0ca990c4e")
    )

    confidence_threshold: float = field(default_factory=lambda: _get_float("CONFIDENCE_THRESHOLD", 0.8))
    worker_id: str = field(default_factory=lambda: _get("WORKER_ID", "main-pc"))
    lease_seconds: int = field(default_factory=lambda: _get_int("LEASE_SECONDS", 900))
    poll_interval: int = field(default_factory=lambda: _get_int("POLL_INTERVAL", 15))
    max_retries: int = field(default_factory=lambda: _get_int("MAX_RETRIES", 3))
    max_content_chars: int = field(default_factory=lambda: _get_int("MAX_CONTENT_CHARS", 24000))

    def require_notion(self) -> str:
        if not self.notion_api_key:
            raise RuntimeError("NOTION_API_KEY is not set (see .env.example).")
        return self.notion_api_key

    def require_openai(self) -> str:
        if not self.openai_api_key:
            raise RuntimeError("OPENAI_API_KEY is not set (see .env.example).")
        return self.openai_api_key


settings = Settings()


class Inbox:
    """Knowledge Inbox property names + select options."""

    TITLE = "제목"
    SUMMARY = "요약"
    SOURCE_URL = "원문 URL"
    FILE = "원본 파일"
    STATUS = "상태"
    MATERIAL_TYPE = "자료 유형"
    ACCESS = "접근 등급"
    TOPICS = "주제"
    REL_CONCEPTS = "개념"
    REL_ENTITIES = "엔터티"
    REL_CLAIMS = "주장"
    HASH = "콘텐츠 해시"
    ERROR = "오류"
    RETRIES = "재시도"
    VERSION = "처리 버전"
    WORKER_ID = "Worker ID"
    LEASE_UNTIL = "Lease Until"
    AI_ALLOWED = "AI 처리 허용"

    # 상태 options
    ST_PENDING = "대기"
    ST_PROCESSING = "처리중"
    ST_REVIEW = "검토"
    ST_DONE = "완료"
    ST_FAILED = "실패"

    # 자료 유형 options
    TYPE_TEXT = "텍스트"
    TYPE_WEB = "웹페이지"
    TYPE_PAPER = "논문"
    TYPE_PDF = "PDF"
    TYPE_DOC = "문서"
    TYPE_IMAGE = "이미지"

    MATERIAL_TYPES = [TYPE_TEXT, TYPE_WEB, TYPE_PAPER, TYPE_PDF, TYPE_DOC, TYPE_IMAGE]
    TOPIC_OPTIONS = ["업무", "개인", "연구"]
    ACCESS_OPTIONS = ["일반", "개인", "민감"]


class Concepts:
    TITLE = "개념명"
    DEFINITION = "정의"
    FIELD = "분야"
    ALIASES = "별칭"
    REVIEW = "검토 상태"
    REL_INBOX = "관련 자료"

    RV_PROPOSED = "제안"
    RV_APPROVED = "승인"
    RV_MERGE = "병합 후보"


class Entities:
    TITLE = "이름"
    TYPE = "유형"
    DESCRIPTION = "설명"
    ALIASES = "별칭"
    OFFICIAL_URL = "공식 URL"
    REVIEW = "검토 상태"
    REL_INBOX = "관련 자료"

    TYPE_OPTIONS = ["인물", "조직", "기술", "제품", "장소", "기타"]
    RV_PROPOSED = "제안"
    RV_MERGE = "병합 후보"


class Claims:
    TITLE = "주장"
    EVIDENCE = "근거"
    EVIDENCE_LOC = "근거 위치"
    RELATION = "관계"
    CONFIDENCE = "AI 신뢰도"
    VERIFY = "검증 상태"
    REL_INBOX = "원본 자료"

    RELATION_OPTIONS = ["주장", "찬성", "반박", "보완"]
    VF_UNREVIEWED = "미검토"
    VF_REVIEWING = "검토중"
    VF_CONFIRMED = "확인"
    VF_CAUTION = "주의"
