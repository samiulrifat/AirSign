import { useCallback, useEffect, useRef, useState } from 'react'
import { Link, Navigate, useLocation, useNavigate, useParams } from 'react-router-dom'
import {
  ApiError,
  get,
  post,
  type Building,
  type Intent,
  type Invoice,
  type Nearby,
  type Recording,
  type Shop,
  type SignoffResult,
  type SimKind,
} from '../lib/api'
import { errorText, floorLabel, money, useApp } from '../lib/app'
import { prepareApproval, type ApprovalSession } from '../lib/paypal'
import { CheckIcon, SimBadge, Spinner, TierBadge, TopBar } from '../components/bits'
import type { BillStart } from '../components/BillAlerts'
import { AirSignPad, useMotion } from '../components/AirSignPad'

type Loc = { lat: number; lon: number; accuracy: number; altitude?: number | null; altitude_accuracy?: number | null }
type Found = { shop: Shop; how: 'popup' | 'list' | 'code' | 'qr'; checkinPass?: string; invoice?: Invoice | null }

export type CheckinState = { shop: Shop; invoice: Invoice | null; checkin_pass: string }

function locate(): Promise<Loc> {
  return new Promise((resolve, reject) => {
    if (!navigator.geolocation) return reject(new Error('This browser cannot share location.'))
    navigator.geolocation.getCurrentPosition(
      (p) =>
        resolve({
          lat: p.coords.latitude,
          lon: p.coords.longitude,
          accuracy: p.coords.accuracy,
          altitude: p.coords.altitude,
          altitude_accuracy: p.coords.altitudeAccuracy,
        }),
      (e) => reject(new Error(e.code === e.PERMISSION_DENIED ? 'Location permission was denied.' : 'Could not get your location.')),
      { enableHighAccuracy: true, timeout: 12000, maximumAge: 0 },
    )
  })
}

// Step 1: where am I? -------------------------------------------------------------

