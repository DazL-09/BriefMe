import { useCallback, useEffect, useState } from 'react'
import { AnimatePresence, motion, MotionConfig } from 'framer-motion'
import {
  Document as DocxDocument,
  HeadingLevel,
  Packer,
  Paragraph,
  Table,
  TableCell,
  TableRow,
  TextRun,
  WidthType,
} from 'docx'
import { saveAs } from 'file-saver'

type FlowItem = {
  component_display_name?: string
  component_id?: string
  results?: { message?: { text?: string } }
  artifacts?: { message?: string }
  text?: string
}

type FlowResponse = {
  outputs?: { outputs?: FlowItem[] }[]
}

type ThoughtRecord = {
  headers: string[]
  rows: string[][]
  raw: string
}

type Analysis = {
  situation: string
  thought: string
  emotion: string
  behavior: string
}

type AltSource = '' | 'manual' | 'ai'

type HistoryItem = {
  _id: string
  journal: string
  analysis: Analysis
  alternateThought: { text: string; source: AltSource }
  documentPath: string
  createdAt: string
}

type Busy = null | 'analyze' | 'ai' | 'doc'

const TABLE_HEADERS = ['Situasi', 'Pikiran', 'Emosi', 'Perilaku', 'Pikiran Alternatif']
const ALT_PLACEHOLDERS = [
  '-',
  'n/a',
  'na',
  'tidak ada',
  'tidak diketahui',
  'tidak disebutkan',
]

function extractText(item: FlowItem | undefined): string {
  if (!item) return ''
  const candidates = [item.results?.message?.text, item.artifacts?.message, item.text]
  for (const c of candidates) {
    if (c) return c
  }
  return ''
}

/** Normalisasi sel tabel: &nbsp; (HTML space) & tanda kosong lain dianggap kosong. */
function cleanCell(cell: string): string {
  return cell.replace(/&nbsp;/g, '').replace(/\u00a0/g, '').trim()
}

function parseThoughtRecord(md: string): ThoughtRecord {
  const pipeLines = md
    .split('\n')
    .map((line) => line.trim())
    .filter((line) => line.startsWith('|') && line.endsWith('|'))
  const toCells = (line: string): string[] =>
    line
      .slice(1, -1)
      .split('|')
      .map((cell) => cleanCell(cell))
  const headers: string[] = []
  const rows: string[][] = []
  for (const [i, line] of pipeLines.entries()) {
    if (i === 0) {
      headers.push(...toCells(line))
    } else if (/^[\s|:-]+$/.test(line)) {
      continue // baris pemisah |---|---|
    } else {
      rows.push(toCells(line))
    }
  }
  return { headers, rows, raw: md }
}

/** G1 (klien) — validasi curhatan sebelum memanggil model. */
function validateJournal(value: string): string | null {
  const t = value.trim()
  if (t.length > 4000) return 'Curhatan terlalu panjang (maks. 4000 karakter).'
  const words = t.split(/\s+/).filter(Boolean)
  if (t.length < 40 || words.length < 8) {
    return 'Curhatan terlalu pendek untuk dianalisis (min. 8 kata).'
  }
  const compact = t.replace(/\s+/g, '')
  const letters = compact.replace(/[^\p{L}]/gu, '')
  if (compact.length > 0 && letters.length / compact.length <= 0.4) {
    return 'Ini sepertinya bukan curhatan — coba tulis dengan kata-kata Anda sendiri.'
  }
  const counts = new Map<string, number>()
  for (const w of words) {
    const key = w.toLowerCase().replace(/[^\p{L}]/gu, '')
    if (key) counts.set(key, (counts.get(key) ?? 0) + 1)
  }
  const maxCount = Math.max(0, ...counts.values())
  if (maxCount / words.length >= 0.7) {
    return 'Ini sepertinya bukan curhatan — terlalu banyak kata berulang.'
  }
  return null
}

