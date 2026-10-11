import "dotenv/config";
import express from "express";
import mongoose from "mongoose";

const PORT = Number(process.env.PORT || 8790);
const MONGODB_URI =
  process.env.MONGODB_URI || "mongodb://127.0.0.1:27017/briefme";

const JOURNAL_MIN = 40;
const JOURNAL_MAX = 4000;
const ALT_MIN = 5;
const ALT_PLACEHOLDERS = ["-", "n/a", "na", "tidak ada", "tidak diketahui", "tidak disebutkan"];
const ANALYSIS_FIELDS = ["situation", "thought", "emotion", "behavior"];

const analysisSchema = new mongoose.Schema(
  {
    situation: { type: String, required: true, trim: true },
    thought: { type: String, required: true, trim: true },
    emotion: { type: String, required: true, trim: true },
    behavior: { type: String, required: true, trim: true },
  },
  { _id: false },
);

const altSchema = new mongoose.Schema(
  {
    text: { type: String, default: "", trim: true },
    source: { type: String, enum: ["", "manual", "ai"], default: "" },
  },
  { _id: false },
);

const entrySchema = new mongoose.Schema(
  {
    journal: { type: String, required: true, trim: true },
    analyses: { type: [analysisSchema], default: [] },
    alternateThoughts: { type: [altSchema], default: [] },
    documentPath: { type: String, default: "", trim: true },
  },
  { timestamps: true },
);

/** G5 — validasi aturan lintas field pada state gabungan dokumen. */
function validateEntry(doc) {
  const errors = [];
  const journal = (doc.journal || "").trim();
  if (journal.length < JOURNAL_MIN)
    errors.push(`journal minimal ${JOURNAL_MIN} karakter (belum berisi curhatan?)`);
  if (journal.length > JOURNAL_MAX)
    errors.push(`journal maksimal ${JOURNAL_MAX} karakter`);

  const analyses = doc.analyses || [];
  if (analyses.length === 0) errors.push("analyses minimal 1 baris");
  analyses.forEach((a, i) => {
    for (const key of ANALYSIS_FIELDS) {
      if (!(a?.[key] || "").trim())
        errors.push(`analyses[${i}].${key} wajib terisi`);
    }
  });

  const alts = doc.alternateThoughts || [];
  if (alts.length > analyses.length)
    errors.push("alternateThoughts tidak boleh lebih banyak dari analyses");
  alts.forEach((alt, i) => {
    const t = (alt?.text || "").trim();
    const s = alt?.source || "";
    if (t.length > 0 && t.length < ALT_MIN)
      errors.push(`alternateThoughts[${i}] minimal ${ALT_MIN} karakter`);
    if (t.toLowerCase() in Object.fromEntries(ALT_PLACEHOLDERS.map((p) => [p, 1])))
      errors.push(`alternateThoughts[${i}] berupa placeholder`);
    if (t && !["manual", "ai"].includes(s))
      errors.push(`alternateThoughts[${i}].source harus 'manual' atau 'ai'`);
    if (!t && s) alt.source = "";
  });

  // G4 — dokumen hanya setelah SEMUA alt terisi
  if ((doc.documentPath || "").trim()) {
    const allFilled = analyses.length > 0 && analyses.every((_, i) => {
      const t = (alts[i]?.text || "").trim();
      return t.length >= ALT_MIN && !(t.toLowerCase() in Object.fromEntries(ALT_PLACEHOLDERS.map((p) => [p, 1])));
    });
    if (!allFilled)
      errors.push("documentPath hanya boleh diisi setelah semua alternateThoughts terisi");
  }

  return errors;
}

const Entry = mongoose.model("Entry", entrySchema);
const app = express();
app.use(express.json({ limit: "256kb" }));

function fail(res, errors, status = 400) {
  res.status(status).json({ error: errors.join("; ") });
}

app.get("/api/health", (_req, res) => {
  res.json({ ok: true, db: mongoose.connection.readyState === 1 ? "up" : "down" });
});

app.get("/api/history", async (_req, res, next) => {
  try {
    const items = await Entry.find().sort({ createdAt: -1 }).limit(200);
    res.json({ items });
  } catch (err) {
    next(err);
  }
});

app.post("/api/history", async (req, res, next) => {
  try {
    const b = req.body || {};
    const doc = new Entry({
      journal: String(b.journal ?? ""),
      analyses: (Array.isArray(b.analyses) ? b.analyses : []).map((a) => ({
        situation: String(a?.situation ?? ""),
        thought: String(a?.thought ?? ""),
        emotion: String(a?.emotion ?? ""),
        behavior: String(a?.behavior ?? ""),
      })),
      alternateThoughts: (Array.isArray(b.alternateThoughts) ? b.alternateThoughts : []).map((a) => ({
        text: String(a?.text ?? ""),
        source: String(a?.source ?? ""),
      })),
      documentPath: String(b.documentPath ?? ""),
    });
    const errors = validateEntry(doc);
    if (errors.length) return fail(res, errors);
    await doc.save();
    res.status(201).json(doc);
  } catch (err) {
    next(err);
  }
});

const PATCHABLE = ["journal", "analyses", "alternateThoughts", "documentPath"];

app.patch("/api/history/:id", async (req, res, next) => {
  try {
    if (!mongoose.isValidObjectId(req.params.id))
      return fail(res, ["id tidak valid"], 404);
    const doc = await Entry.findById(req.params.id);
    if (!doc) return fail(res, ["history tidak ditemukan"], 404);
    const b = req.body || {};
    for (const key of PATCHABLE) {
      if (b[key] !== undefined) doc[key] = b[key];
    }
    const errors = validateEntry(doc);
    if (errors.length) return fail(res, errors);
    await doc.save();
    res.json(doc);
  } catch (err) {
    next(err);
  }
});

app.delete("/api/history/:id", async (req, res, next) => {
  try {
    if (!mongoose.isValidObjectId(req.params.id))
      return fail(res, ["id tidak valid"], 404);
    const doc = await Entry.findByIdAndDelete(req.params.id);
    if (!doc) return fail(res, ["history tidak ditemukan"], 404);
    res.json({ ok: true });
  } catch (err) {
    next(err);
  }
});

app.use((err, _req, res, _next) => {
  console.error(err);
  res.status(500).json({ error: err.message || "server error" });
});

mongoose
  .connect(MONGODB_URI)
  .then(() => {
    app.listen(PORT, "127.0.0.1", () => {
      console.log(`BriefME history API → http://127.0.0.1:${PORT}/api/history`);
      console.log(`mongo=${MONGODB_URI}`);
    });
  })
  .catch((err) => {
    console.error("MongoDB tidak bisa dihubungi:", err.message);
    process.exit(1);
  });
