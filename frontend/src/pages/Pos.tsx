import { useCallback, useEffect, useRef, useState } from 'react'
import QRCode from 'qrcode'
import { get, post, type Invoice, type Shop } from '../lib/api'
import { errorText, floorLabel, money, useApp } from '../lib/app'
import { canListen, listen, listenProblem, speak, stopSpeaking } from '../lib/speech'
import { CheckIcon, DemoControls, SimBadge, Spinner, TopBar } from '../components/bits'
import VoiceAssistant from '../components/VoiceAssistant'
import CameraScanner from '../components/CameraScanner'
import { applyDraft, cents, describeDraft, type Draft, type Line } from '../lib/cart'

type Customer = { name: string; code: string | null }
type PosInvoice = Invoice & {
  broadcast: boolean
  customer: Customer | null
  payment: { state: string; tier: number | null; simulated: boolean; webhook_confirmed: boolean } | null
}
type CatalogItem = { name: string; price: string }
type Nearby = { id: string; name: string; code: string; arrived_at: string; simulated: boolean; via: 'location' | 'code' | 'chosen'; chose: string | null }
type Mode = 'person' | 'ai'
type NameMatch = { status: 'sent' | 'choose' | 'nobody'; candidates: Nearby[]; customer: Customer | null }

const PHOTO_MAX_PX = 1024
const MODE_KEY = 'airsign.pos-mode'

function savedMode(): Mode {
  try {
    return localStorage.getItem(MODE_KEY) === 'ai' ? 'ai' : 'person'
  } catch {
    return 'person'
  }
}

function minutesAgo(iso: string, now: number): string {
  const m = Math.floor((now - new Date(iso).getTime()) / 60000)
  return m < 1 ? 'just arrived' : `${m} min`
}

/** Shrink a photo before upload: small enough to send quickly, sharp enough to read the items. */
async function shrinkPhoto(file: File): Promise<string> {
  const bitmap = await createImageBitmap(file)
  const scale = Math.min(1, PHOTO_MAX_PX / Math.max(bitmap.width, bitmap.height))
  const canvas = document.createElement('canvas')
  canvas.width = Math.round(bitmap.width * scale)
  canvas.height = Math.round(bitmap.height * scale)
  canvas.getContext('2d')?.drawImage(bitmap, 0, 0, canvas.width, canvas.height)
  bitmap.close()
  return canvas.toDataURL('image/jpeg', 0.8)
}

function Login({ onLogin }: { onLogin: (s: Shop) => void }) {
  const [shops, setShops] = useState<Shop[]>([])
  const [shop, setShop] = useState('')
  const [pin, setPin] = useState('')
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    get<Shop[]>('/pos/shops').then((s) => {
      setShops(s)
      setShop(s.find((x) => x.id === 'shop_lumen')?.id ?? s[0]?.id ?? '')
    })
  }, [])

  async function submit(e: React.FormEvent) {
    e.preventDefault()
    try {
      onLogin(await post<Shop>('/pos/login', { merchant_id: shop, pin }))
    } catch (err) {
      setError(errorText(err))
    }
  }

  return (
    <form className="card" onSubmit={submit} style={{ maxWidth: 420, margin: '0 auto' }}>
      <h1>Open the till</h1>
      <label className="field">
        <span>Shop</span>
        <select value={shop} onChange={(e) => setShop(e.target.value)}>
          {shops.map((s) => (
            <option key={s.id} value={s.id}>
              {s.name} ({floorLabel(s.floor)})
            </option>
          ))}
        </select>
      </label>
      <label className="field">
        <span>PIN (demo shops use 1234)</span>
        <input type="password" inputMode="numeric" value={pin} onChange={(e) => setPin(e.target.value)} />
      </label>
      {error && <div className="notice bad">{error}</div>}
      <button className="btn block">Open the till</button>
    </form>
  )
}

