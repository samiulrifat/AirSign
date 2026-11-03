import { useCallback, useEffect, useRef, useState } from 'react'
import QRCode from 'qrcode'
import { get, post, type Invoice, type Shop } from '../lib/api'
import { errorText, floorLabel, money, useApp } from '../lib/app'
import { SimBadge, Spinner, TopBar } from '../components/bits'

type PosInvoice = Invoice & { payment: { state: string; tier: number | null; simulated: boolean; webhook_confirmed: boolean } | null }
type Line = { name: string; qty: number; price: string }

type Draft = { lines: Line[]; unmatched: string[]; source: 'gemini' | 'keywords' }
type CatalogItem = { name: string; price: string }

const PHOTO_MAX_PX = 1024

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

type SpeechResult = { results: { 0: { transcript: string } }[] }
type Recognition = { lang: string; onresult: (e: SpeechResult) => void; onend: () => void; onerror: () => void; start: () => void }

function speechRecognition(): (new () => Recognition) | undefined {
  const w = window as unknown as { SpeechRecognition?: new () => Recognition; webkitSpeechRecognition?: new () => Recognition }
  return w.SpeechRecognition ?? w.webkitSpeechRecognition
}

function AiCashier({ onDraft }: { onDraft: (d: Draft) => void }) {
  const { config } = useApp()
  const [text, setText] = useState('')
  const [busy, setBusy] = useState(false)
  const [listening, setListening] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const Speech = speechRecognition()

  async function ask(body: { text?: string; image?: string }) {
    setBusy(true)
    setError(null)
    try {
      onDraft(await post<Draft>('/pos/ai-bill', body))
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

  function listen() {
    if (!Speech) return
    const r = new Speech()
    r.lang = navigator.language
    r.onresult = (e) => void ask({ text: e.results[0][0].transcript })
    r.onerror = () => setError('Could not hear that. Type the order instead.')
    r.onend = () => setListening(false)
    setError(null)
    setListening(true)
    r.start()
  }

  return (
    <div className="ai-box">
      <div className="row between">
        <h3 style={{ margin: 0 }}>AI cashier</h3>
        <span className="badge accent">{config?.gemini ? 'Google Gemini' : 'Keyword match'}</span>
      </div>
      <form
        className="row"
        style={{ marginTop: 10 }}
        onSubmit={(e) => {
          e.preventDefault()
          if (text.trim()) void ask({ text })
        }}
      >
        <input
          type="text"
          aria-label="Describe the order"
          maxLength={300}
          value={text}
          placeholder="Two paperbacks and a notebook"
          onChange={(e) => setText(e.target.value)}
          style={{ flex: 1, minWidth: 0 }}
        />
        <button className="btn small" disabled={busy || !text.trim()}>
          {busy ? <Spinner /> : 'Draft'}
        </button>
      </form>
      <div className="grid2" style={{ marginTop: 8 }}>
        {config?.gemini ? (
          <label className={`btn quiet small${busy ? ' disabled' : ''}`}>
            Snap the counter
            <input type="file" accept="image/*" capture="environment" hidden disabled={busy} onChange={photo} />
          </label>
        ) : (
          <button className="btn quiet small" disabled title="Needs AIRSIGN_GEMINI_API_KEY on the server">
            Photo needs Gemini
          </button>
        )}
        {Speech && (
          <button type="button" className="btn quiet small" disabled={busy || listening} onClick={listen}>
            {listening ? 'Listening…' : 'Say the order'}
          </button>
        )}
      </div>
      {config?.gemini && <p className="tiny" style={{ marginTop: 8 }}>Photos and orders go to Google Gemini. Keep people out of the shot.</p>}
      {error && <div className="notice warn" style={{ marginTop: 8 }}>{error}</div>}
    </div>
  )
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
      <h1>Cashier screen</h1>
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

function NewInvoice({ onCreated }: { onCreated: (inv: Invoice) => void }) {
  const [catalog, setCatalog] = useState<CatalogItem[]>([])
  const [draft, setDraft] = useState<Draft | null>(null)
  const [lines, setLines] = useState<Line[]>([])
  const [amount, setAmount] = useState('')
  const [error, setError] = useState<string | null>(null)
  const total = lines.reduce((t, l) => t + l.qty * Math.round(parseFloat(l.price) * 100), 0)

  useEffect(() => {
    get<CatalogItem[]>('/pos/catalog').then(setCatalog).catch(() => {})
  }, [])

  function add(name: string, price: string) {
    setLines((ls) => {
      const i = ls.findIndex((l) => l.name === name)
      if (i >= 0) return ls.map((l, j) => (j === i ? { ...l, qty: l.qty + 1 } : l))
      return [...ls, { name, qty: 1, price }]
    })
  }

  async function create() {
    setError(null)
    try {
      const body = lines.length ? { items: lines } : { amount }
      onCreated(await post<Invoice>('/pos/invoices', body))
      setLines([])
      setAmount('')
      setDraft(null)
    } catch (e) {
      setError(errorText(e))
    }
  }

  return (
    <div className="card">
      <h2>New bill</h2>
      <AiCashier
        onDraft={(d) => {
          setDraft(d)
          setLines(d.lines)
        }}
      />
      {draft && (
        <div className={`notice ${draft.lines.length ? 'ok' : 'warn'}`} style={{ marginBottom: 12 }}>
          {draft.lines.length ? `${draft.source === 'gemini' ? 'Drafted by Gemini' : 'Drafted by keyword match'}. Check it before creating the bill.` : 'Nothing matched this shop’s items.'}
          {draft.unmatched.length > 0 && <div className="tiny">Not in your catalog: {draft.unmatched.join(', ')}</div>}
        </div>
      )}
      <div className="grid2" style={{ marginBottom: 12 }}>
        {catalog.map(({ name, price }) => (
          <button key={name} className="btn quiet small" onClick={() => add(name, price)}>
            {name} · {price}
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

function CheckinPanel({ invoice }: { invoice: PosInvoice }) {
  const [tok, setTok] = useState<{ token: string; code: string } | null>(null)
  const [left, setLeft] = useState(30)
  const canvas = useRef<HTMLCanvasElement>(null)

  const refresh = useCallback(async () => {
    try {
      const t = await post<{ token: string; code: string }>('/pos/checkin-token', { invoice_id: invoice.id })
      setTok(t)
      setLeft(30)
    } catch {
      setTok(null)
    }
  }, [invoice.id])

  useEffect(() => {
    if (invoice.status !== 'OPEN') return
    refresh()
    const t = setInterval(refresh, 30000)
    const c = setInterval(() => setLeft((l) => Math.max(0, l - 1)), 1000)
    return () => {
      clearInterval(t)
      clearInterval(c)
    }
  }, [invoice.id, invoice.status, refresh])

  useEffect(() => {
    if (tok && canvas.current) {
      QRCode.toCanvas(canvas.current, `${window.location.origin}/c?t=${encodeURIComponent(tok.token)}`, { width: 220, margin: 1 })
    }
  }, [tok])

  const p = invoice.payment
  return (
    <div className="card center">
      <p className="tiny">Bill {invoice.id}</p>
      <div className="amount">{money(invoice.amount_cents, invoice.currency)}</div>
      {invoice.status === 'PAID' ? (
        <div className="notice ok">
          Paid with AirSign{p?.tier ? ` (tier ${p.tier})` : ''}. {p?.webhook_confirmed ? 'PayPal webhook confirmed.' : 'Waiting for the PayPal webhook…'}
        </div>
      ) : invoice.status !== 'OPEN' ? (
        <div className="notice">This bill is {invoice.status.toLowerCase()}.</div>
      ) : (
        <>
          {p && <div className="notice warn">Customer is paying: {p.state.toLowerCase()}</div>}
          <p className="small muted">Phones here with bill alerts on have been alerted. Or scan to check in, or type the code.</p>
          <div className="qr">
            <canvas ref={canvas} />
          </div>
          <div className="code">{tok?.code ?? '······'}</div>
          <p className="tiny">New code in {left}s. Each code works once.</p>
        </>
      )}
    </div>
  )
}

export default function Pos() {
  const [shop, setShop] = useState<Shop | null | undefined>(undefined)
  const [invoices, setInvoices] = useState<PosInvoice[]>([])
  const [selected, setSelected] = useState<string | null>(null)

  useEffect(() => {
    get<Shop | null>('/pos/me').then(setShop).catch(() => setShop(null))
  }, [])

  const load = useCallback(async () => {
    try {
      setInvoices(await get<PosInvoice[]>('/pos/invoices'))
    } catch {
      /* keep the last list */
    }
  }, [])

  useEffect(() => {
    if (!shop) return
    load()
    const t = setInterval(load, 2000)
    return () => clearInterval(t)
  }, [shop, load])

  const current = invoices.find((i) => i.id === selected)

  return (
    <div className="app wide">
      <TopBar />
      {shop === undefined ? (
        <Spinner />
      ) : shop === null ? (
        <Login onLogin={setShop} />
      ) : (
        <>
          <div className="row between" style={{ marginBottom: 12 }}>
            <div>
              <h1 style={{ margin: 0 }}>{shop.name}</h1>
              <span className="muted small">{floorLabel(shop.floor)} · cashier screen</span>
            </div>
            <button
              className="btn ghost small"
              onClick={async () => {
                await post('/pos/logout')
                setShop(null)
              }}
            >
              Log out
            </button>
          </div>
          <div className="pos-grid">
            <div>
              <NewInvoice
                onCreated={(inv) => {
                  setSelected(inv.id)
                  load()
                }}
              />
              <div className="card">
                <h2>Today's bills</h2>
                {invoices.length === 0 ? (
                  <p className="muted small">No bills yet.</p>
                ) : (
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
                                  load()
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
              </div>
            </div>
            <div>{current ? <CheckinPanel invoice={current} /> : <div className="card muted">Create or select a bill to show its check-in code.</div>}</div>
          </div>
        </>
      )}
    </div>
  )
}
