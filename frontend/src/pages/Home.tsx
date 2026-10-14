import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { del, get, post, type Intent, type Me } from '../lib/api'
import { errorText, floorLabel, money, useApp } from '../lib/app'
import { SimBadge, Spinner, TopBar } from '../components/bits'

function Welcome() {
  const { setMe } = useApp()
  const [name, setName] = useState('')
  const [consent, setConsent] = useState(false)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  async function start(e: React.FormEvent) {
    e.preventDefault()
    setBusy(true)
    setError(null)
    try {
      setMe(await post<Me>('/session', { display_name: name, consent, demo_history: true }))
    } catch (err) {
      setError(errorText(err))
    } finally {
      setBusy(false)
    }
  }

  return (
    <>
      <div className="card">
        <h1>Pay in a shop with a move of your phone</h1>
        <p className="muted">
          AirSign finds the shop you are in, you approve the payment in PayPal, then you confirm it by moving your phone
          in your own air signature. No card, no PIN, no terminal.
        </p>
      </div>
      <form className="card" onSubmit={start}>
        <h2>Get started</h2>
        <label className="field">
          <span>Your first name</span>
          <input type="text" value={name} onChange={(e) => setName(e.target.value)} maxLength={60} required autoComplete="given-name" />
        </label>
        <label className="check">
          <input type="checkbox" checked={consent} onChange={(e) => setConsent(e.target.checked)} />
          <span>
            I agree that AirSign stores an encrypted template of my motion signature to verify my payments. Raw sensor
            recordings are discarded, and I can delete my signature at any time.
          </span>
        </label>
        {error && <div className="notice bad">{error}</div>}
        <button className="btn block" disabled={!name.trim() || !consent || busy}>
          {busy ? <Spinner /> : 'Continue'}
        </button>
        <p className="tiny" style={{ marginTop: 12 }}>
          This is a hackathon prototype on the PayPal sandbox. No real money moves. Your account includes a synthetic
          payment history so the risk engine has something to compare against.
        </p>
      </form>
      <p className="center small">
        <Link to="/pos">Cashier? Open the shop screen</Link>
      </p>
    </>
  )
}

function stateBadge(state: string) {
  const cls = state === 'COMPLETED' ? 'ok' : ['LOCKED', 'FAILED'].includes(state) ? 'bad' : ['EXPIRED', 'CANCELLED'].includes(state) ? '' : 'warn'
  return <span className={`badge ${cls}`}>{state.toLowerCase()}</span>
}

function Dashboard({ me }: { me: Me }) {
  const { setMe, config } = useApp()
  const [payments, setPayments] = useState<Intent[] | null>(null)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    get<Intent[]>('/intents').then(setPayments).catch(() => setPayments([]))
  }, [])

  async function action(fn: () => Promise<Me>) {
    setError(null)
    try {
      setMe(await fn())
    } catch (e) {
      setError(errorText(e))
    }
  }

  const sig = me.signature
  return (
    <>
      <div className="card">
        <p className="muted small" style={{ marginBottom: 4 }}>Hello, {me.user!.display_name}</p>
        {sig ? (
          <>
            <h1>Ready to pay</h1>
            <p className="muted">Walk into an AirSign shop, then tap below.</p>
            <Link to="/pay" className="btn block">
              Pay in a shop
            </Link>
          </>
        ) : (
          <>
            <h1>Set up your air signature</h1>
            <p className="muted">
              Pick a personal movement, like drawing your initials in the air with your phone. You will repeat it 7
              times so AirSign learns how you do it.
            </p>
            <Link to="/enroll" className="btn block">
              Set up air signature
            </Link>
          </>
        )}
      </div>

      {error && <div className="notice bad">{error}</div>}

      {sig && (
        <div className="card">
          <div className="row between">
            <h2>Your air signature</h2>
            {sig.simulated && <SimBadge />}
          </div>
          <p className="muted small">
            {sig.repetitions} repetitions, verified with {sig.method === 'fused' ? 'DTW and the neural model' : 'dynamic time warping'}.
            Stored encrypted.
          </p>
          <div className="grid2">
            <Link to="/enroll" className="btn quiet">
              Re-record
            </Link>
            <button
              className="btn danger"
              onClick={() => confirm('Delete your air signature? You will need to set it up again before paying.') && action(() => del<Me>('/me/signature'))}
            >
              Delete
            </button>
          </div>
        </div>
      )}

      <div className="card">
        <h2>Recent payments</h2>
        {payments === null ? (
          <Spinner />
        ) : payments.length === 0 ? (
          <p className="muted small">No payments yet.</p>
        ) : (
          <ul className="list">
            {payments.map((p) => (
              <li key={p.id}>
                <Link to={`/pay/${p.id}`} className="item" style={{ textDecoration: 'none' }}>
                  <span>
                    <strong>{p.shop.name}</strong>
                    <br />
                    <span className="tiny">
                      {floorLabel(p.shop.floor)} · {new Date(p.created_at).toLocaleString()}
                    </span>
                  </span>
                  <span className="stack" style={{ textAlign: 'right' }}>
                    <strong>{money(p.amount_cents, p.currency)}</strong>
                    <div>{stateBadge(p.state)}</div>
                  </span>
                </Link>
              </li>
            ))}
          </ul>
        )}
      </div>

      <details className="card flat">
        <summary>Privacy and demo settings</summary>
        <div className="spacer" />
        <p className="small muted">
          Synthetic payment history: {me.synthetic_history ? `${me.synthetic_history} made-up payments` : 'off'}. It lets the
          risk engine tell routine payments from unusual ones in the demo.
        </p>
        <div className="grid2">
          <button className="btn quiet small" onClick={() => action(() => post<Me>('/me/demo-history', { enabled: !me.synthetic_history }))}>
            {me.synthetic_history ? 'Clear history' : 'Add history'}
          </button>
          <button
            className="btn danger small"
            onClick={async () => {
              if (!confirm('Delete your AirSign account and all its data?')) return
              await del('/me')
              setMe({ user: null })
            }}
          >
            Delete all my data
          </button>
        </div>
        <p className="tiny" style={{ marginTop: 12 }}>
          PayPal: {config?.paypal_mode === 'sandbox' ? 'sandbox' : 'built-in mock (sandbox credentials not configured)'}.
          Location is only read when you tap, and only the matched shop is kept.
        </p>
      </details>
    </>
  )
}

export default function Home() {
  const { me } = useApp()
  return (
    <div className="app">
      <TopBar links={[{ to: '/', label: 'Home' }, { to: '/how', label: 'How it works' }]} />
      {me === null ? <Spinner /> : me.user ? <Dashboard me={me} /> : <Welcome />}
    </div>
  )
}
