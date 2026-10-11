"""Pengujian terintegrasi (end-to-end) BriefME — dua engine (E2E_ENGINE=bob|gemini).

Alur engine bob (default — arsitektur utama hackathon):
  1. Preflight: LANGFLOW_API_KEY + BOB_API_KEY + bob CLI + .bob/mcp.json +
     Langflow hidup + tool MCP terdaftar + Express history API (:8790) + MongoDB
  2. F1  POST /api/v1/run/briefme             → tabel CBT 4 kolom terisi, kolom
      "Pikiran Alternatif" KOSONG, TANPA baris "Dokumen:" (AI tidak mengisi alt),
      guard regex PII + guard kebocoran instruksi prompt
  3. F3  POST /api/v1/run/briefme-alternatif  → SATU saran pikiran alternatif
      (>= 15 karakter, tanpa pembuka/preamble)
  4. F2  POST /api/v1/run/briefme-dokumen (JSON 5 field) → "Dokumen: <path>" +
      cek fisik .docx: tabel 5 kolom, sel alt terisi (bukan placeholder)
  5. Roundtrip history Express/MongoDB: POST valid 201, G1 journal pendek 400,
     analysis kosong 400, documentPath tanpa alt 400 (G4), PATCH alt, DELETE

Engine gemini (E2E_ENGINE=gemini — flow cadangan "BriefME (Gemini)", perilaku
legacy): preflight GOOGLE_API_KEY kunci asli; satu POST briefme-gemini → tabel
5 kolom terisi + baris "Dokumen: <path>" dalam satu run; riwayat tetap diuji.

Pemakaian:
    .venv/bin/python scripts/run_e2e.py                       # engine bob
    E2E_ENGINE=gemini .venv/bin/python scripts/run_e2e.py     # flow cadangan
    (kunci dari .env — skrip memuatnya sendiri)
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
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

ENGINE = os.environ.get("E2E_ENGINE", "bob")
if ENGINE not in ("bob", "gemini"):
    sys.exit(f"E2E_ENGINE tidak dikenal: {ENGINE!r} (pakai bob|gemini)")

LF_URL = os.environ.get("LANGFLOW_URL", "http://127.0.0.1:7860").rstrip("/")
LF_KEY = os.environ.get("LANGFLOW_API_KEY", "")
HISTORY_URL = os.environ.get("HISTORY_URL", "http://127.0.0.1:8790").rstrip("/")
MCP_URL = os.environ.get("MCP_URL", "http://127.0.0.1:8788/mcp")
GEMINI_KEY = os.environ.get("GOOGLE_API_KEY", "").strip()
BOB_KEY = os.environ.get("BOB_API_KEY", "").strip()

# Endpoint per tahap (engine bob) / endpoint legacy (engine gemini).
ENDPOINTS = {
    "bob": {"f1": "briefme", "f3": "briefme-alternatif", "f2": "briefme-dokumen"},
    "gemini": {"legacy": "briefme-gemini"},
}

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
# potongan prompt (builder) yang tidak boleh bocor ke output flow maupun dokumen
INSTRUCTION_FRAGMENTS = [
    "langkah wajib",
    "analisis teks secara internal",
    "objek json dari langkah",
    "satu baris data per situasi",
    "hasil restrukturisasi kognitif ringan",
    "identitas pasien",
    "panggilan tool",
    "generate_clinical_document",
    "saran medis",
    "hanya dengan satu baris",
    "tanpa teks lain",
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


def request_json(method: str, url: str, payload: dict | None = None,
                 headers: dict | None = None, timeout: int = 1800,
                 raise_http: bool = True) -> tuple[int, dict]:
    """Kirim JSON, kembalikan (status, body). raise_http=True → error HTTP jadi SystemExit."""
    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode() if payload is not None else None,
        method=method,
        headers={"Content-Type": "application/json", **(headers or {})},
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            status, raw = resp.status, resp.read()
    except urllib.error.HTTPError as e:
        status, raw = e.code, e.read()
        if raise_http:
            raise SystemExit(f"HTTP {e.code} dari {url}: {raw.decode()[:2000]}")
    if raw[:2] == b"\x1f\x8b":
        import gzip

        raw = gzip.decompress(raw)
    return status, (json.loads(raw) if raw.strip() else {})


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


def run_flow(endpoint: str, text: str, timeout: int = 1800) -> tuple[dict, float]:
    """POST /api/v1/run/<endpoint> → ({nama_component: item}, elapsed)."""
    t0 = time.time()
    status, res = request_json(
        "POST", f"{LF_URL}/api/v1/run/{endpoint}",
        {"input_value": text, "output_type": "chat", "data_type": "text"},
        headers={"x-api-key": LF_KEY}, timeout=timeout, raise_http=False,
    )
    if status != 200:
        raise SystemExit(f"HTTP {status} dari run/{endpoint}: {json.dumps(res)[:1000]}")
    items: dict[str, dict] = {}
    for out in res.get("outputs", []):
        for item in out.get("outputs", []):
            items[item.get("component_display_name") or item.get("component_id", "")] = item
    return items, round(time.time() - t0, 1)


def md_table_rows(text: str) -> list[list[str]]:
    """Baris tabel Markdown dari output (header termasuk, separator dibuang)."""
    rows = []
    for line in text.splitlines():
        line = line.strip()
        if not line.startswith("|") or set(line) <= set("|- "):
            continue
        cells = [c.strip() for c in line.strip("|").split("|")]
        rows.append(cells)
    return rows


def preflight() -> bool:
    """Gagal cepat sebelum menyentuh layanan bila prasyarat tidak terpenuhi."""
    ok = True
    print(f"engine: {ENGINE}  endpoint: {ENDPOINTS[ENGINE]}")
    if not LF_KEY:
        print("[FAIL] LANGFLOW_API_KEY kosong — cek .env")
        ok = False
    if ENGINE == "bob":
        if not BOB_KEY or len(BOB_KEY) < 20:
            print("[FAIL] BOB_API_KEY kosong/pendek di .env — isi kunci API resmi Bob "
                  "lalu jalankan ulang")
            ok = False
        if not (BASE / ".bob" / "mcp.json").exists():
            print("[FAIL] .bob/mcp.json tidak ada — Bob tak bisa memanggil tool MCP "
                  "(jalankan builder/pastikan workspace .bob terbentuk)")
            ok = False
    else:
        if not GEMINI_KEY or GEMINI_KEY == "xxx":
            print("[FAIL] GOOGLE_API_KEY belum diisi kunci asli di .env — flow cadangan "
                  "butuh Gemini; isi kuncinya lalu jalankan "
                  "scripts/create_langflow_flow.py --engine gemini ulang")
            ok = False
    return ok


def step1_health() -> None:
    print("\n[1] Health check komponen")
    check("Langflow hidup", get(f"{LF_URL}/health").get("status") == "ok")
    # MCP: lewat client fastmcp (dipakai Bob via .bob/mcp.json ATAU node MCP Tools)
    import asyncio

    from fastmcp import Client

    async def tools():
        async with Client(MCP_URL) as c:
            return [t.name for t in await c.list_tools()]

    names = asyncio.run(tools())
    check("Tool MCP generate_clinical_document terdaftar",
          "generate_clinical_document" in names, ", ".join(names))
    if ENGINE == "bob":
        bob = shutil.which("bob") or str(Path.home() / ".local/share/npm/bin/bob")
        try:
            out = subprocess.run([bob, "--version"], capture_output=True,
                                 text=True, timeout=30)
            ver = (out.stdout or out.stderr).strip().splitlines()[:1]
            check("bob CLI sehat (bob --version)", out.returncode == 0,
                  ver[0] if ver else "")
        except Exception as exc:  # noqa: BLE001 — laporkan apa adanya
            check("bob CLI sehat (bob --version)", False, str(exc))
    # Express (history API) + MongoDB — prasyarat penyimpanan riwayat
    try:
        health = get(f"{HISTORY_URL}/api/health", timeout=10)
        check("Express history API hidup (:8790)", health.get("ok") is True)
        check("MongoDB terhubung (db=up)", health.get("db") == "up",
              str(health.get("db")))
    except Exception as exc:  # noqa: BLE001
        check("Express history API hidup (:8790)", False,
              f"{exc} — jalankan: node server/index.js (mongo harus UP)")


def _guard_output_common(label: str, out: str) -> None:
    leaked_pii = pii_hits(out)
    check(f"{label}: bebas pola PII (regex — teks input tak memuatnya)",
          not leaked_pii, f"bocor: {leaked_pii}" if leaked_pii else "")
    leaked = [f for f in INSTRUCTION_FRAGMENTS if f in out.lower()]
    check(f"{label}: bebas kebocoran instruksi prompt", not leaked,
          f"bocor: {leaked}" if leaked else "")
    check(f"{label}: bukan error BOB_GAGAL", "BOB_GAGAL" not in out, out[:200])


def step2_f1_analysis() -> tuple[list[str], str]:
    """F1 — analisis: tabel 4 kolom terisi, kolom alt KOSONG, tanpa dokumen.

    Mengembalikan (4_sel_cbt, output_teks).
    """
    print(f"\n[2] F1 — jurnal pasien -> analisis CBT (endpoint "
          f"{ENDPOINTS['bob']['f1']})")
    items, dt = run_flow(ENDPOINTS["bob"]["f1"], TEST_TEXT)
    print(f"  elapsed: {dt}s")
    thought = item_text(items.get("Thought Record", ""))
    print(f"  output : {thought[:500]}")
    check("Hanya satu output 'Thought Record'", sorted(items) == ["Thought Record"],
          str(sorted(items)))
    check("Output F1 tidak kosong", bool(thought.strip()))
    if not thought.strip():
        return [], thought

    _guard_output_common("F1", thought)
    missing = [h for h in TABLE_HEADERS if h not in thought]
    check("Output F1 = tabel dgn 5 header Indonesia (kolom alt dibuka)",
          not missing, f"header hilang: {missing}" if missing else "")
    check("F1 TANPA baris dokumen (AI tak membuat .docx di tahap analisis)",
          "Dokumen:" not in thought and ".docx" not in thought, thought[:300])

    rows = md_table_rows(thought)
    data = [r for r in rows if len(r) >= 4 and r[0] not in ("Situasi", "")]
    check("Tabel F1 memuat baris data", bool(data))
    # sel ke-5 boleh hilang bila model mengirim baris tanpa pipa penutup;
    # &nbsp; (HTML space) dinilai kosong — UI melakukan normalisasi yang sama
    alt_cells = [(r[4] if len(r) > 4 else "").replace("&nbsp;", "")
                 for r in data] if data else []
    check("Sel 'Pikiran Alternatif' KOSONG di semua baris (guardrail produk)",
          bool(data) and all(c.strip() in ("", "-") for c in alt_cells),
          str(alt_cells))
    cbt = data[0][:4] if data and len(data[0]) >= 4 else []
    check("4 sel CBT (Situasi/Pikiran/Emosi/Perilaku) terisi konten nyata",
          len(cbt) == 4 and all(len(c.strip()) >= 3 for c in cbt), str(cbt))
    return cbt, thought


def step3_f3_alternate(cbt: list[str]) -> str:
    """F3 — saran pikiran alternatif AI (opsi; pasien disarankan menulis manual)."""
    print(f"\n[3] F3 — saran pikiran alternatif (endpoint "
          f"{ENDPOINTS['bob']['f3']})")
    if not cbt:
        check("Payload F3 tersedia (dari hasil F1)", False)
        return ""
    payload = json.dumps({
        "situation": cbt[0], "thought": cbt[1],
        "emotion": cbt[2], "behavior": cbt[3],
    }, ensure_ascii=False)
    items, dt = run_flow(ENDPOINTS["bob"]["f3"], payload)
    print(f"  elapsed: {dt}s")
    alt = item_text(items.get("Saran Pikiran Alternatif", ""))
    print(f"  output : {alt[:400]}")
    check("Hanya satu output 'Saran Pikiran Alternatif'",
          sorted(items) == ["Saran Pikiran Alternatif"], str(sorted(items)))
    _guard_output_common("F3", alt)
    check("Saran alternatif >= 15 karakter", len(alt.strip()) >= 15,
          f"{len(alt.strip())} karakter")
    check("Tanpa pembuka 'Pikiran alternatif:' / tanda kutip pembungkus",
          not alt.strip().startswith(("Pikiran", '"', "'", "Tentu", "Berikut")),
          alt[:80])
    return alt.strip()


def step4_f2_document(cbt: list[str], alt: str) -> str:
    """F2 — dokumen .docx dari JSON 5 field; kembalikan path file."""
    print(f"\n[4] F2 — pembuatan dokumen (endpoint {ENDPOINTS['bob']['f2']})")
    if not cbt or not alt:
        check("Payload F2 lengkap (4 sel CBT + alt dari F3)", False)
        return ""
    before = {p.name for p in (BASE / "output").glob("brief_*.docx")}
    payload = json.dumps({
        "situation": cbt[0], "thought": cbt[1],
        "emotion": cbt[2], "behavior": cbt[3],
        "alternate_thought": alt,
    }, ensure_ascii=False)
    items, dt = run_flow(ENDPOINTS["bob"]["f2"], payload)
    print(f"  elapsed: {dt}s")
    out = item_text(items.get("Dokumen", ""))
    print(f"  output : {out[:400]}")
    check("Hanya satu output 'Dokumen'", sorted(items) == ["Dokumen"],
          str(sorted(items)))
    _guard_output_common("F2", out)
    check("Output F2 = baris 'Dokumen: <path>'", out.strip().startswith("Dokumen:"),
          out[:200])
    check("F2 tanpa 'ERROR:' (tool MCP berhasil)", "ERROR:" not in out, out[:200])

    m = (re.search(r"(/[\w./-]*/brief_[0-9a-f]{8}\.docx)", out)
         or re.search(r"(output/brief_[0-9a-f]{8}\.docx)", out))
    check("Tool MCP mengembalikan path dokumen", bool(m),
          out[:300] if not m else m.group(1))
    if not m:
        return ""
    path = Path(m.group(1) if m.group(1).startswith("/") else BASE / m.group(1))
    check("File .docx dibuat & ada", path.exists(), str(path))
    if path.exists():
        check("File .docx BARU dibuat pada run ini", path.name not in before,
              path.name)
    return str(path) if path.exists() else ""


def step5_output_file(doc_path: str, alt: str) -> None:
    print("\n[5] Cek output fisik (.docx — tabel Thought Record 5 kolom)")
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
            check("Sel 'Pikiran Alternatif' berisi alt hasil F3 (bukan placeholder)",
                  len(cells[4]) >= 15 and cells[4].lower() not in PLACEHOLDERS,
                  cells[4][:80])
            if alt:
                check("Sel alt di dokumen = saran F3", cells[4] == alt,
                      f"docx={cells[4][:60]!r} vs f3={alt[:60]!r}")
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


def step6_history_roundtrip(cbt: list[str], alt: str, doc_path: str) -> None:
    """Roundtrip history di Express/MongoDB — guardrail G1/G2/G4/G5."""
    print("\n[6] Roundtrip history Express/MongoDB (guardrail G1/G2/G4/G5)")
    analysis = {"situation": cbt[0] if cbt else "-", "thought": cbt[1] if cbt else "-",
                "emotion": cbt[2] if cbt else "-", "behavior": cbt[3] if cbt else "-"}
    full = {
        "journal": TEST_TEXT,
        "analysis": analysis,
        "alternateThought": {"text": alt, "source": "manual"},
        "documentPath": doc_path,
    }

    # POST valid → 201
    status, created = request_json("POST", f"{HISTORY_URL}/api/history", full,
                                   raise_http=False)
    check("POST history valid → 201", status == 201,
          f"status={status} body={json.dumps(created)[:200]}")
    created_id = created.get("_id", "")

    # GET list memuat entri
    status, listing = request_json("GET", f"{HISTORY_URL}/api/history",
                                   raise_http=False)
    ids = [it.get("_id") for it in listing.get("items", [])] if status == 200 else []
    check("GET /api/history → 200 & entri terdaftar", status == 200 and created_id in ids,
          f"status={status} jumlah={len(ids)}")

    # G1 — journal terlalu pendek → 400
    bad = dict(full, journal="pendek")
    status, body = request_json("POST", f"{HISTORY_URL}/api/history", bad,
                                raise_http=False)
    check("G1: journal < 40 karakter → 400", status == 400,
          f"status={status} {json.dumps(body)[:160]}")

    # G2/G5 — analysis kosong → 400
    bad = dict(full, analysis={"situation": "", "thought": "", "emotion": "",
                               "behavior": ""})
    status, body = request_json("POST", f"{HISTORY_URL}/api/history", bad,
                                raise_http=False)
    check("G2: analysis kosong → 400", status == 400,
          f"status={status} {json.dumps(body)[:160]}")

    # G4 — documentPath tanpa alt → 400
    bad = dict(full, alternateThought={"text": "", "source": ""},
               documentPath=doc_path or "output/brief_x.docx")
    status, body = request_json("POST", f"{HISTORY_URL}/api/history", bad,
                                raise_http=False)
    check("G4: documentPath tanpa alternateThought → 400", status == 400,
          f"status={status} {json.dumps(body)[:160]}")

    # PATCH alt → 200
    if created_id:
        status, body = request_json(
            "PATCH", f"{HISTORY_URL}/api/history/{created_id}",
            {"alternateThought": {"text": alt or "ditulis manual pasien",
                                  "source": "manual"}},
            raise_http=False)
        check("PATCH alternateThought → 200", status == 200,
              f"status={status} {json.dumps(body)[:160]}")
        # G5 — PATCH alt terlalu pendek → 400
        status, body = request_json(
            "PATCH", f"{HISTORY_URL}/api/history/{created_id}",
            {"alternateThought": {"text": "ab", "source": "manual"}},
            raise_http=False)
        check("G3: alternateThought < 5 karakter → 400", status == 400,
              f"status={status} {json.dumps(body)[:160]}")

        # Bersihkan entri uji
        status, body = request_json(
            "DELETE", f"{HISTORY_URL}/api/history/{created_id}",
            raise_http=False)
        check("DELETE history → 200", status == 200,
              f"status={status} {json.dumps(body)[:160]}")
    else:
        check("Entri uji dibuat untuk PATCH/DELETE", False)


def legacy_gemini() -> tuple[str, str]:
    """Jalur cadangan Gemini: satu run → tabel 5 kolom + baris Dokumen (legacy)."""
    print(f"\n[2] Satu POST ke Langflow: jurnal pasien -> "
          f"Agent (Gemini) -> node MCP Tools -> {ENDPOINTS['gemini']['legacy']}")
    items, dt = run_flow(ENDPOINTS["gemini"]["legacy"], TEST_TEXT)
    print(f"  elapsed: {dt}s")
    print(f"  output : {sorted(items)}")
    thought = item_text(items.get("Thought Record", {}))
    print(f"  output : {thought[:500]}")
    check("Hanya satu output 'Thought Record'", sorted(items) == ["Thought Record"],
          str(sorted(items)))
    check("Output Thought Record tidak kosong", bool(thought.strip()))
    return thought, ""


def step_legacy_verify(thought: str) -> str:
    """Verifikasi output legacy (tabel 5 kolom terisi + baris Dokumen)."""
    print("\n[3] Verifikasi output legacy (Agent Gemini + tool MCP)")
    result_str = thought.strip()
    missing_headers = [h for h in TABLE_HEADERS if h not in result_str]
    check("Output = tabel Markdown dgn header Indonesia",
          not missing_headers,
          f"header hilang: {missing_headers}" if missing_headers else "")
    _guard_output_common("legacy", result_str)
    m = (re.search(r"(/[\w./-]*/brief_[0-9a-f]{8}\.docx)", result_str)
         or re.search(r"(output/brief_[0-9a-f]{8}\.docx)", result_str))
    check("Tool MCP mengembalikan path dokumen", bool(m),
          result_str[:300] if not m else m.group(1))
    if not m:
        return ""
    path = Path(m.group(1) if m.group(1).startswith("/") else BASE / m.group(1))
    check("File .docx dibuat & ada", path.exists(), str(path))
    return str(path) if path.exists() else ""


def main() -> int:
    t0 = time.time()
    if not preflight():
        return 1
    step1_health()

    if ENGINE == "bob":
        cbt, _ = step2_f1_analysis()
        alt = step3_f3_alternate(cbt)
        doc_path = step4_f2_document(cbt, alt)
        step5_output_file(doc_path, alt)
        step6_history_roundtrip(cbt, alt, doc_path)
    else:
        thought, _ = legacy_gemini()
        doc_path = step_legacy_verify(thought)
        step5_output_file(doc_path, alt="")
        rows = md_table_rows(thought)
        data = [r for r in rows if len(r) >= 5 and r[0] not in ("Situasi", "")]
        cbt = data[0][:4] if data and len(data[0]) >= 4 else []
        alt = data[0][4] if data and len(data[0]) >= 5 else ""
        step6_history_roundtrip(cbt, alt, doc_path)

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
