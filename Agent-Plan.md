# AI Agent Implementation Plan (HearMeOut / BriefME)
**Dokumen:** `Agent-Plan.md`
**Arsitektur final (11 Okt 2026):** **IBM Bob (engine, API key resmi) + Langflow (orkestrasi) dengan MCP di sisi Bob**, riwayat pada **stack MERN (MongoDB + Express)** — domain tetap BriefME. Produk berjalan lewat **tiga flow** (analisis → saran alternatif → dokumen); flow Gemini 5 node hanya cadangan.

## 1. Arsitektur Komponen

1. **Tiga flow Langflow (utama):**
   * `briefme` (F1 — analisis): menerima teks jurnal → rantai **Chat Input → Prompt → Python Interpreter → Parse JSON → Parser → Chat Output "Thought Record"** (6 node / 5 edge) → tabel CBT **4 kolom terisi, kolom Pikiran Alternatif KOSONG**, `bob --mode agent --disable-mcp`, **tanpa** tool call & tanpa baris `Dokumen:` (AI tidak mengisi alt otomatis — guardrail produk).
   * `briefme-alternatif` (F3 — saran AI, opsional): input JSON 4 kunci CBT → `bob --mode ask --disable-mcp` → **SATU** kalimat pikiran alternatif seimbang (opsi tambahan; pasien disarankan menulis manual).
   * `briefme-dokumen` (F2 — dokumen): input JSON 5 kunci (alt wajib terisi) → `bob --mode agent` **dengan MCP aktif** → memanggil tool `generate_clinical_document` tepat satu kali → balas HANYA `Dokumen: <path>`.
   Ketiga flow berbagi rantai node yang sama; hanya prompt, flag bob, dan label Chat Output yang beda (id node suffix `HEARB`/`HEARA`/`HEARD`).
2. **Cognitive Brain = IBM Bob:** node **Python Interpreter** menjalankan `bob run` (kunci `BOB_API_KEY`, `--workspace` root proyek). Prompt per tahap (`PROMPT_ANALYSIS` / `PROMPT_ALTERNATE` / `PROMPT_DOCUMENT`) dirender node **Prompt** lewat str.format `{text}`. Bob memanggil tool **MCP `hearmeout-tools`** (hanya F2) lewat `.bob/mcp.json` (`url: http://127.0.0.1:8788/mcp`) → dokumen `.docx` dibuat oleh Bob sendiri.
3. **Pasca-proses native Langflow:** output bob (`stdout` JSON) → node **Parse JSON** (jq: ekstrak `last_message`, bersihkan fence) → node **Parser** (`pattern={text}`) → **Chat Output** (label per tahap).
4. **Execution Tool (MCP Server):** `mcp_server.py` (Python, fastmcp, docxtpl) mengekspos `generate_clinical_document` → render `template_cbt.docx` → `output/brief_<id>.docx` + `audit.jsonl`.
5. **History MERN (Express :8790 + MongoDB :27017):** `server/index.js` — CRUD `/api/history` dengan **guardrail G5** (journal 40–4000 karakter, 4 field analisis wajib, `source ∈ {manual, ai}`, `documentPath` hanya bila alt terisi). Web auto-save saat analisis PATCH alt & dokumen; panel Riwayat (buka/hapus).
6. **Flow cadangan "BriefME (Gemini)"** (`briefme-gemini`): arsitektur lama 5 node — Chat Input → Agent (+GoogleGenerativeAI + MCPTools) → Chat Output (perilaku legacy: alt + docx satu run). Dibuat/di-clone otomatis oleh builder; dipakai bila engine Bob bermasalah (perlu `GOOGLE_API_KEY` asli).

```
Web UI :5173 ─┬─ POST /api/v1/run/briefme{,-alternatif,-dokumen} ─► Langflow :7860
              │    3 flow × 6 node: Prompt → Python Interpreter (bob run) →
              │    Parse JSON → Parser → Chat Output
              │              │ (F2 saja) Bob → .bob/mcp.json → MCP :8788 → .docx
              └─ /api/history (CRUD) ─► Express :8790 ─► MongoDB :27017
```

---

## 2. Tahap 1: Konfigurasi Flow Langflow (BriefME)

