import { useCallback, useEffect, useState } from 'react'
import { del, get, post, type Recording } from '../lib/api'
import { errorText } from '../lib/app'
import { Spinner, TopBar } from '../components/bits'
import { SignPad, useMotion } from '../components/SignPad'

type Kind = 'enroll' | 'genuine' | 'imitation'
type Posture = 'standing' | 'sitting' | 'walking'

type Participant = {
  id: string
  alias: string
  counts: Record<Kind, number>
  targets: Record<Kind, number>
  postures: Record<Posture, number>
  imitated: Record<string, number>
}

type Diagnostics = {
  usable: boolean
  code?: string
  message?: string
  duration_s?: number
  direction_changes?: number
  peak_rotation?: number
  rate_hz?: number
  jitter_ms?: number
  noise_log10?: number[]
  lead_in_still?: boolean
  sensor_flags?: string[]
  enroll_quality?: string
  score?: { z: number; tier1: string; tier2: string }
}

type Rate = { k: number; n: number; rate: number | null; ci95: [number, number] }
type Summary = {
  counts: Record<string, number>
  tiers: Record<string, { k: number; genuine_accepted: Rate; imitation_accepted: Rate; impostor_accepted: Rate }>
  eer_imitation: { eer: number; threshold_z: number } | null
  eer_impostor: { eer: number; threshold_z: number } | null
  replay_detected: Rate
  genuine_flagged_as_replay: Rate
  genuine_flagged_by_sensor_check: Rate
  sensor_flags_by_platform: Record<string, { n: number; flagged: number; flags: Record<string, number> }>
  per_participant: Record<string, { genuine_accepted_tier1: Rate; imitation_accepted_tier1: Rate }>
  notes: Record<string, string>
}

const KIND_LABEL: Record<Kind, string> = { enroll: 'Enrollment', genuine: 'Genuine', imitation: 'Imitation' }

function readLocal(key: string): string {
  try {
    return localStorage.getItem(key) ?? ''
  } catch {
    return ''
  }
}
function writeLocal(key: string, value: string) {
  try {
    localStorage.setItem(key, value)
  } catch {
    /* storage unavailable: the label is only a convenience */
  }
}

function pct(r: Rate) {
  if (!r.n || r.rate === null) return 'n/a'
  return `${(100 * r.rate).toFixed(1)}% (${r.k}/${r.n}, CI ${Math.round(100 * r.ci95[0])}–${Math.round(100 * r.ci95[1])}%)`
}

function Login({ onDone }: { onDone: () => void }) {
  const [code, setCode] = useState('')
  const [error, setError] = useState<string | null>(null)
  return (
    <form
      className="card"
      onSubmit={async (e) => {
        e.preventDefault()
        try {
          await post('/lab/login', { code })
          onDone()
        } catch (err) {
          setError(errorText(err))
        }
      }}
    >
      <h1>Recording lab</h1>
      <p className="muted">For the AirSign team: record gestures on real phones for the evaluation.</p>
      <label className="field">
        <span>Lab code</span>
        <input type="password" value={code} onChange={(e) => setCode(e.target.value)} />
      </label>
      {error && <div className="notice bad">{error}</div>}
      <button className="btn block">Enter</button>
    </form>
  )
}

function AddParticipant({ onAdded }: { onAdded: (id: string) => void }) {
  const [alias, setAlias] = useState('')
  const [consent, setConsent] = useState(false)
  const [error, setError] = useState<string | null>(null)
  return (
    <form
      className="stack"
      onSubmit={async (e) => {
        e.preventDefault()
        try {
          const r = await post<{ id: string }>('/lab/participants', { alias, consent })
          setAlias('')
          setConsent(false)
          onAdded(r.id)
        } catch (err) {
          setError(errorText(err))
        }
      }}
    >
      <input type="text" placeholder="Nickname (stays on this server, not in exports)" value={alias} onChange={(e) => setAlias(e.target.value)} maxLength={40} />
      <label className="check">
        <input type="checkbox" checked={consent} onChange={(e) => setConsent(e.target.checked)} />
        <span>
          I agree that my raw motion recordings are stored to evaluate and train AirSign's verifier, under a pseudonym (P1,
          P2, ...), and that I can withdraw and have them deleted at any time.
        </span>
      </label>
      {error && <div className="notice bad">{error}</div>}
      <button className="btn quiet block" disabled={!alias.trim() || !consent}>
        Add participant
      </button>
    </form>
  )
}