function FindShop({ onFound, onLocation }: { onFound: (f: Found) => void; onLocation: (l: Loc | null, simulated: boolean) => void }) {
  const { config } = useApp()
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [nearby, setNearby] = useState<Extract<Nearby, { status: 'ok' }> | null>(null)
  const [buildings, setBuildings] = useState<Building[]>([])
  const [listFor, setListFor] = useState<string | null>(null)
  const [code, setCode] = useState('')
  const [lastLoc, setLastLoc] = useState<Loc | null>(null)
  const [noneHere, setNoneHere] = useState(false)

  useEffect(() => {
    get<Building[]>('/buildings').then(setBuildings).catch(() => {})
  }, [])

  // Location already allowed for this site: find the shop straight away instead of
  // waiting for a tap. Without permission we wait, so the browser never asks unprompted.
  useEffect(() => {
    let cancelled = false
    navigator.permissions
      ?.query({ name: 'geolocation' as PermissionName })
      .then((s) => {
        if (!cancelled && s.state === 'granted') findMe()
      })
      .catch(() => {})
    return () => {
      cancelled = true
    }
  }, [])

  async function search(loc: Loc, simulated: boolean) {
    onLocation(loc, simulated)
    const q = new URLSearchParams({ lat: String(loc.lat), lon: String(loc.lon), accuracy: String(Math.max(1, loc.accuracy)) })
    if (loc.altitude != null && loc.altitude_accuracy != null) {
      q.set('altitude', String(loc.altitude))
      q.set('altitude_accuracy', String(loc.altitude_accuracy))
    }
    const r = await get<Nearby>(`/shops/nearby?${q}`)
    if (r.status === 'ok') {
      setNearby(r)
      if (!r.suggestion) setListFor(r.building.id)
    } else {
      setError(r.message)
      setNoneHere(r.status === 'none')
    }
  }

  async function findMe() {
    setBusy(true)
    setError(null)
    setNoneHere(false)
    try {
      const loc = await locate()
      setLastLoc(loc)
      await search(loc, false)
    } catch (e) {
      setError(errorText(e))
    } finally {
      setBusy(false)
    }
  }

  async function simulateIn(b: Building) {
    setBusy(true)
    setError(null)
    try {
      await search({ lat: b.lat, lon: b.lon, accuracy: 25 }, true)
    } catch (e) {
      setError(errorText(e))
    } finally {
      setBusy(false)
    }
  }

  async function placeHere(b: Building) {
    if (!lastLoc) return
    setBusy(true)
    try {
      await post('/sim/move-building', { building_id: b.id, lat: lastLoc.lat, lon: lastLoc.lon })
      setError(null)
      setNoneHere(false)
      await search(lastLoc, false)
    } catch (e) {
      setError(errorText(e))
    } finally {
      setBusy(false)
    }
  }

  async function useCode(e: React.FormEvent) {
    e.preventDefault()
    setBusy(true)
    setError(null)
    try {
      const r = await post<CheckinState>('/checkin', { code })
      onLocation(null, false)
      onFound({ shop: r.shop, how: 'code', checkinPass: r.checkin_pass, invoice: r.invoice })
    } catch (err) {
      setError(errorText(err))
    } finally {
      setBusy(false)
    }
  }

  const suggested = nearby?.shops.find((s) => s.id === nearby.suggestion)
  const listBuilding = buildings.find((b) => b.id === listFor)
  const listShops = nearby && nearby.building.id === listFor ? nearby.shops : listBuilding?.shops ?? []

  if (suggested && !listFor) {
    return (
      <div className="card popup">
        <p className="tiny">{nearby!.building.name}</p>
        <h2>Are you at {suggested.name}?</h2>
        <p className="muted">
          {floorLabel(suggested.floor)} · {Math.round((suggested.probability ?? 0) * 100)}% likely
        </p>
        <div className="grid2">
          <button className="btn ghost" onClick={() => setListFor(nearby!.building.id)}>
            No
          </button>
          <button className="btn" onClick={() => onFound({ shop: suggested, how: 'popup' })}>
            Yes
          </button>
        </div>
      </div>
    )
  }

  if (listFor) {
    return (
      <div className="card">
        <h2>Which shop are you in?</h2>
        <p className="muted small">{nearby?.building.name ?? listBuilding?.name}, every shop by floor</p>
        <ul className="list">
          {[...listShops]
            .sort((a, b) => b.floor - a.floor)
            .map((s) => (
              <li key={s.id}>
                <button className="item" onClick={() => onFound({ shop: s, how: 'list' })}>
                  <span className="row">
                    <span className="floor">{s.floor}F</span>
                    <span>
                      {s.name}
                      <br />
                      <span className="tiny">{s.open_now === false ? 'Closed now' : s.category}</span>
                    </span>
                  </span>
                  <span aria-hidden="true">›</span>
                </button>
              </li>
            ))}
        </ul>
        <button className="btn ghost small" onClick={() => { setListFor(null); setNearby(null) }}>
          Back
        </button>
      </div>
    )
  }

  return (
    <>
      <div className="card">
        <h1>Pay in a shop</h1>
        <p className="muted">AirSign uses your location once to find the building, then suggests the shop.</p>
        {error && <div className="notice warn">{error}</div>}
        <button className="btn block" disabled={busy} onClick={findMe}>
          {busy ? <Spinner /> : 'Find my shop'}
        </button>
        {noneHere && config?.allow_simulation && lastLoc && (
          <div className="sim-strip stack" style={{ marginTop: 12 }}>
            <h3>Simulation</h3>
            <p className="small muted">The demo shops are somewhere else. Move a demo building to where you are standing?</p>
            {buildings.map((b) => (
              <button key={b.id} className="btn sim small block" disabled={busy} onClick={() => placeHere(b)}>
                Place {b.name} here
              </button>
            ))}
          </div>
        )}
        <hr className="sep" />
        <form onSubmit={useCode} className="stack">
          <label className="field" style={{ marginBottom: 0 }}>
            <span>Or type the 6-digit code on the cashier's screen</span>
            <input inputMode="numeric" pattern="[0-9]{6}" maxLength={6} value={code} onChange={(e) => setCode(e.target.value.replace(/\D/g, ''))} placeholder="123456" />
          </label>
          <button className="btn quiet block" disabled={code.length !== 6 || busy}>
            Use code
          </button>
        </form>
        <hr className="sep" />
        <p className="small muted">Or choose the building yourself:</p>
        <div className="stack">
          {buildings.map((b) => (
            <button key={b.id} className="btn ghost block" onClick={() => { onLocation(null, false); setListFor(b.id) }}>
              {b.name}
            </button>
          ))}
        </div>
      </div>
      {config?.allow_simulation && (
        <div className="sim-strip stack">
          <h3>Simulated location</h3>
          <p className="small muted">For testing away from the shops: pretend your phone reports a position inside a building.</p>
          {buildings.map((b) => (
            <button key={b.id} className="btn sim block" disabled={busy} onClick={() => simulateIn(b)}>
              I am in {b.name}
            </button>
          ))}
        </div>
      )}
    </>
  )
}

