# AI Agent Implementation Plan (HearMeOut / BriefME)
**Dokumen:** `Agent-Plan.md`
**Arsitektur final (11 Okt 2026):** **IBM Bob (engine, API key resmi) + Langflow (orkestrasi) dengan MCP di sisi Bob** — domain tetap BriefME. Flow Gemini 5 node hanya cadangan.

## 1. Arsitektur Komponen

1. **Flow Langflow "BriefME" (utama):** `POST /api/v1/run/briefme` menerima teks jurnal pasien (tanpa data pribadi) dan menggerakkan rantai **Chat Input → Prompt → Python Interpreter → Parse JSON → Parser → Chat Output "Thought Record"** (6 node / 5 edge).
2. **Cognitive Brain = IBM Bob:** node **Python Interpreter** menjalankan `bob run --mode agent --format json` (kunci `BOB_API_KEY`, `--workspace` root proyek). Prompt CBT (`AGENT_SYSTEM_PROMPT`) dirender node **Prompt** lewat str.format `{text}`. Bob memanggil tool **MCP `hearmeout-tools`** lewat `.bob/mcp.json` (`url: http://127.0.0.1:8788/mcp`) → dokumen `.docx` dibuat oleh Bob sendiri.
3. **Pasca-proses native Langflow:** output bob (`stdout` JSON) → node **Parse JSON** (jq: ekstrak `last_message`, bersihkan fence) → node **Parser** (`pattern={text}`, satu baris per data) → **Chat Output "Thought Record"** (tabel Markdown + baris `Dokumen: <path>`).
4. **Execution Tool (MCP Server):** `mcp_server.py` (Python, fastmcp, docxtpl) mengekspos `generate_clinical_document` → render `template_cbt.docx` → `output/brief_<id>.docx` + `audit.jsonl`.
5. **Flow cadangan "BriefME (Gemini)"** (`briefme-gemini`): arsitektur lama 5 node — Chat Input → Agent (+GoogleGenerativeAI + MCPTools) → Chat Output. Dibuat/di-clone otomatis oleh builder; dipakai bila engine Bob bermasalah (perlu `GOOGLE_API_KEY` asli).

```
Payload uji → POST /api/v1/run/briefme
┌─ Langflow :7860 — flow "BriefME": 6 node / 5 edge ───────────────────────────┐
│ Chat Input → Prompt(kode CBT + {text}) → Python Interpreter → Parse JSON →   │
│ Parser({text}) → Chat Output "Thought Record"                                │
└───────────────────────────────────┬──────────────────────────────────────────┘
        Python Interpreter: bob run --mode agent --format json
        (.bob/mcp.json → hearmeout-tools, kunci BOB_API_KEY)
                                    ▼
┌─ MCP Server HearMeOut_Tools :8788 (mcp_server.py) ───────────────────────────┐
│ docxtpl → template_cbt.docx → output/brief_<id>.docx (+ audit.jsonl)         │
└──────────────────────────────────────────────────────────────────────────────┘
```

---

## 2. Tahap 1: Konfigurasi Flow Langflow (BriefME)

*   **Node Setup (6 node / 5 edge):** `Chat Input` → `Prompt Template` (template = kode python, variabel dinamis `text`) → `Python Interpreter` (`global_imports=math,os,subprocess`; kode `bob run`, timeout 900s) → `Parse JSON` (query jq `last_message`) → `Parser` (`pattern={text}`) → `Chat Output "Thought Record"`.
*   **Endpoint:** `POST /api/v1/run/briefme` (`output_type=chat`); cadangan `briefme-gemini`.
*   **Builder ganda:** `python scripts/create_langflow_flow.py [--engine bob|gemini]` (default `bob`); migrasi meng-clone flow Gemini ke "BriefME (Gemini)" sebelum primary ditimpa data bob.
*   **Sandbox Python Interpreter:** kode tanpa pernyataan `import` (ditolak `validate_code_safety`) — module dari `global_imports`; tanpa `open()` — fallback kunci dibaca via `subprocess cat .env`.
*   ~~Tahap sensor Ollama/anonymizer~~ — **dibuang 9 Okt**; ~~arsitektur Agent Gemini murni~~ — **diganti 11 Okt** (Bob via API resmi kembali jadi engine; lihat `AGENT_LOG.md` bag.17–18).

---

## 3. Tahap 2: Cognitive Brain — IBM Bob (via API resmi)

*   **Role:** Clinical Journaling Copilot (`AGENT_SYSTEM_PROMPT`, teks sama dengan yang dipakai fallback Gemini — tanpa jargon CLI, tanpa kurawal).
*   **Runtime:** `bob run --accept-license --mode agent --format json --log-level error --trust --workspace <project> --max-turns <BOB_MAX_TURNS> --disable-subagents <prompt>` (stdin ditutup; `bobshell` 2.0.6 dari npm, tanpa shim).
*   **Koneksi alat:** `.bob/mcp.json` → `{"mcpServers": {"hearmeout-tools": {"url": "http://127.0.0.1:8788/mcp", "timeout": 60000, "alwaysAllow": ["generate_clinical_document"]}}}` — MCP **di sisi Bob**, bukan lewat node Langflow.
*   **Kunci API:** `BOB_API_KEY` di `.env` (juga dibaca runtime node dari `.env` via `cat` bila env proses Langflow kosong); `BOB_MAX_TURNS` default `6`.
*   **System Prompt (ringkas):** ekstraksi jurnal ke JSON 5 kunci (situation/thought/emotion/behavior/alternate_thought) → panggil `generate_clinical_document` tepat satu kali → akhiri dengan tabel Markdown 5 kolom + baris `Dokumen: <path>`; larangan saran medis/diagnosa; gagal tool = ulangi sekali lalu laporkan. Teks lengkap: `AGENT_SYSTEM_PROMPT` di `scripts/create_langflow_flow.py`.

