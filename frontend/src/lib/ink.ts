// Turns a motion recording into a pen stroke that looks like a handwritten signature.
//
// This is only a picture for the person signing; the verifier works on the raw
// sensor data. People sign in the air in two ways, often mixed: moving the phone
// like a pen (in-plane translation, seen by the accelerometer) and turning the
// wrist (rotation, seen by the gyroscope). We reconstruct both, line them up with
// each other, and draw the result with ink that thins where the hand moved fast.
//
// Rows are [t_ms, ax, ay, az, gx, gy, gz]: m/s^2 and degrees per second, in the
// phone's own frame (x right, y up along the screen).

export type Ink = { pts: [number, number][]; widths: number[]; aspect: number }

const D2R = Math.PI / 180
const ACTIVE_DPS = 40 // rotation rate that counts as "signing"
const SHAKE_FRACTION = 0.17 // of the fastest moment: slower than this at either end is a shake
// A burst at either end, cut off by a pause, is a jolt if it is short and much slower than
// the signature. A faster one could be a real last stroke, like the dot on an "i".
const SHAKE_MAX_S = 0.3
const JOLT_FRACTION = 0.32
const SHAKE_GAP_S = 0.15 // the pause that separates a jolt from the signature
const SMOOTH_S = 0.1 // smooths hand tremor out of the line, without rounding off strokes
const STILL_DPS = 8 // slower than this the wrist is not writing, so the pen does not move

function movingAverage(v: number[], k: number): number[] {
  if (k <= 1 || v.length < 3) return v.slice()
  const out = new Array(v.length)
  const h = Math.floor(k / 2)
  for (let i = 0; i < v.length; i++) {
    let s = 0
    let n = 0
    for (let j = Math.max(0, i - h); j <= Math.min(v.length - 1, i + h); j++) {
      s += v[j]
      n++
    }
    out[i] = s / n
  }
  return out
}

function integrate(v: number[], dt: number[]): number[] {
  const out = new Array(v.length)
  let s = 0
  for (let i = 0; i < v.length; i++) {
    s += v[i] * dt[i]
    out[i] = s
  }
  return out
}

/** Remove the straight line between the first and last value (the hand is still at both ends). */
function detrend(v: number[]): number[] {
  const n = v.length
  if (n < 2) return v
  const a = v[0]
  const b = v[n - 1]
  return v.map((x, i) => x - (a + ((b - a) * i) / (n - 1)))
}

function standardize(v: number[]): { z: number[]; sd: number } {
  const n = v.length
  const m = v.reduce((s, x) => s + x, 0) / n
  const sd = Math.sqrt(v.reduce((s, x) => s + (x - m) ** 2, 0) / n)
  return { z: sd > 1e-9 ? v.map((x) => (x - m) / sd) : v.map(() => 0), sd }
}

function corr(a: number[], b: number[]): number {
  let s = 0
  for (let i = 0; i < a.length; i++) s += a[i] * b[i]
  return s / a.length
}

/**
 * Combine several estimates of one screen axis. The first candidate (from the
 * accelerometer) sets the direction when it agrees with the others; rotation
 * estimates are flipped to match it, because their sign depends on how the
 * phone is held.
 */
function fuse(cands: number[][]): number[] {
  const zs = cands.map((c) => standardize(c)).filter((c) => c.sd > 1e-9).map((c) => c.z)
  if (!zs.length) return cands[0].map(() => 0)
  // Reference: the candidate that agrees most with the rest.
  let ref = 0
  let bestAgreement = -1
  zs.forEach((z, i) => {
    const agreement = zs.reduce((s, o, j) => (i === j ? s : s + Math.abs(corr(z, o))), 0)
    if (agreement > bestAgreement + 1e-6) {
      bestAgreement = agreement
      ref = i
    }
  })
  const out = zs[ref].slice()
  zs.forEach((z, i) => {
    if (i === ref) return
    const c = corr(zs[ref], z)
    if (Math.abs(c) < 0.2) return
    for (let k = 0; k < out.length; k++) out[k] += Math.sign(c) * Math.abs(c) * z[k]
  })
  return out
}

/** A window of about `seconds`, in samples, whatever rate this phone reports at. */
function windowFor(rows: number[][], seconds: number): number {
  const meanMs = (rows[rows.length - 1][0] - rows[0][0]) / Math.max(1, rows.length - 1)
  return Math.max(3, Math.round((seconds * 1000) / Math.max(meanMs, 1))) | 1
}

/**
 * The signature inside a recording, without small shakes before or after it. Quiet
 * shakes fall below the threshold, which scales with how hard this person signed. Short,
 * weak jolts cut off by a pause are dropped too. Pauses inside the signature stay.
 */
function activeRange(rows: number[][]): [number, number] | null {
  const rate = movingAverage(
    rows.map((r) => Math.hypot(r[4], r[5], r[6])),
    windowFor(rows, 0.08),
  )
  const peak = Math.max(...rate)
  const threshold = Math.max(ACTIVE_DPS, peak * SHAKE_FRACTION)
  const gap = windowFor(rows, SHAKE_GAP_S)
  const groups: [number, number][] = []
  rate.forEach((x, i) => {
    if (x <= threshold) return
    const last = groups[groups.length - 1]
    if (last && i - last[1] <= gap) last[1] = i
    else groups.push([i, i])
  })
  if (!groups.length) return null
  const jolt = windowFor(rows, SHAKE_MAX_S)
  const isJolt = ([a, b]: [number, number]) => b - a + 1 < jolt && Math.max(...rate.slice(a, b + 1)) < peak * JOLT_FRACTION
  while (groups.length > 1 && isJolt(groups[0])) groups.shift()
  while (groups.length > 1 && isJolt(groups[groups.length - 1])) groups.pop()
  const s = Math.max(0, groups[0][0] - 3)
  const e = Math.min(rows.length - 1, groups[groups.length - 1][1] + 3)
  return e - s >= 10 ? [s, e] : null
}