// Step 2: which invoice? ------------------------------------------------------------

function PickInvoice({ found, onPick, onBack, autoPick }: { found: Found; onPick: (inv: Invoice) => void; onBack: () => void; autoPick: boolean }) {
  const [invoices, setInvoices] = useState<Invoice[] | null>(null)

  // Exactly one open bill at this shop: that is the customer's, no need to ask.
  useEffect(() => {
    if (autoPick && invoices?.length === 1) onPick(invoices[0])
  }, [autoPick, invoices, onPick])

  useEffect(() => {
    let stop = false
    const load = async () => {
      try {
        const r = await get<{ invoices: Invoice[] }>(`/shops/${found.shop.id}/invoices`)
        if (!stop) setInvoices(r.invoices)
      } catch {
        if (!stop) setInvoices([])
      }
    }
    load()
    const t = setInterval(load, 3000)
    return () => {
      stop = true
      clearInterval(t)
    }
  }, [found.shop.id])

  return (
    <div className="card">
      <p className="tiny">
        {found.shop.name} · {floorLabel(found.shop.floor)}
      </p>
      <h2>{invoices && invoices.length > 1 ? 'Which one is yours?' : 'Your bill'}</h2>
      {invoices === null ? (
        <Spinner />
      ) : invoices.length === 0 ? (
        <div className="notice">
          <span className="row">
            <Spinner /> Waiting for the cashier to create your bill…
          </span>
        </div>
      ) : (
        <ul className="list">
          {invoices.map((inv) => (
            <li key={inv.id}>
              <button className="item" onClick={() => onPick(inv)}>
                <span>
                  <strong>{money(inv.amount_cents, inv.currency)}</strong>
                  <br />
                  <span className="tiny">
                    {inv.items.length ? inv.items.map((i) => `${i.qty}× ${i.name}`).join(', ') : 'No item list'} ·{' '}
                    {new Date(inv.created_at).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}
                  </span>
                </span>
                <span aria-hidden="true">›</span>
              </button>
            </li>
          ))}
        </ul>
      )}
      <button className="btn ghost small" onClick={onBack} style={{ marginTop: 8 }}>
        Wrong shop
      </button>
    </div>
  )
}

// Shared: what is being paid, always from the server record ------------------------

function Summary({ intent }: { intent: Intent }) {
  return (
    <div className="card">
      <div className="row between">
        <span className="tiny">
          {intent.shop.name} · {floorLabel(intent.shop.floor)}
        </span>
        <span className="row" style={{ gap: 6 }}>
          {intent.funding === 'wallet' && <span className="badge accent">Saved PayPal</span>}
          {intent.location_verified && <span className="badge ok">At the counter</span>}
          {intent.simulated && <SimBadge />}
        </span>
      </div>
      <div className="amount">{money(intent.amount_cents, intent.currency)}</div>
      {intent.invoice.items.length > 0 && (
        <p className="small muted" style={{ marginBottom: 0 }}>
          {intent.invoice.items.map((i) => `${i.qty}× ${i.name}`).join(', ')}
        </p>
      )}
    </div>
  )
}

