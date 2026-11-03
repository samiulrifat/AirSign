import { useEffect, useRef, useState } from 'react'
import { Link, useLocation, useNavigate } from 'react-router-dom'
import { del, get, post, type Building, type Invoice, type Shop } from '../lib/api'
import { errorText, floorLabel, money, useApp } from '../lib/app'
import { prompt } from '../lib/speech'
import { cue, unlockFeedback } from '../lib/feedback'
import { askNotifications, notify } from '../lib/notify'
import { DemoControls, SimBadge, Spinner } from './bits'

type Bill = { shop: Shop; invoice: Invoice }
type Ready = { shop: Shop; invoice_id: string }
type Position = { lat: number; lon: number; accuracy: number }
type CheckIn = { you: { name: string; code: string; building: string } | null; reason: 'no_shop' | 'low_accuracy' | 'no_location' | null; bills: Bill[]; ready: Ready[] }
type Joined = { invoice: Invoice | null; checkin_pass: string; shop: Shop }

/** Passed to the payment page, which goes straight from the bill to signing. */
export type BillStart = Bill & { location: Position | null; simulated: boolean; sent: boolean; checkinPass: string | null }

const POLL_MS = 3000
const REMEMBER = 'airsign.bill-alerts'
const JOIN_PASS = 'airsign.join-pass'

function savedPass(): string | null {
  try {
    return sessionStorage.getItem(JOIN_PASS)
  } catch {
    return null
  }
}

function savePass(value: string | null) {
  try {
    if (value) sessionStorage.setItem(JOIN_PASS, value)
    else sessionStorage.removeItem(JOIN_PASS)
  } catch {
    /* it just lasts until the page closes */
  }
}

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
  const joinedByScan = (useLocation().state as { joined?: string } | null)?.joined ?? null
  const [joinPass, setJoinPass] = useState<string | null>(joinedByScan ?? savedPass())
  const [code, setCode] = useState('')
  const [joining, setJoining] = useState(false)
  const [offline, setOffline] = useState(false)
  const [on, setOn] = useState(joinPass !== null)
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
    savePass(joinPass)
  }, [joinPass])

  // A locked phone stops checking in, so keep the screen on while waiting for a bill.
  useEffect(() => {
    if (!on || !navigator.wakeLock) return
    let lock: WakeLockSentinel | null = null
    const acquire = () => {
      if (document.visibilityState === 'visible') {
        navigator.wakeLock
          .request('screen')
          .then((l) => (lock = l))
          .catch(() => {})
      }
    }
    acquire()
    document.addEventListener('visibilitychange', acquire)
    return () => {
      document.removeEventListener('visibilitychange', acquire)
      void lock?.release().catch(() => {})
    }
  }, [on])

  useEffect(() => {
    if (!on || simulated || joinPass) return
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
  }, [on, simulated, joinPass])

  useEffect(() => {
    if (!on || (!position && !joinPass)) return
    let stop = false
    const check = async () => {
      const r = await post<CheckIn>('/presence', joinPass ? { checkin_pass: joinPass } : { ...position, simulated }).catch(() => null)
      if (stop) return
      setOffline(!r)
      if (!r) return
      if (joinPass && !r.you) {
        setJoinPass(null) // the pass expired: go back to location, or a new code
        return
      }
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
        prompt(title)
      }
    }
    void check()
    const t = setInterval(check, POLL_MS)
    return () => {
      stop = true
      clearInterval(t)
    }
  }, [on, position, simulated, joinPass])

  async function joinWithCode(e: React.FormEvent) {
    e.preventDefault()
    setJoining(true)
    setError(null)
    try {
      const r = await post<Joined>('/checkin', { code })
      setCode('')
      if (r.invoice) {
        navigate('/pay', { state: { checkin: r } })
        return
      }
      unlockFeedback()
      setPosition(null)
      setSimulated(false)
      setJoinPass(r.checkin_pass)
      setOn(true)
    } catch (err) {
      setError(errorText(err))
    } finally {
      setJoining(false)
    }
  }

  async function placeHere(b: Building) {
    if (!position) return
    setError(null)
    try {
      await post('/sim/move-building', { building_id: b.id, lat: position.lat, lon: position.lon })
    } catch (err) {
      setError(errorText(err))
    }
  }

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
    setJoinPass(null)
    setOffline(false)
    remember(false)
    void del('/presence').catch(() => {})
  }

  function pretend(b: Building) {
    unlockFeedback()
    setError(null)
    setJoinPass(null)
    setSimulated(true)
    setPosition({ lat: b.lat, lon: b.lon, accuracy: 25 })
    setOn(true)
  }

  function pay(b: Bill, sent: boolean) {
    const start: BillStart = { ...b, location: position, simulated, sent, checkinPass: joinPass }
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
          ) : (
            <>
              <h1>{state?.reason === 'low_accuracy' ? "Your location isn't precise enough" : state?.reason === 'no_shop' ? 'No AirSign shop found here' : 'Finding you'}</h1>
              {state?.reason ? (
                <p className="muted">Enter the code on the cashier's screen instead.</p>
              ) : (
                <p className="row muted">
                  <Spinner /> Checking which building you're in
                </p>
              )}
              <form className="row" style={{ marginBottom: 12 }} onSubmit={joinWithCode}>
                <input
                  aria-label="Code on the cashier's screen"
                  inputMode="numeric"
                  pattern="[0-9]{6}"
                  maxLength={6}
                  placeholder="123456"
                  value={code}
                  onChange={(e) => setCode(e.target.value.replace(/\D/g, ''))}
                />
                <button className="btn" disabled={code.length !== 6 || joining}>
                  Join
                </button>
              </form>
            </>
          )}
          <button className="btn quiet block" onClick={turnOff}>
            I've left
          </button>
        </div>
      )}
      {error && <div className="notice warn">{error}</div>}
      {offline && <div className="notice warn">Can't reach AirSign, so the cashier may not see you. Trying again…</div>}
      {!bill &&
        ready.map((r) => (
          <button key={r.invoice_id} className="ready-row" onClick={() => open(r)}>
            <span>
              A bill is ready at <strong>{r.shop.name}</strong>
            </span>
            <span aria-hidden="true">›</span>
          </button>
        ))}
      {config?.allow_simulation && buildings.length > 0 && !bill && !you && (
        <DemoControls>
          <p className="small muted">Pretend this phone is inside a building.</p>
          {buildings.map((b) => (
            <button key={b.id} className="btn sim small block" onClick={() => pretend(b)}>
              I'm in {b.name}
            </button>
          ))}
          {state?.reason === 'no_shop' && !simulated && position && (
            <>
              <p className="small muted">Or move a demo building to where you're standing. This moves it for everyone.</p>
              {buildings.map((b) => (
                <button key={b.id} className="btn sim small block" onClick={() => placeHere(b)}>
                  Place {b.name} here
                </button>
              ))}
            </>
          )}
        </DemoControls>
      )}
    </>
  )
}
