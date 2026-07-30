"""Classification stage: OpenAI Structured Outputs.

Sends the extracted text to the model with ``Classification`` as a strict
``json_schema`` response format, so the return value is always a validated
``Classification`` object — never free-form text to parse.
"""

from __future__ import annotations

import os

from openai import OpenAI

from .config import DOMAINS, Inbox, settings
from .extract import image_data_url
from .models import Classification, RawDocument

_SYSTEM_PROMPT = f"""\
당신은 한국어 개인 지식 관리 시스템(Personal Knowledge OS)의 자료 분류기입니다.
주어진 자료 본문을 읽고 아래를 추출하세요.

1. title: 자료를 대표하는 간결한 제목.
2. summary: 3~5문장의 한국어 요약.
3. material_type: 자료의 형식.
4. domain: 다음 세 도메인 중 가장 적합한 하나만 선택 — {", ".join(DOMAINS)}.
   - "AI·기술": 인공지능, 소프트웨어, 하드웨어, 공학, 자연과학.
   - "인문예술": 철학, 역사, 문학, 예술, 언어, 문화.
   - "사회경제": 경제, 경영, 정치, 사회, 법, 정책.
5. topics: 업무/개인/연구 중 해당되는 것(복수 가능, 없으면 빈 배열).
6. concepts: 자료의 핵심 개념. 각 개념은 이름/정의/별칭.
7. entities: 등장하는 고유명사. 각 엔터티는 이름/유형/설명.
8. claims: 자료가 제시하는 검증 가능한 주장. 각 주장은 근거와 신뢰도(0~1)를 포함.
9. confidence: 이 분류 전체에 대한 종합 신뢰도(0~1). 본문이 짧거나 모호하면 낮게,
   근거가 분명하고 추출이 확실하면 높게 매기세요.

과도하게 많은 항목을 만들지 말고, 자료에서 실제로 뒷받침되는 것만 추출하세요.
불확실하면 신뢰도를 낮추고 추측성 항목은 제외하세요.
"""


def _client() -> OpenAI:
    return OpenAI(api_key=settings.require_openai())


def _empty(doc: RawDocument, message: str) -> Classification:
    """Low-confidence placeholder so the pipeline routes to 검토, never crashes."""
    return Classification(
        title=doc.title or "빈 자료",
        summary=message,
        material_type=doc.material_type,
        domain=DOMAINS[0],
        topics=[],
        concepts=[],
        entities=[],
        claims=[],
        confidence=0.0,
    )


def _parse(messages: list) -> Classification:
    completion = _client().beta.chat.completions.parse(
        model=settings.openai_model,
        messages=messages,
        response_format=Classification,
        temperature=0.2,
    )
    parsed = completion.choices[0].message.parsed
    if parsed is None:  # refusal or empty parse
        raise RuntimeError("Classifier returned no parsed output.")
    return parsed


def _classify_image(doc: RawDocument) -> Classification:
    """OCR + classify an image in one vision call (needs a vision model)."""
    if doc.source_url and doc.source_url.startswith(("http://", "https://")):
        image_url = doc.source_url
    elif doc.local_path and os.path.exists(doc.local_path):
        image_url = image_data_url(doc.local_path)
    else:
        return _empty(doc, "이미지를 불러오지 못했습니다.")

    user_content = [
        {
            "type": "text",
            "text": (
                "다음 이미지의 모든 텍스트와 시각 정보를 읽고(OCR 포함) 분류하세요. "
                "material_type 은 '이미지'로 두세요.\n"
                f"[원문 URL] {doc.source_url or '없음'}"
            ),
        },
        {"type": "image_url", "image_url": {"url": image_url}},
    ]
    return _parse(
        [
            {"role": "system", "content": _SYSTEM_PROMPT},
            {"role": "user", "content": user_content},
        ]
    )


def classify(doc: RawDocument) -> Classification:
    """Classify a ``RawDocument`` into a strict ``Classification`` object."""
    if doc.material_type == Inbox.TYPE_IMAGE and (doc.local_path or doc.source_url):
        return _classify_image(doc)

    body = doc.text.strip()
    if not body:
        # Nothing to work with; route to 검토 rather than crashing.
        return _empty(doc, "본문을 추출하지 못했습니다.")

    body = body[: settings.max_content_chars]
    user_prompt = (
        f"[자료 형식] {doc.material_type}\n"
        f"[원문 URL] {doc.source_url or '없음'}\n"
        f"[추출 제목] {doc.title}\n\n"
        f"[본문]\n{body}"
    )
    return _parse(
        [
            {"role": "system", "content": _SYSTEM_PROMPT},
            {"role": "user", "content": user_prompt},
        ]
    )