---

## 4. Tahap 3: Pembuatan MCP Server (Tool Execution)

*   **Stack:** Python, `fastmcp`, `docxtpl`.
*   **Persiapan Template:** `template_cbt.docx` = tabel Thought Record 5 kolom (header Indonesia: Situasi, Pikiran, Emosi, Perilaku, Pikiran Alternatif) dengan tag Jinja2 `{{ situation }}`, `{{ thought }}`, `{{ emotion }}`, `{{ behavior }}`, `{{ alternate_thought }}` — dibuat oleh `make_template.py`.
*   **Skrip `mcp_server.py`:** tool `generate_clinical_document` → render `template_cbt.docx` → simpan ke `output/brief_<id>.docx` (+ jejak `audit.jsonl`). Dipanggil **oleh Bob** (bukan oleh node Langflow).

---

## 5. Tahap 4: Alur Pengujian Terintegrasi (End-to-End)

1.  **Preflight (`E2E_ENGINE=bob|gemini`, default bob):** bob → `BOB_API_KEY` terisi, `.bob/mcp.json` ada, `bob --version` sehat (2.0.6); gemini → `GOOGLE_API_KEY` bukan placeholder; `LANGFLOW_API_KEY` wajib.
2.  **Health:** Langflow `/health` + MCP `generate_clinical_document` terdaftar + `bob --version`.
3.  **Kirim payload uji:** satu POST ke endpoint sesuai engine dengan teks jurnal tanpa data pribadi.
4.  **Verifikasi output:** SATU output **Thought Record** (tabel Markdown header Indonesia + baris `Dokumen: <path>`); guard regex PII + `INSTRUCTION_FRAGMENTS` (kebocoran prompt) + bukan dump JSON mentah.
5.  **Cek output fisik:** `output/brief_*.docx` — tabel 5 kolom terisi, sel "Pikiran Alternatif" bukan placeholder, konten selaras input, bebas PII.

---

## Lampiran — Status Implementasi (11 Okt 2026)

| Tahap | Status | Berkas |
|---|---|---|
| Tahap 1 — Flow Langflow | ✅ flow **6 node / 5 edge** `BriefME` (endpoint `briefme`, id `fb741b8a-…`) + fallback **5 node / 4 edge** `BriefME (Gemini)` (endpoint `briefme-gemini`, id `fcddb060-…`); builder `--engine bob\|gemini` + migrasi clone | `scripts/create_langflow_flow.py`, `.flow_id`, `.flow_id.gemini` |
| Tahap 2 — Cognitive Brain Bob | ✅ `bob run --mode agent` dari node Python Interpreter; MCP sisi Bob via `.bob/mcp.json` (kunci `url`); uji tool penuh → `brief_446d6a46.docx` | `.bob/mcp.json`, `.env` (`BOB_API_KEY`, `BOB_MAX_TURNS`) |
| Tahap 3 — MCP Server | ✅ tool live di `http://127.0.0.1:8788/mcp` | `mcp_server.py`, `template_cbt.docx`, `make_template.py` |
| Tahap 4 — E2E test | ✅ **semua pemeriksaan LOLOS 19.9s** (engine bob: preflight BOB key + `bob --version`, run 13.5s, docx 5 kolom, bebas PII/prompt leak) | `scripts/run_e2e.py` |
| Tahap 5 — Web UI | ✅ React + Vite + TS: jurnal → **Bob Agent** → Thought Record; footer transparansi AI; `npm run build` lolos | `web/` |

**Penyimpangan dari rencana (keputusan eksplisit):**

* **Tahap sensor lokal DIBUANG (9 Okt)** — jurnal dianggap tanpa data pribadi; PII jadi guard regex (`AGENT_LOG.md` bag.3–8).
* **Arsitektur murni Gemini sempat jadi utama (11 Okt, bag.17)** — lalu **dikembalikan ke Bob via API resmi** sesuai kriteria hackathon (Bob + Langflow, MCP di sisi Bob); Gemini dipertahankan sebagai flow cadangan (`AGENT_LOG.md` bag.18).
* **Bob gateway shim pernah dipasang lalu dihapus** — jalur resmi kini API key (`bob run` tanpa shim); `bobshell` 2.0.6 terpasang via npm.
* **Akun model:** engine bob tidak butuh `GOOGLE_API_KEY`; kunci Gemini hanya untuk flow cadangan.

Lihat `README.md` untuk urutan menjalankan seluruh komponen, `AGENT_LOG.md` untuk angka ukur dan riwayat diagnosis.