export function computeInk(rows: number[][], accelIncludesGravity = false): Ink | null {
  if (rows.length < 20) return null
  const range = activeRange(rows)
  if (!range) return null
  const [s, e] = range
  const seg = rows.slice(s, e + 1)
  const dt = seg.map((r, i) => (i ? Math.min(0.05, Math.max(0, (r[0] - seg[i - 1][0]) / 1000)) : 0))

  // Acceleration in the screen plane, without offsets (or gravity, if included).
  const acc = (col: number) => {
    const v = seg.map((r) => r[col])
    if (accelIncludesGravity) {
      const slow = movingAverage(v, 31)
      return v.map((x, i) => x - slow[i])
    }
    const bias = v.slice(0, 4).reduce((a, b) => a + b, 0) / Math.min(4, v.length)
    return v.map((x) => x - bias)
  }
  // Pen position from acceleration: velocity is zero at both ends of the stroke.
  const px = integrate(detrend(integrate(acc(1), dt)), dt)
  const py = integrate(detrend(integrate(acc(2), dt)), dt)
  // Pen position from wrist rotation: angle turned about each axis.
  const turned = (col: number) => integrate(seg.map((r) => (Math.abs(r[col]) < STILL_DPS ? 0 : r[col] * D2R)), dt)
  const ox = turned(4)
  const oy = turned(5)
  const oz = turned(6)

  let X = fuse([px, oy, oz])
  let Y = fuse([py, ox]).map((y) => -y) // screen y grows downwards
  // Two passes make a gentle bell-shaped filter, which rounds strokes less than one long pass.
  const k = windowFor(seg, SMOOTH_S)
  X = movingAverage(movingAverage(X, k), k)
  Y = movingAverage(movingAverage(Y, k), k)

  // Signatures are wider than tall; keep the measured proportion within reason.
  const sdx = standardize(px).sd
  const sdy = standardize(py).sd
  const aspect = sdx > 1e-6 && sdy > 1e-6 ? Math.min(3, Math.max(1, sdx / sdy)) : 1.8

  // Ink width: thinner where the hand moved fast, like a real pen.
  const speed = X.map((x, i) => (i ? Math.hypot(x - X[i - 1], Y[i] - Y[i - 1]) : 0))
  const sorted = speed.slice().sort((a, b) => a - b)
  const fast = sorted[Math.floor(sorted.length * 0.9)] || 1
  const widths = movingAverage(
    speed.map((v) => 1 - Math.min(1, v / fast) * 0.75),
    7,
  )
  return { pts: X.map((x, i) => [x * aspect, Y[i]]), widths, aspect }
}

export function inkColor(el: Element): string {
  return getComputedStyle(el).getPropertyValue('--accent').trim() || '#1f35c4'
}

/**
 * Draw `ink` into a canvas, fitted with padding. `progress` (0..1) draws only the
 * first part of the stroke, for the replay animation.
 */
export function drawInk(canvas: HTMLCanvasElement, ink: Ink | null, progress = 1, maxWidthPx = 4.5) {
  const dpr = window.devicePixelRatio || 1
  const w = (canvas.width = Math.round(canvas.clientWidth * dpr))
  const h = (canvas.height = Math.round(canvas.clientHeight * dpr))
  const ctx = canvas.getContext('2d')
  if (!ctx) return
  ctx.clearRect(0, 0, w, h)
  if (!ink || ink.pts.length < 2) return
  const xs = ink.pts.map((p) => p[0])
  const ys = ink.pts.map((p) => p[1])
  const minX = Math.min(...xs)
  const maxX = Math.max(...xs)
  const minY = Math.min(...ys)
  const maxY = Math.max(...ys)
  const pad = 0.12
  const scale = Math.min((w * (1 - 2 * pad)) / Math.max(maxX - minX, 1e-6), (h * (1 - 2 * pad)) / Math.max(maxY - minY, 1e-6))
  const ox = (w - (maxX - minX) * scale) / 2
  const oy = (h - (maxY - minY) * scale) / 2
  const map = (p: [number, number]) => [ox + (p[0] - minX) * scale, oy + (p[1] - minY) * scale]

  ctx.strokeStyle = inkColor(canvas)
  ctx.lineCap = 'round'
  ctx.lineJoin = 'round'
  const n = Math.max(2, Math.floor(ink.pts.length * Math.min(1, progress)))
  for (let i = 1; i < n; i++) {
    const [x0, y0] = map(ink.pts[i - 1])
    const [x1, y1] = map(ink.pts[i])
    ctx.lineWidth = Math.max(0.8, ink.widths[i] * maxWidthPx) * dpr
    ctx.beginPath()
    ctx.moveTo(x0, y0)
    ctx.lineTo(x1, y1)
    ctx.stroke()
  }
}
