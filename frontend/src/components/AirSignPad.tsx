import { useEffect, useRef, useState } from 'react'
import type { Recording } from '../lib/api'
import { cue, unlockFeedback } from '../lib/feedback'
import { computeInk, drawInk, type Ink } from '../lib/ink'
import { LivePen, MotionLevel } from '../lib/motion-level'
import { MotionRecorder, needsPermissionTap, requestMotionPermission } from '../lib/sensors'

// Hands-free capture: hold the phone still, sign in the air, hold still again.
// Thresholds are in degrees per second (rotation) and m/s^2 (acceleration), and apply
// to a short average of the motion (see MotionLevel), so a small shake crosses none of them.
const STILL_DPS = 20
const STILL_ACC = 0.8
const SETTLE_MS = 700 // stillness needed before "ready"; also kept as the lead-in
const START_DPS = 60
const START_ACC = 2.5
const START_MS = 150 // motion must last this long to start a recording
const STOP_DPS = 30
const STOP_ACC = 1.1
const STOP_MS = 600 // stillness that ends a recording; kept as the tail
const MAX_GESTURE_MS = 5000
const MIN_GESTURE_MS = 1000
const MIN_PEAK_DPS = 70 // weaker than this is not a signature, just a twitch
const SILENT_BELOW_MS = 500 // something this brief was a bump: drop it without comment

// Once motion access is granted it stays granted for this page; asking again on
// every screen would be a needless tap.
let grantedThisSession = false

export function useMotion() {
  const ref = useRef<MotionRecorder | null>(null)
  if (!ref.current) ref.current = new MotionRecorder()
  const [granted, setGranted] = useState(!needsPermissionTap() || grantedThisSession)
  const [live, setLive] = useState(false)
  const [denied, setDenied] = useState(false)

  useEffect(() => {
    const r = ref.current!
    r.attach()
    const t = setInterval(() => setLive(r.live), 500)
    // Any first tap unlocks the sound cues (browsers need a user gesture for audio).
    const unlock = () => unlockFeedback()
    document.addEventListener('pointerdown', unlock, { once: true })
    return () => {
      clearInterval(t)
      r.detach()
      document.removeEventListener('pointerdown', unlock)
    }
  }, [])

  async function enable() {
    unlockFeedback()
    const ok = await requestMotionPermission()
    grantedThisSession = ok
    setGranted(ok)
    setDenied(!ok)
  }

  return { recorder: ref.current, granted, denied, live, enable }
}

type Phase = 'settle' | 'ready' | 'recording' | 'review' | 'sending'

/** A canvas that shows a signature, replaying the stroke when it changes. */
export function InkCanvas({ ink, animate = true, className }: { ink: Ink | null; animate?: boolean; className?: string }) {
  const ref = useRef<HTMLCanvasElement>(null)
  useEffect(() => {
    const c = ref.current
    if (!c) return
    if (!animate || !ink) {
      drawInk(c, ink)
      return
    }
    let raf = 0
    const t0 = performance.now()
    const step = () => {
      const p = Math.min(1, (performance.now() - t0) / 700)
      drawInk(c, ink, p)
      if (p < 1) raf = requestAnimationFrame(step)
    }
    raf = requestAnimationFrame(step)
    return () => cancelAnimationFrame(raf)
  }, [ink, animate])
  return <canvas ref={ref} className={className} aria-hidden="true" />
}

