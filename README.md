# HearMeOut — BriefME · Clinical Journaling Copilot (IBM Bob + Langflow + MCP)

Implementasi `Agent-Plan.md` untuk hackathon: **engine IBM Bob (API key resmi) +
Langflow sebagai orkestrasi, dengan MCP di sisi Bob** — domain tetap BriefME.
Alur produk dijalankan lewat **tiga flow Langflow** (analisis → saran alternatif →
dokumen) dan riwayat curhatan disimpan pada stack **MERN (MongoDB + Express +
React)**.

```
                                   ┌─ Web UI (React/Vite :5173) ─────────────────┐
                                   │  1 Analisis → 2 Pikiran Alternatif →        │
                                   │  3 Buat Dokumen + panel Riwayat             │
                                   └───────┬──────────────────────┬───────────────┘
                     POST /api/v1/run/*    │                      │  /api/history (CRUD)
                                           ▼                      ▼
┌─ Langflow :7860 ─ 3 flow (masing-masing 6 node / 5 edge) ─┐   ┌─ Express :8790 ─┐
│                                                            │   │  server/index.js│
│  briefme            F1 analisis   (bob --mode agent        │   │  guard G1–G5    │
│    Chat Input → Prompt → Python Interpreter → Parse JSON   │   └────────┬────────┘
│    → Parser → Chat Output "Thought Record"                 │            ▼
│    (4 kolom CBT terisi, kolom Pikiran Alternatif KOSONG,    │   ┌─ MongoDB :27017 ─┐
│     --disable-mcp, TANPA baris Dokumen)                    │   │ koleksi entries   │
│                                                            │   │ (journal, analysis│
│  briefme-alternatif  F3 saran alt (bob --mode ask,         │   │  , alt, docPath)  │
│    --disable-mcp → SATU kalimat seimbang)                  │   └───────────────────┘
│                                                            │
│  briefme-dokumen     F2 dokumen (bob --mode agent, MCP     │
│    aktif → balas HANYA "Dokumen: <path>")                  │
└──────────────────────────┬─────────────────────────────────┘
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

* **Tiga tahap eksplisit** — F1 analisis matriks CBT 4 kolom (Situasi, Pikiran,
  Emosi, Perilaku) oleh **Bob dengan prompt klinis ketat** (tanpa saran medis,
  tanpa diagnosa); kolom **Pikiran Alternatif KOSONG** — AI sengaja tidak
  mengisi otomatis (guardrail produk).
* **Pikiran alternatif manual (disarankan) atau saran AI (opsi)** — saran AI
  diberi badge `AI`, boleh diedit; divalidasi sebelum disimpan (min. 5 karakter,
  bukan placeholder).
* **Dokumen `.docx` hanya setelah alt valid** — tombol *Buat Dokumen* (G4)
  memanggil flow F2 → Bob → tool MCP `generate_clinical_document` → jejak audit
  JSONL.
* **Riwayat curhatan (MERN)** — auto-save saat analisis berhasil, PATCH saat alt
  & dokumen selesai, buka/hapus dari panel Riwayat; MongoDB via Express.
* **Guardrails G1–G5** — G1 validasi curhatan di klien (min. 8 kata / 40
  karakter, deteksi bukan-curhatan), G2 analisis kosong tidak disimpan, G3 alt
  minimal 5 karakter non-placeholder, G4 dokumen butuh alt, G5 Express
  revalidasi semua tulis.
* **Guard otomatis di e2e** — regex PII, deteksi kebocoran instruksi prompt,
  cek isi `.docx`, roundtrip history (400 pada pelanggaran guardrail).

## Komponen & berkas

| Tahap | Berkas | Keterangan |
|---|---|---|
| 1 — Flow Langflow | `scripts/create_langflow_flow.py`, `.flow_id`, `.flow_id.dokumen`, `.flow_id.alternatif`, `.flow_id.gemini` | builder `--engine bob` (default) membuat **3 flow**; `--engine gemini` = cadangan; migrasi clone otomatis |
| 2 — Brain Bob | `.bob/mcp.json`, `.env` (`BOB_API_KEY`, `BOB_MAX_TURNS`) | `bob run` dari node Python Interpreter; MCP sisi Bob; flag `--disable-mcp` untuk F1/F3 |
| 3 — MCP Server | `mcp_server.py`, `template_cbt.docx`, `make_template.py` | tool `generate_clinical_document` (port 8788) |
| 4 — History API | `server/index.js`, `server/package.json` | Express + Mongoose (:8790) — CRUD `/api/history` + guardrail G5 |
| 5 — E2E test | `scripts/run_e2e.py` | `E2E_ENGINE=bob\|gemini`: preflight → F1 → F3 → F2 → cek .docx → roundtrip history |
| 6 — Web (UI) | `web/` (React + Vite + TS) | alur 3 tahap + panel Riwayat; proxy `/api/history` → Express, `/api` → Langflow |

## Setup

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/python make_template.py        # buat template_cbt.docx
cp .env.example .env                     # lalu isi LANGFLOW_API_KEY, BOB_API_KEY

cd server && npm install                 # dependensi Express/Mongoose
```

