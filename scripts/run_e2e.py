"""Tahap 4 — Pengujian terintegrasi (end-to-end) BriefME — Arsitektur native Langflow.

Alur yang diuji:
  1. Preflight: GOOGLE_API_KEY harus kunci asli (bukan placeholder) + layanan sehat
     (Langflow, MCP Server hearmeout-tools)
  2. Satu POST ke API Langflow:
     POST /api/v1/run/briefme                              (endpoint: briefme)
     respons SATU output: "Thought Record" (tabel MD + baris Dokumen)
  3. Verifikasi output flow (plan: langkah 5, kini di dalam flow):
     Agent (Gemini, prompt CBT) -> tool MCP generate_clinical_document;
     output = tabel Markdown dengan header Indonesia + baris
     "Dokumen: <path>"; guard regex PII
  4. Cek output fisik .docx: tabel 5 kolom terisi, guard regex PII
                                                          (plan: langkah 6)

Catatan: tahap sensor/Ollama/Bob CLI dibuang (keputusan refactor) — teks input
bukan lagi berisi PII, jadi verifikasi PII jadi guard regex (output tidak boleh
memuat pola HP/email/alamat yang tidak pernah dikirim).

Pemakaian:
    .venv/bin/python scripts/run_e2e.py
    (kunci dari .env — skrip memuatnya sendiri)
"""

from __future__ import annotations

import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent


def _load_env_file(path: Path) -> None:
    """Muat KEY=VALUE sederhana dari .env tanpa menimpa env yang sudah ada."""
    if not path.exists():
        return
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key, value = key.strip(), value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


_load_env_file(BASE / ".env")

LF_URL = os.environ.get("LANGFLOW_URL", "http://127.0.0.1:7860").rstrip("/")
LF_KEY = os.environ.get("LANGFLOW_API_KEY", "")
LF_ENDPOINT = os.environ.get("LANGFLOW_FLOW_ENDPOINT", "briefme")
MCP_URL = os.environ.get("MCP_URL", "http://127.0.0.1:8788/mcp")
GEMINI_KEY = os.environ.get("GOOGLE_API_KEY", "").strip()

# Jurnal pasien realistis TANPA data pribadi (input flow memang dianggap bersih).
TEST_TEXT = (
    "Kemarin saya berdiskusi dengan atasan soal target kuartal di kantor. "
    "Sejak itu saya cemas berlebihan, jantung berdebar, dan susah tidur. "
    "Saya takut saya akan dipecat."
)

# Guard regex PII: input tidak memuat pola ini, jadi output/dokumen tidak boleh.
PII_PATTERNS = {
    "nomor HP": re.compile(r"\b0\d{2,4}[-\s]?\d{3,4}[-\s]?\d{3,4}\b"),
    "email": re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]{2,}"),
    "alamat jalan": re.compile(r"\bJl\.\s*\w+", re.I),
}
# header tabel Markdown (output) & tabel .docx — urutan sama persis
TABLE_HEADERS = ["Situasi", "Pikiran", "Emosi", "Perilaku", "Pikiran Alternatif"]
# kata kunci isi teks yang harus muncul di dokumen (selaras dgn TEST_TEXT)
DOCX_CONTENT_KEYWORDS = ["atasan", "kantor", "dipecat", "berdebar"]
# nilai pengganti yang berarti "tidak ada isi", bukan hasil ekstraksi nyata
PLACEHOLDERS = ("tidak disebutkan", "tidak ada", "n/a", "-", "tidak diketahui")
# potongan prompt AGENT_SYSTEM_PROMPT (builder) yang tidak boleh bocor ke
# output flow maupun dokumen
INSTRUCTION_FRAGMENTS = [
    "langkah wajib",
    "analisis teks",
    "format json",
    "satu baris data per situasi",
    "bukan kutipan dari teks",
    "tanpa teks lain",
    "hasil restrukturisasi kognitif ringan",
    "identitas pasien",
    "panggilan tool",
    "generate_clinical_document",
    "saran medis",
]
# frasa yang memang ada di footer statis template_cbt.docx (bukan kebocoran prompt)
TEMPLATE_STATIC_TEXT = ("saran medis", "objektif-klinis", "jurnal pasien")

failures: list[str] = []


