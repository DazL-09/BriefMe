# AI Agent Implementation Plan (HearMeOut)
**Dokumen:** `Agent-Plan.md`
**Fokus:** Eksekusi Lokal (Langflow native) & tool MCP lokal — tanpa dependensi cloud agent eksternal.

## 1. Arsitektur Komponen
1. **Flow Langflow (BriefME):** Menerima teks jurnal pasien (tanpa data pribadi — keputusan 9 Okt, tahap sensor lokal dibuang) dan menggerakkan ekstraksi CBT via node **Agent** (Gemini) dengan tool calling ke MCP.
2. **Cognitive Brain (Agent Gemini di dalam Langflow):** Menerima teks jurnal, melakukan ekstraksi matriks CBT, dan menentukan pemanggilan alat (*tool calling*) — model lewat node **GoogleGenerativeAI**, tool lewat **MCPTools**.
3. **Execution Tool (MCP Server):** Server lokal berbasis Python yang mengekspos fungsi pembuatan file `.docx` untuk dipanggil oleh Agent.

---

## 2. Tahap 1: Konfigurasi Flow Langflow (BriefME)
*   **Node Setup (5 node / 4 edge):** `Chat Input` $\rightarrow$ `Agent` (prompt CBT `AGENT_SYSTEM_PROMPT`; input `model` dari node `GoogleGenerativeAI` gemini-2.5-flash `tool_model_enabled=true`, input `tools` dari node `MCPTools` server `hearmeout-tools`) $\rightarrow$ `Chat Output "Thought Record"`.
*   **Endpoint:** `POST /api/v1/run/briefme` (`output_type=chat`).
*   ~~Tahap sensor Ollama/anonymizer (Prompt `[DISENSOR]` + model `qwen2.5:3b`)~~ — **dibuang 9 Okt**: input jurnal dianggap tanpa data pribadi.
*   ~~Python Interpreter (`bob run`) + Parse JSON/Parser~~ — **dibuang 11 Okt**: Bob CLI di-uninstall, arsitektur pindah ke Agent native.

---

## 3. Tahap 2: Cognitive Brain — Agent Gemini (node Langflow)
*   **Role:** Clinical Journaling Copilot.
*   **Koneksi Alat:** Server MCP `hearmeout-tools` (`http://127.0.0.1:8788/mcp`) terdaftar di Langflow (API v2) dan dirujuk node `MCPTools` — Agent menerima tool `generate_clinical_document`.
*   **Kunci API:** variabel global Langflow `GOOGLE_API_KEY` (disinkron builder dari `.env`, `load_from_db=true`, divalidasi live ke Google saat disimpan).
*   **System Prompt:** embedded di builder sebagai `AGENT_SYSTEM_PROMPT` (sumber historis: `bob_agent/system_prompt.txt` / `.bob/custom_modes.yaml` — berkas dihapus saat cleanup):
    ```text
    Anda adalah Clinical Copilot dari HearMeOut. Anda menerima teks jurnal pasien
    (tanpa data pribadi) dan tugas Anda mengekstraknya ke matriks CBT lalu membuat
    dokumen lewat tool yang tersedia.

    Langkah wajib Anda:
    1. Analisis teks dan ekstrak ke dalam format JSON dengan kunci:
       - situation (Situasi/kejadian pemicu)
       - thought (Pikiran otomatis yang muncul saat kejadian)
       - emotion (Emosi yang dirasakan)
       - behavior (Perilaku yang dilakukan)
       - alternate_thought (Pikiran alternatif yang lebih seimbang — hasil
         restrukturisasi kognitif ringan, bukan kutipan dari teks)
    2. Setelah ekstraksi selesai, panggil tool 'generate_clinical_document'
       dengan mengirimkan data JSON tersebut (tepat satu kali per teks masuk).
    3. Setelah tool sukses, akhiri pesan HANYA dengan format berikut (tanpa teks lain):
       | Situasi | Pikiran | Emosi | Perilaku | Pikiran Alternatif |
       |---|---|---|---|---|
       | ... | ... | ... | ... | ... |
       (satu baris data per situasi; bila teks memuat beberapa situasi, buat beberapa baris)

       Lalu satu baris kosong dan satu baris: Dokumen: <path file dokumen>

    Aturan:
    - Jangan berikan saran medis kepada pengguna. Fokus pada ekstraksi data secara objektif.
    - Asumsikan teks jurnal tidak memuat data pribadi; jangan bertanya mengenai identitas pasien.
    - Ekstraksi harus objektif: kutip/parafrase keluhan apa adanya, tanpa diagnosa.
    - Bila panggilan tool gagal, ulangi tepat satu kali, lalu laporkan error apa adanya —
      jangan pernah menjawab "timeout" atau "server tidak tersedia" tanpa mencoba tool.
    - Bila teks tidak memuat keluhan yang bisa diekstrak, tetap buat tabel dengan sel "-".
    ```

---

## 4. Tahap 3: Pembuatan MCP Server (Tool Execution)
*   **Stack:** Python, `fastmcp`, `docxtpl`.
*   **Persiapan Template:** `template_cbt.docx` = tabel Thought Record 5 kolom
    (header Indonesia: Situasi, Pikiran, Emosi, Perilaku, Pikiran Alternatif) dengan
    tag Jinja2 `{{ situation }}`, `{{ thought }}`, `{{ emotion }}`, `{{ behavior }}`,
    `{{ alternate_thought }}` — dibuat oleh `make_template.py`.