Pra-syarat mesin:

* **Node.js ≥ 22** user-level — untuk `bobshell` (Bob 2.0.6, `~/.local/share/npm/bin/bob`)
  dan build web UI.
* **Docker** — MongoDB history: `docker run -d --name briefme-mongo -p 27017:27017
  --restart unless-stopped mongo:7`
* **Langflow** dengan API key superuser (sekali: `cd ~/langflow && ./.venv/bin/langflow api-key`).
* **Kunci API Bob asli** di `.env` (`BOB_API_KEY=…`) — engine utama.
* *(Opsional — hanya untuk flow cadangan Gemini)* kunci `GOOGLE_API_KEY` asli;
  placeholder `xxx` ditolak Langflow (validasi live ke Google).

## Menjalankan

```bash
set -a; . ./.env; set +a

# 1 — MongoDB history (sekali per mesin)
docker start briefme-mongo 2>/dev/null || docker run -d --name briefme-mongo \
  -p 27017:27017 --restart unless-stopped mongo:7

# 2 — MCP Server (tool dokumen)
.venv/bin/python mcp_server.py            # → http://127.0.0.1:8788/mcp

# 3 — History API (Express + Mongoose)
node server/index.js                      # → http://127.0.0.1:8790/api/history

# 4 — Buat/update flow (saat prompt/arsitektur berubah)
python scripts/create_langflow_flow.py                 # engine bob → 3 flow
python scripts/create_langflow_flow.py --engine gemini # flow cadangan Gemini

# 5 — Uji end-to-end: preflight → F1 → F3 → F2 → .docx → roundtrip history
.venv/bin/python scripts/run_e2e.py
E2E_ENGINE=gemini .venv/bin/python scripts/run_e2e.py  # uji flow cadangan

# 6 — Web UI: dev server + proxy (History → Express, sisanya → Langflow)
cd web && npm install && npm run dev   # → http://localhost:5173/
```

### Kirim payload manual

```bash
set -a; . ./.env; set +a
# F1 — analisis (tabel 4 kolom, alt KOSONG):
curl -s http://127.0.0.1:7860/api/v1/run/briefme \
  -H "Content-Type: application/json" -H "x-api-key: $LANGFLOW_API_KEY" \
  -d '{"input_value":"Kemarin saya berdiskusi dengan atasan di kantor. Saya cemas berlebihan dan jantung berdebar.","output_type":"chat","data_type":"text"}'
# F3 — saran pikiran alternatif (input = JSON 4 kunci CBT):
curl -s http://127.0.0.1:7860/api/v1/run/briefme-alternatif \
  -H "Content-Type: application/json" -H "x-api-key: $LANGFLOW_API_KEY" \
  -d '{"input_value":"{\"situation\":\"diskusi target\",\"thought\":\"saya akan dipecat\",\"emotion\":\"cemas\",\"behavior\":\"susah tidur\"}","output_type":"chat","data_type":"text"}'
# F2 — dokumen (input = JSON 5 kunci, alternate_thought wajib terisi):
curl -s http://127.0.0.1:7860/api/v1/run/briefme-dokumen \
  -H "Content-Type: application/json" -H "x-api-key: $LANGFLOW_API_KEY" \
  -d '{"input_value":"{\"situation\":\"diskusi target\",\"thought\":\"saya akan dipecat\",\"emotion\":\"cemas\",\"behavior\":\"susah tidur\",\"alternate_thought\":\"satu diskusi belum tentu berarti dipecat\"}","output_type":"chat","data_type":"text"}'
```