*   **Node Setup (6 node / 5 edge, identik ketiga flow):** `Chat Input` → `Prompt Template` (template = kode python, variabel dinamis `text`) → `Python Interpreter` (`global_imports=math,os,subprocess`; kode `bob run`, timeout 900s) → `Parse JSON` (query jq `last_message`) → `Parser` (`pattern={text}`) → `Chat Output` (label `Thought Record` / `Saran Pikiran Alternatif` / `Dokumen`).
*   **Endpoint:** `POST /api/v1/run/briefme`, `/briefme-alternatif`, `/briefme-dokumen`; cadangan `briefme-gemini`.
*   **Builder:** `python scripts/create_langflow_flow.py [--engine bob|gemini]` (default `bob`) — loop `BOB_FLOWS` (3 spesifikasi: prompt, mode, `--disable-mcp`, label); id disimpan di `.flow_id`, `.flow_id.dokumen`, `.flow_id.alternatif`; migrasi meng-clone flow Gemini ke "BriefME (Gemini)" sebelum primary ditimpa data bob.
*   **Sandbox Python Interpreter:** kode tanpa pernyataan `import` (ditolak `validate_code_safety`) — module dari `global_imports`; tanpa `open()` — fallback kunci dibaca via `subprocess cat .env`.
*   ~~Tahap sensor Ollama/anonymizer~~ — **dibuang 9 Okt**; ~~arsitektur Agent Gemini murni~~ — **diganti 11 Okt** (Bob via API resmi kembali jadi engine; lihat `AGENT_LOG.md` bag.17–18).

---

## 3. Tahap 2: Cognitive Brain — IBM Bob (via API resmi)

*   **Role:** Clinical Journaling Copilot — prompt per tahap (`PROMPT_ANALYSIS`, `PROMPT_ALTERNATE`, `PROMPT_DOCUMENT` di `scripts/create_langflow_flow.py`; teks tanpa jargon CLI, **tanpa kurung kurawal** karena ikut ter-embed ke str.format; prompt legacy `PROMPT_LEGACY_GEMINI` dipakai fallback Gemini).
*   **Runtime:** `bob run --accept-license --mode <agent|ask> --format json --log-level error --trust --workspace <project> --max-turns <BOB_MAX_TURNS> --disable-subagents [--disable-mcp] <prompt>` (stdin ditutup; `bobshell` 2.0.6 dari npm, tanpa shim). F1/F3: `--disable-mcp` (F1 `--mode agent`, F3 `--mode ask`); F2: `--mode agent` tanpa `--disable-mcp`.
*   **Koneksi alat:** `.bob/mcp.json` → `{"mcpServers": {"hearmeout-tools": {"url": "http://127.0.0.1:8788/mcp", "timeout": 60000, "alwaysAllow": ["generate_clinical_document"]}}}` — MCP **di sisi Bob**, hanya berpengaruh di F2.
*   **Kunci API:** `BOB_API_KEY` di `.env` (juga dibaca runtime node dari `.env` via `cat` bila env proses Langflow kosong); `BOB_MAX_TURNS` default `6`.
*   **Prompt per tahap (ringkas):**
    * **F1 `PROMPT_ANALYSIS`:** ekstraksi 4 kunci CBT **secara internal**; akhiri HANYA tabel Markdown 5 header dengan sel Pikiran Alternatif **kosong total** (tanpa `&nbsp;`); dilarang memanggil tool / menyebut file / JSON mentah di pesan akhir.
    * **F3 `PROMPT_ALTERNATE`:** input JSON 4 kunci → SATU pikiran alternatif seimbang (maks. 2 kalimat, bahasa Indonesia); balas HANYA teksnya.
    * **F2 `PROMPT_DOCUMENT`:** input JSON 5 kunci → panggil `generate_clinical_document` tepat satu kali → balas HANYA `Dokumen: <path>` (atau `ERROR: <pesan>` bila tool gagal setelah satu percobaan ulang).

---

## 4. Tahap 3: Pembuatan MCP Server (Tool Execution)

*   **Stack:** Python, `fastmcp`, `docxtpl`.
*   **Persiapan Template:** `template_cbt.docx` = tabel Thought Record 5 kolom (header Indonesia: Situasi, Pikiran, Emosi, Perilaku, Pikiran Alternatif) dengan tag Jinja2 `{{ situation }}`, `{{ thought }}`, `{{ emotion }}`, `{{ behavior }}`, `{{ alternate_thought }}` — dibuat oleh `make_template.py`.
*   **Skrip `mcp_server.py`:** tool `generate_clinical_document` → render `template_cbt.docx` → simpan ke `output/brief_<id>.docx` (+ jejak `audit.jsonl`). Dipanggil **oleh Bob** (bukan oleh node Langflow).

---

## 5. Tahap 4: Alur Pengujian Terintegrasi (End-to-End)

