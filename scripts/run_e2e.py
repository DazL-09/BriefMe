"""Pengujian terintegrasi (end-to-end) BriefME — SATU flow multi-mode.

Alur:
  1. Preflight: LANGFLOW_API_KEY + BOB_API_KEY + bob CLI + Langflow hidup +
     Express history API (:8790) + MongoDB
  2. F1  POST /api/v1/run/briefme (jurnal polos) → tabel CBT 4 kolom terisi,
       kolom "Pikiran Alternatif" KOSONG, TANPA baris "Dokumen:",
       guard regex PII + guard kebocoran instruksi prompt
  3. F3  POST /api/v1/run/briefme (prefix "MODE=ALTERNATIF\\n" + JSON 4 field)
       → SATU saran pikiran alternatif (>= 15 karakter, tanpa pembuka/preamble)
  4. Roundtrip history Express/MongoDB: POST valid 201, G1 journal pendek 400,
     analysis kosong 400, documentPath tanpa alt 400 (G4), PATCH alt, DELETE

Dokumen .docx TIDAK diuji di sini — dibuat di browser (web, library docx);
Playwright flow.mjs yang mengujinya.

Pemakaian:
    .venv/bin/python scripts/run_e2e.py
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

LF_URL = os.environ.get("LANGFLOW_URL", "http://127.0.0.1:7860").rstrip("/")
LF_KEY = os.environ.get("LANGFLOW_API_KEY", "")
HISTORY_URL = os.environ.get("HISTORY_URL", "http://127.0.0.1:8790").rstrip("/")
BOB_KEY = os.environ.get("BOB_API_KEY", "").strip()

ENDPOINT = "briefme"

# Jurnal pasien realistis TANPA data pribadi (input flow memang dianggap bersih).
TEST_TEXT = (
    "Kemarin saya berdiskusi dengan atasan soal target kuartal di kantor. "
    "Sejak itu saya cemas berlebihan, jantung berdebar, dan susah tidur. "
    "Saya takut saya akan dipecat."
)

# Guard regex PII: input tidak memuat pola ini, jadi output tidak boleh.
PII_PATTERNS = {
    "nomor HP": re.compile(r"\b0\d{2,4}[-\s]?\d{3,4}[-\s]?\d{3,4}\b"),
    "email": re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]{2,}"),
    "alamat jalan": re.compile(r"\bJl\.\s*\w+", re.I),
}
# header tabel Markdown (output)
TABLE_HEADERS = ["Situasi", "Pikiran", "Emosi", "Perilaku", "Pikiran Alternatif"]
# potongan prompt (builder) yang tidak boleh bocor ke output flow
INSTRUCTION_FRAGMENTS = [
    "langkah wajib",
    "analisis teks secara internal",
    "objek json dari langkah",
    "satu baris data per situasi",
    "gaya restrukturisasi kognitif ringan",
    "identitas pasien",
    "memanggil tool",
    "saran medis",
    "tanpa teks lain",
]

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


def run_flow(text: str, timeout: int = 1800) -> tuple[dict, float]:
    """POST /api/v1/run/briefme → ({nama_component: item}, elapsed)."""
    t0 = time.time()
    status, res = request_json(
        "POST", f"{LF_URL}/api/v1/run/{ENDPOINT}",
        {"input_value": text, "output_type": "chat", "data_type": "text"},
        headers={"x-api-key": LF_KEY}, timeout=timeout, raise_http=False,
    )
    if status != 200:
        raise SystemExit(f"HTTP {status} dari run/{ENDPOINT}: {json.dumps(res)[:1000]}")
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
    print(f"endpoint: {ENDPOINT} (satu flow multi-mode)")
    if not LF_KEY:
        print("[FAIL] LANGFLOW_API_KEY kosong — cek .env")
        ok = False
    if not BOB_KEY or len(BOB_KEY) < 20:
        print("[FAIL] BOB_API_KEY kosong/pendek di .env — isi kunci API resmi Bob "
              "lalu jalankan ulang")
        ok = False
    return ok


def step1_health() -> None:
    print("\n[1] Health check komponen")
    check("Langflow hidup", get(f"{LF_URL}/health").get("status") == "ok")
    bob_path = shutil.which("bob") or str(Path.home() / ".local/share/npm/bin/bob")
    try:
        bob = subprocess.run([bob_path, "--version"], capture_output=True,
                             text=True, timeout=30)
        ver = (bob.stdout or bob.stderr).strip().splitlines()[:1]
        check("bob CLI sehat (bob --version)", bob.returncode == 0,
              ver[0] if ver else "")
    except Exception as exc:  # noqa: BLE001 — laporkan apa adanya
        check("bob CLI sehat (bob --version)", False, str(exc))
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
    print(f"\n[2] F1 — jurnal pasien -> analisis CBT (endpoint {ENDPOINT})")
    items, dt = run_flow(TEST_TEXT)
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
    """F3 — saran pikiran alternatif AI (opsi; pasien disarankan menulis manual).

    Input = prefix "MODE=ALTERNATIF\\n" + JSON 4 field — endpoint yang SAMA.
    """
    print(f"\n[3] F3 — saran pikiran alternatif (prefix MODE=ALTERNATIF, endpoint "
          f"{ENDPOINT})")
    if not cbt:
        check("Payload F3 tersedia (dari hasil F1)", False)
        return ""
    payload = "MODE=ALTERNATIF\n" + json.dumps({
        "situation": cbt[0], "thought": cbt[1],
        "emotion": cbt[2], "behavior": cbt[3],
    }, ensure_ascii=False)
    items, dt = run_flow(payload)
    print(f"  elapsed: {dt}s")
    alt = item_text(items.get("Thought Record", ""))
    print(f"  output : {alt[:400]}")
    check("Hanya satu output 'Thought Record'", sorted(items) == ["Thought Record"],
          str(sorted(items)))
    _guard_output_common("F3", alt)
    check("Saran alternatif >= 15 karakter", len(alt.strip()) >= 15,
          f"{len(alt.strip())} karakter")
    check("Tanpa tabel/pembuka — SATU paragraf saran",
          "|" not in alt and not alt.strip().startswith(
              ("Pikiran", '"', "'", "Tentu", "Berikut")),
          alt[:80])
    return alt.strip()


def step4_history_roundtrip(cbt: list[str], alt: str) -> None:
    """Roundtrip history di Express/MongoDB — guardrail G1/G2/G4/G5.

    documentPath dikirim '' (produk menyimpannya di client-side browser saja).
    """
    print("\n[4] Roundtrip history Express/MongoDB (guardrail G1/G2/G4/G5)")
    analysis = {"situation": cbt[0] if cbt else "-", "thought": cbt[1] if cbt else "-",
                "emotion": cbt[2] if cbt else "-", "behavior": cbt[3] if cbt else "-"}
    full = {
        "journal": TEST_TEXT,
        "analysis": analysis,
        "alternateThought": {"text": alt, "source": "manual"},
        "documentPath": "",
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

    # G4 — documentPath tanpa alt → 400 (kontrak Express tetap ada meski web
    # tidak lagi mengirim documentPath)
    bad = dict(full, alternateThought={"text": "", "source": ""},
               documentPath="output/brief_x.docx")
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


def main() -> int:
    t0 = time.time()
    if not preflight():
        return 1
    step1_health()
    cbt, _ = step2_f1_analysis()
    alt = step3_f3_alternate(cbt)
    step4_history_roundtrip(cbt, alt)

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
