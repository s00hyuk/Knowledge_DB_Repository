"""Notion Personal Knowledge OS ingest pipeline.

extract (web/PDF/text) -> classify (OpenAI Structured Outputs) -> notion_store
orchestrated by ``pipeline`` and driven by a single-worker ``worker`` CLI.
"""

__version__ = "1.0.0"
