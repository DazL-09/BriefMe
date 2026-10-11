# AGENT_LOG — HearMeOut

Catatan hasil ukur & keputusan teknis selama implementasi `Agent-Plan.md`.

## 1. Lingkungan eksekusi (terukur)

| Aspek | Nilai |
|---|---|
| CPU | AMD Ryzen 5 PRO 4650U, 12 vCPU, governor `powersave` |
| GPU | AMD Renoir (Vega) **tidak dipakai** — Ollama 0.24.0 (build resmi) → `100% CPU` |
| Throughputgenerate | ±0,9–1,0 token/detik |
| Throughput prompt eval | ±2,3 token/detik |
| Model | `qwen2.5:3b` (Q4, 1,9 GB) dan tag `qwen2.5:3b-sensor` |

Dampaknya: satu langkah LLM (±400 token prompt + ±90 token output) memakan waktu
**±1,5 menit saat model sudah warm** dan **±30 menit saat model baru di-load**
(satelit run "K" cold-load tercatat 1976 detik; run warm 102 detik).

## 2. Temuan: temperature 0 tidak terkirim dari Langflow

Komponen Ollama Langflow (`ext:ollama:ChatOllamaComponent`) menyusun parameter dengan:

```python
"temperature": self.temperature or None,   # 0.0 or None → None → dibuang
llm_params = {k: v for k, v in llm_params.items() if v is not None}
```

Akibatnya nilai `Temperature: 0.0` di node **tidak pernah sampai ke Ollama**; server memakai
default 0,8 (sampling), sehingga hasil sensor tidak konsisten.

**Workaround:** model tag yang sama dengan temperature 0 di-bake di sisi server
(`models/Modelfile.sensor` → `PARAMETER temperature 0 / top_k 1 / top_p 0`), dipakai lewat
field `model_name` node Ollama. Field `temperature` tetap diisi `0.0` sesuai rencana.

## 3. Iterasi prompt sensor (semuanya via jalur chat yang dipakai Langflow, temperature 0)

| Varian | Prompt | PII bocor |
|---|---|---|
| Awal (rencana, *zero-shot*) | 4 baris apa adanya | nama, nama jalan, kota, "+Bu Siti" (hanya telepon tertutup) |
| V4 | + 2 contoh generik | nama, nama jalan, kota |
| P1 | + 3 contoh, daftar PII diperjelas | nama jalan, kota |
| P3 | contoh mencerminkan pola payload | nama jalan, kota |
| P4 | P3 + baris "cek ulang" sebelum `Output:` | **kota saja** |
| **P5 (dipakai)** | P4 + contoh "kota tunggal" | **tidak ada — PASS** |
| P6 | P5 + "tulis ulang seluruh teks" | tidak ada — PASS |

Diagnosa tambahan: prompt yang benar-benar dikirim Langflow sudah diverifikasi identik
dengan prompt di atas (baca `vertex_build.artifacts` pada DB Langflow), dan
`/api/chat` vs `/api/generate` bukan penyebabnya (keduanya lemah pada prompt yang sama).

## 4. Iterasi prompt ekstraksi CBT (Cognitive Brain)

| Varian | Bentuk instruksi | Hasil |
|---|---|---|
| Awal (rencana) | bullet `- trigger: kejadian/kondisi yang memicu keluhan.` dst. | **bocor**: `trigger` = definisi kunci (`brief_7c13ce85`); `distortions` = `tidak disebutkan` (`brief_37ecd166`) |
| **V1 (dipakai)** | skema JSON satu baris + aturan ringkas; distorsi **boleh disimpulkan** dari `automatic_thoughts` | lolos — 4 kunci terisi nyata, distorsi `Personalisasi (...)` |

Pendukung V1 (`brain_server.py`):
* `_problems()` menandai nilai **placeholder** (`tidak disebutkan`, `n/a`, …) atau **bocor instruksi** (`LEAK_FRAGMENTS`); bila ada → **retry 1×** dengan pesan korektif, lalu dilaporkan di respons (`cbt_problems`).
* Prompt gabungan turun ±413 → ±354 token/panggilan; `keep_alive: 30m` agar model tidak di-unload antar request.
* Sisi uji: `run_e2e.py` menambah cek "instruksi prompt tidak bocor" ke JSON **dan** `.docx`
  (frasa `saran medis` dikecualikan karena memang ada di footer statis `template_cbt.docx`).

## 5. Diagnosis performa (mengapa 3B terasa lambat)

Benchmark langsung `/api/generate` (`qwen2.5:3b`, num_predict 48–64):

| Metrik | Terukur | Wajar (6C/12T Zen2, AVX2, Q4_K_M) |
|---|---|---|
| generate | **0,94 tok/s** | ±8–12 tok/s |
| prompt eval | **0,94 tok/s** | ±15–25 tok/s |
| clock saat beban penuh | **1.397 MHz** (runner 16 thread, 550% CPU) | boost s/d 4.000 MHz |

Bukan thermal (45 °C), bukan VM, bukan swap (PSI memory ~0). Penyebab: firmware ThinkPad
dipaksa **`platform_profile = low-power`** → `scaling_max_freq 1.7 GHz` (dari 4,0 GHz),
`boost = 0`, governor `powersave`. Artinya CPU dikunci ±35% kemampuannya.
Perbaikan (butuh root, belum dijalankan):

```bash
echo performance | sudo tee /sys/firmware/acpi/platform_profile
echo 1 | sudo tee /sys/devices/system/cpu/cpufreq/boost
sudo cpupower frequency-set -g performance
```

## 6. Hasil uji end-to-end

