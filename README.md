# HearMeOut — BriefME · Clinical Journaling Copilot (IBM Bob + Langflow)

Implementasi `Agent-Plan.md` untuk hackathon: **engine IBM Bob (API key resmi) +
Langflow sebagai orkestrasi** — domain BriefME. Produk berjalan lewat **SATU flow
Langflow multi-mode** (analisis / saran alternatif via awalan input) dan riwayat
curhatan disimpan pada stack **MERN (MongoDB + Express + React)**. Dokumen
`.docx` dibuat **di browser** (library `docx` + `file-saver`) — tanpa AI/MCP.

```
                                   ┌─ Web UI (React/Vite :5173) ─────────────────┐
                                   │  1 Analisis → 2 Pikiran Alternatif →        │
                                   │  3 Buat Dokumen (.docx lokal) + Riwayat     │
                                   └───────┬──────────────────────┬───────────────┘
                     POST /api/v1/run/     │                      │  /api/history (CRUD)
                     briefme               ▼                      ▼
┌─ Langflow :7860 ─ SATU flow "BriefME" (6 node / 5 edge) ──┐   ┌─ Express :8790 ─┐
│                                                            │   │  server/index.js│
│  briefme                                                   │   │  guard G1–G5    │
│    Chat Input → Prompt → Python Interpreter → Parse JSON   │   └────────┬────────┘
│    → Parser → Chat Output "Thought Record"                 │            ▼
│                                                            │   ┌─ MongoDB :27017 ─┐
│  Mode dibaca dari AWALAN input (bukan flow terpisah):      │   │ koleksi entries   │
│    - jurnal polos → PROMPT_ANALYSIS (bob --mode agent      │   │ (journal, analysis│
│      --disable-mcp): tabel CBT 4 kolom, alt KOSONG         │   │  , alt)           │
│    - "MODE=ALTERNATIF\\n{json}" → PROMPT_ALTERNATE        │   └───────────────────┘
│      (bob --mode ask --disable-mcp): SATU kalimat saran    │
└────────────────────────────────────────────────────────────┘
         Dokumen .docx: web membangunnya lokal (docx Packer → saveAs)
         — TANPA tool call, TANPA path disimpan di server.
```

## Fitur

* **Analisis matriks CBT 4 kolom** (Situasi, Pikiran, Emosi, Perilaku) oleh
  **Bob dengan prompt klinis ketat** (tanpa saran medis, tanpa diagnosa); kolom
  **Pikiran Alternatif KOSONG** — AI sengaja tidak mengisi otomatis (guardrail
  produk).
* **Pikiran alternatif manual (disarankan) atau saran AI (opsi)** — saran AI
  lewat awalan `MODE=ALTERNATIF` pada **endpoint yang sama**; diberi badge `AI`,
  boleh diedit; divalidasi sebelum disimpan (min. 5 karakter, bukan placeholder).
* **Dokumen `.docx` hanya setelah alt valid** — tombol *Buat Dokumen* (G4)
  membangun `.docx` di browser (library `docx` + `file-saver`): tabel 5 kolom +
  catatan objektif-klinis; TANPA AI/tool/MCP dan TANPA menyimpan path di server.
* **Riwayat curhatan (MERN)** — auto-save saat analisis berhasil, PATCH saat alt
  selesai, buka/hapus dari panel Riwayat; MongoDB via Express.
* **Guardrails G1–G5** — G1 validasi curhatan di klien (min. 8 kata / 40
  karakter, deteksi bukan-curhatan), G2 analisis kosong tidak disimpan, G3 alt
  minimal 5 karakter non-placeholder, G4 dokumen butuh alt, G5 Express
  revalidasi semua tulis.
* **Guard otomatis di e2e** — regex PII, deteksi kebocoran instruksi prompt,
  roundtrip history (400 pada pelanggaran guardrail). Isi `.docx` diverifikasi
  di Playwright (unduhan browser).

## Komponen & berkas

| Tahap | Berkas | Keterangan |
|---|---|---|
| 1 — Flow Langflow | `scripts/create_langflow_flow.py`, `.flow_id` | builder membuat **SATU flow** multi-mode `BriefME` (`briefme`); prefix `MODE=ALTERNATIF` memilih prompt + flag bob |
| 2 — Brain Bob | `.env` (`BOB_API_KEY`, `BOB_MAX_TURNS`) | `bob run` dari node Python Interpreter; selalu `--disable-mcp` (produk tanpa tool) |
| 3 — History API | `server/index.js`, `server/package.json` | Express + Mongoose (:8790) — CRUD `/api/history` + guardrail G5 |
| 4 — E2E test | `scripts/run_e2e.py` | preflight → F1 → F3 (prefix) → roundtrip history |
| 5 — Web (UI) | `web/` (React + Vite + TS + `docx` + `file-saver`) | alur 3 tahap + panel Riwayat + pembuatan `.docx` lokal; proxy `/api/history` → Express, `/api` → Langflow |
| Arsip | `mcp_server.py`, `template_cbt.docx`, `make_template.py`, `.bob/mcp.json` | jalur MCP lama — **tidak lagi dipakai produk** (docx kini di browser); disimpan sebagai arsip |

## Setup

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
cp .env.example .env                     # lalu isi LANGFLOW_API_KEY, BOB_API_KEY