function OrderInput({ lines, onDraft }: { lines: Line[]; onDraft: (d: Draft) => void }) {
  const { config } = useApp()
  const [text, setText] = useState('')
  const [busy, setBusy] = useState(false)
  const [listening, setListening] = useState(false)
  const [scanning, setScanning] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const hasCamera = Boolean(navigator.mediaDevices?.getUserMedia)

  async function ask(body: { text?: string; image?: string }) {
    setBusy(true)
    setError(null)
    try {
      // The bill on screen goes along, so "remove the tea" or "make it three" makes sense.
      onDraft(await post<Draft>('/pos/ai-bill', { ...body, cart: lines.map(({ name, qty }) => ({ name, qty })) }))
      setText('')
    } catch (e) {
      setError(errorText(e))
    } finally {
      setBusy(false)
    }
  }

  async function photo(e: React.ChangeEvent<HTMLInputElement>) {
    const file = e.target.files?.[0]
    e.target.value = ''
    if (!file) return
    try {
      await ask({ image: await shrinkPhoto(file) })
    } catch {
      setError('That photo could not be opened. Try another one.')
    }
  }

  async function sayOrder() {
    setError(null)
    setListening(true)
    const heard = await listen()
    setListening(false)
    if (heard[0]) await ask({ text: heard[0] })
    else setError(listenProblem() ?? 'Nothing was heard. Say it again, or type it.')
  }

  return (
    <div className="order-input">
      <form
        className="row"
        onSubmit={(e) => {
          e.preventDefault()
          if (text.trim()) void ask({ text })
        }}
      >
        <input type="text" aria-label="Add, remove or change items" maxLength={300} value={text} placeholder={lines.length ? 'Add a pen, or remove the notebook' : 'Two paperbacks and a notebook'} onChange={(e) => setText(e.target.value)} />
        <button className="btn" disabled={busy || !text.trim()}>
          {busy ? <Spinner /> : 'Go'}
        </button>
      </form>
      <div className="row wrap" style={{ marginTop: 8 }}>
        {config?.gemini && hasCamera && (
          <button type="button" className={`btn small ${scanning ? 'quiet' : ''}`} onClick={() => setScanning(!scanning)}>
            {scanning ? 'Close camera' : 'Scan with camera'}
          </button>
        )}
        {config?.gemini && !hasCamera && (
          <label className={`btn quiet small${busy ? ' disabled' : ''}`}>
            Snap the counter
            <input type="file" accept="image/*" capture="environment" hidden disabled={busy} onChange={photo} />
          </label>
        )}
        {canListen() && (
          <button type="button" className="btn quiet small" disabled={busy || listening} onClick={sayOrder}>
            {listening ? 'Listening…' : 'Say it'}
          </button>
        )}
        <span className="tiny">{config?.gemini ? 'Read by Google Gemini. Keep people out of the camera.' : 'Add AIRSIGN_GEMINI_API_KEY to use the camera.'}</span>
      </div>
      {scanning && <CameraScanner onAdd={onDraft} onClose={() => setScanning(false)} />}
      {error && <div className="notice warn" style={{ margin: '8px 0 0' }}>{error}</div>}
    </div>
  )
}

type Note = { text: string; ok: boolean; unmatched: string[]; source: Draft['source'] }

