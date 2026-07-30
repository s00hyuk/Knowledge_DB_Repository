# Knowledge DB — Notion Personal Knowledge OS 파이프라인

Notion **Personal Knowledge OS**(Inbox·Concepts·Entities·Claims)에 자료를 자동으로
추출·분류·기록하는 지식DB 파이프라인입니다. 메인 PC 1대에서 도는 단일 워커가
Notion **Knowledge Inbox**를 중앙 허브로 삼아 큐를 소비합니다.

```
자료(웹/PDF/텍스트) → Inbox(대기) → [worker] → extract → classify → notion_store
                                                                        │
                                          Concepts / Entities / Claims 관계형 링크
                                          + 원본 파일 첨부 + 신뢰도 기반 상태 처리
```

## 동작 개요

1. **입력** — Notion `Knowledge Inbox`에 자료를 등록(웹 URL / PDF / 텍스트). `ingest` CLI로도 등록 가능.
2. **큐잉 & 리스** — 워커가 `상태=대기` + `AI 처리 허용` 항목을 하나씩 집어
   `Worker ID`/`Lease Until`을 찍고 `처리중`으로 전환. 크래시 시 만료된 리스를 재확보.
3. **extract** — `원문 URL`(웹/PDF), `원본 파일`, 또는 인라인 텍스트에서 본문·해시 추출.
4. **classify** — OpenAI **Structured Outputs**(strict json_schema)로 요약·도메인·개념·엔터티·주장을 구조화.
5. **notion_store** — Concepts/Entities는 제목으로 **중복 제거 후 upsert**, Claims는 생성.
   Inbox↔자식 관계를 양방향으로 연결하고 원본 파일을 첨부.
6. **규칙** — 종합 신뢰도 **0.8 이상**이면 Inbox `상태=완료`, 미만이면 `검토`.
   각 주장은 신뢰도 0.8↑이면 `검증 상태=확인`, 아니면 `미검토`.

## 도메인 (3개)

`분야`(Concepts) 값으로 다음 중 하나가 부여됩니다.

| 도메인 | 범위 |
|---|---|
| **AI·기술** | 인공지능, 소프트웨어/하드웨어, 공학, 자연과학 |
| **인문예술** | 철학, 역사, 문학, 예술, 언어, 문화 |
| **사회경제** | 경제, 경영, 정치, 사회, 법, 정책 |

## 대상 Notion 스키마

코드가 맞물리는 실제 DB(기본값은 `.env.example`에 지정, 필요 시 재정의):

- **Knowledge Inbox** `977c9481…` — 제목/요약/원문 URL/원본 파일/상태/자료 유형/접근 등급/주제/개념·엔터티·주장(relation)/콘텐츠 해시/오류/재시도/Worker ID/Lease Until/AI 처리 허용
- **Concepts** `3db62bfa…` — 개념명/정의/분야/별칭/검토 상태/관련 자료
- **Entities** `9f4b16da…` — 이름/유형/설명/별칭/공식 URL/검토 상태/관련 자료
- **Claims** `3e54f8ff…` — 주장/근거/근거 위치/관계/AI 신뢰도/검증 상태/원본 자료

## 설치

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e .
cp .env.example .env   # NOTION_API_KEY / OPENAI_API_KEY 입력
```

- Notion 내부 통합(integration) 토큰을 발급하고, `Personal Knowledge OS` 페이지에 통합을 공유해야 합니다.
- OpenAI 모델은 Structured Outputs(strict)를 지원하는 모델이어야 합니다(기본 `gpt-4o`).

## 사용법

```bash
# 워커 상시 실행 (Inbox 폴링)
knowledge-db run
python -m knowledge_db run          # 동일

# 큐를 한 번만 비우고 종료
knowledge-db run --once
```

### 자료 등록 (ingest)

원본을 **위치 인자로 그냥 넘기면 URL/파일/텍스트를 자동 감지**합니다. 여러 개를
한 번에, 파이프(stdin)로도 등록할 수 있습니다.

```bash
# 자동 감지 — 형식을 지정할 필요 없음
knowledge-db ingest https://example.com/article
knowledge-db ingest ./paper.pdf --now                 # 등록 후 즉시 처리
knowledge-db ingest "정리해둘 메모…"