/** G3 (klien) — pikiran alternatif wajib nyata, bukan placeholder. */
function validateAlt(value: string): string | null {
  const t = value.trim()
  if (t.length < 5) return 'Pikiran alternatif terlalu pendek (min. 5 karakter).'
  if (ALT_PLACEHOLDERS.includes(t.toLowerCase())) {
    return 'Itu masih placeholder — tulis pikiran alternatif yang sebenarnya.'
  }
  return null
}

function analysisFromRows(rows: string[][]): Analysis | null {
  const row = rows.find((r) => r.length >= 4)
  if (!row) return null
  const analysis: Analysis = {
    situation: row[0].trim(),
    thought: row[1].trim(),
    emotion: row[2].trim(),
    behavior: row[3].trim(),
  }
  const values = Object.values(analysis)
  if (values.some((v) => !v || v === '-')) return null
  return analysis
}

async function runFlow(
  endpoint: string,
  inputValue: string,
): Promise<Record<string, FlowItem>> {
  const res = await fetch(`/api/v1/run/${endpoint}`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      input_value: inputValue,
      output_type: 'chat',
      data_type: 'text',
    }),
  })
  if (!res.ok) {
    const body = await res.text()
    throw new Error(`HTTP ${res.status}: ${body.slice(0, 300)}`)
  }
  const data = (await res.json()) as FlowResponse
  const items: Record<string, FlowItem> = {}
  for (const out of data.outputs ?? []) {
    for (const item of out.outputs ?? []) {
      const name = item.component_display_name ?? item.component_id ?? ''
      items[name] = item
    }
  }
  return items
}

async function historyRequest(
  method: string,
  path: string,
  body?: unknown,
): Promise<{ ok: boolean; status: number; data: unknown }> {
  try {
    const res = await fetch(`/api/history${path}`, {
      method,
      headers: { 'Content-Type': 'application/json' },
      body: body === undefined ? undefined : JSON.stringify(body),
    })
    const data = await res.json().catch(() => ({}))
    return { ok: res.ok, status: res.status, data }
  } catch (e) {
    return { ok: false, status: 0, data: { error: e instanceof Error ? e.message : String(e) } }
  }
}

function historyError(data: unknown): string {
  if (data && typeof data === 'object' && 'error' in data) {
    return String((data as { error: unknown }).error)
  }
  return 'server history tidak bisa dihubungi'
}

function formatDate(iso: string): string {
  try {
    return new Date(iso).toLocaleString('id-ID', {
      day: '2-digit',
      month: 'short',
      hour: '2-digit',
      minute: '2-digit',
    })
  } catch {
    return iso
  }
}