function NewInvoice({ lines, setLines, onCreated }: { lines: Line[]; setLines: React.Dispatch<React.SetStateAction<Line[]>>; onCreated: (inv: Invoice) => void }) {
  const [catalog, setCatalog] = useState<CatalogItem[]>([])
  const [note, setNote] = useState<Note | null>(null)
  const [amount, setAmount] = useState('')
  const [error, setError] = useState<string | null>(null)
  const total = cents(lines)

  useEffect(() => {
    get<CatalogItem[]>('/pos/catalog').then(setCatalog).catch(() => {})
  }, [])

  function add(name: string, price: string) {
    setLines((ls) => applyDraft(ls, { clear: false, ops: [{ action: 'add', name, qty: 1, price }] }))
  }

  /** Every input adds to the same bill. Only a spoken or typed "clear" empties it. */
  function applyAi(d: Draft) {
    setLines((ls) => applyDraft(ls, d))
    const change = describeDraft(d)
    setNote({ text: change ?? "That didn't match anything this shop sells. The bill is unchanged.", ok: Boolean(change), unmatched: d.unmatched, source: d.source })
  }

  async function create() {
    setError(null)
    try {
      const body = lines.length ? { items: lines } : { amount }
      onCreated(await post<Invoice>('/pos/invoices', body))
      setLines([])
      setAmount('')
      setNote(null)
    } catch (e) {
      setError(errorText(e))
    }
  }

  return (
    <div className="card">
      <div className="row" style={{ justifyContent: 'space-between' }}>
        <h2>New bill</h2>
        {lines.length > 0 && (
          <button className="btn ghost small" onClick={() => setLines([])}>
            Clear all
          </button>
        )}
      </div>
      <OrderInput lines={lines} onDraft={applyAi} />
      {note && (
        <div className={`notice ${note.ok ? 'ok' : 'warn'}`}>
          {note.text}
          {note.ok && note.source === 'keywords' && <span className="tiny"> (keyword match)</span>}
          {note.unmatched.length > 0 && <div className="tiny">Not in your catalog: {note.unmatched.join(', ')}</div>}
        </div>
      )}
      <div className="catalog">
        {catalog.map(({ name, price }) => (
          <button key={name} className="btn quiet small" onClick={() => add(name, price)}>
            <span>{name}</span>
            <span className="price">{price}</span>
          </button>
        ))}
      </div>
      {lines.length > 0 ? (
        <ul className="list small">
          {lines.map((l) => (
            <li key={l.name} className="item" style={{ cursor: 'default' }}>
              <span>
                {l.qty}× {l.name}
              </span>
              <span className="row">
                {money(l.qty * Math.round(parseFloat(l.price) * 100))}
                <button className="btn ghost small" onClick={() => setLines((ls) => ls.filter((x) => x.name !== l.name))} aria-label={`Remove ${l.name}`}>
                  ×
                </button>
              </span>
            </li>
          ))}
        </ul>
      ) : (
        <label className="field">
          <span>Or type an amount</span>
          <input inputMode="decimal" value={amount} placeholder="0.00" onChange={(e) => setAmount(e.target.value.replace(/[^0-9.]/g, ''))} />
        </label>
      )}
      {error && <div className="notice bad">{error}</div>}
      <button className="btn block" onClick={create} disabled={!lines.length && !(parseFloat(amount) > 0)}>
        Create bill {lines.length > 0 && `· ${money(total)}`}
      </button>
    </div>
  )
}

function CheckinCode({ invoice, label }: { invoice?: PosInvoice; label: string }) {
  const [tok, setTok] = useState<{ token: string; code: string } | null>(null)
  const [left, setLeft] = useState(30)
  const canvas = useRef<HTMLCanvasElement>(null)

  const refresh = useCallback(async () => {
    try {
      const t = await post<{ token: string; code: string }>('/pos/checkin-token', { invoice_id: invoice?.id ?? null })
      setTok(t)
      setLeft(30)
    } catch {
      setTok(null)
    }
  }, [invoice?.id])

  useEffect(() => {
    void refresh()
    const t = setInterval(refresh, 30000)
    const c = setInterval(() => setLeft((l) => Math.max(0, l - 1)), 1000)
    return () => {
      clearInterval(t)
      clearInterval(c)
    }
  }, [refresh])

  useEffect(() => {
    if (tok && canvas.current) {
      QRCode.toCanvas(canvas.current, `${window.location.origin}/c?t=${encodeURIComponent(tok.token)}`, { width: 200, margin: 1 })
    }
  }, [tok])

  return (
    <div className="center join-code">
      <p className="small muted">{label}</p>
      <div className="qr">
        <canvas ref={canvas} />
      </div>
      <div className="code">{tok?.code ?? '······'}</div>
      <p className="tiny">New code in {left}s. Each code works once.</p>
    </div>
  )
}

function NearbyList({ people, onPick, busy }: { people: Nearby[]; onPick: (p: Nearby) => void; busy: boolean }) {
  // Re-rendered by the cashier screen's polling, so the arrival times stay current.
  const [now, setNow] = useState(() => Date.now())
  useEffect(() => {
    const t = setInterval(() => setNow(Date.now()), 30000)
    return () => clearInterval(t)
  }, [])
  return (
    <ul className="list people">
      {people.map((p) => (
        <li key={p.id}>
          <button className="item" disabled={busy} onClick={() => onPick(p)}>
            <span className="row">
              <span className="code-chip">{p.code}</span>
              <span>
                <strong>{p.name}</strong>
                <br />
                <span className="tiny">
                  {minutesAgo(p.arrived_at, now)}
                  {p.via === 'chosen' && ` · chose ${p.chose ?? 'a shop'} on their phone`}
                  {p.via === 'code' && ' · joined with your code'}
                  {p.simulated && ' · simulated'}
                </span>
              </span>
            </span>
            <span className="send-label">Send</span>
          </button>
        </li>
      ))}
    </ul>
  )
}

