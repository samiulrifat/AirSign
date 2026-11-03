import { useState } from 'react'
import { del, post, type Me } from '../lib/api'
import { errorText, useApp } from '../lib/app'
import { Spinner } from './bits'

/** Save PayPal once, so a bill goes straight to the air signature. */
export default function WalletCard({ me }: { me: Me }) {
  const { setMe } = useApp()
  const [busy, setBusy] = useState(false)
  const [mock, setMock] = useState(false)
  const [error, setError] = useState<string | null>(null)

  async function run(fn: () => Promise<void>) {
    setBusy(true)
    setError(null)
    try {
      await fn()
    } catch (e) {
      setError(errorText(e))
    } finally {
      setBusy(false)
    }
  }

  const start = () =>
    run(async () => {
      const r = await post<{ approve_url: string | null; mock: boolean }>('/wallet/link')
      if (r.approve_url) window.location.assign(r.approve_url)
      else setMock(r.mock)
    })

  const finish = () =>
    run(async () => {
      setMe(await post<Me>('/wallet/link/finish'))
      setMock(false)
    })

  const remove = () => run(async () => setMe(await del<Me>('/wallet')))

  return (
    <div className={me.wallet ? 'settings-row' : 'card flat'}>
      <div className="row between">
        <h3 style={{ margin: 0 }}>{me.wallet ? 'PayPal' : 'Skip the PayPal window'}</h3>
        {me.wallet && <span className="badge ok">Saved</span>}
      </div>
      {me.wallet ? (
        <>
          <p className="small muted" style={{ margin: '8px 0 12px' }}>
            {me.wallet.email ?? 'Your PayPal account'} is charged only after your signature passes. Bills over $200 still open PayPal.
          </p>
          <button className="btn quiet small" disabled={busy} onClick={remove}>
            Remove saved PayPal
          </button>
        </>
      ) : mock ? (
        <div className="sim-strip stack" style={{ marginTop: 12 }}>
          <h3>Mock PayPal approval</h3>
          <p className="small muted">PayPal sandbox credentials are not configured, so a local stand-in plays PayPal's part.</p>
          <button className="btn sim block" disabled={busy} onClick={finish}>
            {busy ? <Spinner /> : 'Approve saving my account (mock)'}
          </button>
        </div>
      ) : (
        <>
          <p className="small muted" style={{ margin: '8px 0 12px' }}>
            Approve once. After that, your air signature is all a payment needs.
          </p>
          <button className="btn quiet block" disabled={busy} onClick={start}>
            {busy ? <Spinner /> : 'Save my PayPal'}
          </button>
        </>
      )}
      {error && (
        <div className="notice warn" style={{ marginTop: 12 }}>
          {error}
        </div>
      )}
    </div>
  )
}
