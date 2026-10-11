import { useCallback, useEffect, useRef, useState } from 'react'
import { AnimatePresence, motion, MotionConfig } from 'framer-motion'
import {
  Document as DocxDocument,
  HeadingLevel,
  Packer,
  PageBreak,
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
}

type Analysis = {
  situation: string
  thought: string
  emotion: string
  behavior: string
}

type AltSource = '' | 'manual' | 'ai'

type Alt = { text: string; source: AltSource }

type HistoryItem = {
  _id: string
  journal: string
  analyses: Analysis[]
  alternateThoughts: Alt[]
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
  return { headers, rows }
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

function analysesFromRows(rows: string[][]): Analysis[] {
  const result: Analysis[] = []
  for (const row of rows) {
    if (row.length < 4) continue
    const a: Analysis = {
      situation: row[0].trim(),
      thought: row[1].trim(),
      emotion: row[2].trim(),
      behavior: row[3].trim(),
    }
    if (Object.values(a).every((v) => v && v !== '-')) result.push(a)
  }
  return result
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
): Promise<{ ok: boolean; data: unknown }> {
  try {
    const res = await fetch(`/api/history${path}`, {
      method,
      headers: { 'Content-Type': 'application/json' },
      body: body === undefined ? undefined : JSON.stringify(body),
    })
    const data = await res.json().catch(() => ({}))
    return { ok: res.ok, data }
  } catch (e) {
    return { ok: false, data: { error: e instanceof Error ? e.message : String(e) } }
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

function dateLabel(iso: string): string {
  try {
    return new Date(iso).toLocaleDateString('id-ID', {
      weekday: 'long',
      day: 'numeric',
      month: 'long',
      year: 'numeric',
    })
  } catch {
    return iso.slice(0, 10)
  }
}

function groupByDate(items: HistoryItem[]): { label: string; items: HistoryItem[] }[] {
  const map = new Map<string, HistoryItem[]>()
  for (const item of items) {
    const key = dateLabel(item.createdAt)
    if (!map.has(key)) map.set(key, [])
    map.get(key)!.push(item)
  }
  return Array.from(map.entries()).map(([label, groupItems]) => ({ label, items: groupItems }))
}

function buildThoughtRecordTable(rows: string[][], alts: Alt[]): Table {
  const cell = (text: string) =>
    new TableCell({
      children: [new Paragraph({ children: [new TextRun({ text, size: 22 })] })],
    })
  return new Table({
    width: { size: 100, type: WidthType.PERCENTAGE },
    rows: [
      new TableRow({
        children: TABLE_HEADERS.map((h) =>
          new TableCell({
            children: [
              new Paragraph({
                children: [new TextRun({ text: h, bold: true, size: 22 })],
              }),
            ],
          }),
        ),
      }),
      ...rows.map((row, i) =>
        new TableRow({
          children: [
            cell(row[0] || '—'),
            cell(row[1] || '—'),
            cell(row[2] || '—'),
            cell(row[3] || '—'),
            cell(alts[i]?.text || '—'),
          ],
        }),
      ),
    ],
  })
}

const DOC_FOOTER =
  'Catatan: dokumen ini bersifat objektif-klinis, berisi ekstraksi data saja tanpa saran medis. Teks bersumber dari jurnal pasien (tanpa data pribadi).'

/** Typewriter: reveal text char-by-char; mulut Kiko "bicara" selama mengetik. */
function useTypewriter(full: string, speed = 28): { shown: string; typing: boolean } {
  const [shown, setShown] = useState(full)
  const [typing, setTyping] = useState(false)
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null)

  useEffect(() => {
    // Skip animasi bila reduced-motion
    if (window.matchMedia('(prefers-reduced-motion: reduce)').matches) {
      setShown(full)
      setTyping(false)
      return
    }
    setShown('')
    setTyping(true)
    let i = 0
    const tick = () => {
      i += 1
      setShown(full.slice(0, i))
      if (i < full.length) {
        timer.current = setTimeout(tick, speed)
      } else {
        setTyping(false)
      }
    }
    timer.current = setTimeout(tick, speed)
    return () => {
      if (timer.current) clearTimeout(timer.current)
    }
  }, [full, speed])

  return { shown, typing }
}

export default function App() {
  const [text, setText] = useState('')
  const [busy, setBusy] = useState<Busy>(null)
  const [error, setError] = useState<string | null>(null)
  const [note, setNote] = useState<string | null>(null)
  const [record, setRecord] = useState<ThoughtRecord | null>(null)
  const [analyses, setAnalyses] = useState<Analysis[]>([])
  const [alts, setAlts] = useState<Alt[]>([])
  const [activeRow, setActiveRow] = useState<number | null>(null)
  const [altMode, setAltMode] = useState<null | 'manual' | 'ai'>(null)
  const [altDraft, setAltDraft] = useState('')
  const [docPath, setDocPath] = useState('')
  const [savedId, setSavedId] = useState<string | null>(null)
  const [history, setHistory] = useState<HistoryItem[]>([])
  const [historyOpen, setHistoryOpen] = useState(false)
  const [recordOpen, setRecordOpen] = useState(false)
  const [selectedIds, setSelectedIds] = useState<Set<string>>(new Set())
  const [expandedGroups, setExpandedGroups] = useState<Set<string>>(new Set())

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

  const altFilledCount = alts.filter((a) => validateAlt(a.text) === null).length
  const allAltsValid = analyses.length > 0 && alts.length === analyses.length && altFilledCount === analyses.length
  const journalError = text.trim() ? validateJournal(text) : null
  const canAnalyze = Boolean(text.trim()) && busy === null && journalError === null

  const bubbleMsg =
    busy === 'analyze'
      ? 'Sabar ya — Bob sedang menganalisis jurnalmu…'
      : busy === 'ai'
        ? 'Bob sedang menyiapkan saran pikiran alternatif…'
        : record && altFilledCount < analyses.length
          ? `Nah, ini Thought Record-mu! Tulis pikiran alternatif untuk tiap baris (${altFilledCount}/${analyses.length} selesai).`
          : record && allAltsValid
            ? 'Keren! Semua pikiran alternatif tersimpan. Mau buat dokumennya?'
            : 'Halo! Aku Kiko, pemandu journaling-mu. Tulis apa saja yang ada di pikiranmu di bawah, yuk!'
  const { shown: bubbleShown, typing: bubbleTyping } = useTypewriter(bubbleMsg)

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
    setAnalyses([])
    setAlts([])
    setActiveRow(null)
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
      const parsedAnalyses = analysesFromRows(parsed.rows)
      if (parsedAnalyses.length === 0) {
        // G2 — analisis kosong/gagal: tampilkan ke user, JANGAN simpan riwayat.
        setError('Analisis tidak menghasilkan tabel CBT yang valid. Coba tulis ulang curhatannya.')
        return
      }
      const emptyAlts: Alt[] = parsedAnalyses.map(() => ({ text: '', source: '' }))
      setRecord(parsed)
      setAnalyses(parsedAnalyses)
      setAlts(emptyAlts)
      setActiveRow(0)
      setRecordOpen(true)
      // Simpan ke riwayat (journal + semua analisis; alt & dokumen menyusul).
      const res = await historyRequest('POST', '', {
        journal: text.trim(),
        analyses: parsedAnalyses,
        alternateThoughts: emptyAlts,
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
    if (busy || activeRow === null || !analyses[activeRow]) return
    setBusy('ai')
    setError(null)
    setNote(null)
    try {
      const a = analyses[activeRow]
      // Satu endpoint briefme — mode dibaca dari awalan input oleh kode node.
      const items = await runFlow(
        'briefme',
        'MODE=ALTERNATIF\n' +
          JSON.stringify({
            situation: a.situation,
            thought: a.thought,
            emotion: a.emotion,
            behavior: a.behavior,
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
      if (suggestion.split(/\s+/).length > 60) {
        setNote('Saran AI cukup panjang — potong agar lebih ringkas (maks. ~40 kata).')
      }
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setBusy(null)
    }
  }

  async function saveAlt() {
    if (activeRow === null) return
    const validation = validateAlt(altDraft)
    if (validation) {
      setError(validation)
      return
    }
    const source: AltSource = altMode === 'ai' ? 'ai' : 'manual'
    const next: Alt = { text: altDraft.trim(), source }
    const newAlts = alts.map((a, i) => (i === activeRow ? next : a))
    setAlts(newAlts)
    setAltMode(null)
    setError(null)
    // Auto-advance ke baris berikutnya yang masih kosong
    const nextEmpty = newAlts.findIndex((a, i) => i > activeRow && validateAlt(a.text) !== null)
    setActiveRow(nextEmpty >= 0 ? nextEmpty : null)
    if (savedId) {
      const res = await historyRequest('PATCH', `/${savedId}`, {
        alternateThoughts: newAlts,
      })
      if (!res.ok) setNote(`Riwayat gagal diperbarui: ${historyError(res.data)}`)
      void refreshHistory()
    }
  }

  function makeDocument() {
    // G4 — dokumen hanya setelah semua alt valid; dibuat lokal di browser.
    if (busy || analyses.length === 0 || !allAltsValid) return
    setBusy('doc')
    setError(null)
    setNote(null)
    try {
      const rows = analyses.map((a, i) => [
        a.situation,
        a.thought,
        a.emotion,
        a.behavior,
        alts[i]?.text || '',
      ])
      const table = buildThoughtRecordTable(rows, alts)
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
                    text: 'Dokumen dibuat otomatis oleh Brief Me (web)',
                    italics: true,
                    color: '666666',
                  }),
                ],
              }),
              table,
              new Paragraph({
                children: [
                  new TextRun({ text: DOC_FOOTER, size: 18, color: '444444' }),
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

  function makeBulkDocument() {
    const items = history.filter((h) => selectedIds.has(h._id))
    if (busy || items.length === 0) return
    setBusy('doc')
    setError(null)
    setNote(null)
    try {
      const children: (Paragraph | Table)[] = []
      new Paragraph({
        heading: HeadingLevel.HEADING_1,
        children: [new TextRun('Brief Me — Kumpulan Thought Record (CBT)')],
      })
      children.push(
        new Paragraph({
          heading: HeadingLevel.HEADING_1,
          children: [new TextRun('Brief Me — Kumpulan Thought Record (CBT)')],
        }),
        new Paragraph({
          children: [
            new TextRun({
              text: `${items.length} entri journaling · dibuat otomatis oleh Brief Me (web)`,
              italics: true,
              color: '666666',
            }),
          ],
        }),
        new Paragraph({ children: [] }),
      )
      items.forEach((item, i) => {
        const itemRows = (item.analyses || []).map((a, j) => [
          a.situation,
          a.thought,
          a.emotion,
          a.behavior,
          item.alternateThoughts?.[j]?.text || '',
        ])
        children.push(
          new Paragraph({
            heading: HeadingLevel.HEADING_2,
            children: [
              new TextRun(`${i + 1}. ${formatDate(item.createdAt)}`),
            ],
          }),
          new Paragraph({
            children: [
              new TextRun({
                text: `Jurnal: ${item.journal.slice(0, 200)}${item.journal.length > 200 ? '…' : ''}`,
                size: 18,
                color: '555555',
              }),
            ],
          }),
          buildThoughtRecordTable(itemRows, item.alternateThoughts || []),
        )
        if (i < items.length - 1) {
          children.push(new Paragraph({ children: [new PageBreak()] }))
        }
      })
      children.push(
        new Paragraph({ children: [] }),
        new Paragraph({
          children: [new TextRun({ text: DOC_FOOTER, size: 18, color: '444444' })],
        }),
      )
      const doc = new DocxDocument({ sections: [{ children }] })
      void Packer.toBlob(doc).then((blob) => {
        const stamp = new Date().toISOString().slice(0, 16).replace(/[:T]/g, '-')
        saveAs(blob, `briefme-cbt-bulk-${items.length}entri-${stamp}.docx`)
        setNote(`Bulk export: ${items.length} entri → 1 dokumen.`)
        setSelectedIds(new Set())
      })
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setBusy(null)
    }
  }

  function openHistoryItem(item: HistoryItem) {
    const itemAnalyses = item.analyses || []
    const itemAlts = item.alternateThoughts || []
    setText(item.journal)
    setRecord({
      headers: TABLE_HEADERS,
      rows: itemAnalyses.map((a, i) => [
        a.situation,
        a.thought,
        a.emotion,
        a.behavior,
        itemAlts[i]?.text || '',
      ]),
    })
    setAnalyses(itemAnalyses)
    setAlts(itemAlts)
    // Aktifkan baris pertama yang alt-nya masih kosong
    const firstEmpty = itemAlts.findIndex((a) => validateAlt(a.text) !== null)
    setActiveRow(firstEmpty >= 0 ? firstEmpty : null)
    setAltMode(null)
    setAltDraft('')
    setDocPath(item.documentPath)
    setSavedId(item._id)
    setError(null)
    setNote(null)
    setHistoryOpen(false)
    setRecordOpen(true)
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

  const busyLabel: Record<'analyze' | 'doc', string> = {
    analyze: 'Menganalisis…',
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
      <img className="corner tl" src="assets/corner-tl.png" alt="" aria-hidden="true" />
      <img className="corner tr" src="assets/corner-tl.png" alt="" aria-hidden="true" />
      <img className="corner bl" src="assets/corner-br.png" alt="" aria-hidden="true" />
      <img className="corner br" src="assets/corner-br.png" alt="" aria-hidden="true" />
      <img className="spark s1" src="assets/sparkles.png" alt="" aria-hidden="true" />
      <img className="spark s2" src="assets/sparkles.png" alt="" aria-hidden="true" />
      <main className="page">
        <header className="hero">
          <div className="hero-row">
            <div>
              <h1>BRIEF ME</h1>
              <p className="sub">
                Journaling Asisten AI — tulis jurnal, dapatkan Thought Record +
                pikiran alternatif + dokumen (.docx) otomatis
              </p>
            </div>
            <button
              type="button"
              className="btn-blue"
              onClick={() => setHistoryOpen((v) => !v)}
            >
              Riwayat ({history.length})
            </button>
          </div>
        </header>

        <section className="panel kiko" aria-label="Kiko">
          <div className={`bubble${bubbleTyping ? ' talking' : ''}`}>
            {bubbleShown}
            {bubbleTyping && <span className="cursor" aria-hidden="true" />}
          </div>
          <img
            className={`cat${bubbleTyping ? ' talking' : ''}`}
            src="assets/kiko-closed.png"
            alt="Kiko, kucing penyihir"
          />
        </section>

        <AnimatePresence>
          {historyOpen && (
            <>
              <motion.div
                key="history-backdrop"
                className="sidebar-backdrop"
                initial={{ opacity: 0 }}
                animate={{ opacity: 1 }}
                exit={{ opacity: 0 }}
                transition={{ duration: 0.2 }}
                onClick={() => setHistoryOpen(false)}
              />
              <motion.aside
                key="history-sidebar"
                className="sidebar"
                initial={{ x: '100%' }}
                animate={{ x: 0 }}
                exit={{ x: '100%' }}
                transition={{ type: 'tween', duration: 0.25, ease: [0.2, 0.8, 0.2, 1] }}
                aria-label="Riwayat"
              >
                <div className="sidebar-head">
                  <h2>Riwayat</h2>
                  <button
                    type="button"
                    className="ghost small"
                    onClick={() => setHistoryOpen(false)}
                  >
                    ✕
                  </button>
                </div>
                {history.length === 0 ? (
                  <p className="muted">Belum ada riwayat — analisis pertama akan tersimpan otomatis.</p>
                ) : (
                  <>
                    <div className="sidebar-toolbar">
                      <label className="history-check-all">
                        <input
                          type="checkbox"
                          checked={selectedIds.size === history.length && history.length > 0}
                          onChange={(e) => {
                            if (e.target.checked) {
                              setSelectedIds(new Set(history.map((h) => h._id)))
                            } else {
                              setSelectedIds(new Set())
                            }
                          }}
                        />
                        Pilih semua ({history.length})
                      </label>
                    </div>
                    <div className="sidebar-body">
                      {groupByDate(history).map((group) => {
                        const expanded = expandedGroups.has(group.label)
                        return (
                          <div key={group.label} className="history-group">
                            <button
                              type="button"
                              className={`history-group-label${expanded ? '' : ' collapsed'}`}
                              onClick={() => {
                                const next = new Set(expandedGroups)
                                if (expanded) next.delete(group.label)
                                else next.add(group.label)
                                setExpandedGroups(next)
                              }}
                            >
                              <span className="group-chevron" aria-hidden="true">
                                {expanded ? '▾' : '▸'}
                              </span>
                              {group.label}
                              <span className="group-count">{group.items.length}</span>
                            </button>
                            {expanded && (
                              <ul className="history-list">
                                {group.items.map((item) => (
                                  <li key={item._id} className="history-item">
                                    <label className="history-check">
                                      <input
                                        type="checkbox"
                                        checked={selectedIds.has(item._id)}
                                        onChange={(e) => {
                                          const next = new Set(selectedIds)
                                          if (e.target.checked) next.add(item._id)
                                          else next.delete(item._id)
                                          setSelectedIds(next)
                                        }}
                                      />
                                    </label>
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
                          </div>
                        )
                      })}
                    </div>
                    <div className="sidebar-export">
                      <button
                        type="button"
                        onClick={() => makeBulkDocument()}
                        disabled={busy !== null || selectedIds.size === 0}
                      >
                        {busy === 'doc'
                          ? 'Membuat bulk…'
                          : `Export .docx (${selectedIds.size})`}
                      </button>
                    </div>
                  </>
                )}
              </motion.aside>
            </>
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

        <AnimatePresence>
          {record && analyses.length > 0 && recordOpen && (
            <motion.div
              key="record-overlay"
              className="record-overlay"
              initial={{ opacity: 0 }}
              animate={{ opacity: 1 }}
              exit={{ opacity: 0 }}
              transition={{ duration: 0.2 }}
              onClick={() => setRecordOpen(false)}
            >
              <motion.section
                key="record"
                className="record-modal"
                initial={{ opacity: 0, y: 16, scale: 0.97 }}
                animate={{ opacity: 1, y: 0, scale: 1 }}
                exit={{ opacity: 0, y: 12, scale: 0.97 }}
                transition={{ type: 'spring', duration: 0.4, bounce: 0 }}
                onClick={(e) => e.stopPropagation()}
                aria-label="Thought Record"
              >
                <div className="record-head">
                  <h2>Thought Record</h2>
                  <button
                    type="button"
                    className="ghost small"
                    onClick={() => setRecordOpen(false)}
                  >
                    ✕
                  </button>
                </div>
                <div className="record-body">
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
                          <tr
                            key={i}
                            className={activeRow === i ? 'active-row' : ''}
                            onClick={() => {
                              if (altMode === null) {
                                setActiveRow(i)
                                setAltDraft(alts[i]?.text || '')
                              }
                            }}
                          >
                            {row.map((cell, j) => (
                              <td key={j}>
                                {j === 4 ? (
                                  alts[i]?.text ? (
                                    <span className="alt-cell">
                                      {alts[i].text}
                                      {alts[i].source === 'ai' && <span className="badge">AI</span>}
                                    </span>
                                  ) : (
                                    <span className="alt-empty">—</span>
                                  )
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

                  {/* Progress: berapa baris sudah punya alt */}
                  <p className="progress-hint">
                    {altFilledCount}/{analyses.length} baris sudah memiliki pikiran alternatif
                    {activeRow !== null && (
                      <> · mengedit baris {activeRow + 1}</>
                    )}
                  </p>

                  {/* Panel editor kontekstual untuk baris aktif */}
                  {activeRow !== null && !altMode && (
                    <div className="next-step">
                      <h3>
                        Pikiran Alternatif — Baris {activeRow + 1}
                        {alts[activeRow]?.text && (
                          <span className="badge">terisi</span>
                        )}
                      </h3>
                      {alts[activeRow]?.text ? (
                        <p className="muted">
                          Baris ini sudah terisi. Klik baris lain untuk mengedit, atau
                          lanjut ke dokumen bila semua baris sudah selesai.
                        </p>
                      ) : (
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
                      )}
                    </div>
                  )}

                  {/* Editor textarea (altMode aktif) */}
                  {activeRow !== null && altMode && (
                    <div className="next-step">
                      <h3>
                        Pikiran Alternatif — Baris {activeRow + 1}
                        {altMode === 'ai' && (
                          <span className="badge" title="Dihasilkan AI — boleh diedit">
                            AI
                          </span>
                        )}
                      </h3>
                      {altMode === 'ai' && (
                        <p className="muted">
                          Ini saran AI — boleh Anda edit agar terasa lebih natural.
                        </p>
                      )}
                      <label htmlFor="alt-input">
                        Pikiran alternatif Anda
                      </label>
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
                    </div>
                  )}

                  {/* Semua alt terisi → tombol Buat Dokumen (G4) */}
                  {allAltsValid && activeRow === null && !altMode && (
                    <div className="next-step">
                      <h3>
                        Langkah berikutnya
                        {alts.some((a) => a.source === 'ai') && <span className="badge">AI</span>}
                      </h3>
                      {!docPath ? (
                        <>
                          <p className="muted">
                            Semua pikiran alternatif tersimpan — buat dokumen Thought Record
                            (.docx) bila Anda sudah puas dengan isinya.
                          </p>
                          <div className="actions">
                            <button
                              type="button"
                              onClick={() => makeDocument()}
                              disabled={busy !== null || !allAltsValid} // G4
                            >
                              {busy === 'doc' ? busyLabel.doc : 'Buat Dokumen'}
                            </button>
                            <button
                              type="button"
                              className="ghost"
                              onClick={() => {
                                const firstWithAlt = alts.findIndex((a) => a.text)
                                if (firstWithAlt >= 0) {
                                  setActiveRow(firstWithAlt)
                                  setAltDraft(alts[firstWithAlt].text)
                                  setAltMode(alts[firstWithAlt].source === 'ai' ? 'ai' : 'manual')
                                }
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

                  {error && <p className="error">{error}</p>}
                  {note && <p className="muted note">{note}</p>}
                </div>
              </motion.section>
            </motion.div>
          )}
        </AnimatePresence>

        {record && analyses.length > 0 && !recordOpen && (
          <button
            type="button"
            className="btn-blue reopen-record"
            onClick={() => setRecordOpen(true)}
          >
            Lihat Thought Record
          </button>
        )}

        <footer className="muted footer-note">
          ⚠ Ini bukan diagnosis atau saran medis. Konsultasikan keluhan Anda
          kepada tenaga profesional.
        </footer>
      </main>
    </MotionConfig>
  )
}