type AskPhase = 'idle' | 'asking' | 'listening' | 'matching' | 'choose' | 'nobody' | 'unheard'

/** AI auto mode: the till asks for a name out loud and sends the bill on one clear match. */
function AskName({ invoice, autoAsk, onSent, onPick, busy }: { invoice: PosInvoice; autoAsk: boolean; onSent: () => void; onPick: (p: Nearby) => void; busy: boolean }) {
  const [phase, setPhase] = useState<AskPhase>('idle')
  const [candidates, setCandidates] = useState<Nearby[]>([])
  const [typed, setTyped] = useState('')
  const [error, setError] = useState<string | null>(null)
  const run = useRef(0)

  const match = useCallback(
    async (heard: string[], id: number) => {
      setPhase('matching')
      try {
        const r = await post<NameMatch>(`/pos/invoices/${invoice.id}/match-name`, { heard })
        if (id !== run.current) return
        if (r.status === 'sent') {
          onSent()
          void speak(`Sent to ${r.customer?.name}. Please sign on your phone.`)
        } else {
          setCandidates(r.candidates)
          setPhase(r.status)
          void speak(r.status === 'choose' ? 'Please choose your name on the screen.' : 'I could not find that name nearby.')
        }
      } catch (e) {
        setError(errorText(e))
        setPhase('idle')
      }
    },
    [invoice.id, onSent],
  )

  const ask = useCallback(async () => {
    const id = ++run.current
    setError(null)
    setPhase('asking')
    await speak('What name should I send this bill to?')
    if (id !== run.current) return
    if (!canListen()) {
      setPhase('idle')
      return
    }
    setPhase('listening')
    const heard = await listen()
    if (id !== run.current) return
    if (heard.length) await match(heard, id)
    else {
      setError(listenProblem())
      setPhase('unheard')
    }
  }, [match])

  useEffect(() => {
    if (autoAsk) void ask()
    return () => {
      run.current++
      stopSpeaking()
    }
  }, [autoAsk, ask])

  const status: Record<AskPhase, string> = {
    idle: 'Ask the customer their name',
    asking: 'Asking for a name…',
    listening: 'Listening…',
    matching: 'Looking for that name…',
    choose: 'Which one is it?',
    nobody: "That name isn't nearby",
    unheard: 'Nothing was heard',
  }

  return (
    <div>
      <div className={`voice ${phase}`} role="status" aria-live="polite">
        {['asking', 'listening', 'matching'].includes(phase) && <Spinner />} {status[phase]}
      </div>
      {phase === 'nobody' && <p className="small muted">They need AirSign open with "I'm at a shop" on.</p>}
      {phase === 'choose' && <NearbyList people={candidates} busy={busy} onPick={onPick} />}
      <form
        className="row"
        style={{ marginTop: 10 }}
        onSubmit={(e) => {
          e.preventDefault()
          if (!typed.trim()) return
          const id = ++run.current
          stopSpeaking()
          void match([typed], id)
          setTyped('')
        }}
      >
        <input type="text" aria-label="Customer name" maxLength={60} placeholder="Or type the name" value={typed} onChange={(e) => setTyped(e.target.value)} />
        <button className="btn quiet" disabled={!typed.trim()}>
          Send
        </button>
      </form>
      {canListen() && (
        <button className="btn ghost small" style={{ marginTop: 8 }} onClick={() => void ask()} disabled={['asking', 'listening', 'matching'].includes(phase)}>
          Ask again
        </button>
      )}
      {error && <div className="notice warn" style={{ marginTop: 8 }}>{error}</div>}
    </div>
  )
}