// Step 3: PayPal approval -------------------------------------------------------------

function Approve({ intent, onApproved, onCancel }: { intent: Intent; onApproved: (nonce: string, i: Intent) => void; onCancel: () => void }) {
  const { config } = useApp()
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [session, setSession] = useState<ApprovalSession | null>(null)
  const [sdkState, setSdkState] = useState<'loading' | 'ready' | 'ineligible' | 'unavailable'>('loading')
  const paypalButton = useRef<HTMLElement | null>(null)

  const confirm = useCallback(async () => {
    setBusy(true)
    setError(null)
    try {
      const r = await post<{ nonce: string; intent: Intent }>(`/intents/${intent.id}/approved`)
      onApproved(r.nonce, r.intent)
    } catch (e) {
      setError(errorText(e))
      setBusy(false)
    }
  }, [intent.id, onApproved])

  useEffect(() => {
    if (config?.paypal_mode !== 'sandbox' || !config.paypal_client_id) return
    let alive = true
    prepareApproval(
      config.paypal_client_id,
      { currencyCode: intent.currency, amount: (intent.amount_cents / 100).toFixed(2) },
      {
        onApprove: () => confirm(),
        onCancel: () => setError('You closed PayPal before approving.'),
        onError: (e) => {
          const code = (e as { code?: string })?.code
          setError(
            code === 'ERR_DEV_UNABLE_TO_OPEN_POPUP'
              ? 'Your browser blocked the PayPal window. Allow pop-ups for this site and try again.'
              : `PayPal reported an error${code ? ` (${code})` : ''}. Please try again.`,
          )
        },
      },
    )
      .then((s) => {
        if (!alive) return
        setSession(s)
        setSdkState(s.eligible ? 'ready' : 'ineligible')
      })
      .catch(() => alive && setSdkState('unavailable'))
    return () => {
      alive = false
    }
  }, [config, confirm, intent.currency, intent.amount_cents])

  // <paypal-button> is a web component, so the click is wired directly; start()
  // must run inside the click for the browser to allow the PayPal window.
  const orderId = intent.paypal.order_id
  useEffect(() => {
    const el = paypalButton.current
    if (!el || !session?.eligible || !orderId) return
    const onClick = () => {
      setError(null)
      session.open(orderId).catch(() => setError('Could not open PayPal. Please try again.'))
    }
    el.addEventListener('click', onClick)
    return () => el.removeEventListener('click', onClick)
  }, [session, orderId, busy])

  async function mockApprove() {
    setBusy(true)
    setError(null)
    try {
      await post(`/mock-paypal/orders/${intent.paypal.order_id}/approve`)
      await confirm()
    } catch (e) {
      setError(errorText(e))
      setBusy(false)
    }
  }

  return (
    <div className="card">
      <h2>Approve with PayPal</h2>
      <p className="muted small">
        PayPal holds the approval. Nothing is charged until you sign in the air on the next screen.
      </p>
      {error && <div className="notice warn">{error}</div>}
      {config?.paypal_mode === 'sandbox' ? (
        sdkState === 'ready' && session ? (
          busy ? (
            <div className="row">
              <Spinner /> Confirming your approval with PayPal…
            </div>
          ) : (
            <paypal-button ref={paypalButton} type="pay" className="paypal-gold"></paypal-button>
          )
        ) : sdkState === 'loading' ? (
          <Spinner />
        ) : sdkState === 'ineligible' ? (
          <div className="notice bad">PayPal is not available for this payment in your region or browser.</div>
        ) : (
          <div className="notice bad">The PayPal SDK could not load.</div>
        )
      ) : (
        <div className="sim-strip stack">
          <h3>Mock PayPal approval</h3>
          <p className="small muted">
            PayPal sandbox credentials are not configured on this server, so a local stand-in plays PayPal's part. Order{' '}
            <code>{intent.paypal.order_id}</code>.
          </p>
          <button className="btn sim block" disabled={busy} onClick={mockApprove}>
            {busy ? <Spinner /> : 'Approve order (mock)'}
          </button>
        </div>
      )}
      <button className="btn ghost small" style={{ marginTop: 12 }} onClick={onCancel} disabled={busy}>
        Cancel payment
      </button>
    </div>
  )
}

