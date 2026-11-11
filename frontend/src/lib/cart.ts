// The bill on the till, built from taps, typing, voice and the camera. Every source
// adds to the same bill: nothing replaces it except "clear", said or typed on purpose.

import { money } from './app'

export type Line = { name: string; qty: number; price: string }
export type Op = { action: 'add' | 'remove' | 'set'; name: string; qty: number; price: string }
export type Draft = { clear: boolean; ops: Op[]; lines: Line[]; unmatched: string[]; source: 'gemini' | 'keywords' }

const MAX_QTY = 99

export function cents(lines: Line[]): number {
  return lines.reduce((t, l) => t + l.qty * Math.round(parseFloat(l.price) * 100), 0)
}

/** The bill after the AI's changes, in order. "Remove" with no number takes the whole line off. */
export function applyDraft(lines: Line[], d: Pick<Draft, 'clear' | 'ops'>): Line[] {
  const out = d.clear ? [] : lines.map((l) => ({ ...l }))
  for (const op of d.ops) {
    const i = out.findIndex((l) => l.name === op.name)
    if (op.action === 'add') {
      if (i >= 0) out[i].qty = Math.min(MAX_QTY, out[i].qty + op.qty)
      else out.push({ name: op.name, qty: op.qty, price: op.price })
    } else if (op.action === 'remove') {
      if (i < 0) continue
      if (op.qty === 0 || op.qty >= out[i].qty) out.splice(i, 1)
      else out[i].qty -= op.qty
    } else if (op.qty === 0) {
      if (i >= 0) out.splice(i, 1)
    } else if (i >= 0) {
      out[i].qty = Math.min(MAX_QTY, op.qty)
    } else {
      out.push({ name: op.name, qty: Math.min(MAX_QTY, op.qty), price: op.price })
    }
  }
  return out
}

/** "Added 2× Tea. Took off Croissant." for the screen, or null when nothing changed. */
export function describeDraft(d: Pick<Draft, 'clear' | 'ops'>): string | null {
  const parts: string[] = []
  if (d.clear) parts.push('Cleared the bill.')
  const of = (a: Op['action']) => d.ops.filter((o) => o.action === a)
  const added = of('add').map((o) => `${o.qty}× ${o.name}`)
  const removed = of('remove').map((o) => (o.qty ? `${o.qty}× ${o.name}` : o.name))
  const set = of('set').map((o) => (o.qty ? `${o.name} to ${o.qty}` : `${o.name} off`))
  if (added.length) parts.push(`Added ${added.join(', ')}.`)
  if (removed.length) parts.push(`Took off ${removed.join(', ')}.`)
  if (set.length) parts.push(`Changed ${set.join(', ')}.`)
  return parts.length ? parts.join(' ') : null
}

/** How the till reads the bill back: "2 Tea, 1 Muffin. That's $10.70." */
export function readBack(lines: Line[]): string {
  if (!lines.length) return 'The bill is empty.'
  return `${lines.map((l) => `${l.qty} ${l.name}`).join(', ')}. That's ${money(cents(lines))}.`
}