function Progress({ p }: { p: Participant }) {
  return (
    <div className="stack">
      {(Object.keys(KIND_LABEL) as Kind[]).map((k) => {
        const done = Math.min(p.counts[k], p.targets[k])
        return (
          <div key={k}>
            <div className="row between tiny">
              <span>{KIND_LABEL[k]}</span>
              <span>
                {p.counts[k]} / {p.targets[k]}
              </span>
            </div>
            <div className="meter">
              <i style={{ width: `${(100 * done) / p.targets[k]}%`, background: done >= p.targets[k] ? 'var(--ok)' : 'var(--accent)' }} />
            </div>
          </div>
        )
      })}
      <p className="tiny">
        Genuine by posture: standing {p.postures.standing}, sitting {p.postures.sitting}, walking {p.postures.walking}
        {Object.keys(p.imitated).length > 0 && ` · imitated: ${Object.entries(p.imitated).map(([t, n]) => `${t} ×${n}`).join(', ')}`}
      </p>
    </div>
  )
}

function DiagnosticsCard({ d, onUndo }: { d: Diagnostics; onUndo: () => void }) {
  if (!d.usable) {
    return (
      <div className="notice warn">
        Not usable: {d.message}
        <div className="spacer" />
        <button className="btn ghost small" onClick={onUndo}>
          Delete this take
        </button>
      </div>
    )
  }
  const flags = d.sensor_flags ?? []
  return (
    <div className={`notice ${flags.length >= 2 ? 'bad' : 'ok'}`}>
      <strong>Saved.</strong> {d.duration_s}s gesture, {d.direction_changes} direction changes, peak {d.peak_rotation} rad/s.
      <div className="tiny" style={{ marginTop: 6, color: 'inherit' }}>
        Sensor: {d.rate_hz} Hz, jitter {d.jitter_ms} ms, noise log10 [{d.noise_log10?.join(', ')}]
        {d.lead_in_still === false && ' · no still lead-in'}
        {flags.length > 0 && ` · flags: ${flags.join(', ')}`}
        {d.enroll_quality && d.enroll_quality !== 'ok' && ` · enrollment check: ${d.enroll_quality}`}
      </div>
      {d.score && (
        <div className="tiny" style={{ marginTop: 6, color: 'inherit' }}>
          Distance z = {d.score.z} → tier 1 {d.score.tier1.toLowerCase()}, tier 2 {d.score.tier2.toLowerCase()}
        </div>
      )}
      <div className="spacer" />
      <button className="btn ghost small" onClick={onUndo}>
        Bad take? Delete it
      </button>
    </div>
  )
}

