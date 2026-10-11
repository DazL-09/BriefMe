import { useState } from 'react'
import { AnimatePresence, motion, MotionConfig } from 'framer-motion'

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
  docPath: string | null
  raw: string
}

function extractText(item: FlowItem | undefined): string {
  if (!item) return ''
  const candidates = [item.results?.message?.text, item.artifacts?.message, item.text]
  for (const c of candidates) {
    if (c) return c
  }
  return ''
}

function parseThoughtRecord(md: string): ThoughtRecord {
  const docPath = /Dokumen:\s*(\S+)/.exec(md)?.[1] ?? null
  const pipeLines = md
    .split('\n')
    .map((line) => line.trim())
    .filter((line) => line.startsWith('|') && line.endsWith('|'))
  const toCells = (line: string): string[] =>
    line.slice(1, -1).split('|').map((cell) => cell.trim())
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
  return { headers, rows, docPath, raw: md }
}

const SAMPLE =
  'Kemarin saya berdiskusi dengan atasan soal target kuartal di kantor. ' +
  'Sejak itu saya cemas berlebihan, jantung berdebar, dan susah tidur. ' +
  'Saya takut saya akan dipecat.'

export default function App() {
  const [text, setText] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [record, setRecord] = useState<ThoughtRecord | null>(null)

  async function run() {
    if (!text.trim() || busy) return
    setBusy(true)
    setError(null)
    setRecord(null)
    try {
      const res = await fetch('/api/v1/run/briefme', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          input_value: text,
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
      const recordText = extractText(items['Thought Record'])
      if (recordText) {
        setRecord(parseThoughtRecord(recordText))
      } else {
        setError('Output "Thought Record" kosong atau tidak dikenal.')
      }
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setBusy(false)
    }
  }

  return (
    // reducedMotion="user": setiap animasi menghormati prefers-reduced-motion
    <MotionConfig reducedMotion="user">
      <main className="page">
        <header className="hero">
          <h1>HearMeOut</h1>
          <p>Clinical Journaling Copilot — jurnal → Agent CBT → Thought Record + dokumen</p>
        </header>

        <section className="card">
          <label htmlFor="journal">Tulis jurnal harian Anda (apa adanya)</label>
          <textarea
            id="journal"
            rows={7}
            value={text}
            placeholder="Contoh: Kemarin saya berdiskusi dengan atasan di kantor…"
            onChange={(e) => setText(e.target.value)}
          />
          <div className="actions">
            <button type="button" onClick={run} disabled={busy || !text.trim()}>
              {busy ? 'Menganalisis…' : 'Analisis'}
            </button>
            <button type="button" className="ghost" onClick={() => setText(SAMPLE)}>
              Pakai contoh
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
                  ± 1 menit (AI + dokumen)
                </motion.span>
              )}
            </AnimatePresence>
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
          </AnimatePresence>
        </section>

        <AnimatePresence mode="wait">
          {record && (
            <motion.section
              key="record"
              className="card"
              initial={{ opacity: 0, y: 8, filter: 'blur(4px)' }}
              animate={{ opacity: 1, y: 0, filter: 'blur(0px)' }}
              exit={{ opacity: 0, y: -8, filter: 'blur(4px)' }}
              transition={{ type: 'spring', duration: 0.45, bounce: 0 }}
            >
              <h2>Thought Record</h2>
              {record.headers.length > 0 && record.rows.length > 0 ? (
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
                            <td key={j}>{cell}</td>
                          ))}
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              ) : (
                <pre className="raw">{record.raw}</pre>
              )}
              <p className="muted doc-path">
                Dokumen: <code>{record.docPath ?? '(tidak ditemukan)'}</code>
              </p>
            </motion.section>
          )}
        </AnimatePresence>
      </main>
    </MotionConfig>
  )
}
