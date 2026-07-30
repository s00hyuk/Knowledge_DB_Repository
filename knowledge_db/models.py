"""Pydantic schemas.

``Classification`` is the strict schema handed to OpenAI Structured Outputs, so
every field is required (nullable where a value may be absent) and the domain /
type / relation enums exactly match the Notion select options in ``config``.
``RawDocument`` is the plain container produced by the extract module.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Optional

from pydantic import BaseModel, Field

Domain = Literal["AI·기술", "인문예술", "사회경제"]
MaterialType = Literal["텍스트", "웹페이지", "논문", "PDF", "문서", "이미지"]
Topic = Literal["업무", "개인", "연구"]
EntityType = Literal["인물", "조직", "기술", "제품", "장소", "기타"]
ClaimRelation = Literal["주장", "찬성", "반박", "보완"]


@dataclass
class RawDocument:
    """Output of the extract stage before any AI processing."""

    title: str
    text: str
    material_type: MaterialType
    source_url: Optional[str] = None
    local_path: Optional[str] = None
    content_hash: str = ""


class ConceptOut(BaseModel):
    name: str = Field(description="개념명 (짧은 명사구, 한국어)")
    definition: str = Field(description="한두 문장으로 요약한 정의")
    aliases: list[str] = Field(default_factory=list, description="동의어/약어")


class EntityOut(BaseModel):
    name: str = Field(description="고유명사 (인물/조직/기술/제품/장소 등)")
    type: EntityType
    description: str = Field(description="엔터티에 대한 한 문장 설명")
    aliases: list[str] = Field(default_factory=list)
    official_url: Optional[str] = Field(default=None, description="공식 홈페이지 URL, 없으면 null")


class ClaimOut(BaseModel):
    statement: str = Field(description="자료가 제시하는 핵심 주장 한 문장")
    evidence: str = Field(description="주장을 뒷받침하는 근거 요약")
    evidence_location: str = Field(description="근거의 위치(섹션/페이지/문단), 모르면 빈 문자열")
    relation: ClaimRelation = Field(description="자료가 이 주장에 대해 취하는 태도")
    confidence: float = Field(ge=0.0, le=1.0, description="이 주장의 신뢰도 0~1")


class Classification(BaseModel):
    """Strict Structured-Output schema returned by the classifier."""

    title: str = Field(description="자료를 대표하는 간결한 제목")
    summary: str = Field(description="3~5문장 한국어 요약")
    material_type: MaterialType
    domain: Domain = Field(description="세 도메인 중 가장 적합한 하나")
    topics: list[Topic] = Field(default_factory=list)
    concepts: list[ConceptOut] = Field(default_factory=list)
    entities: list[EntityOut] = Field(default_factory=list)
    claims: list[ClaimOut] = Field(default_factory=list)
    confidence: float = Field(ge=0.0, le=1.0, description="분류 전체의 종합 신뢰도 0~1")