Lihat `output/audit.jsonl` untuk setiap dokumen yang pernah dibuat.

* `03-10 22:33–22:50` — **SEMUA PEMERIKSAAN LOLOS** (19/19) dalam 1.023 dtk;
  dokumen `brief_1fe7c5a6.docx`; sensor lolos, ekstraksi tanpa placeholder/kebocoran,
  `.docx` terisi & bebas PII.
* Run sebelumnya (`brief_7c13ce85`, `brief_37ecd166`) gagal pada pemeriksaan placeholder
  dan kebocoran instruksi — itulah pemicu iterasi di bagian 4.

## 7. Percobaan `qwen2.5:1.5b` untuk sensor — DITOLAK

Pertimbangan kecepatan vs kualitas. Disk dibersihkan dulu (cache yay 1,3 GB + npm +
pip + trash ≈ +1,6 GB), `ollama pull qwen2.5:1.5b` (986 MB), tag `qwen2.5:1.5b-sensor`
dibuat dari `models/Modelfile.sensor15` (temperature 0 / top_k 1, sama seperti tag 3B).

**Benchmark A/B** (prompt identik 64 token, clock masih terkunci 1,4 GHz):

| Model | Generate | Prompt eval |
|---|---|---|
| `qwen2.5:3b` | 1,06 tok/s | 1,32 tok/s |
| `qwen2.5:1.5b` | **2,08 tok/s** | **2,61 tok/s** |

±2× lebih cepat — sesuai ekspektasi, tapi belum menjamin kualitas.

**Uji sensor: 3 payload (P5, payload-email, payload di luar pola contoh) × 3 varian prompt:**

| Varian prompt | payload-E2E | payload-email | payload di luar pola |
|---|---|---|---|
| P5 (lolos di 3B) | **FAIL** — nama orang bocor (`Ahmad`, `Wijaya`, `Siti`) | **FAIL** — kalimat disusun ulang | **FAIL** — `Hadi`, `Sudirman`, `Jakarta` bocor |
| P5.1 (+ "salin persis", contoh 4) | **FAIL** — `Bandung` bocor | **FAIL** — isi klinis dihalusinasi jadi "Sembuh sudah …" | **FAIL** — `Jakarta` bocor |
| P5.2 (+ aturan kota eksplisit) | **FAIL** — nama + nomor telepon bocor | **FAIL** — "sulit tidur" jadi `[DISENSOR]` | **FAIL** — `Jakarta` bocor |

**Keputusan:** setelah 2 iterasi (batas yang direncanakan) 1.5B tetap tidak lolos —
bocor PII di 2–3 payload sekaligus *dan* mengubah/menghapus isi klinis. Sensor
**tetap `qwen2.5:3b-sensor`**, ekstraksi CBT tetap `qwen2.5:3b`
(konfigurasi `.env` dan flow Langflow tidak pernah diubah; rollback = tidak ada aksi).
Model `qwen2.5:1.5b(-sensor)` dibiarkan terpasang (986 MB) untuk eksperimen berikutnya.
Lebih baik menerima 17 menit yang lolos uji daripada 8 menit yang membocorkan PII.

## 8. Integrasi IBM Bob (4 Okt)

Node.js 22.23.3 dipasang *user-level* (`~/.local/node-v22.23.3-linux-x64`, symlink di
`~/.local/bin`) karena AUR butuh sudo; Bob CLI `2.0.5` terpasang dari npm global.
`bob mcp list` membaca `.bob/mcp.json` → server `hearmeout-tools` aktif.

**Temuan autentikasi:** `bob chat` jalan dengan SSO login, tetapi `bob run` (dipakai
flow) selalu menolak tanpa API key (`Bob API key is required. Set BOB_API_KEY …`) —
validasi di awal, tanpa fallback SSO.

**Solusi — shim gateway lokal** (`scripts/bob_gateway_shim.py`, systemd --user
`bob-gateway-shim.service`, `127.0.0.1:8899` → `api.us-east.bob.ibm.com`):

* membaca token SSO per-request dari `~/.bob/settings/auth-secrets.json`
  (kunci `bob.auth.tokens-https://api.us-east.bob.ibm.com`) sehingga token expired
  tidak perlu restart layanan;
* menulis ulang `Authorization: apikey <X>` → `Bearer <token>` dan menyuntik
  `x-instance-id` + `x-team-id` (tanpa `x-team-id` gateway menjawab *402 Team user
  not found*);
* merestream body SSE agar streaming bob tetap jalan.

Cara pakai: `BOB_GATEWAY_URL=http://127.0.0.1:8899 BOB_API_KEY=sso-shim bob run …`.

**Patch `bob.js`:** fungsi `lfr()` (host-region override yang menolak URL lokal)
diberi guard `127.0.0.1`/`localhost` (backup `/tmp/opencode/bob.js.bak`), dan
`bobShell.autoUpdate=false` di `~/.bob/settings/settings.json` agar patch bertahan.
Output `bob run --format json`: `status`, `stats.tool_calls`, `last_message`.

## 9. Arsitektur A — flow 7 node (4 Okt)

Keputusan user: **Arsitektur A** — Langflow → Prompt#2 → node **Python Interpreter**
→ `bob run --mode clinical-copilot` → MCP → Chat Output "Bob Output" (bukan
`brain_server.py`). `scripts/create_langflow_flow.py` dibangun ulang: 7 node /
6 edge, endpoint `hearmeiout-anonymizer`, flow `fb741b8a-c2c0-4d41-9b75-df9294732ab5`.