Output F1 = tabel Markdown (header `Situasi | Pikiran | Emosi | Perilaku |
Pikiran Alternatif`, sel alt kosong); F3 = satu kalimat saran; F2 = `Dokumen:
<path output/brief_*.docx>`.

## Catatan penting

* **Rantai per flow.** Prompt node me-render kode builder (`{text}` → input);
  outputnya menimpa field `python_code` di runtime; Python Interpreter menjalankan
  `bob run` (timeout 900s); Parse JSON mengekstrak `last_message` dari stdout
  JSON Bob (tahan noise + code fence); Parser memformat baris. Prompt per tahap:
  `PROMPT_ANALYSIS` (F1), `PROMPT_ALTERNATE` (F3), `PROMPT_DOCUMENT` (F2) —
  semuanya tanpa kurung kurawal (wajib untuk str.format).
* **Flag Bob per tahap.** F1/F3 memakai `--disable-mcp` (tak menyentuh tool);
  F3 `--mode ask` (jawaban tunggal); F2 `--mode agent` dengan MCP aktif. Bob
  baca `.bob/mcp.json` (kunci **`url`**, bukan `httpURL`, `timeout` milidetik,
  `alwaysAllow` untuk `generate_clinical_document`).
* **Sandbox Python Interpreter.** Kode node tanpa pernyataan `import` (ditolak
  `validate_code_safety`) — module datang dari `global_imports`
  (`math,os,subprocess`); builtins tanpa `open()` — fallback kunci dibaca via
  `subprocess cat .env`. Path bob absolut karena PATH Langflow tidak memuat
  `~/.local/share/npm/bin`.
* **Proxy web.** `web/vite.config.ts` mendaftarkan `/api/history` (Express :8790)
  **sebelum** `/api` (Langflow) — urutan ini wajib; `x-api-key` disuntikkan proxy
  (key tidak pernah masuk frontend).
* **Kunci & validasi.** `GOOGLE_API_KEY` (cadangan) disinkron builder ke variabel
  global Langflow dengan strategi DELETE+POST (PATCH 404 untuk variabel provider)
  dan divalidasi live — placeholder ditolak. `BOB_API_KEY` cukup di `.env`
  (dibaca builder/e2e dari sana dan node Python saat runtime).
* **Autosave Langflow.** Tab editor yang terbuka bisa men-*save* flow dan
  mengembalikannya ke versi lama (terbukti 11 Okt). **Tutup tab editor flow
  sebelum/sesudah builder**, lalu verifikasi jumlah node tiap flow = `6`
  (id: `.flow_id` fb741b8a…, `.flow_id.dokumen` f035c624…, `.flow_id.alternatif`
  ae7cc7b8…).
* **Sel alt `&nbsp;`.** Bob kadang menulis `&nbsp;` untuk sel kosong — UI dan
  e2e menormalisasi jadi kosong (prompt F1 juga melarangnya).
* **Tanpa sensor lokal (keputusan 9 Okt).** Input dianggap tanpa data pribadi;
  PII jadi guard regex di e2e. Riwayat iterasi di `AGENT_LOG.md` bag.3–8.
* **Web UI (`web/`).** React + Vite + TypeScript + framer-motion; footer
  transparansi AI di halaman; `npm run build` lolos (tsc + vite).

## Verifikasi manual

* Status e2e terakhir: **semua pemeriksaan LOLOS 34.4s** (engine bob: preflight →
  F1 → F3 → F2 + docx → roundtrip history G1–G5; `E2E_ENGINE=gemini` butuh kunci
  Gemini asli)
* Uji browser (Playwright, 19 pemeriksaan): **19/19 LOLOS** — G1, sel alt kosong,
  G3, saran AI + badge, G4, dokumen, riwayat buka/hapus
* Dokumen hasil: `output/brief_*.docx` + jejak audit `output/audit.jsonl`
* Log MCP: `tail -f /tmp/opencode/mcp_server.log`
* Log Langflow: `tail -f /tmp/langflow-server.log`
* Keputusan & angka ukur: `AGENT_LOG.md` (bag.18 final Bob, bag.19 fitur baru)