export function AirSignPad({
  motion,
  busy,
  onRecording,
  readyLabel = 'Ready. Sign in the air',
  reviewMs = 1500,
  preview,
}: {
  motion: ReturnType<typeof useMotion>
  busy: boolean
  onRecording: (rec: Recording) => Promise<unknown> | void
  readyLabel?: string
  /** How long the captured signature is shown, with a Redo button, before it is sent. */
  reviewMs?: number
  /** A recording made elsewhere (simulation) to draw on the pad. */
  preview?: { rows: number[][]; gravity: boolean; key: number } | null
}) {
  const [phase, setPhase] = useState<Phase>('settle')
  const [message, setMessage] = useState<string | null>(null)
  const [ink, setInk] = useState<Ink | null>(null)
  const [reviewLeft, setReviewLeft] = useState(0)
  const phaseRef = useRef<Phase>('settle')
  const canvas = useRef<HTMLCanvasElement>(null)
  const captured = useRef<Recording | null>(null)
  const sendRef = useRef<() => void>(() => {})

  const go = (p: Phase) => {
    phaseRef.current = p
    setPhase(p)
  }

  // The parent is checking a submission (or a simulated one): hold the pad.
  useEffect(() => {
    if (busy && phaseRef.current !== 'sending') go('sending')
    if (!busy && phaseRef.current === 'sending') go('settle')
  }, [busy])

  useEffect(() => {
    if (preview) setInk(computeInk(preview.rows, preview.gravity))
  }, [preview])

  // Sensor stream state machine.
  useEffect(() => {
    if (!motion.granted) return
    const buffer: number[][] = []
    let rec: number[][] = []
    let stillSince: number | null = null
    let movingSince: number | null = null
    let motionStart = 0
    let peak = 0
    let stopSince: number | null = null
    let ema = [0, 0, 0]
    let pen = new LivePen(0)
    const level = new MotionLevel()
    let raf = 0

    const drawLive = () => {
      raf = 0
      const c = canvas.current
      if (c && pen.pts.length > 1) drawInk(c, { pts: pen.pts, widths: pen.pts.map(() => 0.8), aspect: 1 })
    }

    const off = motion.recorder.onRow((row) => {
      const t = row[0]
      buffer.push(row)
      while (buffer.length && buffer[0][0] < t - 1500) buffer.shift()
      const p = phaseRef.current
      if (p === 'review' || p === 'sending' || document.visibilityState !== 'visible') return

      const gravity = motion.recorder.device().accel_includes_gravity
      let a = [row[1], row[2], row[3]]
      if (gravity) {
        ema = ema.map((e, i) => (e === 0 ? a[i] : e * 0.92 + a[i] * 0.08))
        a = a.map((x, i) => x - ema[i])
      }
      const { rot, acc } = level.push(t, Math.hypot(row[4], row[5], row[6]), Math.hypot(a[0], a[1], a[2]))

      if (p === 'settle') {
        if (rot < STILL_DPS && acc < STILL_ACC) {
          stillSince ??= t
          if (t - stillSince >= SETTLE_MS) {
            go('ready')
            setMessage(null)
            cue('ready')
          }
        } else {
          stillSince = null
        }
      } else if (p === 'ready') {
        movingSince = rot > START_DPS || acc > START_ACC ? (movingSince ?? t) : null
        if (movingSince !== null && t - movingSince >= START_MS) {
          // Keep the still moment before the first stroke: the server measures sensor noise there.
          rec = buffer.filter((r) => r[0] >= t - SETTLE_MS - 100)
          motionStart = movingSince
          peak = rot
          stopSince = null
          pen = new LivePen(t)
          setInk(null)
          go('recording')
          cue('start')
        }
      } else if (p === 'recording') {
        rec.push(row)
        peak = Math.max(peak, rot)
        pen.push(t, row[4], row[5])
        if (!raf) raf = requestAnimationFrame(drawLive)

        const quiet = rot < STOP_DPS && acc < STOP_ACC
        stopSince = quiet ? (stopSince ?? t) : null
        const gestureMs = (stopSince ?? t) - motionStart
        if ((stopSince !== null && t - stopSince >= STOP_MS) || gestureMs >= MAX_GESTURE_MS) {
          stillSince = null
          movingSince = null
          if (gestureMs < MIN_GESTURE_MS || peak < MIN_PEAK_DPS) {
            // A twitch, not a signature: drop it without using up an attempt.
            if (gestureMs >= SILENT_BELOW_MS) {
              setMessage('That was too short to be a signature. Hold still, then sign your full signature.')
              cue('retry')
            }
            go('settle')
            return
          }
          captured.current = { samples: rec, device: motion.recorder.device(), simulated: false }
          setInk(computeInk(rec, gravity))
          go('review')
          cue('done')
        }
      }
    })
    return () => {
      off()
      if (raf) cancelAnimationFrame(raf)
    }
  }, [motion.granted, motion.recorder])

  // Review: show the signature briefly, then send it unless the person taps Redo.
  useEffect(() => {
    if (phase !== 'review') return
    const t0 = performance.now()
    setReviewLeft(1)
    const tick = setInterval(() => {
      const left = 1 - (performance.now() - t0) / reviewMs
      setReviewLeft(Math.max(0, left))
      if (left <= 0) {
        clearInterval(tick)
        sendRef.current()
      }
    }, 50)
    return () => clearInterval(tick)
  }, [phase, reviewMs])

  sendRef.current = async () => {
    const rec = captured.current
    if (!rec || phaseRef.current !== 'review') return
    go('sending')
    try {
      await onRecording(rec)
    } finally {
      if ((phaseRef.current as Phase) === 'sending' && !busy) go('settle')
    }
  }

  function redo() {
    captured.current = null
    setInk(null)
    setMessage(null)
    go('settle')
  }

  if (!motion.granted) {
    return (
      <div className="stack">
        {ink && (
          <div className="pad air review">
            <InkCanvas ink={ink} key={ink.pts.length} />
          </div>
        )}
        <p className="muted">
          AirSign reads your phone's motion sensors only on this screen, while you sign. You will not need to touch the
          screen to sign.
        </p>
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

  const noSensor = !motion.live && phase === 'settle'
  const label = {
    settle: noSensor ? 'No motion sensor on this device' : 'Hold your phone still',
    ready: readyLabel,
    recording: 'Signing…',
    review: 'Got it',
    sending: 'Checking…',
  }[phase]
  const hint = {
    settle: noSensor ? 'Simulated signatures are drawn here.' : 'Then sign in the air. No need to touch the screen.',
    ready: 'Move the phone like a pen, then hold still to finish.',
    recording: 'Hold still when you are done.',
    review: '',
    sending: '',
  }[phase]

  return (
    <div>
      <div className={`pad air ${phase}`} role="status" aria-live="polite" aria-label={label}>
        {phase === 'recording' ? (
          <canvas ref={canvas} aria-hidden="true" />
        ) : (
          <InkCanvas ink={ink} key={ink ? ink.pts.length : 0} />
        )}
        <div className="label">
          {label}
          {hint && <div className="hint">{hint}</div>}
          {phase === 'review' && (
            <div className="review">
              <div className="meter">
                <i style={{ width: `${reviewLeft * 100}%`, background: 'var(--accent)' }} />
              </div>
              <button className="btn ghost small" onClick={redo}>
                Redo
              </button>
            </div>
          )}
        </div>
      </div>
      {message && (
        <div className="notice warn" style={{ marginTop: 12 }}>
          {message}
        </div>
      )}
    </div>
  )
}
