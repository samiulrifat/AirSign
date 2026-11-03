import { useEffect, useState } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import { del, get, post, type Intent, type Me } from '../lib/api'
import { errorText, money, useApp } from '../lib/app'
import { SimBadge, Spinner, TopBar } from '../components/bits'
import BillAlerts from '../components/BillAlerts'
import WalletCard from '../components/WalletCard'

function Welcome() {
  const { setMe } = useApp()
  const navigate = useNavigate()
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
      navigate('/enroll') // the next thing a new user needs
    } catch (err) {
      setError(errorText(err))
    } finally {
      setBusy(false)
    }
  }

  return (
    <>
      <div className="welcome">
        <h1>Sign the air. You've paid.</h1>
        <p className="muted">Your phone is the pen. Your PayPal pays the shop. No card, no PIN, no terminal.</p>
      </div>
      <form className="card" onSubmit={start}>
        <label className="field">
          <span>Your first name</span>
          <input type="text" value={name} onChange={(e) => setName(e.target.value)} maxLength={60} required autoComplete="given-name" autoFocus />
        </label>
        <label className="check">
          <input type="checkbox" checked={consent} onChange={(e) => setConsent(e.target.checked)} />
          <span>
            AirSign keeps an encrypted template of my motion signature to check my payments. Recordings are discarded,
            and I can delete it any time.
          </span>
        </label>
        {error && <div className="notice bad">{error}</div>}
        <button className="btn block" disabled={!name.trim() || !consent || busy}>
          {busy ? <Spinner /> : 'Continue'}
        </button>
        <p className="tiny" style={{ marginTop: 12 }}>
          A prototype on the PayPal sandbox: no real money moves. Your account starts with a made-up payment history for the risk checks.
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
      {sig ? (
        <BillAlerts />
      ) : (
        <div className="hero">
          <p className="eyebrow">Hello, {me.user!.display_name}</p>
          <h1>Set up your air signature</h1>
          <p className="muted">Write your initials in the air with your phone, 7 times, the same way each time.</p>
          <Link to="/enroll" className="btn block">
            Set up air signature
          </Link>
        </div>
      )}

      {error && <div className="notice bad">{error}</div>}

      {sig && !me.wallet && <WalletCard me={me} />}

      {payments && payments.length > 0 && (
        <details className="card flat">
          <summary>Recent payments</summary>
          <ul className="list">
            {payments.map((p) => (
              <li key={p.id}>
                <Link to={`/pay/${p.id}`} className="item" style={{ textDecoration: 'none' }}>
                  <span>
                    <strong>{p.shop.name}</strong>
                    <br />
                    <span className="tiny">{new Date(p.created_at).toLocaleString()}</span>
                  </span>
                  <span className="stack" style={{ textAlign: 'right' }}>
                    <strong>{money(p.amount_cents, p.currency)}</strong>
                    <div>{stateBadge(p.state)}</div>
                  </span>
                </Link>
              </li>
            ))}
          </ul>
        </details>
      )}

      <details className="card flat">
        <summary>Account and privacy</summary>
        <div className="spacer" />
        {me.wallet && <WalletCard me={me} />}
        {sig && (
          <div className="settings-row">
            <div className="row between">
              <h3 style={{ margin: 0 }}>Air signature</h3>
              {sig.simulated && <SimBadge />}
            </div>
            <p className="small muted">
              {sig.repetitions} repetitions, checked with {sig.method === 'fused' ? 'DTW and the neural network' : 'DTW'}. Stored encrypted.
            </p>
            <div className="grid2">
              <Link to="/enroll" className="btn quiet small">
                Re-record
              </Link>
              <button
                className="btn danger small"
                onClick={() => confirm('Delete your air signature? You will need to set it up again before paying.') && action(() => del<Me>('/me/signature'))}
              >
                Delete
              </button>
            </div>
          </div>
        )}
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
          Location is read when you tap, or while "I'm at a shop" is on. Your position is not stored. While it is on,
          cashiers in that building see your first name and code.
        </p>
      </details>
    </>
  )
}

export default function Home() {
  const { me } = useApp()
  return (
    <div className="app">
      <TopBar links={[{ to: '/how', label: 'How it works' }]} />
      {me === null ? <Spinner /> : me.user ? <Dashboard me={me} /> : <Welcome />}
    </div>
  )
}