cd server && npm install                 # dependensi Express/Mongoose
cd web && npm install                    # dependensi web (termasuk docx, file-saver)
```

Pra-syarat mesin:

* **Node.js ≥ 22** user-level — untuk `bobshell` (Bob 2.0.6, `~/.local/share/npm/bin/bob`)
  dan build web UI.
* **Docker** — MongoDB history: `docker run -d --name briefme-mongo -p 27017:27017
  --restart unless-stopped mongo:7`
* **Langflow** dengan API key superuser (sekali: `cd ~/langflow && ./.venv/bin/langflow api-key`).
* **Kunci API Bob asli** di `.env` (`BOB_API_KEY=…`) — satu-satunya engine AI.

## Menjalankan

```bash
set -a; . ./.env; set +a

# 1 — MongoDB history (sekali per mesin)
docker start briefme-mongo 2>/dev/null || docker run -d --name briefme-mongo \
  -p 27017:27017 --restart unless-stopped mongo:7

# 2 — History API (Express + Mongoose)
node server/index.js                      # → http://127.0.0.1:8790/api/history

# 3 — Buat/update flow (saat prompt/arsitektur berubah)
python scripts/create_langflow_flow.py    # SATU flow multi-mode

# 4 — Uji end-to-end: preflight → F1 → F3 (prefix) → roundtrip history
.venv/bin/python scripts/run_e2e.py

# 5 — Web UI: dev server + proxy (History → Express, sisanya → Langflow)
cd web && npm install && npm run dev   # → http://localhost:5173/
```

### Kirim payload manual

```bash
set -a; . ./.env; set +a
# Analisis (jurnal polos — tabel 4 kolom, alt KOSONG):
curl -s http://127.0.0.1:7860/api/v1/run/briefme \
  -H "Content-Type: application/json" -H "x-api-key: $LANGFLOW_API_KEY" \
  -d '{"input_value":"Kemarin saya berdiskusi dengan atasan di kantor. Saya cemas berlebihan dan jantung berdebar.","output_type":"chat","data_type":"text"}'
# Saran pikiran alternatif (awalan MODE=ALTERNATIF + JSON 4 kunci CBT):
curl -s http://127.0.0.1:7860/api/v1/run/briefme \
  -H "Content-Type: application/json" -H "x-api-key: $LANGFLOW_API_KEY" \
  -d '{"input_value":"MODE=ALTERNATIF\n{\"situation\":\"diskusi target\",\"thought\":\"saya akan dipecat\",\"emotion\":\"cemas\",\"behavior\":\"susah tidur\"}","output_type":"chat","data_type":"text"}'
```

Output analisis = tabel Markdown (header `Situasi | Pikiran | Emosi | Perilaku |
Pikiran Alternatif`, sel alt kosong); output alternatif = satu paragraf saran.

## Catatan penting

* **Rantai flow.** Prompt node me-render kode builder (`{text}` → input);
  outputnya menimpa field `python_code` di runtime; Python Interpreter menjalankan
  `bob run` (timeout 900s); Parse JSON mengekstrak `last_message` dari stdout
  JSON Bob (tahan noise + code fence); Parser memformat baris. Dua prompt
  (`PROMPT_ANALYSIS`, `PROMPT_ALTERNATE`) di-embed dalam satu kode node —
  percabangan `raw.startswith('MODE=ALTERNATIF')` memilih prompt + flag bob.
  Keduanya tanpa kurung kurawal (wajib untuk str.format).
* **Flag Bob.** Kedua mode memakai `--disable-mcp`; mode analisis `--mode agent`,
  mode alternatif `--mode ask` (jawaban tunggal). Produk **tidak memakai tool
  MCP** — dokumen `.docx` dibuat di browser.
* **Sandbox Python Interpreter.** Kode node tanpa pernyataan `import` (ditolak
  `validate_code_safety`) — module datang dari `global_imports`
  (`math,os,subprocess`); builtins tanpa `open()` — fallback kunci dibaca via
  `subprocess cat .env`. Path bob absolut karena PATH Langflow tidak memuat
  `~/.local/share/npm/bin`.
* **Proxy web.** `web/vite.config.ts` mendaftarkan `/api/history` (Express :8790)
  **sebelum** `/api` (Langflow) — urutan ini wajib; `x-api-key` disuntikkan proxy
  (key tidak pernah masuk frontend).
* **Autosave Langflow.** Tab editor yang terbuka bisa men-*save* flow dan
  mengembalikannya ke versi lama. **Tutup tab editor flow sebelum/sesudah
  builder**, lalu verifikasi jumlah node flow = `6` (id: `.flow_id` fb741b8a…).
* **Sel alt `&nbsp;`.** Bob kadang menulis `&nbsp;` untuk sel kosong — UI dan
  e2e menormalisasi jadi kosong (prompt analisis juga melarangnya).
* **Tanpa sensor lokal (keputusan 9 Okt).** Input dianggap tanpa data pribadi;
  PII jadi guard regex di e2e. Riwayat iterasi di `AGENT_LOG.md` bag.3–8.
* **Web UI (`web/`).** React + Vite + TypeScript + framer-motion + `docx` +
  `file-saver`; footer transparansi AI di halaman; `npm run build` lolos
  (tsc + vite).

## Verifikasi manual

* Status e2e terakhir: **SEMUA PEMERIKSAAN LOLOS 20.0s** (preflight → F1 →
  F3 prefix → roundtrip history G1–G5)
* Uji browser (Playwright, 25 pemeriksaan): **25/25 LOLOS** — G1, sel alt kosong,
  G3, saran AI + badge, G4, **unduhan `.docx` terverifikasi** (isi tabel +
  catatan objektif-klinis), riwayat buka/pulihkan
* Dokumen hasil: unduhan browser `briefme-thought-record-*.docx`
* Log Langflow: `tail -f /tmp/langflow-server.log`
* Keputusan & angka ukur: `AGENT_LOG.md` (bag.19 3-flow era, bag.20 konsolidasi
  1-flow)