// Step 4: sign in the air -------------------------------------------------------------

const SIM_BUTTONS: { kind: SimKind | 'replay'; label: string }[] = [
  { kind: 'genuine', label: 'Me, signing normally' },
  { kind: 'imitation', label: 'Someone who watched me' },
  { kind: 'impostor', label: 'A different person' },
  { kind: 'injection', label: 'Generated sensor data' },
  { kind: 'replay', label: 'Replay my last good signature' },
]

function MatchMeter({ z, k }: { z: number; k: number }) {
  // Show distance to the threshold: left is a close match, the line is the threshold.
  const max = Math.max(k * 2.5, 5)
  const pos = Math.min(1, Math.max(0, (z + 2) / (max + 2)))
  const th = (k + 2) / (max + 2)
  const ok = z <= k
  return (
    <div style={{ marginTop: 10 }}>
      <div className="meter">
        <i style={{ width: `${pos * 100}%`, background: ok ? 'var(--ok)' : 'var(--bad)' }} />
        <b style={{ left: `${th * 100}%` }} />
      </div>
      <p className="tiny" style={{ marginTop: 4 }}>
        Distance from your enrolled signature: {z.toFixed(2)} (threshold {k.toFixed(1)}, in units of your own natural variation)
      </p>
    </div>
  )
}

function AiChecks({ result }: { result: SignoffResult }) {
  const m = result.match
  if (!m) return null
  const rows: [string, boolean][] = [
    ...(m.z_nn !== null ? ([['Neural network match (Siamese CNN)', m.z_nn <= m.k]] as [string, boolean][]) : []),
    ['Motion shape matches (DTW)', m.z_dtw <= m.k],
    ['Real sensor data', !result.sensor_flags?.length],
    ['Live, not a replay', !result.replay_flags?.length],
  ]
  return (
    <ul className="ai-checks">
      {rows.map(([label, ok]) => (
        <li key={label} className={ok ? 'ok' : 'bad'}>
          <span aria-hidden="true">{ok ? '✓' : '✕'}</span> {label}
        </li>
      ))}
    </ul>
  )
}

// The last accepted recording this tab submitted, kept in memory only (never in storage) so
// the demo can replay an earlier payment's signature against a new payment.
let lastRecording: Recording | null = null

