import { useCallback, useEffect, useRef, useState } from 'react'
import { Link, Navigate, useLocation, useNavigate, useParams } from 'react-router-dom'
import {
  ApiError,
  get,
  post,
  type Intent,
  type Invoice,
  type Recording,
  type Shop,
  type SignoffResult,
  type SimKind,
} from '../lib/api'
import { errorText, floorLabel, money, useApp } from '../lib/app'
import { prepareApproval, type ApprovalSession } from '../lib/paypal'
import { CheckIcon, DemoControls, SimBadge, Spinner, Stepper, TierBadge, TopBar } from '../components/bits'
import { computeInk } from '../lib/ink'
import { setSpokenCues } from '../lib/feedback'
import { prompt } from '../lib/speech'
import type { BillStart } from '../components/BillAlerts'
import { AirSignPad, InkCanvas, useMotion } from '../components/AirSignPad'

type Loc = { lat: number; lon: number; accuracy: number; altitude?: number | null; altitude_accuracy?: number | null }
// How this bill reached the customer: sent by name, opened from "everyone nearby" (popup),
// or claimed with its own QR or code. There is no way to pick a bill off a shop's list.
type Found = { shop: Shop; how: 'popup' | 'code' | 'qr' | 'sent'; checkinPass?: string; invoice?: Invoice | null }

export type CheckinState = { shop: Shop; invoice: Invoice | null; checkin_pass: string }

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
      <p className="muted small">Nothing is charged until you sign in the air.</p>
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
          <h3>Mock PayPal</h3>
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
        {ok ? 'Within your natural variation' : 'Outside your natural variation'}: score {z.toFixed(2)}, limit {k.toFixed(1)}
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

  // The phone is moving and hard to read while signing, so the steps are also spoken.
  useEffect(() => {
    prompt(`${intent.shop.name}, ${money(intent.amount_cents, intent.currency)}. Hold your phone still.`)
    setSpokenCues(true)
    return () => setSpokenCues(false)
  }, [intent.id, intent.shop.name, intent.amount_cents, intent.currency])

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
      if (r.state !== 'COMPLETED') prompt(r.reason)
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
      {risk && risk.tier > 1 && (
        <div className="notice warn">
          <div className="row between wrap" style={{ marginBottom: 4 }}>
            <TierBadge tier={risk.tier} />
            {risk.explanation_source === 'gemini' && <span className="badge accent">Worded by Gemini</span>}
          </div>
          {risk.explanation}
          {risk.history_is_synthetic && <div className="tiny">Compared with your made-up demo history.</div>}
        </div>
      )}
      <div className="sign-area">
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
        <DemoControls>
          <p className="small muted">Sign as someone else with labeled synthetic recordings. They go through the same checks.</p>
          <div className="grid2">
            {SIM_BUTTONS.map((b) => (
              <button key={b.kind} className="btn sim small" disabled={busy || (b.kind === 'replay' && !canReplay)} onClick={() => simulate(b.kind)}>
                {b.label}
              </button>
            ))}
          </div>
        </DemoControls>
      )}
    </>
  )
}

// Outcome ----------------------------------------------------------------------------

function Outcome({ intent, result }: { intent: Intent; result: SignoffResult | null }) {
  const [fresh, setFresh] = useState(intent)
  // Shown only right after signing, from the recording still in memory. It is never stored.
  const [ink] = useState(() => (result && lastRecording ? computeInk(lastRecording.samples, lastRecording.device.accel_includes_gravity) : null))
  // Only right after signing, not when opening an old payment from the history.
  const paidNow = Boolean(result) && intent.state === 'COMPLETED'
  const amountSpoken = money(intent.amount_cents, intent.currency)
  const shopSpoken = intent.shop.name
  useEffect(() => {
    if (paidNow) prompt(`Paid. ${amountSpoken} to ${shopSpoken}.`)
  }, [paidNow, amountSpoken, shopSpoken])
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
        {ink && (
          <div className="pad air review receipt-ink">
            <InkCanvas ink={ink} />
          </div>
        )}
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
  const [found] = useState<Found | null>(
    routerState?.checkin
      ? { shop: routerState.checkin.shop, how: 'qr', checkinPass: routerState.checkin.checkin_pass, invoice: routerState.checkin.invoice }
      : bill
        ? { shop: bill.shop, how: bill.sent ? 'sent' : 'popup', invoice: bill.invoice, checkinPass: bill.checkinPass ?? undefined }
        : null,
  )
  const [invoice, setInvoice] = useState<Invoice | null>(routerState?.checkin?.invoice ?? bill?.invoice ?? null)
  const [loc] = useState<{ loc: Loc | null; simulated: boolean }>({ loc: bill?.location ?? null, simulated: bill?.simulated ?? false })
  const [intent, setIntent] = useState<Intent | null>(null)
  const [nonce, setNonce] = useState<string | null>(null)
  const [lastResult, setLastResult] = useState<SignoffResult | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)

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
  // Bills reach this page from the home screen, a QR scan or a code. Nothing to pay here otherwise.
  if (!id && !intent && (!found || !invoice)) return <Navigate to="/" replace />

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
    navigate('/', { replace: true })
  }

  /** Not mine: a bill sent by name goes back to the cashier, who can send it to the right person. */
  async function notMine() {
    if (found && invoice && found.how !== 'popup') await post(`/bills/${invoice.id}/decline`).catch(() => {})
    navigate('/', { replace: true })
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
  } else if (found && invoice) {
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
        <button className="btn ghost small" style={{ marginTop: 8 }} onClick={() => void notMine()}>
          Not my bill
        </button>
      </div>
    )
  }

  const steps = ['Shop', 'Bill', ...((intent ? intent.funding === 'wallet' : me?.wallet) ? [] : ['PayPal']), 'Sign', 'Paid']
  const step = intent
    ? intent.state === 'PROPOSED' ? 'PayPal' : intent.state === 'COMPLETED' ? 'Paid' : 'Sign'
    : found ? 'Bill' : 'Shop'
  const failed = intent && ['LOCKED', 'FAILED', 'EXPIRED', 'CANCELLED'].includes(intent.state)

  return (
    <div className="app">
      <TopBar />
      {!failed && step !== 'Paid' && <Stepper steps={steps} current={step} />}
      {body}
    </div>
  )
}
