"""Classification stage: OpenAI Structured Outputs.

Sends the extracted text to the model with ``Classification`` as a strict
``json_schema`` response format, so the return value is always a validated
``Classification`` object — never free-form text to parse.
"""

from __future__ import annotations

from openai import OpenAI

from .config import DOMAINS, settings
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


def classify(doc: RawDocument) -> Classification:
    """Classify a ``RawDocument`` into a strict ``Classification`` object."""
    body = doc.text.strip()
    if not body:
        # Nothing to work with; return a low-confidence empty classification so
        # the pipeline routes it to 검토 rather than crashing.
        return Classification(
            title=doc.title or "빈 자료",
            summary="본문을 추출하지 못했습니다.",
            material_type=doc.material_type,
            domain=DOMAINS[0],
            topics=[],
            concepts=[],
            entities=[],
            claims=[],
            confidence=0.0,
        )

    body = body[: settings.max_content_chars]
    user_prompt = (
        f"[자료 형식] {doc.material_type}\n"
        f"[원문 URL] {doc.source_url or '없음'}\n"
        f"[추출 제목] {doc.title}\n\n"
        f"[본문]\n{body}"
    )

    completion = _client().beta.chat.completions.parse(
        model=settings.openai_model,
        messages=[
            {"role": "system", "content": _SYSTEM_PROMPT},
            {"role": "user", "content": user_prompt},
        ],
        response_format=Classification,
        temperature=0.2,
    )

    parsed = completion.choices[0].message.parsed
    if parsed is None:  # refusal or empty parse
        raise RuntimeError("Classifier returned no parsed output.")
    # Keep the extractor's material_type if the model guessed something odd but
    # trust the model when the extractor only had a generic guess (텍스트/문서).
    return parsed
