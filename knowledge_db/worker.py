"""Single-worker CLI: ``run`` (queue loop) and ``ingest`` (add to Inbox).

Designed for one main PC. The lease fields (Worker ID / Lease Until) let a
restarted worker safely reclaim an item that was in flight when it crashed,
without a second machine.

    knowledge-db run                     # poll the Inbox and process 대기 items
    knowledge-db run --once              # drain the queue once, then exit
    knowledge-db ingest --url https://…  # queue a web page
    knowledge-db ingest --file paper.pdf --now   # queue a PDF and process now
    knowledge-db ingest --text "메모…"   # queue a raw text note
"""

from __future__ import annotations

import argparse
import logging
import os
import signal
import sys
import time

from .config import Inbox, access_allows_ai, settings
from .extract import detect_source_kind, extract, guess_material_type
from .notion_store import NotionStore
from .pipeline import process_doc, process_item

log = logging.getLogger("knowledge_db")

_stop = False


def _install_signal_handlers() -> None:
    def handler(signum, frame):  # noqa: ANN001, ARG001
        global _stop
        _stop = True
        log.info("Signal %s received — finishing current item then stopping.", signum)

    for sig in (signal.SIGINT, signal.SIGTERM):
        signal.signal(sig, handler)


def _setup_logging(verbose: bool) -> None:
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )


# --------------------------------------------------------------------------- #
# run
# --------------------------------------------------------------------------- #
def cmd_run(args: argparse.Namespace) -> int:
    store = NotionStore()
    _install_signal_handlers()
    log.info(
        "Worker '%s' started (poll=%ss, lease=%ss, threshold=%.2f, model=%s).",
        settings.worker_id,
        settings.poll_interval,
        settings.lease_seconds,
        settings.confidence_threshold,
        settings.openai_model,
    )

    processed = 0
    while not _stop:
        item = store.claim_next()
        if item is None:
            if args.once:
                log.info("Queue empty — exiting (--once).")
                break
            time.sleep(settings.poll_interval)
            continue

        try:
            process_item(store, item)
            processed += 1
        except Exception as exc:  # noqa: BLE001 - record and continue the loop
            log.exception("Failed processing %s: %s", item["id"], exc)
            status = store.mark_failed(item["id"], f"{type(exc).__name__}: {exc}", item["retries"])
            log.info("Marked %s as %s (retry %d).", item["id"], status, item["retries"] + 1)

    log.info("Worker stopped. Processed %d item(s) this run.", processed)
    return 0


# --------------------------------------------------------------------------- #
# ingest
# --------------------------------------------------------------------------- #
def _collect_sources(args: argparse.Namespace) -> list[tuple[str, str]]:
    """Gather (kind, value) pairs from positionals, flags, and stdin."""
    items: list[tuple[str, str]] = []
    for src in args.sources or []:
        items.append((detect_source_kind(src), src))
    if args.url:
        items.append(("url", args.url))
    if args.file:
        items.append(("file", args.file))
    if args.text:
        items.append(("text", args.text))
    if args.stdin:
        piped = sys.stdin.read().strip()
        if piped:
            items.append(("text", piped))
    return items


