import { useEffect, useRef, useState } from 'react'
import { ApiError, post } from '../lib/api'
import { errorText } from '../lib/app'
import type { Draft, Op } from '../lib/cart'
import { cue, unlockFeedback } from '../lib/feedback'
import { Spinner } from './bits'

// Works like a barcode scanner: the camera stays open, and each product shown to it is
// added once. Holding it there does not add it again; take it away and show it again
// to add another. A frame goes to Gemini only when the picture has changed and then
// held still, so a quiet counter costs nothing.

const SAMPLE_MS = 250
const SAMPLE_W = 32
const SAMPLE_H = 24
const STILL_SAMPLES = 2 // about half a second without movement
const MOVING = 5 // mean grey-level change between samples that counts as movement
const CHANGED = 7 // change since the last frame read that is worth reading again
const FRAME_MAX_PX = 768
const PAUSE_AFTER_ERROR_MS = 5000

type Phase = 'starting' | 'watching' | 'reading' | 'paused' | 'blocked'

function grey(video: HTMLVideoElement, canvas: HTMLCanvasElement): Uint8ClampedArray | null {
  const ctx = canvas.getContext('2d', { willReadFrequently: true })
  if (!ctx || !video.videoWidth) return null
  ctx.drawImage(video, 0, 0, SAMPLE_W, SAMPLE_H)
  const rgba = ctx.getImageData(0, 0, SAMPLE_W, SAMPLE_H).data
  const out = new Uint8ClampedArray(SAMPLE_W * SAMPLE_H)
  for (let i = 0; i < out.length; i++) out[i] = (rgba[i * 4] * 3 + rgba[i * 4 + 1] * 4 + rgba[i * 4 + 2]) >> 3
  return out
}

function diff(a: Uint8ClampedArray | null, b: Uint8ClampedArray | null): number {
  if (!a || !b) return Infinity
  let total = 0
  for (let i = 0; i < a.length; i++) total += Math.abs(a[i] - b[i])
  return total / a.length
}

function frame(video: HTMLVideoElement): string {
  const scale = Math.min(1, FRAME_MAX_PX / Math.max(video.videoWidth, video.videoHeight))
  const canvas = document.createElement('canvas')
  canvas.width = Math.round(video.videoWidth * scale)
  canvas.height = Math.round(video.videoHeight * scale)
  canvas.getContext('2d')?.drawImage(video, 0, 0, canvas.width, canvas.height)
  return canvas.toDataURL('image/jpeg', 0.75)
}

export default function CameraScanner({ onAdd, onClose }: { onAdd: (d: Draft) => void; onClose: () => void }) {
  const video = useRef<HTMLVideoElement>(null)
  const [phase, setPhase] = useState<Phase>('starting')
  const [inView, setInView] = useState<string[]>([])
  const [added, setAdded] = useState<string | null>(null)
  const [problem, setProblem] = useState<string | null>(null)
  const onAddRef = useRef(onAdd)
  onAddRef.current = onAdd

  useEffect(() => {
    let stream: MediaStream | null = null
    let timer: number | undefined
    let stopped = false
    let busy = false
    let pausedUntil = 0
    let still = 0
    let prev: Uint8ClampedArray | null = null
    let lastRead: Uint8ClampedArray | null = null
    let seen = new Map<string, number>() // what was on the counter at the last read
    const sampler = document.createElement('canvas')
    sampler.width = SAMPLE_W
    sampler.height = SAMPLE_H

    async function read(v: HTMLVideoElement, sample: Uint8ClampedArray) {
      busy = true
      setPhase('reading')
      try {
        const d = await post<Draft>('/pos/ai-scan', { image: frame(v) })
        if (stopped) return
        lastRead = sample
        const now = new Map<string, number>()
        for (const op of d.ops) now.set(op.name, (now.get(op.name) ?? 0) + op.qty)
        // Only what is new since the last read: an item still in view is not added twice.
        const fresh: Op[] = []
        for (const op of d.ops) {
          const extra = (now.get(op.name) ?? 0) - (seen.get(op.name) ?? 0)
          if (extra > 0 && !fresh.some((f) => f.name === op.name)) fresh.push({ ...op, action: 'add', qty: extra })
        }
        seen = now
        setInView([...now].map(([name, n]) => `${n}× ${name}`))
        if (fresh.length) {
          onAddRef.current({ clear: false, ops: fresh, lines: fresh, unmatched: [], source: d.source })
          setAdded(`Added ${fresh.map((f) => `${f.qty}× ${f.name}`).join(', ')}`)
          cue('done')
        }
        setProblem(null)
        setPhase('watching')
      } catch (e) {
        if (stopped) return
        setProblem(e instanceof ApiError && e.status === 429 ? 'Too many frames for now. Scanning again shortly.' : errorText(e))
        pausedUntil = Date.now() + PAUSE_AFTER_ERROR_MS
        setPhase('paused')
      } finally {
        busy = false
      }
    }

    function tick() {
      const v = video.current
      if (stopped || !v || busy || Date.now() < pausedUntil) return
      if (pausedUntil) {
        pausedUntil = 0
        setPhase('watching')
      }
      const sample = grey(v, sampler)
      if (!sample) return
      still = diff(sample, prev) < MOVING ? still + 1 : 0
      prev = sample
      if (still >= STILL_SAMPLES && diff(sample, lastRead) > CHANGED) void read(v, sample)
    }

    ;(async () => {
      unlockFeedback()
      try {
        stream = await navigator.mediaDevices.getUserMedia({ video: { facingMode: { ideal: 'environment' }, width: { ideal: 1280 } }, audio: false })
        if (stopped) return stream.getTracks().forEach((t) => t.stop())
        const v = video.current!
        v.srcObject = stream
        await v.play()
        setPhase('watching')
        timer = window.setInterval(tick, SAMPLE_MS)
      } catch (e) {
        setProblem(e instanceof DOMException && e.name === 'NotAllowedError' ? 'Camera access was blocked. Allow the camera for this site, or snap a photo instead.' : 'The camera could not start on this device. Snap a photo instead.')
        setPhase('blocked')
      }
    })()

    return () => {
      stopped = true
      window.clearInterval(timer)
      stream?.getTracks().forEach((t) => t.stop())
    }
    // The loop reads state through closures and refs, so it starts once per open camera.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  useEffect(() => {
    if (!added) return
    const t = setTimeout(() => setAdded(null), 2500)
    return () => clearTimeout(t)
  }, [added])

  return (
    <div className="scanner">
      <div className="scanner-view">
        <video ref={video} playsInline muted aria-label="Camera view of the counter" />
        {added && (
          <div className="scanner-added" role="status">
            {added}
          </div>
        )}
      </div>
      <div className="scanner-bar">
        <span className="small" role="status" aria-live="polite">
          {phase === 'starting' && (
            <>
              <Spinner /> Opening the camera…
            </>
          )}
          {phase === 'watching' && 'Show each product, hold it still for a moment, then take it away.'}
          {phase === 'reading' && (
            <>
              <Spinner /> Reading…
            </>
          )}
          {phase === 'paused' && 'Paused for a moment.'}
          {phase === 'blocked' && 'No camera.'}
        </span>
        <button type="button" className="btn small" onClick={onClose}>
          Done
        </button>
      </div>
      {inView.length > 0 && <p className="tiny">In view: {inView.join(', ')}</p>}
      {problem && <div className="notice warn" style={{ margin: '8px 0 0' }}>{problem}</div>}
    </div>
  )
}