def pii_hits(text: str) -> list[str]:
    """Nama pola PII yang ditemukan di teks (empty list = bersih)."""
    return [name for name, pat in PII_PATTERNS.items() if pat.search(text)]


def check(label: str, ok: bool, detail: str = "") -> bool:
    mark = "PASS" if ok else "FAIL"
    print(f"  [{mark}] {label}" + (f" — {detail}" if detail else ""))
    if not ok:
        failures.append(label)
    return ok


def post(url: str, payload: dict, headers: dict | None = None, timeout: int = 1800) -> dict:
    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode(),
        method="POST",
        headers={"Content-Type": "application/json", **(headers or {})},
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read()
    except urllib.error.HTTPError as e:
        raise SystemExit(f"HTTP {e.code} dari {url}: {e.read().decode()[:2000]}")
    if raw[:2] == b"\x1f\x8b":
        import gzip

        raw = gzip.decompress(raw)
    return json.loads(raw) if raw.strip() else {}


def get(url: str, timeout: int = 30) -> dict:
    with urllib.request.urlopen(url, timeout=timeout) as resp:
        return json.loads(resp.read())


def item_text(item: dict) -> str:
    """Ambil teks dari satu item output Langflow (coba semua jalur yang diketahui)."""
    for candidate in (
        item.get("results", {}).get("message", {}).get("text"),
        item.get("artifacts", {}).get("message"),
        item.get("text"),
    ):
        if candidate:
            return str(candidate)
    return ""


def step1_health() -> None:
    print("\n[1] Health check komponen")
    check("Langflow hidup", get(f"{LF_URL}/health").get("status") == "ok")
    # MCP: lewat client fastmcp
    import asyncio

    from fastmcp import Client

    async def tools():
        async with Client(MCP_URL) as c:
            return [t.name for t in await c.list_tools()]

    names = asyncio.run(tools())
    check("Tool MCP generate_clinical_document terdaftar",
          "generate_clinical_document" in names, ", ".join(names))


def step2_run_flow() -> tuple[str, dict]:
    print("\n[2] Satu POST ke Langflow: jurnal pasien -> Agent (Gemini) -> MCP")
    t0 = time.time()
    res = post(
        f"{LF_URL}/api/v1/run/{LF_ENDPOINT}",
        {"input_value": TEST_TEXT, "output_type": "chat", "data_type": "text"},
        headers={"x-api-key": LF_KEY},
        timeout=1800,
    )
    dt = round(time.time() - t0, 1)
    items: dict[str, dict] = {}
    for out in res.get("outputs", []):
        for item in out.get("outputs", []):
            items[item.get("component_display_name") or item.get("component_id", "")] = item
    print(f"  elapsed: {dt}s")
    print(f"  output : {sorted(items)}")
    thought = item_text(items.get("Thought Record", {}))
    print(f"  input  : {TEST_TEXT}")
    print(f"  output : {thought[:500]}")
    check("Hanya satu output 'Thought Record' (sensor sudah dibuang)",
          sorted(items) == ["Thought Record"], str(sorted(items)))
    check("Output Thought Record tidak kosong", bool(thought.strip()))
    return thought, res


def _strip_fence(text: str) -> str:
    t = text.strip()
    m = re.match(r"^```(?:json)?\s*(.*?)\s*```$", t, re.S)
    return m.group(1) if m else t


def step3_verify_output(output_text: str) -> str:
    """Kembalikan path .docx dari output node Thought Record (tabel MD + baris Dokumen)."""
    print("\n[3] Verifikasi output Thought Record (Agent Gemini + tool MCP)")
    result_str = _strip_fence(output_text)
    missing_headers = [h for h in TABLE_HEADERS if h not in result_str]
    check("Output = tabel Markdown dgn header Indonesia",
          not missing_headers,
          f"header hilang: {missing_headers}" if missing_headers else "")
    check("Bukan dump JSON mentah (last_message sudah diekstrak)",
          '"last_message"' not in result_str and '"status"' not in result_str,
          result_str[:200])
    leaked_pii = pii_hits(result_str)
    check("Output bebas pola PII (regex — teks input tak memuatnya)",
          not leaked_pii, f"bocor: {leaked_pii}" if leaked_pii else "")
    leaked_instr = [f for f in INSTRUCTION_FRAGMENTS if f in result_str.lower()]
    check("Output flow bebas kebocoran instruksi prompt", not leaked_instr,
          f"bocor: {leaked_instr}" if leaked_instr else "")
    # Output memuat baris: Dokumen: /abs/path/brief_x.docx
    m = (re.search(r"(/[\w./-]*/brief_[0-9a-f]{8}\.docx)", result_str)
         or re.search(r"(output/brief_[0-9a-f]{8}\.docx)", result_str))
    check("Tool MCP mengembalikan path dokumen", bool(m),
          result_str[:300] if not m else m.group(1))
    if not m:
        return ""
    path = Path(m.group(1) if m.group(1).startswith("/") else BASE / m.group(1))
    check("File .docx dibuat & ada", path.exists(), str(path))
    return str(path) if path.exists() else ""


