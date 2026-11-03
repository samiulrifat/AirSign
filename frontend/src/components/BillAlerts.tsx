import { useEffect, useRef, useState } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import { del, get, post, type Building, type Invoice, type Shop } from '../lib/api'
import { floorLabel, money, useApp } from '../lib/app'
import { cue, unlockFeedback } from '../lib/feedback'
import { askNotifications, notify } from '../lib/notify'
import { DemoControls, SimBadge, Spinner } from './bits'

type Bill = { shop: Shop; invoice: Invoice }
type Ready = { shop: Shop; invoice_id: string }
type Position = { lat: number; lon: number; accuracy: number }
type CheckIn = { you: { name: string; code: string; building: string } | null; bills: Bill[]; ready: Ready[] }

/** Passed to the payment page, which goes straight from the bill to signing. */
export type BillStart = Bill & { location: Position; simulated: boolean; sent: boolean }

const POLL_MS = 3000
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

/** The home screen for an enrolled customer: say you are at a shop, then receive the bill. */
export default function BillAlerts() {
  const { config } = useApp()
  const navigate = useNavigate()
  const [on, setOn] = useState(false)
  const [position, setPosition] = useState<Position | null>(null)
  const [simulated, setSimulated] = useState(false)
  const [state, setState] = useState<CheckIn | null>(null)
  const [opened, setOpened] = useState<Bill | null>(null)
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
      (e) => {
        if (e.code === e.PERMISSION_DENIED) setError('Location is off, so the cashier cannot see you. Use the code on the cashier screen instead.')
      },
      { enableHighAccuracy: true, timeout: 15000, maximumAge: 5000 },
    )
    return () => navigator.geolocation.clearWatch(id)
  }, [on, simulated])

  useEffect(() => {
    if (!on || !position) return
    let stop = false
    const check = async () => {
      const r = await post<CheckIn>('/presence', { ...position, simulated }).catch(() => null)
      if (stop || !r) return
      setState(r)
      const fresh: [string, string, string][] = [
        ...r.bills.map((b): [string, string, string] => [b.invoice.id, `${b.shop.name}: ${money(b.invoice.amount_cents, b.invoice.currency)}`, 'Open AirSign and sign in the air to pay.']),
        ...r.ready.map((b): [string, string, string] => [b.invoice_id, `A bill is ready at ${b.shop.name}`, 'Open AirSign to see it.']),
      ]
      for (const [id, title, body] of fresh) {
        if (announced.current.has(id)) continue
        announced.current.add(id)
        cue('ready')
        void notify(title, body, id)
      }
    }
    void check()
    const t = setInterval(check, POLL_MS)
    return () => {
      stop = true
      clearInterval(t)
    }
  }, [on, position, simulated])

  async function turnOn() {
    unlockFeedback()
    void askNotifications()
    setSimulated(false)
    setPosition(null)
    setError(null)
    setOn(true)
    remember(true)
  }

  function turnOff() {
    setOn(false)
    setSimulated(false)
    setPosition(null)
    setState(null)
    setOpened(null)
    setError(null)
    remember(false)
    void del('/presence').catch(() => {})
  }

  function pretend(b: Building) {
    unlockFeedback()
    setError(null)
    setSimulated(true)
    setPosition({ lat: b.lat, lon: b.lon, accuracy: 25 })
    setOn(true)
  }

  function pay(b: Bill, sent: boolean) {
    if (!position) return
    const start: BillStart = { ...b, location: position, simulated, sent }
    navigate('/pay', { state: { bill: start } })
  }

  async function notMine(b: Bill, sent: boolean) {
    setDismissed((d) => [...d, b.invoice.id])
    setOpened(null)
    if (sent) await post(`/bills/${b.invoice.id}/decline`).catch(() => {})
  }

  async function open(r: Ready) {
    setError(null)
    try {
      setOpened(await post<Bill>(`/bills/${r.invoice_id}/open`))
    } catch {
      setError('That bill is no longer available.')
    }
  }

  const sentBill = state?.bills.find((b) => !dismissed.includes(b.invoice.id))
  const bill = sentBill ?? opened
  const ready = state?.ready.filter((r) => !dismissed.includes(r.invoice_id)) ?? []
  const you = state?.you

  return (
    <>
      {bill ? (
        <div className="hero bill-alert">
          <p className="eyebrow">
            {bill.shop.name} · {floorLabel(bill.shop.floor)}
          </p>
          <div className="amount">{money(bill.invoice.amount_cents, bill.invoice.currency)}</div>
          {bill.invoice.items.length > 0 && <p className="muted">{bill.invoice.items.map((i) => `${i.qty}× ${i.name}`).join(', ')}</p>}
          <button className="btn block" onClick={() => pay(bill, Boolean(sentBill))}>
            Sign to pay
          </button>
          <button className="btn ghost block" onClick={() => notMine(bill, Boolean(sentBill))}>
            Not my bill
          </button>
        </div>
      ) : !on ? (
        <div className="hero">
          <h1>At the counter?</h1>
          <p className="muted">Tell the cashier you're here and your bill comes straight to this phone.</p>
          <button className="btn block" onClick={turnOn}>
            I'm at a shop
          </button>
          <Link to="/pay" className="text-link">
            Find the shop myself
          </Link>
        </div>
      ) : (
        <div className="hero">
          {you ? (
            <>
              <p className="eyebrow">
                {you.building} {simulated && <SimBadge />}
              </p>
              <h1>Tell the cashier your name</h1>
              <div className="you">
                <span>{you.name}</span>
                <span className="code-chip" aria-label={`Your code is ${you.code}`}>
                  {you.code}
                </span>
              </div>
              <p className="muted small">Keep AirSign open. Your phone buzzes when the bill arrives.</p>
            </>
          ) : position ? (
            <>
              <h1>No AirSign shop here</h1>
              <p className="muted">Move closer to the shop, or use the code on the cashier's screen.</p>
              <Link to="/pay" className="text-link">
                Use a code
              </Link>
            </>
          ) : (
            <>
              <h1>Finding you</h1>
              <p className="row muted">
                <Spinner /> Checking which building you're in
              </p>
            </>
          )}
          <button className="btn quiet block" onClick={turnOff}>
            I've left
          </button>
        </div>
      )}
      {error && <div className="notice warn">{error}</div>}
      {!bill &&
        ready.map((r) => (
          <button key={r.invoice_id} className="ready-row" onClick={() => open(r)}>
            <span>
              A bill is ready at <strong>{r.shop.name}</strong>
            </span>
            <span aria-hidden="true">›</span>
          </button>
        ))}
      {config?.allow_simulation && buildings.length > 0 && !bill && !(on && !simulated) && (
        <DemoControls>
          <p className="small muted">Pretend this phone is inside a building.</p>
          {buildings.map((b) => (
            <button key={b.id} className="btn sim small block" onClick={() => pretend(b)}>
              I'm in {b.name}
            </button>
          ))}
        </DemoControls>
      )}
    </>
  )
}
