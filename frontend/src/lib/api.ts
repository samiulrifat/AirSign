// Thin client for the AirSign API. Every error comes back as { code, message }.

export class ApiError extends Error {
  code: string
  status: number
  constructor(code: string, message: string, status: number) {
    super(message)
    this.code = code
    this.status = status
  }
}

async function request<T>(method: string, path: string, body?: unknown): Promise<T> {
  const res = await fetch(`/api${path}`, {
    method,
    credentials: 'same-origin',
    headers: body === undefined ? undefined : { 'Content-Type': 'application/json' },
    body: body === undefined ? undefined : JSON.stringify(body),
  })
  const data = await res.json().catch(() => ({}))
  if (!res.ok) {
    throw new ApiError(data.code ?? 'error', data.message ?? `Request failed (${res.status})`, res.status)
  }
  return data as T
}

export const get = <T>(path: string) => request<T>('GET', path)
export const post = <T>(path: string, body: unknown = {}) => request<T>('POST', path, body)
export const del = <T>(path: string) => request<T>('DELETE', path)

// Types -----------------------------------------------------------------------------

export type Config = {
  paypal_mode: 'mock' | 'sandbox'
  paypal_client_id: string | null
  allow_simulation: boolean
  currency: string
  neural_model: boolean
  gemini: boolean
}

export type Me = {
  user: { id: string; display_name: string } | null
  device_id?: string
  signature?: { repetitions: number; method: string; simulated: boolean; created_at: string } | null
  enrollment?: { accepted: number; rejected: number; simulated: boolean } | null
  synthetic_history?: number
  wallet?: { status: string; email: string | null } | null
}

export type Shop = {
  id: string
  name: string
  floor: number
  building_id: string
  category: string
  open_now?: boolean
  probability?: number
}

export type Building = { id: string; name: string; lat: number; lon: number; shops: Shop[] }

export type Nearby =
  | { status: 'ok'; building: { id: string; name: string; distance_m: number }; suggestion: string | null; shops: Shop[] }
  | { status: 'none' | 'low_accuracy'; message: string }

export type Invoice = {
  id: string
  amount_cents: number
  currency: string
  items: { name: string; qty: number; price_cents: number }[]
  status: string
  created_at: string
}

export type Risk = {
  tier: 1 | 2 | 3
  score: number
  explanation: string
  explanation_source: 'gemini' | 'template'
  contributions: Record<string, number>
  ml_score: number | null
  history_is_synthetic: boolean
}

export type Intent = {
  id: string
  state: string
  invoice: Invoice
  shop: Shop
  amount_cents: number
  currency: string
  funding: 'order' | 'wallet'
  nonce?: string | null
  paypal: { mode: string; order_id: string | null; capture_id: string | null; webhook_confirmed: boolean }
  risk: Risk | null
  signatures_required: number
  signatures_passed: number
  attempts_left: number
  location_verified: boolean
  confirmed_by: string
  simulated: boolean
  failure_reason: string | null
  created_at: string
  completed_at: string | null
  attempts: { decision: string; reason: string; flags: string[]; simulated: boolean; latency_ms: number | null }[]
}

export type SignoffResult = {
  decision: 'ACCEPT' | 'STEP_UP' | 'REJECT' | 'UNUSABLE'
  reason: string
  state: string
  attempts_left: number
  signatures_passed: number
  signatures_required: number
  nonce: string | null
  flags: string[]
  replay_flags?: string[]
  sensor_flags?: string[]
  match?: { z: number; k: number; method: string; z_dtw: number; z_nn: number | null }
  intent: Intent
}

export type DeviceInfo = { platform: string; nominal_hz: number | null; accel_includes_gravity: boolean }

export type Recording = { samples: number[][]; device: DeviceInfo; simulated: boolean; label?: string }

export type SimKind = 'genuine' | 'imitation' | 'impostor' | 'injection' | 'shake'
