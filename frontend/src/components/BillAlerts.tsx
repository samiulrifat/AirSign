import { useEffect, useRef, useState } from 'react'
import { useLocation, useNavigate } from 'react-router-dom'
import { del, get, post, type Building, type Invoice, type Nearby, type Shop } from '../lib/api'
import { errorText, floorLabel, money, useApp } from '../lib/app'
import { prompt } from '../lib/speech'
import { cue, unlockFeedback } from '../lib/feedback'
import { askNotifications, notify } from '../lib/notify'
import { DemoControls, SimBadge, Spinner } from './bits'

type Bill = { shop: Shop; invoice: Invoice }
type Ready = { shop: Shop; invoice_id: string }
type Position = { lat: number; lon: number; accuracy: number }
type You = { name: string; code: string; building: string; building_ids: string[]; via: 'location' | 'code' | 'chosen'; shop: Shop | null }
type CheckIn = { you: You | null; reason: 'no_shop' | 'low_accuracy' | 'no_location' | null; bills: Bill[]; ready: Ready[] }
type Joined = { invoice: Invoice | null; checkin_pass: string; shop: Shop }

/** Passed to the payment page, which goes straight from the bill to signing. */
export type BillStart = Bill & { location: Position | null; simulated: boolean; sent: boolean; checkinPass: string | null }

const POLL_MS = 3000
const REMEMBER = 'airsign.bill-alerts'
const JOIN_PASS = 'airsign.join-pass'
const CHOSEN = 'airsign.chosen-shop'

function saved(key: string): string | null {
  try {
    return sessionStorage.getItem(key)
  } catch {
    return null
  }
}

function save(key: string, value: string | null) {
  try {
    if (value) sessionStorage.setItem(key, value)
    else sessionStorage.removeItem(key)
  } catch {
    /* it just lasts until the page closes */
  }
}

function savedShop(): Shop | null {
  try {
    return JSON.parse(saved(CHOSEN) ?? 'null') as Shop | null
  } catch {
    return null
  }
}

/**
 * Every shop, building by building. With a location, the shop ranker (distance, floor,
 * your visits, opening hours, time of day, and which shop just opened a bill) orders the
 * shops by how likely you are in each, and marks the most likely one.
 */
