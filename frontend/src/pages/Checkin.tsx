import { useEffect, useState } from 'react'
import { Link, useNavigate, useSearchParams } from 'react-router-dom'
import { post } from '../lib/api'
import { errorText, useApp } from '../lib/app'
import { Spinner, TopBar } from '../components/bits'
import type { CheckinState } from './Pay'

/** Opened by the phone's camera app from the QR code on the cashier screen. */
export default function Checkin() {
  const { me } = useApp()
  const [params] = useSearchParams()
  const navigate = useNavigate()
  const [error, setError] = useState<string | null>(null)
  const token = params.get('t')

  useEffect(() => {
    if (!token || !me?.user) return
    post<CheckinState>('/checkin', { token })
      // A code for a bill goes straight to paying. A shop's code puts the customer on the cashier's list.
      .then((r) => (r.invoice ? navigate('/pay', { replace: true, state: { checkin: r } }) : navigate('/', { replace: true, state: { joined: r.checkin_pass } })))
      .catch((e) => setError(errorText(e)))
  }, [token, me, navigate])

  return (
    <div className="app">
      <TopBar />
      <div className="card">
        <h1>Checking in</h1>
        {!token ? (
          <div className="notice bad">This link has no check-in code.</div>
        ) : me && !me.user ? (
          <>
            <p className="muted">Set up AirSign first, then scan the code again.</p>
            <Link to="/" className="btn block">
              Get started
            </Link>
          </>
        ) : error ? (
          <>
            <div className="notice bad">{error}</div>
            <Link to="/pay" className="btn block">
              Find the shop another way
            </Link>
          </>
        ) : (
          <Spinner />
        )}
      </div>
    </div>
  )
}
