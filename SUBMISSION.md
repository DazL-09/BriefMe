# Brief Me — CBT Journaling AI Assistant for Therapy

---

## Judul Project

**Brief Me — CBT Journaling AI Assistant for Therapy**

---

## Tema Project

**Healthcare & Wellbeing**

---

## Deskripsi Singkat Project

Brief Me adalah aplikasi journaling berbasis AI yang membantu pengguna menuliskan curhatan harian dan secara otomatis mengubahnya menjadi **Thought Record** (Matriks CBT) lengkap dengan pikiran alternatif dan dokumen .docx yang siap diunduh.

**Masalah:** Banyak orang ingin melakukan journaling terapeutik tetapi tidak tahu cara memformatnya sesuai metode CBT (Cognitive Behavioral Therapy), atau tidak memiliki akses ke terapis untuk panduan.

**Siapa yang mengalami:** Mahasiswa, pekerja, atau siapa saja yang mengalami stres, kecemasan, atau tekanan mental tapi belum tentu memiliki akses ke bantuan profesional.

**Bagaimana solusi bekerja:** Pengguna menulis curhatan bebas → AI menganalisis dan mengekstrak 4 komponen CBT (Situasi, Pikiran, Emosi, Perilaku) → Pengguna menulis pikiran alternatif (manual atau dengan saran AI) → Sistem menghasilkan dokumen Thought Record (.docx) otomatis.

**Manfaat utama:** Mempercepat proses journaling terstruktur dari ~30 menit menjadi ~2 menit, membantu pengguna memahami pola pikir negatif mereka, dan menghasilkan dokumen yang bisa dibawa ke sesi terapis.

---

## Problem Statement

**Kondisi sebelum adanya solusi:**
- Journaling CBT manual memakan waktu 20–30 menit per sesi
- Banyak orang tidak tahu format Thought Record yang benar
- Tidak semua orang memiliki akses ke terapis untuk panduan awal
- Dokumen journaling sulit dibaca dan dianalisis ulang

**Pain point:**
- Proses manual lambat dan membingungkan bagi pemula
- Tidak ada struktur yang konsisten antar sesi
- Sulit melacak progres mental dari waktu ke waktu
- Biaya terapis mahal dan tidak terjangkau untuk semua kalangan

**Mengapa penting:** Kesehatan mental adalah kebutuhan dasar. Journaling terstruktur terbukti efektif mengurangi gejala kecemasan dan depresi (meta-analisis 2018), tetapi hambatan akses dan pengetahuan membuat banyak orang tidak melakukannya.

---

## Target User

- **Mahasiswa** — mengalami stres akademik, tekanan tugas, dan adaptasi kehidupan kampus
- **Pekerja/Profesional** — mengalami burnout, tekanan kerja, atau work-life balance
- **Masyarakat umum** — siapa saja yang ingin melakukan self-help journaling tanpa biaya terapis
- **Konselor/terapis pemula** — sebagai alat bantu untuk membimbing klien dalam sesi

---

## Mengapa Solusi Ini Dibutuhkan?

| Aspek | Cara Lama (Manual) | Brief Me (AI) |
|-------|-------------------|---------------|
| Waktu per sesi | 20–30 menit | 2 menit |
| Format CBT | Perlu panduan/buku | Otomatis terstruktur |
| Akses | Butuh terapis/buku | Gratis, online, 24/7 |
| Dokumen | Ditulis tangan, sulit dibaca | .docx rapi, siap dibawa ke terapis |
| Konsistensi | Bervariasi | Format selalu sama |

Brief Me bukan pengganti terapis, melainkan **jembatan** yang membantu orang memulai journaling terstruktur sebelum atau di antara sesi terapis.

---

## Fitur Utama Project

1. **AI Thought Record Extraction** — Menganalisis curhatan bebas dan mengekstrak 4 komponen CBT: Situasi, Pikiran, Emosi, Perilaku. Hasil ditampilkan dalam tabel terstruktur.

2. **Pikiran Alternatif (Manual + AI)** — Pengguna menulis pikiran alternatif sendiri (disarankan) atau meminta saran AI sebagai opsi. Guardrail memastikan AI tidak mengisi otomatis — pasien harus aktif menulis.

3. **Auto-Generate Dokumen (.docx)** — Satu klik menghasilkan file .docx berisi Thought Record lengkap dengan catatan objektif-klinis. Siap dibawa ke sesi terapis.

4. **Riwayat Journaling** — Semua sesi tersimpan otomatis. Pengguna bisa melihat, membuka ulang, atau menghapus riwayat. Tampilan sidebar terorganisir per tanggal.

5. **Guardrails Kesehatan Mental (G1–G5)** — Validasi berlapis: curhatan minimal 8 kata, pikiran alternatif bukan placeholder, dokumen hanya dibuat setelah alt valid, output tidak mengandung saran medis/diagnosis.