function ShopChooser({ buildings, likely, position, onPick, onBack }: { buildings: Building[]; likely: string[]; position: Position | null; onPick: (s: Shop) => void; onBack: () => void }) {
  const [ranked, setRanked] = useState<Extract<Nearby, { status: 'ok' }> | null>(null)

  useEffect(() => {
    if (!position) return
    const q = new URLSearchParams({ lat: String(position.lat), lon: String(position.lon), accuracy: String(Math.max(1, position.accuracy)) })
    get<Nearby>(`/shops/nearby?${q}`)
      .then((r) => setRanked(r.status === 'ok' ? r : null))
      .catch(() => {})
  }, [position])

  const first = ranked?.building.id
  const ordered = [...buildings].sort((a, b) => {
    const score = (x: Building) => (x.id === first ? 2 : likely.includes(x.id) ? 1 : 0)
    return score(b) - score(a)
  })
  const odds = new Map(ranked?.shops.map((s) => [s.id, s.probability ?? 0]) ?? [])

  return (
    <div className="card">
      <h2>Which shop are you at?</h2>
      <p className="muted small">Your first name goes on that shop's list. The cashier still sends the bill to you, so nobody else's bill reaches you.</p>
      {buildings.length === 0 && <Spinner />}
      {ordered.map((b) => (
        <div key={b.id} style={{ marginTop: 10 }}>
          <p className="tiny">{b.name}</p>
          <ul className="list">
            {[...b.shops]
              .sort((x, y) => (odds.get(y.id) ?? -1) - (odds.get(x.id) ?? -1) || y.floor - x.floor)
              .map((s) => (
                <li key={s.id}>
                  <button className={`item${ranked?.suggestion === s.id ? ' likely' : ''}`} onClick={() => onPick(s)}>
                    <span className="row">
                      <span className="floor">{s.floor}F</span>
                      <span>
                        {s.name}
                        <br />
                        <span className="tiny">
                          {s.open_now === false ? 'Closed now' : s.category}
                          {odds.has(s.id) && ` · ${Math.round((odds.get(s.id) ?? 0) * 100)}% likely`}
                        </span>
                      </span>
                    </span>
                    {ranked?.suggestion === s.id ? <span className="badge accent">Most likely</span> : <span aria-hidden="true">›</span>}
                  </button>
                </li>
              ))}
          </ul>
        </div>
      ))}
      <button className="btn ghost small" style={{ marginTop: 8 }} onClick={onBack}>
        Back
      </button>
    </div>
  )
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
  const [joinPass, setJoinPass] = useState<string | null>(joinedByScan ?? saved(JOIN_PASS))
  const [chosen, setChosen] = useState<Shop | null>(joinedByScan ? null : savedShop())
  const [choosing, setChoosing] = useState(false)
  const [code, setCode] = useState('')
  const [joining, setJoining] = useState(false)
  const [offline, setOffline] = useState(false)
  const [on, setOn] = useState(joinPass !== null || chosen !== null)
  const [position, setPosition] = useState<Position | null>(null)
  const [simulated, setSimulated] = useState(false)
  const [state, setState] = useState<CheckIn | null>(null)
  const [opened, setOpened] = useState<Bill | null>(null)
  const [dismissed, setDismissed] = useState<string[]>([])
  const [error, setError] = useState<string | null>(null)
  const [buildings, setBuildings] = useState<Building[]>([])
  const announced = useRef(new Set<string>())

  useEffect(() => {
    get<Building[]>('/buildings').then(setBuildings).catch(() => {})
  }, [])

  // Turned on earlier and location is already allowed: resume without asking again.
  useEffect(() => {
    if (!remembered()) return
    navigator.permissions
      ?.query({ name: 'geolocation' as PermissionName })
      .then((s) => s.state === 'granted' && setOn(true))
      .catch(() => {})
  }, [])

  useEffect(() => {
    save(JOIN_PASS, joinPass)
  }, [joinPass])

  useEffect(() => {
    save(CHOSEN, chosen ? JSON.stringify(chosen) : null)
  }, [chosen])

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
    if (!on || simulated || joinPass || chosen) return
    if (!navigator.geolocation) {
      setError('This browser cannot share location. Choose your shop, or use the code on the cashier screen.')
      return
    }
    const id = navigator.geolocation.watchPosition(
      (p) => {
        setError(null)
        setPosition({ lat: p.coords.latitude, lon: p.coords.longitude, accuracy: Math.max(1, p.coords.accuracy) })
      },
      (e) => {
        if (e.code === e.PERMISSION_DENIED) setError('Location is off. Choose your shop below, or use the code on the cashier screen.')
      },
      { enableHighAccuracy: true, timeout: 15000, maximumAge: 5000 },
    )
    return () => navigator.geolocation.clearWatch(id)
  }, [on, simulated, joinPass, chosen])

  useEffect(() => {
    if (!on || (!position && !joinPass && !chosen)) return
    let stop = false
    const check = async () => {
      const body = joinPass ? { checkin_pass: joinPass } : chosen ? { shop_id: chosen.id } : { ...position, simulated }
      const r = await post<CheckIn>('/presence', body).catch(() => null)
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
  }, [on, position, simulated, joinPass, chosen])

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
      setChosen(null)
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
    setChosen(null)
    setError(null)
    setOn(true)
    remember(true)
  }

  /** Location could not find the shop, or found the wrong one: the customer says where they are. */
  function choose(s: Shop) {
    unlockFeedback()
    void askNotifications()
    setChoosing(false)
    setJoinPass(null)
    setSimulated(false)
    setChosen(s)
    setState(null)
    setError(null)
    setOn(true)
  }

  function turnOff() {
    setOn(false)
    setSimulated(false)
    setPosition(null)
    setState(null)
    setOpened(null)
    setError(null)
    setJoinPass(null)
    setChosen(null)
    setChoosing(false)
    setOffline(false)
    remember(false)
    void del('/presence').catch(() => {})
  }

  function pretend(b: Building) {
    unlockFeedback()
    setError(null)
    setJoinPass(null)
    setChosen(null)
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

  if (choosing && !bill) {
    return <ShopChooser buildings={buildings} likely={you?.building_ids ?? []} position={position} onPick={choose} onBack={() => setChoosing(false)} />
  }

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
          <button className="text-link" onClick={() => setChoosing(true)}>
            Choose the shop myself
          </button>
        </div>
      ) : (
        <div className="hero">
          {you ? (
            <>
              <p className="eyebrow">
                {you.shop ? `${you.shop.name} · ${floorLabel(you.shop.floor)}` : you.building} {simulated && <SimBadge />}
              </p>
              <h1>Tell the cashier your name</h1>
              <div className="you">
                <span>{you.name}</span>
                <span className="code-chip" aria-label={`Your code is ${you.code}`}>
                  {you.code}
                </span>
              </div>
              <p className="muted small">Keep AirSign open. Your phone buzzes when the cashier sends your bill.</p>
              {you.via === 'chosen' ? (
                <p className="row wrap small">
                  <button className="text-link" onClick={() => setChoosing(true)}>
                    Change shop
                  </button>
                  <button className="text-link" onClick={turnOn}>
                    Find me by location instead
                  </button>
                </p>
              ) : (
                <button className="text-link" onClick={() => setChoosing(true)}>
                  Wrong place? Choose your shop
                </button>
              )}
            </>
          ) : (
            <>
              <h1>{state?.reason === 'low_accuracy' ? "Your location isn't precise enough" : state?.reason === 'no_shop' ? 'No AirSign shop found here' : error ? "Can't use your location" : 'Finding you'}</h1>
              {state?.reason || error ? (
                <p className="muted">Choose your shop, or enter the code on the cashier's screen.</p>
              ) : (
                <p className="row muted">
                  <Spinner /> Checking which building you're in
                </p>
              )}
              <button className={`btn block ${state?.reason || error ? '' : 'quiet'}`} style={{ marginBottom: 12 }} onClick={() => setChoosing(true)}>
                Choose my shop
              </button>
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
