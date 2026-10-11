# HearMeOut — BriefME · Clinical Journaling Copilot (IBM Bob + Langflow + MCP)

Implementasi `Agent-Plan.md` untuk hackathon: **engine IBM Bob (API key resmi) +
Langflow sebagai orkestrasi, dengan MCP di sisi Bob** — domain tetap BriefME.
Satu POST ke flow **BriefME** (6 node / 5 edge) membalas **Thought Record**
(tabel Markdown 5 kolom Indonesia + baris `Dokumen: <path>`) sekaligus membuat
dokumen `.docx` lewat tool MCP `generate_clinical_document`.

```
Payload uji (cURL/Postman/Frontend)
        │  POST /api/v1/run/briefme
        ▼
┌─ Langflow :7860 — flow "BriefME": 6 node / 5 edge ───────────────────────────┐
│                                                                              │
│  Chat Input ──→ Prompt ──→ Python Interpreter ──→ Parse JSON ──→ Parser ──→   │
│  (jurnal)       (kode      (bob run --mode        (jq: ambil      (format     │
│                 CBT +       agent --format         last_message)   {text})    │
│                 {text})     json, timeout 900s)                            │
│                                                    └──────────→ Chat Output   │
│                                                       "Thought Record"        │
└──────────────────────────────────┬───────────────────────────────────────────┘
                                   │  Bob (workspace ./) memanggil tool MCP
                                   ▼
        ┌─ .bob/mcp.json ─► http://127.0.0.1:8788/mcp ─────────────────┐
┌─ MCP Server HearMeOut_Tools :8788 (mcp_server.py) ────────────────────┐
│ docxtpl → template_cbt.docx → output/brief_<id>.docx (+ audit.jsonl) │
└───────────────────────────────────────────────────────────────────────┘

Cadangan: flow "BriefME (Gemini)" (5 node/4 edge, endpoint briefme-gemini) —
Agent + GoogleGenerativeAI + MCPTools; dibuat/di-clone oleh builder.
```

## Fitur

* Ekstraksi jurnal → matriks CBT (Situasi, Pikiran, Emosi, Perilaku, Pikiran
  Alternatif) oleh **Bob** dengan prompt klinis yang ketat (tanpa saran medis,
  tanpa diagnosa, objektif).
* Tool calling Bob ke MCP lokal → dokumen Word siap pakai + jejak audit JSONL.
* Pasca-proses native Langflow (Parse JSON + Parser) sehingga frontend menerima
  tabel siap render, bukan JSON mentah.
* Guard otomatis: regex PII, deteksi kebocoran instruksi prompt, cek isi .docx.

## Komponen & berkas

| Tahap | Berkas | Keterangan |
|---|---|---|
| 1 — Flow Langflow | `scripts/create_langflow_flow.py`, `.flow_id`, `.flow_id.gemini` | builder `--engine bob` (default, 6 node) / `--engine gemini` (cadangan 5 node); migrasi clone otomatis |
| 2 — Brain Bob | `.bob/mcp.json`, `.env` (`BOB_API_KEY`, `BOB_MAX_TURNS`) | `bob run --mode agent` dari node Python Interpreter; MCP sisi Bob |
| 3 — MCP Server | `mcp_server.py`, `template_cbt.docx`, `make_template.py` | tool `generate_clinical_document` (port 8788) |
| 4 — E2E test | `scripts/run_e2e.py` | `E2E_ENGINE=bob\|gemini`: preflight kunci → health → run → verifikasi dokumen |
| 5 — Web (UI) | `web/` (React + Vite + TS) | jurnal → `POST /api/v1/run/briefme` via proxy dev; footer transparansi AI |

## Setup

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/python make_template.py        # buat template_cbt.docx
cp .env.example .env                     # lalu isi LANGFLOW_API_KEY, BOB_API_KEY
```

Pra-syarat mesin:

* **Node.js ≥ 22** user-level — untuk `bobshell` (Bob 2.0.6, `~/.local/share/npm/bin/bob`)
  dan build web UI.
* **Langflow** dengan API key superuser (sekali: `cd ~/langflow && ./.venv/bin/langflow api-key`).
* **Kunci API Bob asli** di `.env` (`BOB_API_KEY=…`) — engine utama.
* *(Opsional — hanya untuk flow cadangan Gemini)* kunci `GOOGLE_API_KEY` asli;
  placeholder `xxx` ditolak Langflow (validasi live ke Google).

## Menjalankan

```bash
set -a; . ./.env; set +a

# 1 — MCP Server (tool dokumen)
.venv/bin/python mcp_server.py            # → http://127.0.0.1:8788/mcp