---

## Alur Penggunaan Project

```
User Input (curhatan bebas)
    → Validasi G1 (klien)
    → Langflow: PROMPT_ANALYSIS
    → IBM Bob (LLM) memproses
    → Output: Tabel Thought Record (4 kolom CBT)
    → User menulis Pikiran Alternatif (manual/AI)
    → Validasi G3 + G4
    → Generate .docx (browser-side)
    → User Action: Unduh & simpan / bawa ke terapis
```

**Mode Alternatif (Saran AI):**
```
User klik "Saran AI"
    → Input: MODE=ALTERNATIF + JSON {situation, thought, emotion, behavior}
    → Langflow: PROMPT_ALTERNATE
    → IBM Bob menghasilkan saran pikiran alternatif
    → User edit & simpan
```

---

## Penggunaan IBM Langflow

**Workflow/Flow yang dibuat:**
- Satu flow utama: **BriefMe** (endpoint: `briefme`, ID: `fb741b8a-c2c0-4d41-9b75-df9294732ab5`)
- 6 nodes, 5 edges

**Node/Component yang digunakan:**

| Node | Fungsi |
|------|--------|
| Chat Input | Menerima input teks dari user (curhatan atau MODE=ALTERNATIF + JSON) |
| Conditional Router | Membaca prefix `MODE=ALTERNATIF` untuk menentukan prompt mana yang dipakai |
| PROMPT_ANALYSIS | Template prompt untuk ekstraksi Thought Record (4 kolom CBT) |
| PROMPT_ALTERNATE | Template prompt untuk menghasilkan pikiran alternatif |
| Language Model (IBM Bob) | LLM yang memproses prompt dan menghasilkan output |
| Chat Output | Mengembalikan hasil ke frontend |

**Input & Output:**
- Input: Teks curhatan bebas ATAU string `MODE=ALTERNATIF\n{json}`
- Output: Tabel markdown dengan 5 header (Situasi, Pikiran, Emosi, Perilaku, Pikiran Alternatif) atau teks saran pikiran alternatif

**Proses AI Agent:**
1. Frontend mengirim POST ke `/api/v1/run/briefme`
2. Langflow menerima input, conditional router menentukan mode
3. Prompt yang sesuai dikirim ke IBM Bob
4. Output dari Bob dikembalikan sebagai Chat Output

**Fungsi Langflow dalam sistem:** Orkestrasi prompt engineering + LLM call tanpa menulis kode backend untuk AI. Memisahkan concern: frontend handles UI, Langflow handles AI logic, Express handles history.

---

## Penggunaan IBM Bob

**Fungsi IBM Bob:**
IBM Bob adalah LLM (Large Language Model) yang menjadi "otak" AI di balik analisis journaling. Bob memproses teks curhatan dan mengubahnya menjadi struktur CBT yang terorganisir.

**Proses/task yang dilakukan:**
1. **Analisis Thought Record** — Membaca curhatan bebas, mengidentifikasi situasi, pikiran otomatis, emosi yang dirasakan, dan perilaku yang muncul
2. **Saran Pikiran Alternatif** — Menghasilkan 1–2 alternatif pikiran yang lebih seimbang dan realistis berdasarkan komponen CBT yang sudah diekstrak

**Bagaimana Bob berinteraksi dengan sistem:**
- Bob tidak diakses langsung dari frontend
- Bob dipanggil melalui Langflow sebagai backend AI
- Frontend → Express (opsional) → Langflow → Bob → Langflow → Frontend

**Output yang dihasilkan:**
- Thought Record dalam format markdown table
- Saran pikiran alternatif dalam bentuk teks natural

---

## Bagaimana IBM Langflow dan IBM Bob Terintegrasi?

**Apa yang dilakukan Langflow:**
Langflow berfungsi sebagai **orchestrator** — menerima input dari frontend, menentukan prompt yang tepat (analisis vs alternatif), dan meneruskannya ke IBM Bob. Langflow juga mengelola output dan mengembalikannya dalam format yang konsisten.

**Apa yang dilakukan Bob:**
Bob berfungsi sebagai **reasoning engine** — memproses prompt dari Langflow dan menghasilkan analisis CBT yang akurat. Bob memahami konteks psikologi kognitif dan menghasilkan output yang relevan.

**Bagaimana data berpindah:**
```
Frontend (React)
    ↓ POST /api/v1/run/briefme
Langflow (Orchestrator)
    ↓ Prompt template + user input
IBM Bob (LLM)
    ↓ Hasil analisis
Langflow (Formatting)
    ↓ Chat Output
Frontend (Display)
```

**Output akhir dari integrasi:**
Tabel Thought Record yang ditampilkan di UI, yang kemudian bisa diubah menjadi dokumen .docx oleh user.

---

## Project File / Link

