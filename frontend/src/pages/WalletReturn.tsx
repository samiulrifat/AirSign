import { useEffect, useRef, useState } from 'react'
import { Link, useNavigate, useSearchParams } from 'react-router-dom'
import { post, type Me } from '../lib/api'
import { errorText, useApp } from '../lib/app'
import { Spinner, TopBar } from '../components/bits'

/** PayPal sends the customer back here after they approve saving their account. */
export default function WalletReturn() {
  const { setMe } = useApp()
  const [params] = useSearchParams()
  const navigate = useNavigate()
  const [error, setError] = useState<string | null>(params.get('cancelled') ? 'You closed PayPal before approving, so nothing was saved.' : null)
  const started = useRef(false)

  useEffect(() => {
    if (started.current || params.get('cancelled')) return
    started.current = true
    post<Me>('/wallet/link/finish')
      .then((m) => {
        setMe(m)
        navigate('/', { replace: true })
      })
      .catch((e) => setError(errorText(e)))
  }, [params, navigate, setMe])

  return (
    <div className="app">
      <TopBar />
      <div className="card">
        <h1>Saving your PayPal</h1>
        {error ? (
          <>
            <div className="notice warn">{error}</div>
            <Link to="/" className="btn block">
              Home
            </Link>
          </>
        ) : (
          <Spinner />
        )}
      </div>
    </div>
  )
}
