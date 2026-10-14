import { useState } from 'react'
import { Link, Navigate } from 'react-router-dom'
import { get, post, type Me, type Recording } from '../lib/api'
import { errorText, useApp } from '../lib/app'
import { CheckIcon, SimBadge, Spinner, TopBar } from '../components/bits'
import { SignPad, useMotion } from '../components/SignPad'

const TARGET = 7
const MINIMUM = 5
const MAX = 10

type SampleResult = { accepted: boolean; reason?: string; count: number; quality?: { duration_s: number; direction_changes: number } }

export default function Enroll() {
  const { me, config, setMe } = useApp()
  const motion = useMotion()
  const [mode, setMode] = useState<'choose' | 'real' | 'sim' | 'done'>('choose')
  const [count, setCount] = useState(0)
  const [busy, setBusy] = useState(false)
  const [last, setLast] = useState<SampleResult | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [report, setReport] = useState<{ kept: number; dropped: number } | null>(null)

  if (me && !me.user) return <Navigate to="/" replace />

  async function begin(simulated: boolean) {
    setError(null)
    setBusy(true)
    try {
      await post('/enroll/start', { simulated })
      setCount(0)
      setLast(null)
      setMode(simulated ? 'sim' : 'real')
    } catch (e) {
      setError(errorText(e))
    } finally {
      setBusy(false)
    }
  }

  async function submit(rec: Recording) {
    setBusy(true)
    setError(null)
    try {
      const r = await post<SampleResult>('/enroll/sample', rec)
      setLast(r)
      setCount(r.count)
    } catch (e) {
      setError(errorText(e))
    } finally {
      setBusy(false)
    }
  }

  async function simSample(kind: 'genuine' | 'shake') {
    setBusy(true)
    try {
      const g = await get<Recording>(`/sim/gesture?kind=${kind}${kind === 'genuine' ? `&enroll_index=${count}` : ''}`)
      await submit({ samples: g.samples, device: g.device, simulated: true })
    } catch (e) {
      setError(errorText(e))
      setBusy(false)
    }
  }

  async function finish() {
    setBusy(true)
    setError(null)
    try {
      const r = await post<{ report: { kept: number; dropped: number }; me: Me }>('/enroll/finish')
      setReport(r.report)
      setMe(r.me)
      setMode('done')
    } catch (e) {
      setError(errorText(e))
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="app">
      <TopBar />
      {mode === 'choose' && (
        <div className="card">
          <h1>Your air signature</h1>
          <p className="muted">
            Choose a movement that is personal and not too simple, for example writing your initials in the air with the
            phone, or a small loop and a zigzag. It should take 2 to 4 seconds.
          </p>
          <ol className="muted small" style={{ paddingLeft: 18 }}>
            <li>Press and hold the pad. Keep the phone still until it turns green.</li>
            <li>Move the phone in your signature, then release.</li>
            <li>Repeat {TARGET} times, the same way each time.</li>
          </ol>
          {error && <div className="notice bad">{error}</div>}
          <button className="btn block" disabled={busy} onClick={() => begin(false)}>
            Record with this phone
          </button>
          {config?.allow_simulation && (
            <>
              <div className="spacer" />
              <button className="btn sim block" disabled={busy} onClick={() => begin(true)}>
                No phone? Use a simulated signature
              </button>
            </>
          )}
        </div>
      )}

      {(mode === 'real' || mode === 'sim') && (
        <div className="card">
          <div className="row between">
            <h2>
              Repetition {Math.min(count + 1, MAX)} of {TARGET}
            </h2>
            {mode === 'sim' && <SimBadge />}
          </div>
          <div className="dots" aria-label={`${count} of ${TARGET} done`}>
            {Array.from({ length: TARGET }, (_, i) => (
              <span key={i} className={i < count ? 'done' : ''} />
            ))}
          </div>

          {mode === 'real' ? (
            count < MAX && <SignPad motion={motion} busy={busy} onRecording={submit} />
          ) : (
            <div className="sim-strip stack">
              <h3>Simulation</h3>
              <p className="small muted">
                Generates a labeled synthetic recording of "your" signature, with natural variation, sensor noise and
                timing jitter. It goes through exactly the same checks as a real one.
              </p>
              <button className="btn sim block" disabled={busy || count >= MAX} onClick={() => simSample('genuine')}>
                {busy ? <Spinner /> : 'Record a simulated repetition'}
              </button>
              <button className="btn ghost small block" disabled={busy} onClick={() => simSample('shake')}>
                Try a lazy shake instead (should be rejected)
              </button>
            </div>
          )}

          {last && (
            <div className={`notice ${last.accepted ? 'ok' : 'warn'}`} style={{ marginTop: 12 }}>
              {last.accepted
                ? `Good repetition (${last.quality?.duration_s}s, ${last.quality?.direction_changes} direction changes).`
                : last.reason}
            </div>
          )}
          {error && <div className="notice bad">{error}</div>}

          {count >= MINIMUM && (
            <button className="btn block" disabled={busy} onClick={finish} style={{ marginTop: 8 }}>
              {count >= TARGET ? 'Finish' : `Finish with ${count} repetitions`}
            </button>
          )}
        </div>
      )}

      {mode === 'done' && (
        <div className="card center">
          <CheckIcon />
          <h1>Air signature saved</h1>
          <p className="muted">
            Kept {report?.kept} repetitions{report?.dropped ? `, set aside ${report.dropped} that differed from the rest` : ''}. Your
            personal threshold is set from how much your own repetitions vary.
          </p>
          <Link to="/pay" className="btn block">
            Pay in a shop
          </Link>
          <div className="spacer" />
          <Link to="/" className="btn ghost block">
            Home
          </Link>
        </div>
      )}
    </div>
  )
}