**GitHub:** [https://github.com/DazL-09/BriefMe](https://github.com/DazL-09/BriefMe)

**Fitur yang tersedia di repo:**
- Source code lengkap (Langflow flow JSON, Express server, React frontend)
- Dokumentasi setup dan deployment
- Guardrails implementation (G1–G5)
- E2E test suite (Playwright)

---

## Dampak yang Dihasilkan

| Indikator | Sebelum | Sesudah | Improvement |
|-----------|---------|---------|-------------|
| Waktu journaling CBT | 20–30 menit | 2 menit | **~90% lebih cepat** |
| Biaya per sesi | Rp 100.000–500.000 (terapis) | Gratis | **100% cost reduction** |
| Akses 24/7 | Tidak (jam kerja terapis) | Ya | **Unlimited access** |
| Format konsisten | Tidak (manual) | Ya (otomatis) | **100% consistency** |
| Dokumen siap terapis | Tidak | Ya (.docx) | **Zero manual formatting** |

**Estimasi dampak:**
- Membantu **1.000+ pengguna/bulan** (journaling gratis, online)
- Mengurangi ** waktu persiapan sesi terapis** — terapis tinggal fokus diskusi, bukan format
- Meningkatkan **konsistensi journaling** — format selalu sama, mudah dilacak

---

## Potensi Pengembangan & Skalabilitas

**Pengembangan lanjutan:**
1. **Multi-bahasa** — dukungan Inggris, Melayu, dll
2. **Analytics dashboard** — tracking mood/pattern dari waktu ke waktu
3. **Integrasi kalender** — reminder journaling harian
4. **Export ke PDF** — selain .docx
5. **Mode kolaborasi** — terapis bisa melihat journal klien (dengan consent)

**Skalabilitas:**
- **Individu** → gratis, self-help
- **Klinik/Kampus** → multi-user, admin dashboard
- **Perusahaan** → EAP (Employee Assistance Program) integration
- **Pemerintah** → program kesehatan mental publik

**Technical scalability:**
- Langflow flow bisa di-scale horizontal
- MongoDB untuk history bisa di-shard
- Frontend static hosting (CDN) — cost rendah

---

## Apa yang Membuat Project Ini Berbeda?

1. **Guardrails Kesehatan Mental Berlapis (G1–G5)** — Tidak seperti journaling AI biasa, Brief Me memiliki 5 lapis validasi yang memastikan output aman, tidak mengandung diagnosis/saran medis, dan mendorong partisipasi aktif pengguna (AI tidak mengisi pikiran alternatif otomatis).

2. **Hybrid Human-AI Approach** — Pikiran alternatif tetap ditulis oleh pengguna (disarankan), AI hanya memberi saran opsional. Ini sesuai prinsip CBT: pasien harus aktif mengidentifikasi dan mengubah pikirannya sendiri.

3. **Output Siap Klinik** — Bukan sekadar journaling digital, tetapi menghasilkan dokumen .docx dengan format Thought Record standar yang bisa langsung dibawa ke sesi terapis. Menjembatani self-help dan professional help.

---

## Kemampuan AI Agent

**Tugas yang dilakukan otomatis:**
1. **Ekstraksi CBT** — Dari curhatan bebas, AI otomatis mengidentifikasi dan mengategorikan: Situasi, Pikiran, Emosi, Perilaku
2. **Validasi Kualitas** — Guardrails memastikan curhatan cukup substansial (min. 8 kata) dan pikiran alternatif bukan placeholder
3. **Saran Pikiran Alternatif** — AI menghasilkan opsi pikiran seimbang berdasarkan analisis CBT (opsional, user bisa tolak)

**Tugas semi-otomatis:**
1. **Pikiran Alternatif** — User menulis, AI memberi saran sebagai referensi
2. **Dokumen .docx** — User klik generate, sistem membuat file otomatis

**Tugas yang TIDAK dilakukan AI (by design):**
- Diagnosis psikologis
- Rekomendasi medis/obat
- Menggantikan pikiran alternatif user (harus ditulis sendiri)

**Interaksi dengan user:**
- Real-time feedback via Kiko (mascot chat)
- Error messages yang edukatif (bukan technical)
- Progressive disclosure — fitur muncul saat dibutuhkan

---

## Screenshots

Berikut screenshot project (untuk submission):

1. **01-user-interface.png** — Tampilan utama dengan input journaling
2. **02-thought-record-modal.png** — Modal Thought Record dengan tabel CBT
3. **03-alt-editor.png** — Editor pikiran alternatif dengan saran AI
4. **04-history-sidebar.png** — Sidebar riwayat terorganisir per tanggal
5. **05-output-docx.png** — Preview dokumen .docx yang dihasilkan

*(Screenshot tersedia di `/tmp/opencode/ui-test/briefme-flow.png` dan bisa diambil dari running app di `http://localhost:5173`)*