function Sign({ intent, nonce, onUpdate, onResult, onCancel }: { intent: Intent; nonce: string; onUpdate: (i: Intent, nonce: string | null) => void; onResult: (r: SignoffResult) => void; onCancel: () => void }) {
  const { config } = useApp()
  const motion = useMotion()
  const [busy, setBusy] = useState(false)
  const [result, setResult] = useState<SignoffResult | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [canReplay, setCanReplay] = useState(lastRecording !== null)
  const [preview, setPreview] = useState<{ rows: number[][]; gravity: boolean; key: number } | null>(null)
  const nonceRef = useRef(nonce)
  nonceRef.current = nonce

  async function submit(rec: Recording, remember = true) {
    setBusy(true)
    setError(null)
    const send = (n: string) => post<SignoffResult>(`/intents/${intent.id}/signoff`, { nonce: n, ...rec })
    try {
      let r: SignoffResult
      try {
        r = await send(nonceRef.current)
      } catch (e) {
        if (!(e instanceof ApiError && e.code === 'nonce_expired')) throw e
        const fresh = await post<{ nonce: string }>(`/intents/${intent.id}/nonce`)
        r = await send(fresh.nonce)
      }
      if (remember && (r.decision === 'ACCEPT' || r.decision === 'STEP_UP')) {
        lastRecording = rec
        setCanReplay(true)
      }
      setResult(r)
      onResult(r)
      onUpdate(r.intent, r.nonce)
    } catch (e) {
      setError(errorText(e))
    } finally {
      setBusy(false)
    }
  }

  async function simulate(kind: SimKind | 'replay') {
    if (kind === 'replay') {
      if (!lastRecording) return
      setPreview({ rows: lastRecording.samples, gravity: lastRecording.device.accel_includes_gravity, key: Date.now() })
      await submit(lastRecording, false)
      return
    }
    setBusy(true)
    try {
      const g = await get<Recording>(`/sim/gesture?kind=${kind}`)
      setPreview({ rows: g.samples, gravity: false, key: Date.now() })
      await submit({ samples: g.samples, device: g.device, simulated: true })
    } catch (e) {
      setError(errorText(e))
      setBusy(false)
    }
  }

  const risk = intent.risk
  const decisionClass = result
    ? result.decision === 'ACCEPT' || result.decision === 'STEP_UP'
      ? 'ok'
      : result.decision === 'UNUSABLE'
        ? 'warn'
        : 'bad'
    : ''

  return (
    <>
      {risk && (
        <div className="card flat">
          <div className="row between wrap">
            <h3 style={{ margin: 0 }}>Security check</h3>
            <span className="row" style={{ gap: 6 }}>
              {risk.explanation_source === 'gemini' && <span className="badge accent">Worded by Gemini</span>}
              <TierBadge tier={risk.tier} />
            </span>
          </div>
          <p className="small muted" style={{ margin: '8px 0 0' }}>
            {risk.explanation}
            {risk.history_is_synthetic && ' (compared with your synthetic demo history)'}
          </p>
        </div>
      )}
      <div className="card">
        <div className="row between">
          <h2>Sign in the air</h2>
          <span className="tiny">
            {intent.signatures_required > 1 && `Signature ${Math.min(intent.signatures_passed + 1, intent.signatures_required)} of ${intent.signatures_required} · `}
            {intent.attempts_left} {intent.attempts_left === 1 ? 'try' : 'tries'} left
          </span>
        </div>
        <AirSignPad
          motion={motion}
          busy={busy}
          onRecording={(r) => submit(r)}
          readyLabel={`Ready. Sign to pay ${money(intent.amount_cents, intent.currency)}`}
          preview={preview}
        />
        {result && (
          <div className={`notice ${decisionClass}`} style={{ marginTop: 12 }}>
            {result.reason}
            {result.flags.length > 0 && <div className="tiny">Flags: {result.flags.join(', ')}</div>}
            <AiChecks result={result} />
            {result.match && <MatchMeter z={result.match.z} k={result.match.k} />}
          </div>
        )}
        {error && <div className="notice bad">{error}</div>}
        <button className="btn ghost small" style={{ marginTop: 12 }} onClick={onCancel} disabled={busy}>
          Not my bill, cancel
        </button>
      </div>
      {config?.allow_simulation && (
        <div className="sim-strip stack">
          <h3>Simulated signer</h3>
          <p className="small muted">Labeled synthetic recordings. They go through the same verifier and replay checks.</p>
          <div className="grid2">
            {SIM_BUTTONS.map((b) => (
              <button key={b.kind} className="btn sim small" disabled={busy || (b.kind === 'replay' && !canReplay)} onClick={() => simulate(b.kind)}>
                {b.label}
              </button>
            ))}
          </div>
        </div>
      )}
    </>
  )
}

// Outcome ----------------------------------------------------------------------------