function BillPanel({ invoice, mode, people, autoAsk, reload }: { invoice: PosInvoice; mode: Mode; people: Nearby[]; autoAsk: boolean; reload: () => void }) {
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [wantCode, setWantCode] = useState(false)
  const showCode = wantCode || (people.length === 0 && mode === 'person')
  const p = invoice.payment
  const items = invoice.items.map((i) => `${i.qty}× ${i.name}`).join(', ')
  const signing = p && !['COMPLETED', 'CANCELLED', 'EXPIRED', 'LOCKED', 'FAILED'].includes(p.state)

  async function act(fn: () => Promise<unknown>) {
    setBusy(true)
    setError(null)
    try {
      await fn()
      reload()
    } catch (e) {
      setError(errorText(e))
    } finally {
      setBusy(false)
    }
  }

  const send = (person: Nearby | null) => act(() => post(`/pos/invoices/${invoice.id}/send`, { presence_id: person?.id ?? null }))

  if (invoice.status === 'PAID') {
    return (
      <div className="card center bill-panel">
        <CheckIcon />
        <h2>Paid with AirSign</h2>
        <div className="amount">{money(invoice.amount_cents, invoice.currency)}</div>
        <p className="muted small">
          {invoice.customer ? `${invoice.customer.name}. ` : ''}
          {p?.webhook_confirmed ? 'Confirmed by PayPal.' : 'Waiting for PayPal to confirm…'}
        </p>
        {p?.simulated && <SimBadge />}
      </div>
    )
  }
  if (invoice.status !== 'OPEN') {
    return (
      <div className="card center bill-panel">
        <div className="amount">{money(invoice.amount_cents, invoice.currency)}</div>
        <p className="muted">This bill is {invoice.status.toLowerCase()}.</p>
      </div>
    )
  }

  return (
    <div className="card bill-panel">
      <div className="center">
        <div className="amount">{money(invoice.amount_cents, invoice.currency)}</div>
        {items && <p className="muted small">{items}</p>}
      </div>
      {error && <div className="notice warn">{error}</div>}
      {invoice.customer ? (
        <>
          <div className="sent-to">
            {invoice.customer.code && <span className="code-chip">{invoice.customer.code}</span>}
            <span>
              {signing ? 'Signing' : 'Sent to'} <strong>{invoice.customer.name}</strong>
            </span>
          </div>
          <p className="small muted center">{signing ? 'Waiting for the air signature.' : 'Waiting for them to open it on their phone.'}</p>
          {!signing && (
            <button className="btn ghost block" disabled={busy} onClick={() => send(null)}>
              Send to someone else
            </button>
          )}
        </>
      ) : signing ? (
        <p className="notice">Someone is signing for this bill.</p>
      ) : mode === 'ai' ? (
        <AskName invoice={invoice} autoAsk={autoAsk} onSent={reload} onPick={send} busy={busy} />
      ) : people.length > 0 ? (
        <>
          <h3>Ask their name, then send</h3>
          <NearbyList people={people} busy={busy} onPick={send} />
          <p className="tiny">People with AirSign open in this building, which can include other floors. Check the code matches their phone.</p>
        </>
      ) : (
        <p className="notice">Nobody nearby yet. The customer can scan the code below to pay this bill.</p>
      )}
      {!invoice.customer && !signing && (
        <div className="fallbacks">
          {invoice.broadcast ? (
            <p className="small muted">Everyone nearby was alerted. They see your shop's name until they open it.</p>
          ) : (
            <button className="btn ghost small" disabled={busy} onClick={() => act(() => post(`/pos/invoices/${invoice.id}/broadcast`))}>
              Alert everyone nearby
            </button>
          )}
          <button className="btn ghost small" onClick={() => setWantCode(!showCode)}>
            {showCode ? 'Hide the code' : 'Customer not listed? Show a code'}
          </button>
        </div>
      )}
      {showCode && !invoice.customer && <CheckinCode invoice={invoice} label="Scan with the phone's camera, or type the code in AirSign. It opens this bill." />}
    </div>
  )
}

const LINES_KEY = 'airsign.pos-bill'

function savedLines(): Line[] {
  try {
    const v = JSON.parse(sessionStorage.getItem(LINES_KEY) ?? '[]')
    return Array.isArray(v) ? (v as Line[]) : []
  } catch {
    return []
  }
}

type Placed = { name: string; radius_m: number } | 'asking' | 'denied' | null
const PLACED_KEY = 'airsign.placed-'

/**
 * Demo only. The demo buildings have made-up addresses, so the till moves its building to
 * where it stands, once per session. Phones at the counter then find the shop by location.
 */