*   **Skrip `mcp_server.py`:** melihat berkas `mcp_server.py` di root proyek (tool `generate_clinical_document` → render `template_cbt.docx` → simpan ke `output/brief_<id>.docx`).

---

## 5. Tahap 4: Alur Pengujian Terintegrasi (End-to-End)
1.  **Preflight:** `scripts/run_e2e.py` menolak berjalan bila `GOOGLE_API_KEY` masih placeholder (`xxx`) / kosong, atau `LANGFLOW_API_KEY` tidak ada.
2.  **Jalankan MCP Server:** Eksekusi `python mcp_server.py` di terminal agar alat *generate docx* aktif (*listening*).
3.  **Inisialisasi Langflow:** Pastikan Langflow siap menerima input (Ollama/Bob tidak lagi dipakai alur ini).
4.  **Kirim Payload Uji:** Satu POST ke `POST /api/v1/run/briefme` dengan teks jurnal tanpa data pribadi.
5.  **Verifikasi Output:** Respons SATU output **Thought Record** (tabel Markdown + baris `Dokumen: <path>`); guard regex PII + `INSTRUCTION_FRAGMENTS` (kebocoran prompt).
6.  **Cek Output Fisik:** Buka direktori `output/` untuk memverifikasi file `.docx` berhasil terbuat dan variabel *template* terisi dengan data analisis CBT yang tepat.

---

## Lampiran — Status Implementasi (diperbarui 11 Okt 2026)

| Tahap | Status | Berkas |
|---|---|---|
| Tahap 1 — Flow Langflow | ✅ flow **5 node / 4 edge** `BriefME` (endpoint `briefme`, id `fb741b8a-c2c0-4d41-9b75-df9294732ab5`) — rename via PATCH dari flow sensor lama; otak = node **Agent** + **GoogleGenerativeAI** + **MCPTools** (tanpa Python Interpreter/Parse JSON/Parser) | `scripts/create_langflow_flow.py`, `.flow_id` |
| Tahap 2 — Agent Gemini | ✅ prompt CBT embedded (`AGENT_SYSTEM_PROMPT`); variabel `GOOGLE_API_KEY` sinkron dari `.env` (DELETE+POST, validasi live); server MCP `hearmeout-tools` terdaftar via API v2 | `scripts/create_langflow_flow.py`, `.env` |
| Tahap 3 — MCP Server | ✅ tool live di `http://127.0.0.1:8788/mcp` | `mcp_server.py`, `template_cbt.docx`, `make_template.py` |
| Tahap 4 — E2E test | ✅ 19 cek per run; preflight kunci Gemini gagal cepat sebelum menyentuh layanan (belum ada run penuh pasca-refactor sampai kunci asli diisi) | `scripts/run_e2e.py` |
| Tahap 5 — Web UI | ✅ scaffold React + Vite + TS: input jurnal → proxy `/api/*` (API key disuntik server) → render tabel **Thought Record** + path dokumen (endpoint `briefme`); copy bar: "jurnal → Agent CBT → Thought Record + dokumen"; **styling motion** per Design MD (Create mode, Primary Emil · Secondary Jakub, framer-motion, reduced-motion) — `npm run build` lolos | `web/` |

**Penyimpangan dari rencana (keputusan eksplisit):**

* **Tahap sensor lokal DIBUANG (9 Okt).** Prompt anonymizer, node Ollama, output
  `Sensor Output`, `brain_server.py`, `models/`, dan env `OLLAMA_*`/`SENSOR_*`/
  `BRAIN_*`/`WATSONX_*`/`BOB_MODEL` dihapus dari project; model Ollama dihapus
  dari mesin (software Ollama dibiarkan terpasang). Alasan: jurnal pasien tidak
  memuat data pribadi — riwayat iterasi sensor di `AGENT_LOG.md` bag.3–8.
* ~~Arsitektur A (Python Interpreter `bob run`)~~ — **diganti 11 Okt**: Bob CLI
  (`bobshell`) di-uninstall dari mesin (npm + unit systemd
  `bob-gateway-shim.service` dihapus); pipeline kini Agent Gemini native +
  MCPTools. Alasan: Bob hanya bisa dijalankan lewat CLI Python-nya sendiri,
  tidak bisa menjadi server MCP — lihat `AGENT_LOG.md` bag.17.
* Output flow kini SATU `Chat Output` (**"Thought Record"**), dipanggil lewat
  `POST /api/v1/run/briefme` (`output_type=chat`) — komponen `Webhook`
  Langflow bersifat input, bukan output.
* ~~Shim gateway `bob_gateway_shim.py`~~ — **dihapus 11 Okt** bersama Bob CLI.
* Fase A (clock CPU 1,4 → 2,1 GHz) menaikkan sensor 0,95 → 2,12 tok/s —
  `AGENT_LOG.md` bag.10.

Lihat `README.md` untuk urutan menjalankan seluruh komponen, `AGENT_LOG.md` untuk
angka ukur dan riwayat diagnosis.