function Outcome({ intent, result }: { intent: Intent; result: SignoffResult | null }) {
  const [fresh, setFresh] = useState(intent)
  useEffect(() => {
    setFresh(intent)
    if (intent.state !== 'COMPLETED' || intent.paypal.webhook_confirmed) return
    // The receipt shows at once from the capture response; the webhook confirms it shortly after.
    const t = setInterval(async () => {
      const i = await get<Intent>(`/intents/${intent.id}`).catch(() => null)
      if (i) setFresh(i)
      if (i?.paypal.webhook_confirmed) clearInterval(t)
    }, 1500)
    return () => clearInterval(t)
  }, [intent])

  if (fresh.state === 'COMPLETED') {
    const last = fresh.attempts[fresh.attempts.length - 1]
    return (
      <div className="card center">
        <CheckIcon />
        <h1>Paid</h1>
        <div className="amount">{money(fresh.amount_cents, fresh.currency)}</div>
        <p className="muted">
          {fresh.shop.name}, {floorLabel(fresh.shop.floor)}
        </p>
        <div className="row wrap" style={{ justifyContent: 'center', marginBottom: 12 }}>
          <span className="badge accent">PayPal {fresh.paypal.mode}</span>
          {fresh.funding === 'wallet' && <span className="badge accent">Saved PayPal</span>}
          {fresh.paypal.webhook_confirmed ? <span className="badge ok">Webhook confirmed</span> : <span className="badge warn">Awaiting webhook</span>}
          {fresh.simulated && <SimBadge />}
        </div>
        <p className="tiny">
          Capture {fresh.paypal.capture_id} · order {fresh.paypal.order_id}
          {last?.latency_ms != null && ` · verified in ${Math.round(last.latency_ms)} ms`}
        </p>
        {result?.match && (
          <div className="notice" style={{ textAlign: 'left', marginBottom: 12 }}>
            <strong className="small">What the AI checked</strong>
            <AiChecks result={result} />
            <MatchMeter z={result.match.z} k={result.match.k} />
          </div>
        )}
        <Link to="/" className="btn block">
          Done
        </Link>
      </div>
    )
  }
  const text: Record<string, string> = {
    LOCKED: 'This payment is locked. Your account is fine; ask the cashier to start a new bill.',
    FAILED: fresh.failure_reason ?? 'PayPal could not complete the payment.',
    EXPIRED: 'This payment expired before it was signed.',
    CANCELLED: 'This payment was cancelled.',
  }
  return (
    <div className="card center">
      <CheckIcon bad />
      <h1>{fresh.state === 'LOCKED' ? 'Payment locked' : 'Not paid'}</h1>
      <p className="muted">{text[fresh.state] ?? fresh.state}</p>
      {fresh.attempts.length > 0 && (
        <p className="tiny">Last check: {fresh.attempts[fresh.attempts.length - 1].reason}</p>
      )}
      <Link to="/" className="btn block">
        Home
      </Link>
    </div>
  )
}

// Page --------------------------------------------------------------------------------

