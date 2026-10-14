import { useEffect, useRef, useState } from 'react'
import type { Recording } from '../lib/api'
import { MotionRecorder, durationS, needsPermissionTap, requestMotionPermission } from '../lib/sensors'

const STILL_MS = 550 // the server measures the sensor noise floor in this lead-in
const MAX_S = 7

export function useMotion() {
  const ref = useRef<MotionRecorder | null>(null)
  if (!ref.current) ref.current = new MotionRecorder()
  const [granted, setGranted] = useState(!needsPermissionTap())
  const [live, setLive] = useState(false)
  const [denied, setDenied] = useState(false)

  useEffect(() => {
    const r = ref.current!
    r.attach()
    const t = setInterval(() => setLive(r.live), 500)
    return () => {
      clearInterval(t)
      r.detach()
    }
  }, [])

  async function enable() {
    const ok = await requestMotionPermission()
    setGranted(ok)
    setDenied(!ok)
  }

  return { recorder: ref.current, granted, denied, live, enable }
}

/** Draws rotation-rate magnitude for the last few seconds. */
function LiveTrace({ recorder, active }: { recorder: MotionRecorder; active: boolean }) {
  const canvas = useRef<HTMLCanvasElement>(null)
  useEffect(() => {
    const values: number[] = []
    const off = recorder.onRow((row) => {
      values.push(Math.hypot(row[4], row[5], row[6]))
      if (values.length > 240) values.shift()
    })
    let raf = 0
    const draw = () => {
      const c = canvas.current
      if (c) {
        const dpr = window.devicePixelRatio || 1
        const w = (c.width = c.clientWidth * dpr)
        const h = (c.height = c.clientHeight * dpr)
        const ctx = c.getContext('2d')!
        ctx.clearRect(0, 0, w, h)
        ctx.strokeStyle = getComputedStyle(c).getPropertyValue('--accent') || '#0d9488'
        ctx.lineWidth = 3 * dpr
        ctx.lineJoin = 'round'
        ctx.beginPath()
        values.forEach((v, i) => {
          const x = (i / 239) * w
          const y = h - Math.min(1, v / 600) * h * 0.85 - h * 0.08
          if (i) ctx.lineTo(x, y)
          else ctx.moveTo(x, y)
        })
        ctx.stroke()
      }
      raf = requestAnimationFrame(draw)
    }
    if (active) raf = requestAnimationFrame(draw)
    return () => {
      off()
      cancelAnimationFrame(raf)
    }
  }, [recorder, active])
  return <canvas ref={canvas} aria-hidden="true" />
}

type Phase = 'idle' | 'still' | 'go'

export function SignPad({
  motion,
  busy,
  onRecording,
  idleLabel = 'Press and hold here',
}: {
  motion: ReturnType<typeof useMotion>
  busy: boolean
  onRecording: (rec: Recording) => void
  idleLabel?: string
}) {
  const [phase, setPhase] = useState<Phase>('idle')
  const [error, setError] = useState<string | null>(null)
  const timer = useRef<number | undefined>(undefined)
  const phaseRef = useRef<Phase>('idle')
  const setP = (p: Phase) => {
    phaseRef.current = p
    setPhase(p)
  }

  if (!motion.granted) {
    return (
      <div className="stack">
        <p className="muted">AirSign reads your phone's motion sensors only while you hold the sign pad.</p>
        <button className="btn block" onClick={motion.enable}>
          Allow motion sensors
        </button>
        {motion.denied && (
          <div className="notice warn">
            Motion access was not allowed. On iPhone, close and reopen this page and tap Allow, or check Settings &gt;
            Safari &gt; Motion &amp; Orientation Access. You can also use the simulation below.
          </div>
        )}
      </div>
    )
  }

  function down(e: React.PointerEvent) {
    if (busy || phaseRef.current !== 'idle') return
    e.currentTarget.setPointerCapture(e.pointerId)
    setError(null)
    motion.recorder.start()
    setP('still')
    timer.current = window.setTimeout(() => setP('go'), STILL_MS)
  }

  function up() {
    if (phaseRef.current === 'idle') return
    window.clearTimeout(timer.current)
    const wasStill = phaseRef.current === 'still'
    setP('idle')
    const rec = motion.recorder.stop()
    if (rec.samples.length === 0) {
      setError('No motion data arrived. This device may not have motion sensors. Use the simulation below.')
      return
    }
    if (wasStill) {
      setError('Keep holding until the pad turns green, then sign.')
      return
    }
    const d = durationS(rec)
    if (d < 1.2) {
      setError('That was very short. Hold, wait for green, then sign for 2 to 4 seconds.')
      return
    }
    if (d > MAX_S) {
      setError('That was too long. Keep the gesture under 5 seconds.')
      return
    }
    onRecording(rec)
  }

  const label = busy ? 'Checking…' : phase === 'still' ? 'Hold still…' : phase === 'go' ? 'Sign now, release when done' : idleLabel
  const hint = phase === 'idle' && !busy ? 'Wait for green, then move the phone in your signature' : ''

  return (
    <div>
      <div
        className={`pad ${phase}${busy ? ' busy' : ''}`}
        onPointerDown={down}
        onPointerUp={up}
        onPointerCancel={up}
        onContextMenu={(e) => e.preventDefault()}
        role="button"
        aria-label={label}
      >
        <LiveTrace recorder={motion.recorder} active={motion.live} />
        <div className="label">
          {label}
          {hint && <div className="hint">{hint}</div>}
        </div>
      </div>
      {!motion.live && <p className="tiny center">No motion events yet. On a computer, use the simulation below.</p>}
      {error && <div className="notice warn" style={{ marginTop: 12 }}>{error}</div>}
    </div>
  )
}
