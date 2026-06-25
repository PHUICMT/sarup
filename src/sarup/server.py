"""Sarup MCP Server — Thai-first context compression for Claude Code.

Tools:
    sarup_compress  — Compress Thai/English/mixed content, return hash + metrics
    sarup_retrieve  — Retrieve original content by hash
    sarup_stats     — Session compression statistics

Usage:
    python -m sarup.server          # stdio (used by Claude Code)
    sarup                           # same via installed script
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from typing import Any

from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import TextContent, Tool

from .compressor import compress, estimate_tokens
from .store import CompressionStore
from .tokens import token_method

logger = logging.getLogger("sarup.server")

COMPRESS_TOOL = "sarup_compress"
RETRIEVE_TOOL = "sarup_retrieve"
STATS_TOOL = "sarup_stats"

_DB_PATH: str | None = os.environ.get("SARUP_DB_PATH") or None


class SarupServer:
    def __init__(self) -> None:
        self.server = Server("sarup")
        self.store = CompressionStore(db_path=_DB_PATH)
        self._total_calls: int = 0
        self._total_tokens_saved: int = 0
        self._register_handlers()

    def _register_handlers(self) -> None:
        @self.server.list_tools()
        async def list_tools() -> list[Tool]:
            return [
                Tool(
                    name=COMPRESS_TOOL,
                    description=(
                        "Compress content for context efficiency. "
                        "Supports Thai prose, English prose, mixed Thai+code, JSON, and logs. "
                        "Returns compressed text, a retrieval hash, and token-saving metrics. "
                        "Use sarup_retrieve(hash=...) to recover the original when needed."
                    ),
                    inputSchema={
                        "type": "object",
                        "properties": {
                            "content": {
                                "type": "string",
                                "description": "Content to compress",
                            },
                            "target_ratio": {
                                "type": "number",
                                "description": "Fraction of prose to keep (0.3–0.7). Default 0.5.",
                                "default": 0.5,
                            },
                            "lossless": {
                                "type": "boolean",
                                "description": (
                                    "Only apply lossless transforms (whitespace/JSON compact). "
                                    "Default false."
                                ),
                                "default": False,
                            },
                            "query": {
                                "type": "string",
                                "description": (
                                    "Optional context query. Sentences relevant to this query "
                                    "are scored higher and more likely to be kept."
                                ),
                                "default": "",
                            },
                            "mode": {
                                "type": "string",
                                "enum": ["extractive", "semantic", "abstractive", "auto"],
                                "description": (
                                    "Prose strategy. 'extractive' (default): offline TF-IDF, "
                                    "verbatim subset. 'semantic': embedding centrality (needs Ollama). "
                                    "'abstractive': local-LLM rewrite, highest savings (needs Ollama). "
                                    "'auto': abstractive if Ollama is up, else extractive. "
                                    "All modes stay 100% recoverable via sarup_retrieve."
                                ),
                                "default": "extractive",
                            },
                        },
                        "required": ["content"],
                    },
                ),
                Tool(
                    name=RETRIEVE_TOOL,
                    description=(
                        "Retrieve the original uncompressed content by hash. "
                        "The hash is returned by sarup_compress."
                    ),
                    inputSchema={
                        "type": "object",
                        "properties": {
                            "hash": {
                                "type": "string",
                                "description": "24-char hash returned by sarup_compress",
                            },
                        },
                        "required": ["hash"],
                    },
                ),
                Tool(
                    name=STATS_TOOL,
                    description="Return cumulative compression statistics for this session.",
                    inputSchema={
                        "type": "object",
                        "properties": {},
                    },
                ),
            ]

        @self.server.call_tool()
        async def call_tool(
            name: str, arguments: dict[str, Any]
        ) -> list[TextContent]:
            if name == COMPRESS_TOOL:
                return await self._handle_compress(arguments)
            if name == RETRIEVE_TOOL:
                return await self._handle_retrieve(arguments)
            if name == STATS_TOOL:
                return await self._handle_stats()
            return [
                TextContent(
                    type="text",
                    text=json.dumps({"error": f"Unknown tool: {name}"}),
                )
            ]

    # ── Tool handlers ──────────────────────────────────────────────────────

    async def _handle_compress(self, args: dict[str, Any]) -> list[TextContent]:
        content: str = args.get("content", "")
        if not content:
            return [_err("content is required")]

        target_ratio = float(args.get("target_ratio", 0.5))
        target_ratio = max(0.1, min(0.9, target_ratio))
        lossless = bool(args.get("lossless", False))
        query: str = args.get("query", "") or ""
        mode: str = args.get("mode", "extractive") or "extractive"

        result = compress(
            content, target_ratio=target_ratio, lossless=lossless, query=query, mode=mode
        )

        h = self.store.store(
            original=content,
            compressed=result.compressed,
            original_tokens=result.original_tokens,
            compressed_tokens=result.compressed_tokens,
        )

        # Roundtrip guarantee: prove the original is recoverable byte-for-byte.
        verified = self.store.verify(h, content)

        self._total_calls += 1
        self._total_tokens_saved += result.tokens_saved

        payload: dict[str, Any] = {
            "compressed": result.compressed,
            "hash": h,
            "original_tokens": result.original_tokens,
            "compressed_tokens": result.compressed_tokens,
            "tokens_saved": result.tokens_saved,
            "savings_percent": result.savings_percent,
            "transforms": result.transforms,
            "lossy": result.lossy,
            "verified": verified,
            "token_method": token_method(),
        }

        if result.tokens_saved > 0:
            payload["note"] = (
                f"Original cached under hash '{h}'. "
                f"Call {RETRIEVE_TOOL}(hash='{h}') to recover full content."
            )

        return [TextContent(type="text", text=json.dumps(payload, ensure_ascii=False))]

    async def _handle_retrieve(self, args: dict[str, Any]) -> list[TextContent]:
        h: str = args.get("hash", "").strip()
        if not h:
            return [_err("hash is required")]

        original = self.store.retrieve(h)
        if original is None:
            return [
                _err(
                    f"No content found for hash '{h}'. "
                    "Hash may have expired (server restarted) or be invalid."
                )
            ]

        return [TextContent(type="text", text=original)]

    async def _handle_stats(self) -> list[TextContent]:
        payload = {
            "total_compressed": self._total_calls,
            "total_tokens_saved": self._total_tokens_saved,
            "store_entries": self.store.size,
        }
        return [TextContent(type="text", text=json.dumps(payload, ensure_ascii=False))]

    # ── Transport ──────────────────────────────────────────────────────────

    async def run_stdio(self) -> None:
        async with stdio_server() as (read_stream, write_stream):
            logger.info("Sarup MCP server started (stdio)")
            await self.server.run(
                read_stream,
                write_stream,
                self.server.create_initialization_options(),
            )


# ── Helpers ────────────────────────────────────────────────────────────────────

def _err(msg: str) -> TextContent:
    return TextContent(type="text", text=json.dumps({"error": msg}))


# ── Entrypoints ────────────────────────────────────────────────────────────────

def main() -> None:
    logging.basicConfig(level=logging.WARNING)
    server = SarupServer()
    asyncio.run(server.run_stdio())


if __name__ == "__main__":
    main()
