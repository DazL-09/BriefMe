# AI Agent Implementation Plan (HearMeOut / BriefME)
**Dokumen:** `Agent-Plan.md`
**Arsitektur final (11 Okt 2026, konsolidasi):** **IBM Bob (engine, API key resmi) + Langflow (orkestrasi)** — domain BriefME. Produk berjalan lewat **SATU flow multi-mode** (analisis / saran alternatif via awalan input); dokumen `.docx` dibuat **di browser** (tanpa MCP); riwayat pada **stack MERN (MongoDB + Express)**.

## 1. Arsitektur Komponen

1. **SATU flow Langflow `BriefME` (endpoint `briefme`):**
   * Menerima teks jurnal → rantai **Chat Input → Prompt → Python Interpreter → Parse JSON → Parser → Chat Output "Thought Record"** (6 node / 5 edge).
   * Mode dibaca dari **awalan input** oleh kode node Python (bukan flow terpisah):
     * **jurnal polos** → `PROMPT_ANALYSIS` (`bob --mode agent --disable-mcp`): tabel CBT **4 kolom terisi, kolom Pikiran Alternatif KOSONG**, tanpa tool call & tanpa baris `Dokumen:` (AI tidak mengisi alt otomatis — guardrail produk).
     * **`MODE=ALTERNATIF\n{json4}`** → `PROMPT_ALTERNATE` (`bob --mode ask --disable-mcp`): **SATU** paragraf pikiran alternatif seimbang (opsi; pasien disarankan menulis manual).
2. **Cognitive Brain = IBM Bob:** node **Python Interpreter** menjalankan `bob run` (kunci `BOB_API_KEY`, `--workspace` root proyek). Dua prompt di-embed dalam satu kode node; percabangan `raw.startswith('MODE=ALTERNATIF')` memilih instruksi + flag bob. Selalu `--disable-mcp` — produk **tidak memakai tool MCP**.
3. **Pasca-proses native Langflow:** output bob (`stdout` JSON) → node **Parse JSON** (jq: ekstrak `last_message`, bersihkan fence) → node **Parser** (`pattern={text}`) → **Chat Output**.
4. **Dokumen `.docx` (web-side):** tombol *Buat Dokumen* membangun dokumen di browser via library `docx` (`Document` + `Table` 5 kolom + catatan objektif-klinis) → `Packer.toBlob` → `file-saver` `saveAs`. **Tanpa AI, tanpa tool, tanpa path di server** (G4: hanya setelah alt valid).
5. **History MERN (Express :8790 + MongoDB :27017):** `server/index.js` — CRUD `/api/history` dengan **guardrail G5** (journal 40–4000 karakter, 4 field analisis wajib, `source ∈ {manual, ai}`, `documentPath` hanya bila alt terisi — kontrak dipertahankan meski web tidak lagi mengirimnya). Web auto-save saat analisis, PATCH alt; panel Riwayat (buka/hapus).

```
Web UI :5173 ─┬─ POST /api/v1/run/briefme ─────────────────────► Langflow :7860
              │    (jurnal polos → analisis; MODE=ALTERNATIF → saran)
              │    6 node: Prompt → Python Interpreter (bob run) →
              │    Parse JSON → Parser → Chat Output
              │    Dokumen .docx dibangun LOKAL di browser (docx + file-saver)
              └─ /api/history (CRUD) ─► Express :8790 ─► MongoDB :27017
```

---

## 2. Tahap 1: Konfigurasi Flow Langflow (BriefME)

*   **Node Setup (6 node / 5 edge):** `Chat Input` → `Prompt Template` (template = kode python multi-mode, variabel dinamis `text`) → `Python Interpreter` (`global_imports=math,os,subprocess`; kode `bob run`, timeout 900s) → `Parse JSON` (query jq `last_message`) → `Parser` (`pattern={text}`) → `Chat Output` (label `Thought Record`).
*   **Endpoint:** `POST /api/v1/run/briefme` — satu endpoint, dua mode via awalan input.
*   **Builder:** `python scripts/create_langflow_flow.py` — `build_python_code_template_multi()` (embed dua prompt + percabangan mode); id disimpan di `.flow_id`. Flow legacy (Gemini/Dokumen/Alternatif) sudah dihapus dari Langflow (arsip di git history).
*   **Sandbox Python Interpreter:** kode tanpa pernyataan `import` (ditolak `validate_code_safety`) — module dari `global_imports`; tanpa `open()` — fallback kunci dibaca via `subprocess cat .env`.

---

## 3. Tahap 2: Cognitive Brain — IBM Bob (via API resmi)

*   **Role:** Clinical Journaling Copilot — dua prompt di `scripts/create_langflow_flow.py` (`PROMPT_ANALYSIS`, `PROMPT_ALTERNATE`); teks tanpa jargon CLI, **tanpa kurung kurawal** karena ikut ter-embed ke str.format.
*   **Runtime:** `bob run --accept-license --mode <agent|ask> --format json --log-level error --trust --workspace <project> --max-turns <BOB_MAX_TURNS> --disable-subagents --disable-mcp <prompt>` (stdin ditutup; `bobshell` 2.0.6 dari npm). Analisis: `--mode agent`; alternatif: `--mode ask`.
*   **Kunci API:** `BOB_API_KEY` di `.env` (juga dibaca runtime node dari `.env` via `cat` bila env proses Langflow kosong); `BOB_MAX_TURNS` default `6`.
*   **Prompt (ringkas):**
    * **`PROMPT_ANALYSIS`:** ekstraksi 4 kunci CBT **secara internal**; akhiri HANYA tabel Markdown 5 header dengan sel Pikiran Alternatif **kosong total** (tanpa `&nbsp;`); dilarang memanggil tool / menyebut file / JSON mentah di pesan akhir.
    * **`PROMPT_ALTERNATE`:** input JSON 4 kunci → SATU pikiran alternatif seimbang (maks. 2 kalimat, bahasa Indonesia); balas HANYA teksnya.

