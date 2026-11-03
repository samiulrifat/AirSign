import { useEffect, useRef, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { get, type Building, type Invoice, type Shop } from '../lib/api'
import { floorLabel, money, useApp } from '../lib/app'
import { cue, unlockFeedback } from '../lib/feedback'
import { askNotifications, notify } from '../lib/notify'
import { SimBadge } from './bits'

type Bill = { shop: Shop; invoice: Invoice }
type Position = { lat: number; lon: number; accuracy: number }

/** Passed to the payment page, which goes straight from the bill to signing. */
export type BillStart = Bill & { location: Position; simulated: boolean }

const POLL_MS = 4000
const REMEMBER = 'airsign.bill-alerts'

function remembered(): boolean {
  try {
    return localStorage.getItem(REMEMBER) === 'on'
  } catch {
    return false
  }
}

function remember(on: boolean) {
  try {
    localStorage.setItem(REMEMBER, on ? 'on' : 'off')
  } catch {
    /* the setting just lasts until the page closes */
  }
}

export default function BillAlerts() {
  const { config } = useApp()
  const navigate = useNavigate()
  const [on, setOn] = useState(false)
  const [position, setPosition] = useState<Position | null>(null)
  const [simulated, setSimulated] = useState(false)
  const [bills, setBills] = useState<Bill[]>([])
  const [dismissed, setDismissed] = useState<string[]>([])
  const [error, setError] = useState<string | null>(null)
  const [buildings, setBuildings] = useState<Building[]>([])
  const announced = useRef(new Set<string>())

  useEffect(() => {
    if (config?.allow_simulation) get<Building[]>('/buildings').then(setBuildings).catch(() => {})
  }, [config?.allow_simulation])

  // Turned on earlier and location is already allowed: resume without asking again.
  useEffect(() => {
    if (!remembered()) return
    navigator.permissions
      ?.query({ name: 'geolocation' as PermissionName })
      .then((s) => s.state === 'granted' && setOn(true))
      .catch(() => {})
  }, [])

  useEffect(() => {
    if (!on || simulated) return
    if (!navigator.geolocation) {
      setError('This browser cannot share location. Use the code on the cashier screen instead.')
      return
    }
    const id = navigator.geolocation.watchPosition(
      (p) => {
        setError(null)
        setPosition({ lat: p.coords.latitude, lon: p.coords.longitude, accuracy: Math.max(1, p.coords.accuracy) })
      },
      (e) => setError(e.code === e.PERMISSION_DENIED ? 'Location permission was denied, so alerts cannot find your shop.' : 'Waiting for your location…'),
      { enableHighAccuracy: true, timeout: 15000, maximumAge: 5000 },
    )
    return () => navigator.geolocation.clearWatch(id)
  }, [on, simulated])

  useEffect(() => {
    if (!on || !position) return
    let stop = false
    const check = async () => {
      const q = new URLSearchParams({ lat: String(position.lat), lon: String(position.lon), accuracy: String(position.accuracy) })
      const r = await get<{ bills: Bill[] }>(`/bills/nearby?${q}`).catch(() => null)
      if (stop || !r) return
      setBills(r.bills)
      for (const b of r.bills) {
        if (announced.current.has(b.invoice.id)) continue
        announced.current.add(b.invoice.id)
        cue('ready')
        void notify(`${b.shop.name}: ${money(b.invoice.amount_cents, b.invoice.currency)}`, 'Open AirSign and sign in the air to pay.', b.invoice.id)
      }
    }
    void check()
    const t = setInterval(check, POLL_MS)
    return () => {
      stop = true
      clearInterval(t)
    }
  }, [on, position])

  async function turnOn() {
    unlockFeedback()
    await askNotifications()
    setSimulated(false)
    setPosition(null)
    setOn(true)
    remember(true)
  }

  function turnOff() {
    setOn(false)
    setSimulated(false)
    setPosition(null)
    setBills([])
    setError(null)
    remember(false)
  }

  function pretend(b: Building) {
    unlockFeedback()
    setError(null)
    setSimulated(true)
    setPosition({ lat: b.lat, lon: b.lon, accuracy: 25 })
    setOn(true)
  }

  const showing = bills.filter((b) => !dismissed.includes(b.invoice.id))

  return (
    <>
      {position &&
        showing.map((b) => (
          <div key={b.invoice.id} className="card popup bill-alert">
            <p className="tiny">
              {b.shop.name} · {floorLabel(b.shop.floor)}
            </p>
            <div className="amount">{money(b.invoice.amount_cents, b.invoice.currency)}</div>
            {b.invoice.items.length > 0 && <p className="small muted">{b.invoice.items.map((i) => `${i.qty}× ${i.name}`).join(', ')}</p>}
            <div className="grid2">
              <button className="btn ghost" onClick={() => setDismissed((d) => [...d, b.invoice.id])}>
                Not mine
              </button>
              <button
                className="btn"
                onClick={() => {
                  const start: BillStart = { ...b, location: position, simulated }
                  navigate('/pay', { state: { bill: start } })
                }}
              >
                Sign to pay
              </button>
            </div>
          </div>
        ))}
      <div className="card flat">
        <div className="row between">
          <h3 style={{ margin: 0 }}>Bill alerts</h3>
          {on && (
            <span className="row" style={{ gap: 6 }}>
              {simulated && <SimBadge />}
              <span className="badge ok">On</span>
            </span>
          )}
        </div>
        <p className="small muted" style={{ margin: '8px 0 12px' }}>
          {on ? 'Keep AirSign open. Your phone alerts you when a shop around you creates a bill.' : 'Get alerted when the cashier creates your bill. Uses your location while on, and does not store it.'}
        </p>
        {error && <div className="notice warn">{error}</div>}
        <button className={`btn block ${on ? 'quiet' : ''}`} onClick={on ? turnOff : turnOn}>
          {on ? 'Turn off' : 'Turn on bill alerts'}
        </button>
        {config?.allow_simulation && buildings.length > 0 && !(on && !simulated) && (
          <div className="stack" style={{ marginTop: 12 }}>
            {buildings.map((b) => (
              <button key={b.id} className="btn sim small block" onClick={() => pretend(b)}>
                Simulate: I am in {b.name}
              </button>
            ))}
          </div>
        )}
      </div>
    </>
  )
}
