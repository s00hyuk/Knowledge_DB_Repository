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

from .config import Inbox, settings
from .extract import extract
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
def cmd_ingest(args: argparse.Namespace) -> int:
    store = NotionStore()
    given = [x for x in (args.url, args.file, args.text) if x]
    if len(given) != 1:
        log.error("Provide exactly one of --url / --file / --text.")
        return 2

    if args.url:
        created = store.create_inbox_item(
            title=args.title or args.url,
            source_url=args.url,
            ai_allowed=not args.no_ai,
        )
    elif args.file:
        if not os.path.isfile(args.file):
            log.error("File not found: %s", args.file)
            return 2
        created = store.create_inbox_item(
            title=args.title or os.path.basename(args.file),
            material_type=Inbox.TYPE_PDF if args.file.lower().endswith(".pdf") else Inbox.TYPE_DOC,
            ai_allowed=not args.no_ai,
        )
    else:  # text
        created = store.create_inbox_item(
            title=args.title or args.text[:80],
            inline_text=args.text,
            material_type=Inbox.TYPE_TEXT,
            ai_allowed=not args.no_ai,
        )

    log.info("Queued Inbox item: %s", created.get("url") or created["id"])

    if args.now:
        if args.no_ai:
            log.warning("--now ignored because --no-ai was set.")
            return 0
        source = args.url or args.file or args.text
        doc = extract(source)
        outcome = process_doc(store, created["id"], doc)
        log.info(
            "Processed now: 상태=%s 신뢰도=%.2f (개념 %d / 엔터티 %d / 주장 %d)",
            outcome.status,
            outcome.confidence,
            outcome.n_concepts,
            outcome.n_entities,
            outcome.n_claims,
        )
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

    ing_p = sub.add_parser("ingest", help="add a new item to the Inbox")
    ing_p.add_argument("--url", help="web page or PDF URL")
    ing_p.add_argument("--file", help="local file path (PDF/text/doc)")
    ing_p.add_argument("--text", help="raw text note")
    ing_p.add_argument("--title", help="override the Inbox 제목")
    ing_p.add_argument("--now", action="store_true", help="process immediately after queuing")
    ing_p.add_argument("--no-ai", action="store_true", help="queue with AI 처리 허용 off")
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