function Results() {
  const [s, setS] = useState<Summary | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  return (
    <div className="card">
      <div className="row between">
        <h2>Results</h2>
        <a className="btn ghost small" href="/api/lab/export">
          Export JSON
        </a>
      </div>
      <p className="small muted">Scores every recording against every enrolled participant. Takes a few seconds.</p>
      <button
        className="btn quiet block"
        disabled={busy}
        onClick={async () => {
          setBusy(true)
          setError(null)
          try {
            setS(await get<Summary>('/lab/summary'))
          } catch (e) {
            setError(errorText(e))
          } finally {
            setBusy(false)
          }
        }}
      >
        {busy ? <Spinner /> : 'Compute results'}
      </button>
      {error && <div className="notice bad">{error}</div>}
      {s && (
        <div style={{ overflowX: 'auto', marginTop: 12 }}>
          <table className="table">
            <thead>
              <tr>
                <th></th>
                <th>Tier 1</th>
                <th>Tier 2</th>
              </tr>
            </thead>
            <tbody>
              {(
                [
                  ['genuine_accepted', 'Genuine accepted'],
                  ['imitation_accepted', 'Imitation accepted'],
                  ['impostor_accepted', 'Other person accepted'],
                ] as const
              ).map(([key, label]) => (
                <tr key={key}>
                  <td>{label}</td>
                  <td>{pct(s.tiers.tier1[key])}</td>
                  <td>{pct(s.tiers.tier2[key])}</td>
                </tr>
              ))}
            </tbody>
          </table>
          <p className="small" style={{ marginTop: 12 }}>
            EER vs imitation: {s.eer_imitation ? `${(100 * s.eer_imitation.eer).toFixed(1)}% at z = ${s.eer_imitation.threshold_z}` : 'n/a'}
            <br />
            EER vs other people: {s.eer_impostor ? `${(100 * s.eer_impostor.eer).toFixed(1)}% at z = ${s.eer_impostor.threshold_z}` : 'n/a'}
            <br />
            Replay detected: {pct(s.replay_detected)}
            <br />
            Genuine flagged as replay: {pct(s.genuine_flagged_as_replay)}
            <br />
            Genuine flagged by sensor check: {pct(s.genuine_flagged_by_sensor_check)}
          </p>
          <h3>Sensor check by platform</h3>
          <ul className="small muted" style={{ paddingLeft: 18 }}>
            {Object.entries(s.sensor_flags_by_platform).map(([p, v]) => (
              <li key={p}>
                {p}: {v.flagged}/{v.n} flagged{Object.keys(v.flags).length > 0 && ` (${Object.entries(v.flags).map(([f, n]) => `${f} ×${n}`).join(', ')})`}
              </li>
            ))}
          </ul>
          {Object.entries(s.notes).map(([p, n]) => (
            <div key={p} className="notice warn">
              {p} could not enroll: {n}
            </div>
          ))}
        </div>
      )}
    </div>
  )
}