# 2 — Buat/update flow (saat prompt/arsitektur berubah)
python scripts/create_langflow_flow.py                 # engine bob (default)
python scripts/create_langflow_flow.py --engine gemini # flow cadangan Gemini

# 3 — Uji end-to-end: preflight → run → verifikasi .docx
.venv/bin/python scripts/run_e2e.py
E2E_ENGINE=gemini .venv/bin/python scripts/run_e2e.py  # uji flow cadangan

# 4 — Web UI: dev server + proxy API ke Langflow
cd web && npm install && npm run dev   # → http://localhost:5173/
```

### Kirim payload manual

```bash
set -a; . ./.env; set +a
curl -s http://127.0.0.1:7860/api/v1/run/briefme \
  -H "Content-Type: application/json" -H "x-api-key: $LANGFLOW_API_KEY" \
  -d '{"input_value":"Kemarin saya berdiskusi dengan atasan di kantor. Saya cemas berlebihan dan jantung berdebar.","output_type":"chat","data_type":"text"}'
```

Satu output dibalas: `Thought Record` (header `Situasi | Pikiran | Emosi |
Perilaku | Pikiran Alternatif` + `Dokumen: <path output/brief_*.docx>`).

## Catatan penting

* **Rantai dalam satu POST.** Prompt node me-render kode CBT (`{text}` → jurnal);
  outputnya menimpa field `python_code` di runtime; Python Interpreter menjalankan
  `bob run` (timeout 900s); Parse JSON mengekstrak `last_message` dari stdout
  JSON Bob (tahan noise + code fence); Parser memformat baris.
* **MCP di sisi Bob.** `.bob/mcp.json` memakai kunci **`url`** (bukan `httpURL`)
  dengan `timeout` milidetik; `alwaysAllow` untuk `generate_clinical_document`.
  Node MCPTools Langflow hanya dipakai flow cadangan Gemini.
* **Sandbox Python Interpreter.** Kode node tanpa pernyataan `import` (ditolak
  `validate_code_safety`) — module datang dari `global_imports`
  (`math,os,subprocess`); builtins tanpa `open()` — fallback kunci dibaca via
  `subprocess cat .env`. Path bob absolut karena PATH Langflow tidak memuat
  `~/.local/share/npm/bin`.
* **Kunci & validasi.** `GOOGLE_API_KEY` (cadangan) disinkron builder ke variabel
  global Langflow dengan strategi DELETE+POST (PATCH 404 untuk variabel provider)
  dan divalidasi live — placeholder ditolak. `BOB_API_KEY` cukup di `.env`
  (dibaca builder/e2e dari sana dan node Python saat runtime).
* **Autosave Langflow.** Tab editor yang terbuka bisa men-*save* flow dan
  mengembalikannya ke versi lama (terbukti lagi 11 Okt — flow 6 node sempat
  ditimpa jadi 4 node). **Tutup tab editor flow sebelum/sesudah builder**, lalu
  verifikasi: jumlah node flow utama harus `6`
  (`curl -s -H "x-api-key: $LANGFLOW_API_KEY" http://127.0.0.1:7860/api/v1/flows/fb741b8a-c2c0-4d41-9b75-df9294732ab5 | jq '.data.nodes|length'`).
* **Kualitas ekstraksi.** `AGENT_SYSTEM_PROMPT` mewajibkan panggilan tool
  (tepat satu kali) dan melarang jawaban "timeout" tanpa mencoba tool; e2e
  memeriksa kebocoran frasa prompt ke output/dokumen (`INSTRUCTION_FRAGMENTS`).
* **Tanpa sensor lokal (keputusan 9 Okt).** Input dianggap tanpa data pribadi;
  PII jadi guard regex di e2e. Riwayat iterasi di `AGENT_LOG.md` bag.3–8.
* **Web UI (`web/`).** React + Vite + TypeScript + framer-motion; proxy dev
  meneruskan `/api/*` ke Langflow dan menyuntikkan `x-api-key` dari `../.env`
  (key tidak pernah masuk ke kode frontend); footer transparansi AI di halaman;
  `npm run build` lolos (tsc + vite).

## Verifikasi manual

* Status e2e terakhir: **semua pemeriksaan LOLOS** (engine bob, ±20s;
  `E2E_ENGINE=gemini` butuh kunci Gemini asli)
* Dokumen hasil: `output/brief_*.docx` + jejak audit `output/audit.jsonl`
* Log MCP: `tail -f /tmp/opencode/mcp_server.log`
* Log Langflow: `tail -f /tmp/langflow-server.log`
* Keputusan & angka ukur: `AGENT_LOG.md` (bag.17 refactor Gemini, bag.18 final Bob)
