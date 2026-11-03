import { useEffect, useRef, useState } from 'react'
import { Link, Navigate } from 'react-router-dom'
import { get, post, type Me, type Recording } from '../lib/api'
import { errorText, useApp } from '../lib/app'
import { computeInk, type Ink } from '../lib/ink'
import { CheckIcon, DemoControls, SimBadge, Spinner, TopBar } from '../components/bits'
import { AirSignPad, InkCanvas, useMotion } from '../components/AirSignPad'

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
  const [inks, setInks] = useState<Ink[]>([])
  const [simInk, setSimInk] = useState<Ink | null>(null)
  const finishing = useRef(false)

  // Finish by itself once the target is reached: one less tap.
  useEffect(() => {
    if ((mode === 'real' || mode === 'sim') && count >= TARGET && !busy && !finishing.current) finish()
  }, [count, busy, mode])

  if (me && !me.user) return <Navigate to="/" replace />

  async function begin(simulated: boolean) {
    setError(null)
    setBusy(true)
    try {
      await post('/enroll/start', { simulated })
      setCount(0)
      setLast(null)
      setInks([])
      setMode(simulated ? 'sim' : 'real')
    } catch (e) {
      setError(errorText(e))
    } finally {
      setBusy(false)
    }
  }

  async function submit(rec: Recording): Promise<SampleResult | null> {
    setBusy(true)
    setError(null)
    try {
      const r = await post<SampleResult>('/enroll/sample', rec)
      setLast(r)
      setCount(r.count)
      if (r.accepted) {
        const ink = computeInk(rec.samples, rec.device.accel_includes_gravity)
        if (ink) setInks((xs) => [...xs, ink])
      }
      return r
    } catch (e) {
      setError(errorText(e))
      return null
    } finally {
      setBusy(false)
    }
  }

  async function simulate(kind: 'genuine' | 'shake', index: number) {
    const g = await get<Recording>(`/sim/gesture?kind=${kind}${kind === 'genuine' ? `&enroll_index=${index}` : ''}`)
    setSimInk(computeInk(g.samples, false))
    return submit({ samples: g.samples, device: g.device, simulated: true })
  }

  /** All remaining simulated repetitions in one go. */
  async function simulateAll() {
    setError(null)
    try {
      let n = count
      while (n < TARGET) {
        const r = await simulate('genuine', n)
        if (!r) break
        n = r.count
        await new Promise((res) => setTimeout(res, 350)) // let each signature draw
      }
    } catch (e) {
      setError(errorText(e))
    }
  }

  async function finish() {
    finishing.current = true
    setBusy(true)
    setError(null)
    try {
      const r = await post<{ report: { kept: number; dropped: number }; me: Me }>('/enroll/finish')
      setReport(r.report)
      setMe(r.me)
      setMode('done')
    } catch (e) {
      // For example, the repetitions were too different: record a few more.
      setError(errorText(e))
      finishing.current = false
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
          <p className="muted">Write your initials in the air with your phone. Make it personal, and take 2 to 4 seconds.</p>
          <ol className="muted small" style={{ paddingLeft: 18 }}>
            <li>Hold still until the pad turns green.</li>
            <li>Sign in the air, then hold still.</li>
            <li>Repeat {TARGET} times, the same way each time.</li>
          </ol>
          {error && <div className="notice bad">{error}</div>}
          <button className="btn block" disabled={busy} onClick={() => begin(false)}>
            Record with this phone
          </button>
        </div>
      )}
      {mode === 'choose' && config?.allow_simulation && (
        <DemoControls>
          <button className="btn sim block" disabled={busy} onClick={() => begin(true)}>
            No phone? Use a simulated signature
          </button>
        </DemoControls>
      )}

      {(mode === 'real' || mode === 'sim') && (
        <div className="card">
          <div className="row between">
            <h2>
              {count >= TARGET ? 'Saving…' : `Signature ${Math.min(count + 1, MAX)} of ${TARGET}`}
            </h2>
            {mode === 'sim' && <SimBadge />}
          </div>

          {mode === 'real' ? (
            count < MAX && (
              <AirSignPad motion={motion} busy={busy} onRecording={submit} reviewMs={900} readyLabel="Ready. Sign now" />
            )
          ) : (
            <div className="stack">
              <div className="pad air review">
                <InkCanvas ink={simInk} key={inks.length} />
                {!simInk && <div className="label">Simulated signatures appear here</div>}
              </div>
              <div className="sim-strip stack">
                <h3>Simulation</h3>
                <p className="small muted">
                  Labeled synthetic recordings of "your" signature, with natural variation, sensor noise and timing jitter.
                  They go through exactly the same checks as real ones.
                </p>
                <button className="btn sim block" disabled={busy || count >= TARGET} onClick={simulateAll}>
                  {busy ? <Spinner /> : count ? `Record the remaining ${TARGET - count}` : `Record all ${TARGET} simulated signatures`}
                </button>
                <button className="btn ghost small block" disabled={busy} onClick={() => simulate('shake', 0).catch((e) => setError(errorText(e)))}>
                  Try a lazy shake instead (should be rejected)
                </button>
              </div>
            </div>
          )}

          {inks.length > 0 && (
            <div className="thumbs" aria-label={`${inks.length} signatures recorded`}>
              {inks.map((ink, i) => (
                <InkCanvas key={i} ink={ink} animate={false} className="thumb" />
              ))}
              {Array.from({ length: Math.max(0, TARGET - inks.length) }, (_, i) => (
                <span key={`e${i}`} className="thumb empty" />
              ))}
            </div>
          )}
          {inks.length > 1 && <p className="tiny center">Similar shapes mean a reliable signature.</p>}

          {last && !last.accepted && (
            <div className="notice warn" style={{ marginTop: 12 }}>
              {last.reason}
            </div>
          )}
          {error && <div className="notice bad">{error}</div>}

          {count >= MINIMUM && count < TARGET && (
            <button className="btn ghost block" disabled={busy} onClick={finish} style={{ marginTop: 8 }}>
              Finish now with {count}
            </button>
          )}
        </div>
      )}

      {mode === 'done' && (
        <div className="card center">
          <CheckIcon />
          <h1>Air signature saved</h1>
          {inks.length > 0 && (
            <div className="pad air review small-pad">
              <InkCanvas ink={inks[inks.length - 1]} />
            </div>
          )}
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