* Prompt Template memakai `str.format` (`use_double_brackets=false`) → hanya
  `{text}` yang boleh berupa kurung kurawal; kode Python template disusun lewat
  token `@@TOKENS@@` + `.replace()` — kalau tidak, `format()` gagal saat build.
* `BOB_CODE_TEMPLATE` menjalankan `bob run` dgn env `PATH` node, `BOB_GATEWAY_URL`,
  `BOB_API_KEY=sso-shim`, `cwd=workspace`, `timeout=900`, lalu mencetak `BOB_RC=…`.
* Parser output API: teks ada di `outputs[].outputs[]` per `component_display_name`
  (`"Chat Output"` / `"Bob Output"`).

**Peringatan:** tab editor Langflow lama bisa men-*autosave* dan menimpa flow
(terbukti 4 Okt 11:06 flow kembali ke 4 node) — hard-refresh tab sebelum menyentuh
editor, lalu verifikasi `GET /api/v1/flows/fb741…` tetap 7 node.

## 10. Fase A — kunci performa CPU (4 Okt)

Dijalankan user (butuh sudo): `platform_profile=performance`, `boost=1`,
`governor=performance`, `scaling_max_freq` naik **1,4 → 2,1 GHz**
(perintah 4,0 GHz ditolak BIOS/firmware).

| Metrik | Sebelum | Sesudah |
|---|---|---|
| generate sensor | 0,95 tok/s | **2,12 tok/s** |
| langkah sensor (flow) | ±239 dtk | **±40 dtk** |
| E2E penuh | ±8 menit | **99,3 dtk** |

## 11. Diagnosis: tool call Bob → MCP "timeout" (4 Okt)

**Gejala:** `bob run` menjawab `SdkError REQUEST_TIMEOUT` walau `mcp_server` merespons
200 dalam 0,05–0,07 dtk; dokumen justru *terbuat* di server (file ada, audit masuk).

Diagnosis: forwarder logging sementara (`/tmp/opencode/mcp_fwd.py`) di antara Bob dan
8788 memperlihatkan `tools/call` → `RESP 200 application/json` — respons sampai, tetapi
bob tetap "timeout". Temuan kunci ada di log shell Bob:

```
Session failed to start → "MCP connection timed out after 60ms"
Failed to load resource templates → "Request timed out"
```

**Akar masalah:** `"timeout": 60` di `.bob/mcp.json` ditafsir Bob sebagai **60 milidetik**
(bukan 60 detik). Akibatnya: sesi pertama gagal connect → toolset di-snapshot sebelum
reconnect → model melihat tool *tidak tersedia* (maka jawaban "cari dokumentasi"/
"timeout" tanpa pernah `tools/call`); `tools/call` yang berjalan pun dibatalkan jauh
sebelum server selesai — padahal request-nya masuk dan dokumen terbuat.

**Fix:** `"timeout": 60000` (60 dtk dalam satuan ms). Verifikasi: **6/6 run berturut
sukses** (3 via forwarder, 3 langsung `127.0.0.1:8788`), error `60ms` hilang.

**Keputusan arsitektur:** forwarder *tidak* diperlukan — unit & skripnya dihapus;
`.bob/mcp.json` menunjuk langsung `http://127.0.0.1:8788/mcp`.
Hipotesis "race keep-alive uvicorn" (patch `timeout_keep_alive: 120` di `mcp_server.py`)
dibuktikan **bukan** penyebab — dibiarkan aktif karena tidak berpengaruh.

**Penguatan prompt** (`custom_modes.yaml` / `bob_agent/system_prompt.txt`): instruksi
keras agar model langsung `tools/call` setelah ekstraksi, dilarang search internal,
dan dilarang menjawab "timeout" tanpa mencoba tool — menutup kasus model yang
mengarang jawaban gagal.

## 12. Hasil uji end-to-end terkini (4 Okt)

* `04-10 12:53` — E2E pertama Arsitektur A: pipeline jalan penuh (sensor → Bob →
  MCP → docx `brief_690810a3`); **2 pemeriksaan gagal hanya karena parser regex**
  tertangkap markdown link utuh — parser diperbaiki (cari path absolut dulu, fallback
  relatif `output/…`).
* `04-10 12:55` — **SEMUA PEMERIKSAAN LOLOS dalam 99,3 dtk** — dokumen
  `brief_69b44d5a.docx`: 4 bagian CBT terisi (distorsi `Catastrophizing`), bebas PII,
  bebas kebocoran instruksi.

## 13. Skema 5 kunci "Thought Record" + pasca-proses node Langflow (9 Okt)

