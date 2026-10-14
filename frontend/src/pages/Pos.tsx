import { useCallback, useEffect, useRef, useState } from 'react'
import QRCode from 'qrcode'
import { get, post, type Invoice, type Shop } from '../lib/api'
import { errorText, floorLabel, money } from '../lib/app'
import { SimBadge, Spinner, TopBar } from '../components/bits'

type PosInvoice = Invoice & { payment: { state: string; tier: number | null; simulated: boolean; webhook_confirmed: boolean } | null }
type Line = { name: string; qty: number; price: string }

const PRESETS: Record<string, [string, string][]> = {
  cafe: [['Flat white', '4.80'], ['Croissant', '3.60'], ['Cold brew', '5.20'], ['Sandwich', '8.90']],
  books: [['Paperback', '14.99'], ['Hardcover', '27.50'], ['Notebook', '9.00'], ['Gift card', '50.00']],
  electronics: [['Screen repair', '189.00'], ['Battery swap', '79.00'], ['Charging cable', '19.00'], ['Case', '24.00']],
  grocery: [['Fruit box', '12.40'], ['Bread', '4.20'], ['Cheese', '9.75'], ['Olive oil', '15.00']],
  clothing: [['T-shirt', '25.00'], ['Jacket', '140.00'], ['Socks', '9.00'], ['Cap', '22.00']],
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

function NewInvoice({ shop, onCreated }: { shop: Shop; onCreated: (inv: Invoice) => void }) {
  const [lines, setLines] = useState<Line[]>([])
  const [amount, setAmount] = useState('')
  const [error, setError] = useState<string | null>(null)
  const total = lines.reduce((t, l) => t + l.qty * Math.round(parseFloat(l.price) * 100), 0)

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
    } catch (e) {
      setError(errorText(e))
    }
  }

  return (
    <div className="card">
      <h2>New bill</h2>
      <div className="grid2" style={{ marginBottom: 12 }}>
        {(PRESETS[shop.category] ?? PRESETS.cafe).map(([name, price]) => (
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
          <p className="small muted">Customer: scan to check in at this counter, or type the code.</p>
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
                shop={shop}
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