export default function Pay() {
  const { me } = useApp()
  const { id } = useParams()
  const navigate = useNavigate()
  const routerState = useLocation().state as { checkin?: CheckinState; bill?: BillStart } | null
  const bill = routerState?.bill
  const [found, setFound] = useState<Found | null>(
    routerState?.checkin
      ? { shop: routerState.checkin.shop, how: 'qr', checkinPass: routerState.checkin.checkin_pass, invoice: routerState.checkin.invoice }
      : bill
        ? { shop: bill.shop, how: 'popup', invoice: bill.invoice }
        : null,
  )
  const [invoice, setInvoice] = useState<Invoice | null>(routerState?.checkin?.invoice ?? bill?.invoice ?? null)
  const [loc, setLoc] = useState<{ loc: Loc | null; simulated: boolean }>({ loc: bill?.location ?? null, simulated: bill?.simulated ?? false })
  const [intent, setIntent] = useState<Intent | null>(null)
  const [nonce, setNonce] = useState<string | null>(null)
  const [lastResult, setLastResult] = useState<SignoffResult | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  // After the customer has gone back once, stop choosing the bill for them.
  const [pickedManually, setPickedManually] = useState(false)

  // Resume an existing payment from its id.
  useEffect(() => {
    if (!id) return
    ;(async () => {
      try {
        const i = await get<Intent>(`/intents/${id}`)
        setIntent(i)
        if (i.state === 'ARMED') setNonce((await post<{ nonce: string }>(`/intents/${id}/nonce`)).nonce)
      } catch (e) {
        setError(errorText(e))
      }
    })()
  }, [id])

  useEffect(() => {
    if (found?.invoice) setInvoice(found.invoice)
  }, [found])

  // Once the shop and bill are known, go straight to PayPal: no extra confirm step,
  // the amount is shown again next to the PayPal button.
  const starting = useRef(false)
  useEffect(() => {
    if (found && invoice && !intent && !id && !starting.current && !error) {
      starting.current = true
      startIntent().finally(() => (starting.current = false))
    }
  }, [found, invoice, intent, id, error])

  if (me && !me.user) return <Navigate to="/" replace />
  if (me?.user && !me.signature && !id) return <Navigate to="/enroll" replace />

  async function startIntent() {
    if (!found || !invoice) return
    setBusy(true)
    setError(null)
    try {
      // A saved PayPal account means the payment comes back ready to sign, with its nonce.
      const i = await post<Intent>('/intents', {
        invoice_id: invoice.id,
        confirmed_by: found.how,
        location: loc.loc ? { lat: loc.loc.lat, lon: loc.loc.lon, accuracy: loc.loc.accuracy } : null,
        simulated_location: loc.simulated,
        checkin_pass: found.checkinPass ?? null,
      })
      setIntent(i)
      if (i.nonce) setNonce(i.nonce)
      navigate(`/pay/${i.id}`, { replace: true })
    } catch (e) {
      setError(errorText(e))
    } finally {
      setBusy(false)
    }
  }

  async function cancel() {
    if (intent) await post(`/intents/${intent.id}/cancel`).catch(() => {})
    navigate('/pay', { replace: true })
    setIntent(null)
    setFound(null)
    setInvoice(null)
  }

  let body
  if (intent) {
    if (intent.state === 'PROPOSED') {
      body = (
        <>
          <Summary intent={intent} />
          <Approve
            intent={intent}
            onApproved={(n, i) => {
              setNonce(n)
              setIntent(i)
            }}
            onCancel={cancel}
          />
        </>
      )
    } else if (intent.state === 'ARMED' && nonce) {
      body = (
        <>
          <Summary intent={intent} />
          <Sign
            intent={intent}
            nonce={nonce}
            onUpdate={(i, n) => {
              setIntent(i)
              if (n) setNonce(n)
            }}
            onResult={setLastResult}
            onCancel={cancel}
          />
        </>
      )
    } else if (['COMPLETED', 'LOCKED', 'FAILED', 'EXPIRED', 'CANCELLED'].includes(intent.state)) {
      body = <Outcome intent={intent} result={lastResult} />
    } else {
      body = (
        <div className="card">
          <Spinner /> {intent.state.toLowerCase()}…
        </div>
      )
    }
  } else if (id) {
    body = error ? <div className="notice bad">{error}</div> : <Spinner />
  } else if (!found) {
    body = <FindShop onFound={setFound} onLocation={(l, s) => setLoc({ loc: l, simulated: s })} />
  } else if (!invoice) {
    body = (
      <PickInvoice
        found={found}
        autoPick={!pickedManually}
        onPick={setInvoice}
        onBack={() => {
          setFound(null)
          setPickedManually(true)
        }}
      />
    )
  } else {
    body = (
      <div className="card">
        <p className="tiny">
          {found.shop.name} · {floorLabel(found.shop.floor)}
        </p>
        <div className="amount">{money(invoice.amount_cents, invoice.currency)}</div>
        {error ? (
          <>
            <div className="notice bad">{error}</div>
            <button className="btn block" disabled={busy} onClick={() => setError(null)}>
              Try again
            </button>
          </>
        ) : (
          <div className="row">
            <Spinner /> Getting your payment ready…
          </div>
        )}
        <button
          className="btn ghost small"
          style={{ marginTop: 8 }}
          onClick={() => {
            setInvoice(null)
            setError(null)
            setPickedManually(true)
          }}
        >
          Not my bill
        </button>
      </div>
    )
  }

  return (
    <div className="app">
      <TopBar />
      {body}
    </div>
  )
}