# 여러 개 한 번에 (URL·파일·텍스트 섞어도 됨)
knowledge-db ingest https://a.com ./b.pdf "메모"

# 파이프로 텍스트 투입
cat note.md | knowledge-db ingest --stdin --now

# 편의 옵션
knowledge-db ingest ./report.pdf --topic 연구 --topic 업무   # 주제 태그
knowledge-db ingest ./secret.pdf --access 민감              # AI 처리 자동 제외
knowledge-db ingest https://… --type 논문 --title "제목 지정"
knowledge-db ingest https://… --no-ai                       # 보관만
```

편의 기능:
- **자동 감지**: `--url/--file/--text`를 몰라도 원본만 넘기면 됨(명시 플래그도 계속 지원).
- **즉시 첨부**: 로컬 파일/URL은 등록 시점에 `원본 파일`로 바로 첨부(처리 전에도 원본 보존).
- **자동 제목**: 제목을 안 주면 처리 후 분류 결과 제목으로 `제목`을 자동 보정.
- **접근 등급 연동**: `--access 민감`이면 `AI 처리 허용`이 자동으로 꺼지고, 워커도 민감
  자료는 외부 모델로 보내지 않고 `검토`로 라우팅합니다(개인정보 보호).

## 설정 (환경변수)

| 변수 | 기본값 | 설명 |
|---|---|---|
| `NOTION_API_KEY` | — | Notion 통합 토큰(필수) |
| `OPENAI_API_KEY` | — | OpenAI 키(필수) |
| `OPENAI_MODEL` | `gpt-4o` | Structured Outputs 지원 모델 |
| `CONFIDENCE_THRESHOLD` | `0.8` | 자동 `완료` 기준 신뢰도 |
| `WORKER_ID` | `main-pc` | 이 워커 식별자 |
| `LEASE_SECONDS` | `900` | 리스 유효 시간(크래시 복구) |
| `POLL_INTERVAL` | `15` | 큐가 빌 때 폴링 간격(초) |
| `MAX_RETRIES` | `3` | 실패 항목 최대 재시도 |
| `MAX_CONTENT_CHARS` | `24000` | 모델에 보내는 본문 최대 길이 |
| `NOTION_*_DB` | 발견된 DB ID | 대상 DB 재정의용 |

## 모듈 구조

```
knowledge_db/
├─ config.py        # 환경설정 + Notion 스키마(속성명/옵션) 상수
├─ models.py        # RawDocument + Structured Outputs 스키마(Classification)
├─ extract.py       # 웹/PDF/텍스트 추출 + 콘텐츠 해시
├─ classify.py      # OpenAI Structured Outputs 분류
├─ notion_store.py  # 큐 리스/파이널라이즈, upsert, 관계 링크, 파일 첨부
├─ pipeline.py      # extract→classify→store 오케스트레이션
└─ worker.py        # run / ingest CLI (단일 워커)
```

## 테스트

```bash
pip install pytest
pytest          # 네트워크/API 없이 도는 순수 단위 테스트
```

## 설계 노트

- **단일 워커 + 리스**: 메인 PC 1대 전제. `Worker ID`/`Lease Until`로 재시작 시
  진행 중이던 항목을 안전하게 회수(중복 처리 방지 & 크래시 복구).
- **멱등 upsert**: 개념/엔터티는 제목으로 조회 후 존재하면 재사용하고 `관련 자료`에
  현재 Inbox를 덧붙여 그래프가 누적됩니다.
- **관계형 전용**: 별도 로컬 DB 없이 Notion relation만으로 지식 그래프를 구성합니다.
- **첨부**: 로컬 파일은 Notion File Upload API로 업로드, 웹 자료는 원문 URL을
  external 파일로 첨부합니다(20MB 초과/실패 시 안전하게 건너뜀).
- **개인정보 보호**: `접근 등급=민감` 자료는 절대 외부 모델로 전송하지 않고 로컬 보관 +
  `검토`로 라우팅합니다(`config.AI_BLOCKED_ACCESS`).

## 배포 (메인 PC 상시 실행)

systemd 유저 서비스 / cron / macOS·Windows 설정은 [`deploy/README.md`](deploy/README.md) 참고.
