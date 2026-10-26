// Motion capture from the browser DeviceMotion event (technical report, section 5).
//
// Rows are [t_ms, ax, ay, az, gx, gy, gz]: acceleration in m/s^2 (linear when the
// device provides it), rotation rate in degrees per second about the phone's own
// x, y and z axes. Timestamps are the event's high-resolution timeStamp, whose
// jitter the server uses as a plausibility signal.

import type { DeviceInfo, Recording } from './api'

type PermissionFn = () => Promise<'granted' | 'denied'>

export function motionSupported(): boolean {
  return typeof window !== 'undefined' && 'DeviceMotionEvent' in window
}

export function needsPermissionTap(): boolean {
  const DME = (window as unknown as { DeviceMotionEvent?: { requestPermission?: PermissionFn } }).DeviceMotionEvent
  return typeof DME?.requestPermission === 'function'
}

/** Must be called from a tap handler on iOS. */
export async function requestMotionPermission(): Promise<boolean> {
  const DME = (window as unknown as { DeviceMotionEvent?: { requestPermission?: PermissionFn } }).DeviceMotionEvent
  if (typeof DME?.requestPermission === 'function') {
    try {
      return (await DME.requestPermission()) === 'granted'
    } catch {
      return false
    }
  }
  return motionSupported()
}

export function platform(): string {
  const ua = navigator.userAgent
  if (/iPhone|iPad|iPod/.test(ua)) return 'iOS'
  if (/Android/.test(ua)) return 'Android'
  return 'other'
}

type Listener = (row: number[]) => void

/**
 * Listens continuously while mounted so the live trace can show motion, and keeps
 * the rows between start() and stop() as one recording.
 */
export class MotionRecorder {
  private rows: number[][] = []
  private recording = false
  private listeners = new Set<Listener>()
  private intervalMs: number | null = null
  private gravity = false
  private lastEventAt = 0
  private handler = (e: DeviceMotionEvent) => this.onMotion(e)

  attach() {
    window.addEventListener('devicemotion', this.handler)
  }

  detach() {
    window.removeEventListener('devicemotion', this.handler)
    this.listeners.clear()
  }

  /** True if any motion event arrived in the last second. */
  get live(): boolean {
    return performance.now() - this.lastEventAt < 1000
  }

  onRow(fn: Listener): () => void {
    this.listeners.add(fn)
    return () => this.listeners.delete(fn)
  }

  start() {
    this.rows = []
    this.recording = true
  }

  /** What the server needs to know about this phone's sensors. */
  device(): DeviceInfo {
    return {
      platform: platform(),
      nominal_hz: this.intervalMs ? Math.round(1000 / this.intervalMs) : null,
      accel_includes_gravity: this.gravity,
    }
  }

  stop(): Recording {
    this.recording = false
    const samples = this.rows
    this.rows = []
    return { samples, device: this.device(), simulated: false }
  }

  private onMotion(e: DeviceMotionEvent) {
    const lin = e.acceleration
    const useLinear = lin && lin.x !== null && lin.y !== null && lin.z !== null
    const acc = useLinear ? lin : e.accelerationIncludingGravity
    const rot = e.rotationRate
    if (!acc || !rot || acc.x === null || rot.alpha === null) return
    this.gravity = !useLinear
    // The spec gives the interval in milliseconds; older iOS reported seconds.
    if (e.interval) this.intervalMs = e.interval > 1 ? e.interval : e.interval * 1000
    this.lastEventAt = performance.now()
    // rotationRate: alpha is about z, beta about x, gamma about y.
    const row = [
      e.timeStamp,
      acc.x ?? 0,
      acc.y ?? 0,
      acc.z ?? 0,
      rot.beta ?? 0,
      rot.gamma ?? 0,
      rot.alpha ?? 0,
    ]
    if (this.recording) this.rows.push(row)
    this.listeners.forEach((fn) => fn(row))
  }
}

export function durationS(rec: Recording): number {
  const s = rec.samples
  return s.length < 2 ? 0 : (s[s.length - 1][0] - s[0][0]) / 1000
}