export default function Lab() {
  const [status, setStatus] = useState<{ enabled: boolean; authorized: boolean } | null>(null)
  const [people, setPeople] = useState<Participant[]>([])
  const [pid, setPid] = useState('')
  const [kind, setKind] = useState<Kind>('enroll')
  const [session, setSession] = useState('A')
  const [posture, setPosture] = useState<Posture>('standing')
  const [target, setTarget] = useState('')
  const [deviceLabel, setDeviceLabel] = useState(readLocal('airsign-lab-device'))
  const [busy, setBusy] = useState(false)
  const [last, setLast] = useState<{ id: number; d: Diagnostics } | null>(null)
  const [error, setError] = useState<string | null>(null)
  const motion = useMotion()

  const loadStatus = useCallback(() => get<{ enabled: boolean; authorized: boolean }>('/lab/status').then(setStatus), [])
  const loadPeople = useCallback(() => get<Participant[]>('/lab/participants').then(setPeople), [])

  useEffect(() => {
    loadStatus()
  }, [loadStatus])
  useEffect(() => {
    if (status?.authorized) loadPeople()
  }, [status, loadPeople])

  const me = people.find((p) => p.id === pid)
  const others = people.filter((p) => p.id !== pid)

  async function submit(rec: Recording) {
    if (!pid) return
    setBusy(true)
    setError(null)
    try {
      const r = await post<{ id: number; diagnostics: Diagnostics; progress: Participant[] }>('/lab/recordings', {
        participant_id: pid,
        kind,
        target_id: kind === 'imitation' ? target : null,
        session: kind === 'genuine' ? session : null,
        posture: kind === 'enroll' ? null : posture,
        device_label: deviceLabel || null,
        samples: rec.samples,
        device: rec.device,
      })
      setLast({ id: r.id, d: r.diagnostics })
      setPeople(r.progress)
    } catch (e) {
      setError(errorText(e))
    } finally {
      setBusy(false)
    }
  }

  async function undo() {
    if (!last) return
    const r = await del<{ progress: Participant[] }>(`/lab/recordings/${last.id}`)
    setPeople(r.progress)
    setLast(null)
  }

  let body
  if (!status) body = <Spinner />
  else if (!status.enabled) body = <div className="card">The recording lab is turned off on this server. Set AIRSIGN_LAB_CODE to enable it.</div>
  else if (!status.authorized) body = <Login onDone={loadStatus} />
  else
    body = (
      <>
        <div className="card">
          <h1>Recording lab</h1>
          <p className="small muted">
            Protocol per person: 7 enrollment takes in one sitting, 15 genuine takes over two sessions on different days
            (standing and sitting), and 10 imitations of someone else after watching them sign. Real phones only, no
            simulation here.
          </p>
          <label className="field">
            <span>This phone (model)</span>
            <input
              type="text"
              placeholder="e.g. Pixel 7, iPhone 13"
              value={deviceLabel}
              onChange={(e) => {
                setDeviceLabel(e.target.value)
                writeLocal('airsign-lab-device', e.target.value)
              }}
            />
          </label>
          <label className="field">
            <span>Who is recording?</span>
            <select value={pid} onChange={(e) => { setPid(e.target.value); setLast(null) }}>
              <option value="">Choose a participant</option>
              {people.map((p) => (
                <option key={p.id} value={p.id}>
                  {p.id} · {p.alias}
                </option>
              ))}
            </select>
          </label>
          <details>
            <summary>Add a participant</summary>
            <div className="spacer" />
            <AddParticipant
              onAdded={async (id) => {
                await loadPeople()
                setPid(id)
              }}
            />
          </details>
        </div>

        {me && (
          <div className="card">
            <h2>
              {me.id} · {me.alias}
            </h2>
            <Progress p={me} />
            <hr className="sep" />
            <div className="grid2" style={{ gridTemplateColumns: 'repeat(3, 1fr)' }}>
              {(Object.keys(KIND_LABEL) as Kind[]).map((k) => (
                <button key={k} className={`btn small ${kind === k ? '' : 'quiet'}`} onClick={() => { setKind(k); setLast(null) }}>
                  {KIND_LABEL[k]}
                </button>
              ))}
            </div>
            <div className="spacer" />
            {kind === 'enroll' && <p className="small muted">Choose your signature and repeat it the same way each time, in your usual posture.</p>}
            {kind !== 'enroll' && (
              <div className="grid2">
                {kind === 'genuine' ? (
                  <label className="field">
                    <span>Session</span>
                    <select value={session} onChange={(e) => setSession(e.target.value)}>
                      <option value="A">A (first day)</option>
                      <option value="B">B (another day)</option>
                    </select>
                  </label>
                ) : (
                  <label className="field">
                    <span>Imitating</span>
                    <select value={target} onChange={(e) => setTarget(e.target.value)}>
                      <option value="">Choose</option>
                      {others.map((p) => (
                        <option key={p.id} value={p.id}>
                          {p.id} · {p.alias}
                        </option>
                      ))}
                    </select>
                  </label>
                )}
                <label className="field">
                  <span>Posture</span>
                  <select value={posture} onChange={(e) => setPosture(e.target.value as Posture)}>
                    <option value="standing">Standing</option>
                    <option value="sitting">Sitting</option>
                    <option value="walking">Walking</option>
                  </select>
                </label>
              </div>
            )}
            {kind === 'imitation' && <p className="small muted">Watch the person sign two or three times first, then try to copy it.</p>}
            {kind === 'imitation' && !target ? (
              <div className="notice">Choose whose signature you are imitating.</div>
            ) : (
              <SignPad motion={motion} busy={busy} onRecording={submit} idleLabel={`Press and hold to record (${KIND_LABEL[kind].toLowerCase()})`} />
            )}
            {last && <div style={{ marginTop: 12 }}><DiagnosticsCard d={last.d} onUndo={undo} /></div>}
            {error && <div className="notice bad">{error}</div>}
          </div>
        )}

        {people.length > 0 && (
          <div className="card">
            <h2>Everyone</h2>
            <table className="table">
              <thead>
                <tr>
                  <th>Who</th>
                  <th>Enroll</th>
                  <th>Genuine</th>
                  <th>Imitation</th>
                </tr>
              </thead>
              <tbody>
                {people.map((p) => (
                  <tr key={p.id}>
                    <td>
                      {p.id} · {p.alias}
                    </td>
                    <td>{p.counts.enroll}/7</td>
                    <td>{p.counts.genuine}/15</td>
                    <td>{p.counts.imitation}/10</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}

        <Results />
      </>
    )

  return (
    <div className="app">
      <TopBar />
      {body}
    </div>
  )
}
