# BriefME — CBT Journaling AI Assistant for Therapy

> **Healthcare & Wellbeing** | Powered by **IBM Bob** & **Langflow** with **MERN Stack**

BriefME adalah asisten journaling berbasis Cognitive Behavioral Therapy (CBT) yang membantu pengguna mengubah curhatan bebas menjadi **Thought Record (Matriks CBT)** terstruktur, melatih restrukturisasi kognitif lewat pikiran alternatif, dan menghasilkan dokumen `.docx` rapi yang siap dibawa ke sesi konseling atau terapi.

---

## 🌟 Fitur Utama

- **AI Thought Record Extraction**: Menganalisis curhatan harian secara objektif dan mengekstrak 4 komponen inti CBT: *Situasi*, *Pikiran Otomatis*, *Emosi*, dan *Perilaku*.
- **Pikiran Alternatif Terpandu (Human-AI Hybrid)**: Kolom pikiran alternatif sengaja dikosongkan oleh AI untuk melatih restrukturisasi kognitif mandiri pasien. Pengguna dapat menulis sendiri (disarankan) atau meminta opsi saran AI.
- **Auto-Generate Dokumen `.docx`**: Membuat dokumen Thought Record lengkap dengan catatan klinis langsung di browser (client-side) dalam 1 klik — cepat, aman, dan tanpa menyimpan data dokumen di server.
- **Riwayat Journaling & Bulk Export**: Semua sesi tersimpan otomatis di database MongoDB. Sidebar riwayat memungkinkan pengguna membuka kembali sesi sebelumnya atau mengekspor beberapa entri sekaligus ke satu dokumen `.docx`.
- **Guardrails Klinis Berlapis (G1–G5)**:
  - **G1**: Validasi panjang curhatan minimal (mencegah teks non-curhatan).
  - **G2**: Mencegah penyimpanan riwayat jika ekstraksi gagal.
  - **G3**: Validasi pikiran alternatif minimal 5 karakter (bukan placeholder).
  - **G4**: Tombol buat dokumen hanya aktif setelah pikiran alternatif terisi.
  - **G5**: Validasi backend Express di setiap operasi simpan/update.

---

## 🏗️ Arsitektur Sistem

```
┌────────────────────────────────────────────────────────┐
│               Frontend (React + Vite :5173)            │
│  - Input Curhatan & Kiko Mascot                        │
│  - Modal Thought Record & Editor Pikiran Alternatif    │
│  - Export Dokumen .docx (Browser-side) & Sidebar History│
└───────────────┬────────────────────────┬───────────────┘
                │                        │
       POST /api/v1/run/briefme          │ /api/history (CRUD)
                ▼                        ▼
┌───────────────────────────────┐ ┌──────────────────────┐
│       Langflow (:7860)        │ │    Express (:8790)   │
│   Flow: "BriefME" (6 nodes)   │ │    server/index.js   │
│   (Orkestrasi Multi-Mode)     │ └──────────┬───────────┘
└───────────────┬───────────────┘            ▼
                ▼                 ┌──────────────────────┐
┌───────────────────────────────┐ │   MongoDB (:27017)   │
│         IBM Bob CLI           │ │   Koleksi: entries   │
│   (Clinical Reasoning Engine) │ └──────────────────────┘
└───────────────────────────────┘
```

---

## 📋 Persyaratan Sistem

- **Node.js** (v20 ke atas) & **npm**
- **Docker** (untuk menjalankan MongoDB)
- **Langflow** (versi terbaru)
- **IBM Bob CLI** terpasang global: `npm install -g @ibm/bob`
- Kunci API resmi IBM Bob (`BOB_API_KEY`)

---

## 🚀 Panduan Menjalankan (Quick Start)

### 1. Konfigurasi Environment

Salin file contoh konfigurasi dan lengkapi kunci API Anda:

```bash
cp .env.example .env
```

Pastikan `.env` memuat:
```env
BOB_API_KEY=kunci_api_resmi_bob_anda
LANGFLOW_URL=http://localhost:7860
LANGFLOW_API_KEY=kunci_api_langflow_anda
HISTORY_URL=http://localhost:8790
```

### 2. Jalankan Database MongoDB

Jalankan container MongoDB via Docker:

```bash
docker run -d --name briefme-mongo -p 27017:27017 --restart unless-stopped mongo:7
```

### 3. Jalankan History API Server

Masuk ke folder `server`, install dependensi, dan jalankan server Express:

```bash
cd server
npm install
node index.js
```
*Server aktif di: `http://localhost:8790`*

### 4. Setup Langflow & Import Flow

1. Jalankan Langflow di terminal:
   ```bash
   langflow run
   ```
2. Buka antarmuka Langflow di browser (`http://localhost:7860`).
3. Import file [`briefme-flow.json`](./briefme-flow.json).
4. Pastikan endpoint flow diset ke **`briefme`**.

*(Flow sudah kompatibel untuk dijalankan di Linux maupun Windows).*

### 5. Jalankan Web UI Frontend

Di terminal baru, masuk ke folder `web`, install dependensi, dan jalankan Vite dev server:

```bash
cd web
npm install
npm run dev
```

Buka aplikasi di browser: **`http://localhost:5173`** 🎉

---

## 🧪 Verifikasi & Pengujian

### Build Web Frontend
Memastikan TypeScript dan bundling Vite bebas dari error:
```bash
cd web && npm run build
```

### Pengujian E2E Browser (Playwright)
Menguji alur end-to-end lengkap (validasi input G1, ekstraksi CBT F1, saran AI F3, validasi G3/G4, download `.docx`, dan riwayat autosave):
```bash
node /tmp/opencode/ui-test/flow.mjs
```
*Status: 32 dari 32 pemeriksaan lolos (32 PASS, 0 FAIL).*

---

## 📁 Struktur Direktori

```text
├── briefme-flow.json   # Konfigurasi flow Langflow resmi (6 node multi-mode)
├── server/             # History API backend (Express + Mongoose)
│   ├── index.js        # Server Express & implementasi guardrails G1-G5
│   └── package.json
├── web/                # Frontend aplikasi (React 19, Vite, TypeScript)
│   ├── src/            # Komponen UI, integrasi Langflow, pembuat .docx
│   └── package.json
├── SUBMISSION.md       # Dokumen submission lengkap hackathon
├── .env.example        # Template konfigurasi environment
└── README.md           # Dokumentasi utama proyek
```

---

## 📄 Lisensi

Dikembangkan untuk Hackathon Healthcare & Wellbeing 2026.