1.  **Preflight (`E2E_ENGINE=bob|gemini`, default bob):** bob → `BOB_API_KEY` terisi, `.bob/mcp.json` ada, `bob --version` sehat (2.0.6); gemini → `GOOGLE_API_KEY` bukan placeholder; `LANGFLOW_API_KEY` wajib; **Express `:8790/api/health` → `ok` + `db=up` (MongoDB)**.
2.  **Health:** Langflow `/health` + MCP `generate_clinical_document` terdaftar + `bob --version` + Express/Mongo.
3.  **F1** — POST `briefme`: SATU output **Thought Record**; 5 header; sel Pikiran Alternatif **kosong**; **tanpa** baris `Dokumen:`; guard regex PII + `INSTRUCTION_FRAGMENTS` (kebocoran prompt) + bukan dump JSON mentah.
4.  **F3** — POST `briefme-alternatif` (JSON 4 kunci dari F1): satu output ≥ 15 karakter, tanpa preamble.
5.  **F2** — POST `briefme-dokumen` (JSON 5 kunci + alt F3): baris `Dokumen: <path>`, file **baru** dibuat, tanpa `ERROR:`.
6.  **Cek output fisik:** `output/brief_*.docx` — tabel 5 kolom terisi, sel alt = teks F3 (bukan placeholder), konten selaras input, bebas PII.
7.  **Roundtrip history (Express/MongoDB):** POST valid → 201; GET list berisi entri; G1 journal < 40 → 400; G2 analysis kosong → 400; G4 documentPath tanpa alt → 400; PATCH alt → 200; G3 alt < 5 → 400; DELETE → 200 (bersihkan entri uji).
8.  **Uji browser (Playwright, `/tmp/opencode/ui-test/flow.mjs`):** G1 klien → Analisis → sel alt `—` → G3 tolak → Saran AI + badge → Simpan → Buat Dokumen → Riwayat buka/pulihkan.

---

## Lampiran — Status Implementasi (11 Okt 2026)

| Tahap | Status | Berkas |
|---|---|---|
| Tahap 1 — Flow Langflow | ✅ **tiga flow 6 node / 5 edge**: `BriefME` (`briefme`, id `fb741b8a-…`), `BriefME — Dokumen` (`briefme-dokumen`, `f035c624-…`), `BriefME — Alternatif` (`briefme-alternatif`, `ae7cc7b8-…`) + fallback **5 node / 4 edge** `BriefME (Gemini)` (`briefme-gemini`, `fcddb060-…`); builder loop `BOB_FLOWS` + migrasi clone | `scripts/create_langflow_flow.py`, `.flow_id`, `.flow_id.dokumen`, `.flow_id.alternatif`, `.flow_id.gemini` |
| Tahap 2 — Cognitive Brain Bob | ✅ `bob run` per tahap (`--mode agent/ask`, `--disable-mcp` untuk F1/F3); MCP sisi Bob via `.bob/mcp.json` (kunci `url`) hanya untuk F2; preflight offline (validate_prompt, validate_code_safety, jq) lolos | `.bob/mcp.json`, `.env` (`BOB_API_KEY`, `BOB_MAX_TURNS`) |
| Tahap 3 — MCP Server | ✅ tool live di `http://127.0.0.1:8788/mcp`; e2e F2 membuat docx baru (mis. `brief_e8bbe38b.docx`) | `mcp_server.py`, `template_cbt.docx`, `make_template.py` |
| History MERN | ✅ Express + Mongoose `:8790` di Docker `mongo:7` (`briefme-mongo`, `:27017`); guardrail G5 lolos semua kasus uji | `server/index.js`, `server/package.json` |
| Tahap 4 — E2E test | ✅ **semua pemeriksaan LOLOS 34.4s** (preflight → F1 → F3 → F2 + docx → roundtrip history G1–G5) | `scripts/run_e2e.py` |
| Tahap 5 — Web UI | ✅ alur 3 tahap (Analisis → alt manual/AI + badge AI → Buat Dokumen), panel Riwayat (buka/hapus), guardrail G1–G4 klien, placeholder netral tanpa "Pakai contoh"; proxy `/api/history` → Express; `npm run build` lolos; **Playwright 19/19 LOLOS** | `web/` |

**Penyimpangan dari rencana (keputusan eksplisit):**

* **Tahap sensor lokal DIBUANG (9 Okt)** — jurnal dianggap tanpa data pribadi; PII jadi guard regex (`AGENT_LOG.md` bag.3–8).
* **Arsitektur murni Gemini sempat jadi utama (11 Okt, bag.17)** — lalu **dikembalikan ke Bob via API resmi** sesuai kriteria hackathon (Bob + Langflow, MCP di sisi Bob); Gemini dipertahankan sebagai flow cadangan (`AGENT_LOG.md` bag.18).
* **Bob gateway shim pernah dipasang lalu dihapus** — jalur resmi kini API key (`bob run` tanpa shim); `bobshell` 2.0.6 terpasang via npm.
* **Akun model:** engine bob tidak butuh `GOOGLE_API_KEY`; kunci Gemini hanya untuk flow cadangan.

Lihat `README.md` untuk urutan menjalankan seluruh komponen, `AGENT_LOG.md` untuk angka ukur dan riwayat diagnosis.