def step4_output_file(doc_path: str) -> None:
    print("\n[4] Cek output fisik (.docx — tabel Thought Record 5 kolom)")
    if not doc_path:
        check("Path dokumen tersedia untuk pemeriksaan isi", False)
        return
    path = Path(doc_path)

    from docx import Document

    doc = Document(str(path))
    body_text = "\n".join(p.text for p in doc.paragraphs)
    table_rows: list[list[str]] = []
    for table in doc.tables:
        for row in table.rows:
            table_rows.append([c.text.strip() for c in row.cells])
    table_text = "\n".join(" | ".join(r) for r in table_rows)
    text = body_text + "\n" + table_text
    print("  --- isi dokumen ---")
    print("  " + text.replace("\n", "\n  "))
    lower = text.lower()

    check("Dokumen memuat tabel", bool(doc.tables))
    if table_rows:
        check("Header tabel sesuai (5 kolom Indonesia)",
              table_rows[0] == TABLE_HEADERS, str(table_rows[0]))
        data_rows = table_rows[1:]
        check("Ada baris data", bool(data_rows))
        if data_rows:
            cells = data_rows[0]
            check("Semua sel terisi konten nyata",
                  all(len(c) >= 3 for c in cells), str(cells))
            check("Sel 'Pikiran Alternatif' bukan placeholder",
                  len(cells[4]) >= 15
                  and cells[4].lower() not in PLACEHOLDERS, cells[4][:80])
    hits = [k for k in DOCX_CONTENT_KEYWORDS if k in lower]
    check("Konten tabel selaras dgn teks input", len(hits) >= 3,
          f"kata kunci ditemukan: {hits}")
    leaked_pii = pii_hits(text)
    check("Dokumen bebas pola PII (regex)", not leaked_pii,
          f"bocor: {leaked_pii}" if leaked_pii else "")
    doc_fragments = [f for f in INSTRUCTION_FRAGMENTS if f not in TEMPLATE_STATIC_TEXT]
    leaked_instr = [f for f in doc_fragments if f in lower]
    check("Dokumen bebas kebocoran instruksi prompt", not leaked_instr,
          f"bocor: {leaked_instr}" if leaked_instr else "")


def preflight() -> bool:
    """Gagal cepat sebelum menyentuh layanan bila prasyarat tidak terpenuhi."""
    ok = True
    if not LF_KEY:
        print("[FAIL] LANGFLOW_API_KEY kosong — cek .env")
        ok = False
    if not GEMINI_KEY or GEMINI_KEY == "xxx":
        print("[FAIL] GOOGLE_API_KEY belum diisi kunci asli di .env — flow butuh "
              "Gemini; isi kuncinya lalu jalankan scripts/create_langflow_flow.py ulang")
        ok = False
    return ok


def main() -> int:
    t0 = time.time()
    if not preflight():
        return 1
    step1_health()
    thought, _ = step2_run_flow()
    doc_path = step3_verify_output(thought)
    step4_output_file(doc_path)

    print("\n=== RINGKASAN ===")
    if failures:
        print(f"GAGAL ({len(failures)} pemeriksaan):")
        for f in failures:
            print(f"  - {f}")
        return 1
    print(f"SEMUA PEMERIKSAAN LOLOS dalam {round(time.time() - t0, 1)}s")
    return 0


if __name__ == "__main__":
    sys.exit(main())