**Kebutuhan output (#2 prioritas):** dari keluaran generik (dump JSON `BOB_RC=0…`)
menjadi tabel CBT siap pakai `Situasi | Pikiran | Emosi | Perilaku | Pikiran
Alternatif` — **Markdown dulu** (ringan), unduhan `.docx` menyusul dari website.
Pasca-proses wajib memakai **node Langflow, bukan kode Python**.

**Perubahan skema** (trigger/automatic_thoughts/somatic_symptoms/cognitive_distortions
→ situation/thought/emotion/behavior/alternate_thought) di lima berkas serentak:
`bob_agent/system_prompt.txt`, `.bob/custom_modes.yaml` (yang dibaca Bob), signature
tool `mcp_server.py`, template 5 kolom `make_template.py` → `template_cbt.docx`
(regenerasi; render self-test lolos), dan fallback `brain_server.py` (CBT_KEYS +
EXTRACT_INSTRUCTION + alias). `alternate_thought` = satu-satunya nilai yang direka
(restrukturisasi ringan); sisanya ekstraksi. Pesan akhir Bob HANYA tabel Markdown
(header Indonesia, satu baris per situasi) + baris `Dokumen: <path>`.

**Flow 7 → 9 node (6 → 8 edge)** — `scripts/create_langflow_flow.py`:

* Python Interpreter kini hanya mengeksekusi `bob run` dan mencetak **stdout
  apa adanya** (sukses) atau teks polos `BOB_GAGAL rc=…` (gagal) — tanpa kurung
  kurawal di template Prompt (tetap `str.format`, hanya placeholder `{text}`).
* Node **ParseJSONData** (jq) — ekstrak `last_message`:
  `.result as $r | ((try fromjson|.last_message catch null) // (capture objek JSON
  bila ada noise di depan) // $r) | gsub buang code-fence | ltrimstr("\n")`.
  Diuji 8 kasus (JSON murni, noise, bukan JSON, last_message null/kosong/braces)
  + lolos `validate_jq_program` (tanpa `env/input/inputs`).
* Node **Parser** pattern `{text}` → Message bersih.
* Display name diganti: `Chat Output` → **Sensor Output**, `Bob Output` →
  **Thought Record**.

**E2E (`scripts/run_e2e.py`) ditulis ulang:** langkah 4 = header tabel Indonesia +
bukan dump JSON + path dokumen (BOB_RC dibuang); langkah 5 = membaca `doc.tables`
(kolom 5, sel alternatif bukan placeholder); `INSTRUCTION_FRAGMENTS` disesuaikan
prompt baru.

**Hasil:**

* Preflight `bob run` langsung: **7,7 dtk**, `status: success`, `tool_calls: 1`,
  stdout JSON murni 632 byte, `last_message` = tabel + `Dokumen: …`.
* E2E penuh: **2 run berturut SEMUA PEMERIKSAAN LOLOS (21 cek)** — run 1 566 dtk
  (sensor cold-start tepat setelah `ollama create`), run 2 **102 dtk flow / 108 dtk
  total** (`brief_a22ad4ab.docx`) — sebanding baseline 99,3 dtk (bag.12).

**Lingkungan dipulihkan:** token SSO Bob kedaluwarsa 4 Okt → diperbarui (login
non-interaktif), shim `bob-gateway-shim.service` dijalankan ulang; `~/.ollama`
kosong → `qwen2.5:3b` di-pull ulang (1,9 GB) + tag `qwen2.5:3b-sensor` dibuat ulang;
MCP server dijalankan ulang (skema tool 5 argumen baru).

## 14. Scaffold Web UI — React + Vite (9 Okt)

`web/` (prioritas rendah, menunggu Design MD untuk styling): React 19.3 + Vite 8.3 +
TypeScript 5.9, `npm run build` lolos (`tsc -b` + vite, 16 modul, ±223 kB JS).

* Aplikasi: textarea jurnal → `POST /api/v1/run/hearmeiout-anonymizer` → menampilkan
  **Sensor Output** dan **Thought Record** (tabel Markdown di-parse jadi `<table>`,
  baris `Dokumen: <path>` ditampilkan apa adanya — unduhan `.docx` menyusul).
* Proxy dev Vite (`server.proxy` + `preview.proxy`): `/api/*` → Langflow
  `:7860` dengan `x-api-key` disuntik dari `../.env` via `configure(proxyReq)` —
  key tidak pernah menyentuh frontend (keputusan: proxy Node, bukan Python).
* Catatan Vite 8: dev server hanya bind `[::1]` → buka `http://localhost:5173/`
  (bukan `127.0.0.1`). Smoke test: `GET /` 200 + `GET /api/v1/flows/` via proxy
  mengembalikan JSON valid (bukti injeksi key bekerja; tanpa key Langflow membalas 401).
* Dev server ditinggal berjalan di background (`/tmp/opencode/vite_dev.log`).

## 15. Penghapusan Sensor Ollama & Rename Flow → BriefME (9 Okt)

**Keputusan pengguna:** "orang curhat tapi isinya no hp" — input jurnal dianggap
tanpa data pribadi, tahap sensor lokal dibuang total dari project; flow/endpoint
dinamai **BriefME / `briefme`** ("Jadi BriefME"); **software Ollama dibiarkan
terpasang** ("kalau ollama biarin aja") tapi modelnya dihapus.

**Flow 9 → 6 node (8 → 5 edge), rename via PATCH** (id lama
`fb741b8a-…` dipertahankan, tidak membuat flow baru) —
`scripts/create_langflow_flow.py`:

* Dibuang: `ANONYMIZER_PROMPT`, Prompt #1, node Ollama, `Chat Output
  "Sensor Output"`, konstanta `MODEL`/`OLLAMA_URL`.
* Rantai baru: `Chat Input → Prompt (kode bob) → Python Interpreter → Parse JSON
  (jq) → Parser → Chat Output "Thought Record"`; lookup by `.flow_id`/nama lama
  (`LEGACY_FLOW_NAMES`) sebelum PATCH.
* Verifikasi API: `name=BriefME`, `endpoint_name=briefme`, 6 node / 5 edge,
  rantai sesuai.

**Prompt & e2e:**

* `bob_agent/system_prompt.txt` + `.bob/custom_modes.yaml`: "teks pasien yang
  sudah disensor (bebas PII)" → "teks jurnal pasien (tanpa data pribadi)";
  `whenToUse` tak lagi menyebut "Cognitive Brain POST /handoff" → "flow Langflow
  BriefME".
* `scripts/run_e2e.py`: cek Ollama & langkah sensor dibuang (kini 4 langkah,
  endpoint default `briefme`); `TEST_TEXT` jurnal realistis tanpa PII (kata kunci
  `atasan/kantor/dipecat/berdebar` dipertahankan untuk cek dokumen); PII literal
  → **guard regex** (HP `08…`, email, `Jl.`) pada output maupun `.docx`; cek baru
  "hanya satu output Thought Record" + "output tidak kosong".

**Penghapusan file:**

* `brain_server.py` (591 baris) dan `models/` (`Modelfile.sensor`,
  `Modelfile.sensor15`) dihapus.
* `.env`/`.env.example`: endpoint → `briefme`; baris `OLLAMA_BASE_URL`,
  `SENSOR_MODEL`, `BRAIN_*`, `WATSONX_*`, `BOB_MODEL`, `BRAIN_MODEL` dihapus.
* `requirements.txt`: `httpx>=0.27` dihapus (hanya dipakai `brain_server.py`).
* Mesin: `ollama rm qwen2.5:3b-sensor` + `ollama rm qwen2.5:3b` (model kosong);
  daemon `ollama serve` dibiarkan jalan sesuai instruksi.
* Web: kartu "Sensor Output" + state `sensor` + `.sensor-text` dihapus; `SAMPLE`
  jurnal realistis tanpa PII; fetch → `/api/v1/run/briefme`; hint → "(Bob +
  dokumen)"; `npm run build` lolos.

**Dokumen:** README (diagram 6/5, prasyarat tanpa Ollama, curl `briefme` satu
output, catatan "Tanpa sensor lokal", autosave verify `6`), Agent-Plan (§1–2
ditulis ulang jadi BriefME, §5 langkah 2/4, lampiran + penyimpangan).

**Insiden autosave Langflow:** run e2e pertama pasca-rename **GAGAL 1 cek** —
respons flow masih memuat `Sensor Output`; API menunjukkan flow balik ke versi
lama (9 node, `updated_at` di tengah run). Penyebab: tab editor Langflow di
Brave masih terbuka → autosave menimpa PATCH (persis pola README §autosave).
Tab ditutup user, flow di-PATCH ulang, lalu:

**Hasil e2e flow BriefME — 2 run berturut SEMUA PEMERIKSAAN LOLOS (19 cek):**

* Run 1: flow 12,0 dtk / total 18,0 dtk (`brief_3b6d3f72.docx`) — output tunggal
  `Thought Record`, guard regex PII bersih.
* Run 2 (sesudah footer template diganti): flow 12,3 dtk / total 18,1 dtk
  (`brief_308a877b.docx`).
* Cek kecepatan: tanpa tahap Ollama, E2E penuh turun dari 102–108 dtk → **~18 dtk**.
* Footer `template_cbt.docx` (regen via `make_template.py`): "…Teks bersumber
  dari jurnal pasien (tanpa data pribadi)." — `TEMPLATE_STATIC_TEXT` di e2e ikut
  disesuaikan.

## 16. Motion Design UI (Design MD: Create mode, 9 Okt)

`design-motion-principles_SKILL.md` (1387 baris, self-contained — `references/*`
tertanam inline) diterapkan ke `web/` via **Create workflow**:

* **Discovery (STEP 1):** konteks = web jurnal kesehatan personal; weighting
  dikonfirmasi user: **Primary Emil · Secondary Jakub** (mapping: productivity
  tool). Mode Create, required reading (Motion Cookbook + accessibility +
  anti-checklist/red-flags sebagai gotcha self-check) selesai dibaca.
* **Dependency:** `framer-motion@14` (untuk `AnimatePresence` exit; cookbook
  memakai API motion/FM).
* **Frekuensi gate (Emil):** mengetik textarea & aksi keyboard → tanpa animasi;
  heading/hero/paragraf statis → tanpa entrance (anti "motion-on-mount-for-
  static-content"); tabel hasil → tanpa stagger (anti stagger-spam); busy state
  → tanpa pulsing (anti pulsing-indicators); tidak ada hover-scale di mana pun.
* **Yang dianimasikan:** kartu `Thought Record` masuk = opacity + translateY 8px
  + blur 4→0, spring `duration 0.45 bounce 0` (Jakub enter recipe; aksi
  occasional — muncul sekali per analisis); exit lebih halus (y −8). Error:
  masuk cepat `0.18s cubic-bezier(0.2,0.8,0.2,1)`, exit y −8 + blur. Hint busy:
  opacity saja 0.15s. Tombol: press `scale(0.97)` (cookbook §10, bukan dari
  scale(0)) + transisi warna 150ms + hover gelap 150ms; textarea focus border +
  ring `box-shadow` 150ms.
* **Easing kustom** `--ease-out: cubic-bezier(0.2,0.8,0.2,1)` — tidak memakai
  `ease`/`ease-in-out` bawaan.
* **Visual (Jakub):** kartu light mode pakai multi-layer `box-shadow`
  (0 0 0 1px / 1px 2px / 2px 4px rgba) menggantikan border; dark mode tetap
  border hard-edge (cookbook: border fine di dark).
* **Aksesibilitas (mandatory):** `MotionConfig reducedMotion="user"` + blok CSS
  `@media (prefers-reduced-motion: reduce)` mematikan transisi/animasi.
* **Performa:** hanya `transform`/`opacity`/`filter` yang dianimasikan; tidak ada
  `width/height/top/left`, tanpa `will-change` global.
* Verifikasi: `npm run build` lolos (tsc + vite; JS 223 → 350 kB karena
  framer-motion); dev server `:5173` jalan terus (HMR aktif).

## 17. Refactor: Bob dibuang — Agent native Langflow + MCPTools (11 Okt)

**Keputusan (via konfirmasi pengguna):** arsitektur = *Agent + MCP Tools, drop
Bob*; LLM = Google Gemini (kunci placeholder `xxx` dulu, asli menyusul);
cleanup = agresif; Bob = uninstall penuh.

* **Alasan Bob tidak bisa dipertahankan:** Bob hanya bisa dieksekusi via CLI
  Python (`bob run`) — tidak memiliki mode server MCP — sehingga menjadi
  dependensi cloud agent eksternal di dalam pipeline. Dihapus dari pipeline,
  lalu di-uninstall dari mesin.
* **Flow baru (5 node / 4 edge, id tetap `fb741b8a-…`, endpoint `briefme`):**
  ChatInput →{`message→input_value`} Agent; GoogleGenerativeAI (gemini-2.5-flash,
  `tool_model_enabled=true`) →{`model_output→model`} Agent; MCPTools
  (`hearmeout-tools`) →{`component_as_tool→tools`} Agent; Agent →{`response→input_value`}
  ChatOutput **"Thought Record"**. Node Python Interpreter/Prompt/Parse JSON/
  Parser dibuang; prompt CBT pindah ke `AGENT_SYSTEM_PROMPT` embedded builder.
* **Temuan teknis penting (dibuktikan via eksperimen API):**
  * `tool_mode`: flip frontend node `tool_mode: true` + ganti `outputs` dengan
    synthesized `component_as_tool` (method `to_toolkit`) — meniru
    `lfx/graph/flow_builder/connect.py::_enable_tool_mode`.
  * Format edge: handle `"` → `œ` (U+0153), id `reactflow__edge-…`,
    `dataType` = `data.type` node, tipe targetHandle = tipe field template
    (`model`/`other`/`str`).
  * `api_key` punya `load_from_db: true` → nilai di flow = **nama variabel**
    (`GOOGLE_API_KEY`), bukan literal; runtime meng-hydrate dari store
    variabel global (ada percobaan decrypt historical `InvalidToken()` —
    baris lama, terhapus saat variabel dibuat ulang).
  * **PATCH variabel provider (GOOGLE_API_KEY) selalu 404** meski GET list
    menunjukkan `is_owner: true` (endpoint `GET /variables/{id}` memang tidak
    ada; fetch row gagal). Strategi builder: **DELETE + POST**.
  * POST variabel provider **mem-validasi kunci live ke Google** (model
    `gemini-3.8-flash`) — placeholder ditolak `400 "Invalid API key for Google
    Generative AI"`. Jadi nilai asli kunci = prasyarat mutlak run penuh.
  * Registrasi MCP server: `POST/PATCH /api/v2/mcp/servers/{name}` body
    `{"url": …}` — idempoten, dipakai builder.
* **Bug builder diperbaiki:** `API_KEY`/`LF_URL` di-binding saat import (sebelum
  `load_env_file`) → dipanggil dari shell bersih langsung `403 No authentication`.
  Kini dibaca lazy per-panggilan (`lf_url()`, `os.environ.get` di `api()`);
  builder maupun `run_e2e.py` memuat `.env` sendiri (tanpa `set -a`).
* **`run_e2e.py`:** cek Bob CLI dibuang; preflight gagal cepat
  (`GOOGLE_API_KEY` kosong/`xxx` → exit 1 sebelum menyentuh layanan);
  `INSTRUCTION_FRAGMENTS` disesuaikan dengan `AGENT_SYSTEM_PROMPT` baru;
  penamaan `bob` → `thought` di seluruh alur.
* **Web copy:** hero = "jurnal → Agent CBT → Thought Record + dokumen"; hint
  busy = "± 1 menit (AI + dokumen)"; `npm run build` lolos.
* **Cleanup agresif:** `__pycache__/` (termasuk pyc yatim `brain_server`),
  `scripts/__pycache__/`, `.freebuff/`, `.bob/`, `bob_agent/`,
  `design-motion-principles_SKILL.md`, `scripts/bob_gateway_shim.py`,
  `web/*.tsbuildinfo`, `web/dist/`, 45 dari 46 `output/brief_*.docx`
  (pertahankan `brief_308a877b.docx` + `audit.jsonl`).
* **Bob uninstall penuh:** unit systemd `bob-gateway-shim.service`
  stop+disable+file dihapus+`daemon-reload`; `npm uninstall -g bobshell`
  (2.0.5, 6 paket) — `bob` hilang dari PATH & `~/.local/share/npm/bin`,
  unit/daemon bersih. Node.js/npm **dipertahankan** (build web).
* **Dokumen ditulis ulang:** `README.md` (diagram native 5 node, setup/run tanpa
  Bob/shim, catatan `api_key`+DELETE/POST+validasi live), `Agent-Plan.md`
  (§1–§5 + tabel status 11 Okt), log ini §17.
* **Status akhir sesi:** builder hijau dari shell bersih (warning placeholder);
  e2e preflight gagal cepat sesuai desain; **run penuh menunggu kunci Gemini
  asli** → isi `.env` → jalankan builder (sinkron variabel) → `run_e2e.py`.

## 18. Arsitektur final hackathon: Bob (API resmi) + Langflow, MCP di sisi Bob (11 Okt)

* **Kebalian sesi:** plan hackathon (deadline Stage 1 hari ini) — engine =
  **IBM Bob via API key resmi** + Langflow sebagai orkestrasi; **MCP dipasang di
  sisi Bob** (`.bob/mcp.json`), bukan lewat node Langflow; domain **BriefME
  tetap** (tanpa pivot MiMo); Gemini 5-node hanya **flow cadangan**. Bob
  2.0.6 kembali terpasang (npm `bobshell`, tanpa shim systemd).
* **Key & CLI:** `BOB_API_KEY` + `BOB_MAX_TURNS=6` di `.env`; `bob run` headless
  = `--accept-license --mode ask|agent --format json --log-level … --trust
  -w $PWD` + stdin `< /dev/null` (2.0.6 tanpa `-p`; prompt positional).
* **MCP sisi Bob:** `.bob/mcp.json` — kunci **`url`** (bukan `httpURL`; bundle
  `bob.js` tak punya string itu; error validator: "must include a command or
  URL"), `timeout` milidetik, `alwaysAllow: [generate_clinical_document]`.
  Uji penuh: `mcp__hearmeout-tools__generate_clinical_document` terpanggil →
  `output/brief_446d6a46.docx`.
* **Preflight sandbox Langflow (offline, sebelum menyentuh flow):**
  * `validate_code_safety` **menolak `ast.Import`** — module wajib lewat field
    `global_imports` (`"math,os,subprocess"`, dipisah koma); builtins sandbox
    **tanpa `open()`** → fallback kunci BOB dibaca via `subprocess cat .env`.
  * Prompt template = kode python str.format; audit `{`/`}` hanya `{text}`;
    `validate_prompt` → `['text']`; field dinamis disuntik manual (bentuk
    `DefaultPromptField.to_dict()`).
  * jq `last_message` (fromjson + capture-noise + fence-strip) lolos
    `validate_jq_program` + 8 kasus uji.
  * Eksekusi nyata di globals sandbox (`import_allowed_module`) → `bob run
    --mode agent` sukses, `tool_calls: 1`, stdout JSON.
  * Mini-flow API membuktikan **gate `allow_custom_components` terbuka** dan
    edge `→python_code` menimpa nilai template saat runtime (persis perilaku
    yang dibutuhkan rantai Prompt→Python).
* **Builder `create_langflow_flow.py` — `--engine bob|gemini` (default bob):**
  * `build_flow_bob()`: **6 node / 5 edge** — ChatInput→Prompt.text→
    PythonREPL.python_code→ParseJSON.input_value→Parser.input_data→
    ChatOutput; `global_imports=math,os,subprocess`; query jq `JQ_LAST_MESSAGE`;
    Parser `pattern={text}`; ChatOutput "Thought Record". Node id `…-HEARB1..6`.
  * Kode python node: absolut `~/.local/share/npm/bin/bob` (PATH Langflow tak
    memuatnya), `subprocess.run([bob,'run',…,prompt], input='', timeout=900,
    env=env, cwd=PROJECT)`, fallback baca `.env` via `cat`, instruksi =
    `AGENT_SYSTEM_PROMPT` (sama, tanpa kurawal).
  * Migrasi: flow Gemini yang menempati primary **di-clone** ke "BriefME
    (Gemini)" endpoint `briefme-gemini` (`.flow_id.gemini`) sebelum primary
    ditimpa data bob. `--engine gemini` = sinkron `GOOGLE_API_KEY` + update
    flow cadangan.
* **Bug predicate:** `"GoogleGenAI" in json.dumps(data)` **selalu False** —
  substring itu tidak ada di `"GoogleGenerativeAIComponent"` (huruf `e` di
  tengah) → klone tak terpicu, data Gemini primary hilang. Diperbaiki ke
  `"GoogleGenerativeAI"`; fallback diregenerasi dari `build_flow_gemini()`
  (id `fcddb060-2eef-45c8-bb20-8cbd385f317d`).
* **Insiden autosave UI (terulang):** smoke test sukses 05:48 (12.7s, tabel +
  `brief_aa09b8ee.docx`) → **05:49:54 flow ditimpa tab editor Langflow** jadi
  4 node tanpa model → run berikutnya `500 "No model selected"`. Rebuild +
  e2e langsung hijau. **Selalu tutup tab editor flow sebelum/sesudah builder.**
* **E2E engine-aware:** `E2E_ENGINE=bob|gemini` — preflight bob: `BOB_API_KEY`
  terisi + `.bob/mcp.json` ada + health `bob --version` (2.0.6); gemini:
  `GOOGLE_API_KEY` bukan placeholder; endpoint `briefme` vs `briefme-gemini`;
  label rantai disesuaikan. **Hasil: semua pemeriksaan LOLOS dalam 19.9s**
  (output tabel 5 kolom + `brief_3cb6d30f.docx`, bebas PII & kebocoran prompt).
* **Web:** hero "jurnal → Bob Agent → Thought Record + dokumen"; hint
  "(Bob + dokumen)"; footer transparansi AI (bantuan AI — bukan diagnosis);
  `npm run build` lolos.

## 19. Fitur baru: 3 flow tahapan + history MERN + guardrail alt (11 Okt)

* **Kebutan/keputusan user:** history pakai **stack MERN** (MongoDB + Express +
  React); pikiran alternatif **TIDAK diisi otomatis** saat analisis — tombol
  manual (**disarankan**) / saran AI (opsi) di tahap analisis; dokumen `.docx`
  dibuat **setelah alt terisi** (tombol "Buat Dokumen"); buang tombol "Pakai
  contoh" + placeholder netral "Ceritakan pengalaman hari ini apa adanya…";
  push GitHub **ditunda** ("jgn git local dlu"); kriteria rubric README
  menyusul (user akan paste); tab editor autosave **sudah ditutup**; deck PR
  urusan user.
* **Guardrails (disepakati & diuji):** G1 klien — curhatan kosong → tombol
  disabled, <40 karakter / <8 kata → tolak, ≥60% non-huruf ATAU ≥70% kata
  berulang → "bukan curhatan", >4000 → tolak; G2 analisis kosong **tidak
  disimpan** ke history; G3 alt <5 karakter / placeholder (`-`, `n/a`,
  `tidak ada`, `tidak diketahui`, `tidak disebutkan`) → blokir simpan; G4
  tombol dokumen hanya bila alt valid; G5 Express revalidasi semua tulis
  (journal 40–4000, 4 field analisis wajib, `source ∈ {manual,ai}`,
  `documentPath` butuh alt) → 400.
* **MongoDB Docker:** image `mongo:7` ditarik → kontainer `briefme-mongo`
  (`:27017`, `--restart unless-stopped`). **Express `server/index.js`**
  (:8790): Mongoose schema journal/analysis{4}/alternate{text,source}/
  documentPath + timestamps; routes GET/POST/PATCH/DELETE `/api/history` +
  `/api/health`. Semua kasus uji endpoint hijau (201/200/400 per guardrail).
  `.env.example` += `MONGODB_URI`, `PORT`; `.gitignore` += `server/node_modules/`.
* **Builder refactor → 3 flow (`BOB_FLOWS`):** rantai 6 node/5 edge dipakai
  ulang via `build_chain_flow(code, ids, display)`; `make_ids(suffix)` →
  `HEARB` (F1), `HEARD` (F2), `HEARA` (F3); `build_python_code_template(instr,
  mode, disable_mcp)` menambah flag `--mode agent|ask` + `--disable-mcp`;
  `upsert_flow(spec, …)` (id_file → nama persis → CREATE; id disimpan ke
  `.flow_id`, `.flow_id.dokumen`, `.flow_id.alternatif`). Prompt lama
  `AGENT_SYSTEM_PROMPT` dipecah: **`PROMPT_ANALYSIS`** (4 kolom, alt kosong
  total tanpa `&nbsp;`, tanpa tool/JSON mentah), **`PROMPT_DOCUMENT`** (tool
  sekali → HANYA `Dokumen: <path>` / `ERROR:`), **`PROMPT_ALTERNATE`** (mode
  ask → satu kalimat ≤2 kalimat) + **`PROMPT_LEGACY_GEMINI`** untuk fallback.
* **Preflight offline (venv langflow) sebelum builder live:** `validate_prompt`
  → `['text']` ketiga kode; `validate_code_safety` lolos; jq lolos; id node 18
  unik; assert prompt tanpa kurung kurawal/`'''` + flag per flow. **Hasil
  builder: 1 UPDATED + 2 CREATED** lalu rebuild idempoten (3 UPDATED, struktur
  6 node/5 edge terverifikasi per flow).
* **Smoke test per flow (API):** F1 **8.1s** — tabel murni, sel alt kosong,
  tanpa `Dokumen:` (sebelumnya Bob menempel **JSON mentah** sebelum tabel →
  prompt F1 diperkuat "JSON hanya internal"); F3 **8.7s** — satu kalimat
  seimbang; F2 **10.1s** — tool MCP → `brief_ac0553c7.docx` baru. Sel kosong
  F1 kadang `&nbsp;` → dilarang prompt + dinormalisasi UI/e2e.
* **E2E ditulis ulang (`scripts/run_e2e.py`):** preflight += Express
  `:8790/api/health` + `db=up`; tahap F1 (5 header, sel alt **kosong**, tanpa
  baris dokumen) → F3 (≥15 karakter) → F2 (JSON 5 field, docx **baru**,
  kolom alt = teks F3) → cek fisik docx → **roundtrip history** (POST 201, GET
  list, G1/G2/G4 → 400, PATCH 200, G3 → 400, DELETE 200). Jalur gemini legacy
  dipertahankan. **Hasil: SEMUA LOLOS 34.4s.**
* **Web rework:** `vite.config.ts` — proxy `/api/history` → Express :8790
  **sebelum** catch-all `/api` → Langflow (urutan wajib; HMR restart otomatis).
  `App.tsx` — state machine alur: Analisis (G1) → tabel + panel "Pikiran
  Alternatif" (Tulis sendiri disarankan / Saran AI ops → editor + badge `AI`,
  G3) → "Buat Dokumen" (G4) → path docx; auto-save history saat analisis +
  PATCH alt/docx; panel Riwayat (buka → pulihkan jurnal+alt+doc, hapus +
  confirm); placeholder netral; tanpa "Pakai contoh"; `&nbsp;` dinormalisasi.
  `npm run build` (tsc+vite) lolos; CSS baru (badge, riwayat, next-step, note,
  dark-mode).
* **Uji browser Playwright (`/tmp/opencode/ui-test/flow.mjs`): 19/19 LOLOS** —
  placeholder netral, G1 hint+disabled, tabel 5 header, sel alt `—`, G4 belum
  ada, G3 tolak alt `ab`, saran AI masuk editor + badge, dokumen dibuat
  (`brief_e8bbe38b.docx`), riwayat auto-save + Buka pulihkan jurnal & alt.
  (2 bug di skrip test murni: locator "Sarankan AI" vs label "Saran AI (opsi)",
  dan menunggu label busy yang tidak ada di tombol AI — UI tidak diubah.)
* **Docs:** README diarsitektur ulang (diagram 3 flow + MERN, run steps mongo/
  server/e2e, payload manual per endpoint, catatan autosave/proxy/`&nbsp;`);
  Agent-Plan §1/§2/§3/§5 + tabel status diperbarui; AGENT_LOG §19 ini.
  Entri uji history dihapus (DB bersih untuk demo).