---

## 4. Tahap 3: Dokumen `.docx` (web-side, tanpa MCP)

*   **Library:** `docx` (Packer) + `file-saver` di `web/`; dependensi via npm.
*   **Isi dokumen:** judul "Thought Record — Matriks CBT", subtitle "Dokumen dibuat otomatis oleh HearMeOut (web)", tabel 5 kolom (header Indonesia + 1 baris data dari analisis + alt), footer "Catatan: dokumen ini bersifat objektif-klinis…".
*   **G4:** tombol *Buat Dokumen* aktif hanya setelah alt valid; nama file `briefme-thought-record-<stamp>.docx`.
*   **Arsip:** `mcp_server.py`, `template_cbt.docx`, `make_template.py`, `.bob/mcp.json` — jalur MCP lama, **tidak lagi dipakai produk** (disimpan sebagai arsip).

---

## 5. Tahap 4: Alur Pengujian Terintegrasi (End-to-End)

1.  **Preflight:** `BOB_API_KEY` terisi; `bob --version` sehat (2.0.6); `LANGFLOW_API_KEY` wajib; **Express `:8790/api/health` → `ok` + `db=up` (MongoDB)**.
2.  **Health:** Langflow `/health` + `bob --version` + Express/Mongo.
3.  **F1** — POST `briefme` (jurnal polos): SATU output **Thought Record**; 5 header; sel Pikiran Alternatif **kosong**; **tanpa** baris `Dokumen:`; guard regex PII + `INSTRUCTION_FRAGMENTS` (kebocoran prompt).
4.  **F3** — POST `briefme` (prefix `MODE=ALTERNATIF` + JSON 4 kunci dari F1): satu output ≥ 15 karakter, tanpa tabel/pembuka.
5.  **Roundtrip history (Express/MongoDB):** POST valid → 201; GET list berisi entri; G1 journal < 40 → 400; G2 analysis kosong → 400; G4 documentPath tanpa alt → 400; PATCH alt → 200; G3 alt < 5 → 400; DELETE → 200 (bersihkan entri uji).
6.  **Uji browser (Playwright, `/tmp/opencode/ui-test/flow.mjs`):** G1 klien → Analisis → sel alt `—` → G3 tolak → Saran AI + badge → Simpan → **Buat Dokumen → unduhan `.docx` terverifikasi** (isi tabel + catatan objektif-klinis) → Riwayat buka/pulihkan.

---

## Lampiran — Status Implementasi (11 Okt 2026, konsolidasi 1-flow)

| Tahap | Status | Berkas |
|---|---|---|
| Tahap 1 — Flow Langflow | ✅ **SATU flow 6 node / 5 edge**: `BriefME` (`briefme`, id `fb741b8a-…`); builder multi-mode; flow legacy (Gemini/Dokumen/Alternatif) dihapus dari Langflow | `scripts/create_langflow_flow.py`, `.flow_id` |
| Tahap 2 — Cognitive Brain Bob | ✅ `bob run` dua mode (`--mode agent/ask`, selalu `--disable-mcp`); preflight offline (validate_prompt, validate_code_safety, jq) lolos | `.env` (`BOB_API_KEY`, `BOB_MAX_TURNS`) |
| Tahap 3 — Dokumen `.docx` | ✅ **web-side** via `docx` + `file-saver`; G4 aktif; Playwright verifikasi unduhan (10 KB, header + alt + catatan) | `web/src/App.tsx`, `web/package.json` |
| History MERN | ✅ Express + Mongoose `:8790` di Docker `mongo:7` (`briefme-mongo`, `:27017`); guardrail G5 lolos semua kasus uji | `server/index.js`, `server/package.json` |
| Tahap 4 — E2E test | ✅ **SEMUA PEMERIKSAAN LOLOS 20.0s** (preflight → F1 → F3 prefix → roundtrip history G1–G5) | `scripts/run_e2e.py` |
| Tahap 5 — Web UI | ✅ alur 3 tahap (Analisis → alt manual/AI + badge AI → Buat Dokumen lokal), panel Riwayat (buka/hapus), guardrail G1–G4 klien; proxy `/api/history` → Express; `npm run build` lolos; **Playwright 25/25 LOLOS** | `web/` |

**Penyimpangan dari rencana (keputusan eksplisit):**

* **Tahap sensor lokal DIBUANG (9 Okt)** — jurnal dianggap tanpa data pribadi; PII jadi guard regex (`AGENT_LOG.md` bag.3–8).
* **Arsitektur 3-flow + MCP DIPANGKAS jadi 1-flow multi-mode (11 Okt, bag.20)** — user minta konsolidasi: hapus Gemini total, satukan flow, saran AI via awalan `MODE=ALTERNATIF`, dokumen `.docx` via library di browser (tanpa MCP). Jalur MCP diarsipkan (`AGENT_LOG.md` bag.20).
* **Bob gateway shim pernah dipasang lalu dihapus** — jalur resmi kini API key (`bob run` tanpa shim); `bobshell` 2.0.6 terpasang via npm.
* **Akun model:** engine bob tidak butuh `GOOGLE_API_KEY`.

Lihat `README.md` untuk urutan menjalankan seluruh komponen, `AGENT_LOG.md` untuk angka ukur dan riwayat diagnosis.
