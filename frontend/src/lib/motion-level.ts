// How much the phone is really moving, for deciding when a signature starts and stops.
//
// Raw sensor rows jump around with every small shake, so these decisions use a short
// average. The rows sent to the server stay untouched: its fake-data check looks for
// the natural noise of real sensors, and filtering it out here would fail real phones.

const WINDOW_MS = 100

export class MotionLevel {
  private recent: [number, number, number][] = []

  /** The average rotation (deg/s) and acceleration (m/s^2) over the last moment. */
  push(t: number, rot: number, acc: number): { rot: number; acc: number } {
    this.recent.push([t, rot, acc])
    while (this.recent.length > 1 && this.recent[0][0] < t - WINDOW_MS) this.recent.shift()
    const n = this.recent.length
    return {
      rot: this.recent.reduce((s, r) => s + r[1], 0) / n,
      acc: this.recent.reduce((s, r) => s + r[2], 0) / n,
    }
  }
}

const PEN_SMOOTHING_MS = 70
const PEN_DEADBAND_DPS = 12

/** The live pen on the pad: follows the hand, without drawing each small shake. */
export class LivePen {
  x = 0
  y = 0
  pts: [number, number][] = [[0, 0]]
  private rateX = 0
  private rateY = 0
  private last: number

  constructor(t: number) {
    this.last = t
  }

  /** Rates are in deg/s, about the phone's x and y axes. */
  push(t: number, gx: number, gy: number) {
    const dtMs = Math.min(50, Math.max(0, t - this.last))
    this.last = t
    const k = 1 - Math.exp(-dtMs / PEN_SMOOTHING_MS)
    this.rateX += (gy - this.rateX) * k // turning about y moves the pen sideways
    this.rateY += (gx - this.rateY) * k // turning about x moves it up and down
    const dead = (v: number) => (Math.abs(v) < PEN_DEADBAND_DPS ? 0 : v)
    this.x += (dead(this.rateX) * dtMs) / 1000
    this.y += (dead(this.rateY) * dtMs) / 1000
    this.pts.push([this.x, this.y])
  }
}