def cmd_ingest(args: argparse.Namespace) -> int:
    store = NotionStore()
    items = _collect_sources(args)
    if not items:
        log.error("Nothing to ingest. Pass a URL/file/text, e.g. `ingest https://…` or `--stdin`.")
        return 2
    if args.title and len(items) > 1:
        log.warning("--title is ignored when ingesting multiple sources.")

    # 접근 등급이 민감이면 AI 처리를 강제로 끈다.
    access = args.access
    ai_allowed = (not args.no_ai) and access_allows_ai(access)
    if args.no_ai is False and not access_allows_ai(access):
        log.info("접근 등급 '%s' — AI 처리 자동 비활성화.", access)

    processed_any = False
    for kind, value in items:
        if kind == "file" and not os.path.isfile(value):
            log.error("File not found, skipping: %s", value)
            continue

        material_type = args.type or guess_material_type(value, kind)
        title = args.title if (args.title and len(items) == 1) else None
        if not title:
            if kind == "url":
                title = value
            elif kind == "file":
                title = os.path.basename(value)
            else:
                title = value[:80]

        created = store.create_inbox_item(
            title=title,
            source_url=value if kind == "url" else None,
            inline_text=value if kind == "text" else None,
            local_path=value if kind == "file" else None,
            material_type=material_type,
            access=access,
            topics=args.topic,
            ai_allowed=ai_allowed,
        )
        log.info("Queued [%s] %s -> %s", kind, title[:60], created.get("url") or created["id"])

        if args.now and ai_allowed:
            doc = extract(value)
            outcome = process_doc(store, created["id"], doc, original_title=title)
            processed_any = True
            log.info(
                "  처리완료: 상태=%s 신뢰도=%.2f (개념 %d / 엔터티 %d / 주장 %d)",
                outcome.status,
                outcome.confidence,
                outcome.n_concepts,
                outcome.n_entities,
                outcome.n_claims,
            )

    if args.now and not ai_allowed:
        log.warning("--now skipped: AI 처리가 비활성화되어 있습니다 (--no-ai 또는 민감 등급).")
    if args.now and not processed_any and ai_allowed:
        log.info("처리할 항목이 없었습니다.")
    return 0


# --------------------------------------------------------------------------- #
# entry
# --------------------------------------------------------------------------- #
def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="knowledge-db",
        description="Notion Personal Knowledge OS ingest pipeline.",
    )
    parser.add_argument("-v", "--verbose", action="store_true", help="debug logging")
    sub = parser.add_subparsers(dest="command", required=True)

    run_p = sub.add_parser("run", help="poll the Inbox and process 대기 items")
    run_p.add_argument("--once", action="store_true", help="drain the queue once then exit")
    run_p.set_defaults(func=cmd_run)

    ing_p = sub.add_parser(
        "ingest",
        help="add one or more items to the Inbox",
        description="원본을 위치 인자로 넘기면 URL/파일/텍스트를 자동 감지합니다. 여러 개 동시 등록 가능.",
    )
    ing_p.add_argument(
        "sources",
        nargs="*",
        help="URL / 파일 경로 / 텍스트 (자동 감지, 여러 개 가능)",
    )
    ing_p.add_argument("--url", help="명시적 웹/ PDF URL")
    ing_p.add_argument("--file", help="명시적 로컬 파일 경로")
    ing_p.add_argument("--text", help="명시적 텍스트 메모")
    ing_p.add_argument("--stdin", action="store_true", help="표준입력(파이프)에서 텍스트 읽기")
    ing_p.add_argument("--title", help="Inbox 제목 지정(단일 항목일 때만)")
    ing_p.add_argument(
        "--type",
        choices=Inbox.MATERIAL_TYPES,
        help="자료 유형 강제 지정(기본: 자동 추정)",
    )
    ing_p.add_argument(
        "--access",
        choices=Inbox.ACCESS_OPTIONS,
        default="일반",
        help="접근 등급(기본 일반). '민감'은 AI 처리에서 제외됩니다.",
    )
    ing_p.add_argument(
        "--topic",
        action="append",
        choices=Inbox.TOPIC_OPTIONS,
        help="주제 태그(복수 지정 가능)",
    )
    ing_p.add_argument("--now", action="store_true", help="등록 후 즉시 처리")
    ing_p.add_argument("--no-ai", action="store_true", help="AI 처리 허용 끄고 보관만")
    ing_p.set_defaults(func=cmd_ingest)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    _setup_logging(getattr(args, "verbose", False))
    try:
        return args.func(args)
    except KeyboardInterrupt:
        return 130
    except Exception as exc:  # noqa: BLE001 - surface a clean message
        log.error("%s: %s", type(exc).__name__, exc)
        return 1


if __name__ == "__main__":
    sys.exit(main())
