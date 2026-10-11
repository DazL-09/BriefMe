"""HearMeOut — Tahap 3: MCP Server (Tool Execution).

Stack: Python + fastmcp + docxtpl.

Menyediakan tool `generate_clinical_document` yang dipanggil oleh Cognitive Brain
(Agent Gemini di Langflow, lewat node MCP Tools) untuk membuat file .docx
Thought Record (tabel CBT 5 kolom).

Jalankan:
    .venv/bin/python mcp_server.py
Endpoint MCP (streamable HTTP):
    http://127.0.0.1:8788/mcp
"""

from __future__ import annotations

import json
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path

from docxtpl import DocxTemplate
from fastmcp import FastMCP

BASE_DIR = Path(__file__).resolve().parent
TEMPLATE_PATH = Path(os.environ.get("CBT_TEMPLATE", BASE_DIR / "template_cbt.docx"))
OUTPUT_DIR = Path(os.environ.get("CBT_OUTPUT_DIR", BASE_DIR / "output"))
AUDIT_LOG = OUTPUT_DIR / "audit.jsonl"

HOST = os.environ.get("MCP_HOST", "127.0.0.1")
PORT = int(os.environ.get("MCP_PORT", "8788"))

mcp = FastMCP("HearMeOut_Tools")


def _audit(record: dict) -> None:
    """Append-only audit trail lokal (pengganti sementara INSERT database)."""
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    with AUDIT_LOG.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(record, ensure_ascii=False) + "\n")


@mcp.tool()
def generate_clinical_document(
    situation: str,
    thought: str,
    emotion: str,
    behavior: str,
    alternate_thought: str,
) -> str:
    """Membuat file DOCX Thought Record (tabel matriks CBT 5 kolom).

    Args:
        situation: Situasi/kejadian pemicu.
        thought: Pikiran otomatis yang muncul.
        emotion: Emosi yang dirasakan.
        behavior: Perilaku yang dilakukan.
        alternate_thought: Pikiran alternatif yang lebih seimbang.

    Returns:
        "SUCCESS: File saved at <path>" atau pesan ERROR.
    """
    try:
        if not TEMPLATE_PATH.exists():
            return f"ERROR: template tidak ditemukan: {TEMPLATE_PATH}"

        OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

        doc = DocxTemplate(str(TEMPLATE_PATH))
        context = {
            "situation": situation,
            "thought": thought,
            "emotion": emotion,
            "behavior": behavior,
            "alternate_thought": alternate_thought,
        }

        file_id = str(uuid.uuid4())[:8]
        filepath = OUTPUT_DIR / f"brief_{file_id}.docx"

        doc.render(context)
        doc.save(str(filepath))

        # Operasi INSERT ke PostgreSQL/Database dapat ditambahkan di sini,
        # mis. psycopg.execute(
        #   "INSERT INTO clinical_briefs (id, path, trigger, ...) VALUES (...)",
        # )
        _audit(
            {
                "ts": datetime.now(timezone.utc).isoformat(),
                "file_id": file_id,
                "path": str(filepath),
                "context": context,
            }
        )

        return f"SUCCESS: File saved at {filepath}"
    except Exception as exc:  # noqa: BLE001 — tool harus selalu mengembalikan pesan
        return f"ERROR: {type(exc).__name__}: {exc}"


if __name__ == "__main__":
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    print(f"HearMeOut_Tools MCP server → http://{HOST}:{PORT}/mcp")
    print(f"template={TEMPLATE_PATH}  output={OUTPUT_DIR}")
    # uvicorn_config.log_config=None -> pakai logging root (ada timestamp),
    # berguna mengorelasikan request MCP dengan timeout klien (Langflow).
    import logging

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    mcp.run(transport="http", host=HOST, port=PORT,
            uvicorn_config={"log_config": None,
                            # keep-alive 120s: klien MCP (Langflow) mengalami
                            # "Request timed out" dgn default 5s — race antara
                            # socket yang ditutup uvicorn dan tool call susulan.
                            # Terbukti: jalur Connection: close (forwarder) sukses.
                            "timeout_keep_alive": 120})
