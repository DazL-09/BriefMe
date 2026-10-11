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

const entrySchema = new mongoose.Schema(
  {
    journal: { type: String, required: true, trim: true },
    analysis: {
      situation: { type: String, required: true, trim: true },
      thought: { type: String, required: true, trim: true },
      emotion: { type: String, required: true, trim: true },
      behavior: { type: String, required: true, trim: true },
    },
    alternateThought: {
      text: { type: String, default: "", trim: true },
      source: { type: String, enum: ["", "manual", "ai"], default: "" },
    },
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

  for (const key of ANALYSIS_FIELDS) {
    if (!(doc.analysis?.[key] || "").trim())
      errors.push(`analysis.${key} wajib terisi`);
  }

  const alt = (doc.alternateThought?.text || "").trim();
  const source = doc.alternateThought?.source || "";
  if (alt.length > 0 && alt.length < ALT_MIN)
    errors.push(`alternateThought minimal ${ALT_MIN} karakter`);
  if (alt.toLowerCase() in Object.fromEntries(ALT_PLACEHOLDERS.map((p) => [p, 1])))
    errors.push("alternateThought berupa placeholder");
  if (alt && !["manual", "ai"].includes(source))
    errors.push("alternateThought.source harus 'manual' atau 'ai'");
  if (!alt && source) doc.alternateThought.source = "";

  // G4 — dokumen hanya setelah alt terisi
  if ((doc.documentPath || "").trim() && !alt)
    errors.push("documentPath hanya boleh diisi setelah alternateThought terisi");

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
      analysis: {
        situation: String(b.analysis?.situation ?? ""),
        thought: String(b.analysis?.thought ?? ""),
        emotion: String(b.analysis?.emotion ?? ""),
        behavior: String(b.analysis?.behavior ?? ""),
      },
      alternateThought: {
        text: String(b.alternateThought?.text ?? ""),
        source: String(b.alternateThought?.source ?? ""),
      },
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

const PATCHABLE = ["journal", "analysis", "alternateThought", "documentPath"];

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