function usePlaceBuilding(shop: Shop | null | undefined, enabled: boolean): [Placed, () => void] {
  const [placed, setPlaced] = useState<Placed>(null)
  const key = shop ? PLACED_KEY + shop.id : null
  const place = useCallback(() => {
    if (!shop || !navigator.geolocation) return
    setPlaced('asking')
    navigator.geolocation.getCurrentPosition(
      (p) =>
        post<{ name: string; radius_m: number }>('/pos/sim/place-building', { lat: p.coords.latitude, lon: p.coords.longitude, accuracy: Math.max(1, p.coords.accuracy) })
          .then((r) => {
            setPlaced(r)
            try {
              sessionStorage.setItem(PLACED_KEY + shop.id, JSON.stringify(r))
            } catch {
              /* asks again next time */
            }
          })
          .catch(() => setPlaced(null)),
      () => setPlaced('denied'),
      { enableHighAccuracy: true, timeout: 15000, maximumAge: 60000 },
    )
  }, [shop])
  useEffect(() => {
    if (!key || !enabled) return
    let before: Placed = null
    try {
      before = JSON.parse(sessionStorage.getItem(key) ?? 'null') as Placed
    } catch {
      /* place it */
    }
    if (before) setPlaced(before)
    else place()
  }, [key, enabled, place])
  return [placed, place]
}