export default function App() {
  const [text, setText] = useState('')
  const [busy, setBusy] = useState<Busy>(null)
  const [error, setError] = useState<string | null>(null)
  const [note, setNote] = useState<string | null>(null)
  const [record, setRecord] = useState<ThoughtRecord | null>(null)
  const [analysis, setAnalysis] = useState<Analysis | null>(null)
  const [alt, setAlt] = useState<{ text: string; source: AltSource }>({
    text: '',
    source: '',
  })
  const [altMode, setAltMode] = useState<null | 'manual' | 'ai'>(null)
  const [altDraft, setAltDraft] = useState('')
  const [docPath, setDocPath] = useState('')
  const [savedId, setSavedId] = useState<string | null>(null)
  const [history, setHistory] = useState<HistoryItem[]>([])
  const [historyOpen, setHistoryOpen] = useState(false)

  const refreshHistory = useCallback(async () => {
    const res = await historyRequest('GET', '')
    if (res.ok) {
      const items = (res.data as { items?: HistoryItem[] }).items ?? []
      setHistory(items)
    }
  }, [])

  useEffect(() => {
    void refreshHistory()
  }, [refreshHistory])

  const altValid = validateAlt(alt.text) === null
  const journalError = text.trim() ? validateJournal(text) : null
  const canAnalyze = Boolean(text.trim()) && busy === null && journalError === null

  async function run() {
    if (busy) return
    const validation = validateJournal(text)
    if (validation) {
      setError(validation)
      return
    }
    setBusy('analyze')
    setError(null)
    setNote(null)
    setRecord(null)
    setAnalysis(null)
    setAlt({ text: '', source: '' })
    setAltMode(null)
    setAltDraft('')
    setDocPath('')
    setSavedId(null)
    try {
      const items = await runFlow('briefme', text)
      const recordText = extractText(items['Thought Record'])
      if (!recordText.trim()) {
        setError('Output "Thought Record" kosong atau tidak dikenal.')
        return
      }
      const parsed = parseThoughtRecord(recordText)
      const parsedAnalysis = analysisFromRows(parsed.rows)
      if (!parsedAnalysis) {
        // G2 — analisis kosong/gagal: tampilkan ke user, JANGAN simpan riwayat.
        setError('Analisis tidak menghasilkan tabel CBT yang valid. Coba tulis ulang curhatannya.')
        return
      }
      setRecord(parsed)
      setAnalysis(parsedAnalysis)
      // Simpan ke riwayat (journal + analisis; alt & dokumen menyusul).
      const res = await historyRequest('POST', '', {
        journal: text.trim(),
        analysis: parsedAnalysis,
        alternateThought: { text: '', source: '' },
        documentPath: '',
      })
      if (res.ok) {
        setSavedId((res.data as { _id?: string })._id ?? null)
        void refreshHistory()
      } else {
        setNote(`Riwayat tidak tersimpan: ${historyError(res.data)}`)
      }
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setBusy(null)
    }
  }

  async function suggestAI() {
    if (busy || !analysis) return
    setBusy('ai')
    setError(null)
    setNote(null)
    try {
      // Satu endpoint briefme — mode dibaca dari awalan input oleh kode node.
      const items = await runFlow(
        'briefme',
        'MODE=ALTERNATIF\n' +
          JSON.stringify({
            situation: analysis.situation,
            thought: analysis.thought,
            emotion: analysis.emotion,
            behavior: analysis.behavior,
          }),
      )
      const suggestion = extractText(items['Thought Record']).trim()
      if (!suggestion || suggestion.startsWith('BOB_GAGAL')) {
        setError('Saran AI kosong/gagal — silakan tulis sendiri.')
        setAltMode('manual')
        return
      }
      setAltMode('ai')
      setAltDraft(suggestion)
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setBusy(null)
    }
  }

  async function saveAlt() {
    const validation = validateAlt(altDraft)
    if (validation) {
      setError(validation)
      return
    }
    const source: AltSource = altMode === 'ai' ? 'ai' : 'manual'
    const next = { text: altDraft.trim(), source }
    setAlt(next)
    setAltMode(null)
    setError(null)
    if (savedId) {
      const res = await historyRequest('PATCH', `/${savedId}`, {
        alternateThought: next,
      })
      if (!res.ok) setNote(`Riwayat gagal diperbarui: ${historyError(res.data)}`)
      void refreshHistory()
    }
  }

  function makeDocument() {
    // G4 — dokumen hanya setelah alt valid; dibuat lokal di browser (library
    // docx + file-saver), TANPA AI/tool call dan tanpa menyimpan path di server.
    if (busy || !analysis || !altValid) return
    setBusy('doc')
    setError(null)
    setNote(null)
    try {
      const headers = TABLE_HEADERS
      const row = [
        analysis.situation,
        analysis.thought,
        analysis.emotion,
        analysis.behavior,
        alt.text,
      ]
      const cell = (text: string) =>
        new TableCell({
          children: [
            new Paragraph({ children: [new TextRun({ text, size: 22 })] }),
          ],
        })
      const table = new Table({
        width: { size: 100, type: WidthType.PERCENTAGE },
        rows: [
          new TableRow({
            children: headers.map((h) =>
              new TableCell({
                children: [
                  new Paragraph({
                    children: [new TextRun({ text: h, bold: true, size: 22 })],
                  }),
                ],
              }),
            ),
          }),
          new TableRow({ children: row.map((c) => cell(c || '—')) }),
        ],
      })
      const doc = new DocxDocument({
        sections: [
          {
            children: [
              new Paragraph({
                heading: HeadingLevel.HEADING_1,
                children: [new TextRun('Thought Record — Matriks CBT')],
              }),
              new Paragraph({
                children: [
                  new TextRun({
                    text: 'Dokumen dibuat otomatis oleh HearMeOut (web)',
                    italics: true,
                    color: '666666',
                  }),
                ],
              }),
              table,
              new Paragraph({
                children: [
                  new TextRun({
                    text: 'Catatan: dokumen ini bersifat objektif-klinis, berisi ekstraksi data saja tanpa saran medis. Teks bersumber dari jurnal pasien (tanpa data pribadi).',
                    size: 18,
                    color: '444444',
                  }),
                ],
              }),
            ],
          },
        ],
      })
      void Packer.toBlob(doc).then((blob) => {
        const stamp = new Date().toISOString().slice(0, 16).replace(/[:T]/g, '-')
        saveAs(blob, `briefme-thought-record-${stamp}.docx`)
        setDocPath('(unduhan browser)')
      })
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setBusy(null)
    }
  }

  function openHistoryItem(item: HistoryItem) {
    setText(item.journal)
    setRecord({
      headers: TABLE_HEADERS,
      rows: [
        [
          item.analysis.situation,
          item.analysis.thought,
          item.analysis.emotion,
          item.analysis.behavior,
          item.alternateThought.text,
        ],
      ],
      raw: '',
    })
    setAnalysis(item.analysis)
    setAlt({
      text: item.alternateThought.text,
      source: item.alternateThought.source,
    })
    setAltMode(null)
    setAltDraft(item.alternateThought.text)
    setDocPath(item.documentPath)
    setSavedId(item._id)
    setError(null)
    setNote(null)
    setHistoryOpen(false)
  }

  async function deleteHistoryItem(item: HistoryItem) {
    if (!window.confirm('Hapus riwayat curhatan ini?')) return
    const res = await historyRequest('DELETE', `/${item._id}`)
    if (!res.ok) {
      setNote(`Gagal menghapus: ${historyError(res.data)}`)
      return
    }
    if (savedId === item._id) setSavedId(null)
    void refreshHistory()
  }

  const busyLabel: Record<Exclude<Busy, null>, string> = {
    analyze: 'Menganalisis…',
    ai: 'Bob menyarankan…',
    doc: 'Membuat dokumen…',
  }
  const busyHint: Record<Exclude<Busy, null>, string> = {
    analyze: '± 10 detik (Bob menganalisis)',
    ai: '± 10 detik (Bob menyarankan)',
    doc: 'sebentar (dibuat di browser)',
  }

  return (
    // reducedMotion="user": setiap animasi menghormati prefers-reduced-motion
    <MotionConfig reducedMotion="user">
      <main className="page">
        <header className="hero">
          <div className="hero-row">
            <div>
              <h1>HearMeOut</h1>
              <p>
                Clinical Journaling Copilot — jurnal → Bob Agent → Thought Record +
                pikiran alternatif + dokumen (.docx dibuat di browser)
              </p>
            </div>
            <button
              type="button"
              className="ghost"
              onClick={() => setHistoryOpen((v) => !v)}
            >
              Riwayat ({history.length})
            </button>
          </div>
        </header>

        <AnimatePresence>
          {historyOpen && (
            <motion.section
              key="history"
              className="card"
              initial={{ opacity: 0, y: 8 }}
              animate={{ opacity: 1, y: 0 }}
              exit={{ opacity: 0, y: -8 }}
              transition={{ duration: 0.25, ease: [0.2, 0.8, 0.2, 1] }}
            >
              <h2>Riwayat curhatan</h2>
              {history.length === 0 ? (
                <p className="muted">Belum ada riwayat — analisis pertama akan tersimpan otomatis.</p>
              ) : (
                <ul className="history-list">
                  {history.map((item) => (
                    <li key={item._id} className="history-item">
                      <div className="history-main">
                        <span className="history-date">{formatDate(item.createdAt)}</span>
                        <span className="history-excerpt">
                          {item.journal.slice(0, 120)}
                          {item.journal.length > 120 ? '…' : ''}
                        </span>
                      </div>
                      <div className="history-actions">
                        <button
                          type="button"
                          className="ghost small"
                          onClick={() => openHistoryItem(item)}
                        >
                          Buka
                        </button>
                        <button
                          type="button"
                          className="danger small"
                          onClick={() => void deleteHistoryItem(item)}
                        >
                          Hapus
                        </button>
                      </div>
                    </li>
                  ))}
                </ul>
              )}
            </motion.section>
          )}
        </AnimatePresence>

        <section className="card">
          <label htmlFor="journal">Tulis jurnal harian Anda (apa adanya)</label>
          <textarea
            id="journal"
            rows={7}
            value={text}
            placeholder="Ceritakan pengalaman hari ini apa adanya…"
            onChange={(e) => setText(e.target.value)}
          />
          <div className="actions">
            <button type="button" onClick={() => void run()} disabled={!canAnalyze}>
              {busy === 'analyze' ? busyLabel.analyze : 'Analisis'}
            </button>
            <AnimatePresence>
              {busy && (
                <motion.span
                  className="hint"
                  initial={{ opacity: 0 }}
                  animate={{ opacity: 1 }}
                  exit={{ opacity: 0 }}
                  transition={{ duration: 0.15 }}
                >
                  {busyHint[busy]}
                </motion.span>
              )}
            </AnimatePresence>
            {journalError && !busy && (
              <span className="hint">{journalError}</span>
            )}
          </div>
          <AnimatePresence>
            {error && (
              <motion.p
                className="error"
                initial={{ opacity: 0, y: -4 }}
                animate={{ opacity: 1, y: 0 }}
                exit={{ opacity: 0, y: -8, filter: 'blur(4px)' }}
                transition={{ duration: 0.18, ease: [0.2, 0.8, 0.2, 1] }}
              >
                {error}
              </motion.p>
            )}
            {note && (
              <motion.p className="muted note" initial={{ opacity: 0 }} animate={{ opacity: 1 }}>
                {note}
              </motion.p>
            )}
          </AnimatePresence>
        </section>

        <AnimatePresence mode="wait">
          {record && analysis && (
            <motion.section
              key="record"
              className="card"
              initial={{ opacity: 0, y: 8, filter: 'blur(4px)' }}
              animate={{ opacity: 1, y: 0, filter: 'blur(0px)' }}
              exit={{ opacity: 0, y: -8, filter: 'blur(4px)' }}
              transition={{ type: 'spring', duration: 0.45, bounce: 0 }}
            >
              <h2>Thought Record</h2>
              {record.rows.length > 0 ? (
                <div className="table-wrap">
                  <table>
                    <thead>
                      <tr>
                        {record.headers.map((h) => (
                          <th key={h}>{h}</th>
                        ))}
                      </tr>
                    </thead>
                    <tbody>
                      {record.rows.map((row, i) => (
                        <tr key={i}>
                          {row.map((cell, j) => (
                            <td key={j}>
                              {j === 4 && alt.text ? (
                                <span className="alt-cell">
                                  {alt.text}
                                  {alt.source === 'ai' && <span className="badge">AI</span>}
                                </span>
                              ) : (
                                cell || '—'
                              )}
                            </td>
                          ))}
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              ) : (
                <pre className="raw">{record.raw}</pre>
              )}

              {/* Langkah berikutnya: pikiran alternatif (guardrail: AI tidak mengisi
                  otomatis — pasien memilih tulis manual atau minta saran AI).
                  Editor (altMode) menggantikan panel tahap berikutnya saat terbuka. */}
              {(!alt.text || altMode) && (
                <div className="next-step">
                  <h3>Pikiran Alternatif</h3>
                  {altMode === null ? (
                    <>
                      <p className="muted">
                        Disarankan untuk melatih pasien menulis sendiri — pikiran
                        alternatif yang seimbang dari sudut pandangnya.
                      </p>
                      <div className="actions">
                        <button
                          type="button"
                          onClick={() => {
                            setAltMode('manual')
                            setAltDraft('')
                            setError(null)
                          }}
                          disabled={busy !== null}
                        >
                          Tulis sendiri (disarankan)
                        </button>
                        <button
                          type="button"
                          className="ghost"
                          onClick={() => void suggestAI()}
                          disabled={busy !== null}
                        >
                          Saran AI (opsi)
                        </button>
                      </div>
                    </>
                  ) : (
                    <>
                      <label htmlFor="alt-input">
                        Pikiran alternatif Anda
                        {altMode === 'ai' && (
                          <span className="badge" title="Dihasilkan AI — boleh diedit">
                            AI
                          </span>
                        )}
                      </label>
                      {altMode === 'ai' && (
                        <p className="muted">
                          Ini saran AI — boleh Anda edit agar terasa lebih natural.
                        </p>
                      )}
                      <textarea
                        id="alt-input"
                        rows={3}
                        value={altDraft}
                        placeholder="Contoh: satu diskusi target belum tentu berarti saya akan dipecat…"
                        onChange={(e) => setAltDraft(e.target.value)}
                      />
                      <div className="actions">
                        <button
                          type="button"
                          onClick={() => void saveAlt()}
                          disabled={busy !== null}
                        >
                          Simpan pikiran alternatif
                        </button>
                        <button
                          type="button"
                          className="ghost"
                          onClick={() => {
                            setAltMode(null)
                            setAltDraft('')
                          }}
                          disabled={busy !== null}
                        >
                          Batal
                        </button>
                      </div>
                    </>
                  )}
                </div>
              )}

              {alt.text && !altMode && (
                <div className="next-step">
                  <h3>
                    Langkah berikutnya
                    {alt.source === 'ai' && <span className="badge">AI</span>}
                  </h3>
                  {!docPath ? (
                    <>
                      <p className="muted">
                        Pikiran alternatif tersimpan — buat dokumen Thought Record
                        (.docx) bila Anda sudah puas dengan isinya.
                      </p>
                      <div className="actions">
                        <button
                          type="button"
                          onClick={() => makeDocument()}
                          disabled={busy !== null || !altValid} // G4
                        >
                          {busy === 'doc' ? busyLabel.doc : 'Buat Dokumen'}
                        </button>
                        <button
                          type="button"
                          className="ghost"
                          onClick={() => {
                            setAltMode(alt.source === 'ai' ? 'ai' : 'manual')
                            setAltDraft(alt.text)
                          }}
                          disabled={busy !== null}
                        >
                          Ubah pikiran alternatif
                        </button>
                        <AnimatePresence>
                          {busy === 'doc' && (
                            <motion.span
                              className="hint"
                              initial={{ opacity: 0 }}
                              animate={{ opacity: 1 }}
                              exit={{ opacity: 0 }}
                            >
                              {busyHint.doc}
                            </motion.span>
                          )}
                        </AnimatePresence>
                      </div>
                    </>
                  ) : (
                    <p className="muted doc-path">
                      Dokumen: <code>{docPath}</code>
                    </p>
                  )}
                </div>
              )}
            </motion.section>
          )}
        </AnimatePresence>

        <footer className="muted footer-note">
          Dibuat dengan bantuan AI (IBM Bob + Langflow). Keluaran adalah ekstraksi
          otomatis dari jurnal Anda — bukan diagnosis atau saran medis.
        </footer>
      </main>
    </MotionConfig>
  )
}