export default function Pos() {
  const { config } = useApp()
  const [shop, setShop] = useState<Shop | null | undefined>(undefined)
  // One bill being built, shared by taps, typing, the camera and the voice assistant.
  // Kept for this browser tab, so an accidental reload does not lose it.
  const [lines, setLines] = useState<Line[]>(savedLines)
  useEffect(() => {
    try {
      sessionStorage.setItem(LINES_KEY, JSON.stringify(lines))
    } catch {
      /* it just lasts until the page reloads */
    }
  }, [lines])
  const [placed, placeAgain] = usePlaceBuilding(shop, Boolean(config?.allow_simulation))
  const [invoices, setInvoices] = useState<PosInvoice[]>([])
  const [people, setPeople] = useState<Nearby[]>([])
  const [selected, setSelected] = useState<string | null>(null)
  const [justCreated, setJustCreated] = useState<string | null>(null)
  const [mode, setMode] = useState<Mode>(savedMode)
  const [showJoin, setShowJoin] = useState(false)

  useEffect(() => {
    get<Shop | null>('/pos/me').then(setShop).catch(() => setShop(null))
  }, [])

  const load = useCallback(async () => {
    try {
      const [inv, near] = await Promise.all([get<PosInvoice[]>('/pos/invoices'), get<Nearby[]>('/pos/nearby')])
      setInvoices(inv)
      setPeople(near)
    } catch {
      /* keep the last list */
    }
  }, [])

  useEffect(() => {
    if (!shop) return
    void load()
    const t = setInterval(load, 2000)
    return () => clearInterval(t)
  }, [shop, load])

  function chooseMode(m: Mode) {
    setMode(m)
    setJustCreated(null)
    try {
      localStorage.setItem(MODE_KEY, m)
    } catch {
      /* the choice just lasts until the page closes */
    }
  }

  const current = invoices.find((i) => i.id === selected)

  // Say how the payment ended, once, and only for a bill this screen saw open.
  const lastSeen = useRef<{ id: string; open: boolean } | null>(null)
  const status = current?.status
  const paymentState = current?.payment?.state
  const payer = current?.customer?.name
  useEffect(() => {
    if (!current) return
    const seen = lastSeen.current
    const open = status === 'OPEN' && paymentState !== 'LOCKED' && paymentState !== 'FAILED'
    if (mode === 'ai' && seen?.id === current.id && seen.open && !open) {
      void speak(status === 'PAID' ? `Payment received.${payer ? ` Thank you, ${payer}.` : ''}` : status === 'OPEN' ? 'That payment did not go through. Please try again.' : '')
    }
    lastSeen.current = { id: current.id, open }
  }, [current, status, paymentState, payer, mode])

  return (
    <div className="app wide">
      <TopBar />
      {shop === undefined ? (
        <Spinner />
      ) : shop === null ? (
        <Login onLogin={setShop} />
      ) : (
        <>
          <div className="till-head">
            <div>
              <h1>{shop.name}</h1>
              <span className="muted small">
                {floorLabel(shop.floor)} · {people.length} nearby
              </span>
            </div>
            <div className="segmented" role="group" aria-label="How bills reach customers">
              <button className={mode === 'person' ? 'on' : ''} aria-pressed={mode === 'person'} onClick={() => chooseMode('person')}>
                Person
              </button>
              <button className={mode === 'ai' ? 'on' : ''} aria-pressed={mode === 'ai'} onClick={() => chooseMode('ai')}>
                AI auto
              </button>
            </div>
            <button
              className="btn ghost small"
              onClick={async () => {
                await post('/pos/logout')
                setLines([])
                setShop(null)
              }}
            >
              Log out
            </button>
          </div>
          <div className="pos-grid">
            <div>
              {mode === 'ai' && (
                <VoiceAssistant
                  lines={lines}
                  setLines={setLines}
                  onCreated={(inv) => {
                    setSelected(inv.id)
                    setJustCreated(inv.id)
                    void load()
                  }}
                />
              )}
              <NewInvoice
                lines={lines}
                setLines={setLines}
                onCreated={(inv) => {
                  setSelected(inv.id)
                  setJustCreated(mode === 'ai' ? inv.id : null)
                  void load()
                }}
              />
              <details className="card flat">
                <summary>Today's bills ({invoices.length})</summary>
                {invoices.length > 0 && (
                  <table className="table">
                    <thead>
                      <tr>
                        <th>Time</th>
                        <th>Amount</th>
                        <th>Status</th>
                        <th></th>
                      </tr>
                    </thead>
                    <tbody>
                      {invoices.map((inv) => (
                        <tr key={inv.id} className={inv.id === selected ? 'selected' : ''} onClick={() => setSelected(inv.id)} style={{ cursor: 'pointer' }}>
                          <td>{new Date(inv.created_at).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}</td>
                          <td>{money(inv.amount_cents, inv.currency)}</td>
                          <td>
                            <span className={`badge ${inv.status === 'PAID' ? 'ok' : inv.status === 'OPEN' ? 'warn' : ''}`}>
                              {inv.status === 'OPEN' && inv.payment ? inv.payment.state.toLowerCase() : inv.status.toLowerCase()}
                            </span>{' '}
                            {inv.payment?.simulated && <SimBadge>sim</SimBadge>}
                          </td>
                          <td>
                            {inv.status === 'OPEN' && (
                              <button
                                className="btn ghost small"
                                onClick={async (e) => {
                                  e.stopPropagation()
                                  await post(`/pos/invoices/${inv.id}/cancel`).catch(() => {})
                                  void load()
                                }}
                              >
                                Void
                              </button>
                            )}
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                )}
              </details>
              {config?.allow_simulation && (
                <DemoControls>
                  <p className="small muted">
                    {placed === 'asking'
                      ? 'Finding where this till is…'
                      : placed === 'denied'
                        ? "Location is off for this till, so phones can't find the shop by location. Customers can still choose the shop on their phone, or scan your code."
                        : placed
                          ? `${placed.name} is placed where this till is${placed.radius_m > 100 ? ` (about ${Math.round(placed.radius_m)} m across, since this device's location is rough)` : ''}, so phones here find it.`
                          : 'The demo shops have made-up addresses. Place this shop where the till is so phones here find it.'}
                  </p>
                  <button className="btn sim small" disabled={placed === 'asking'} onClick={placeAgain}>
                    Place {shop.name}'s building here
                  </button>
                </DemoControls>
              )}
            </div>
            <div>
              {current ? (
                <BillPanel key={`${current.id}-${mode}`} invoice={current} mode={mode} people={people} autoAsk={justCreated === current.id} reload={load} />
              ) : (
                <div className="card bill-panel empty">
                  <h2>No bill open</h2>
                  <p className="muted small">{people.length ? `${people.length} ${people.length === 1 ? 'customer is' : 'customers are'} nearby.` : 'Nobody on the list yet.'}</p>
                  {people.length > 0 && (
                    <ul className="list people">
                      {people.map((p) => (
                        <li key={p.id} className="item">
                          <span className="row">
                            <span className="code-chip">{p.code}</span> <strong>{p.name}</strong>
                          </span>
                        </li>
                      ))}
                    </ul>
                  )}
                  {showJoin || people.length === 0 ? (
                    <CheckinCode label="Customer not listed? They scan this, or type the code in AirSign." />
                  ) : (
                    <button className="btn ghost small" onClick={() => setShowJoin(true)}>
                      Customer not listed? Show a code
                    </button>
                  )}
                </div>
              )}
            </div>
          </div>
        </>
      )}
    </div>
  )
}
