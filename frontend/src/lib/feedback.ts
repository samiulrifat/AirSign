// Eyes-free cues for signing: while the phone is moving through the air the
// screen is hard to read, so state changes are also felt (vibration, where the
// browser allows it) and heard (a soft tone). Audio needs one user tap to unlock.

type Cue = 'ready' | 'start' | 'done' | 'retry'

let audio: AudioContext | null = null

/** Call from a tap handler (for example "Allow motion sensors") so tones can play later. */
export function unlockFeedback() {
  try {
    const Ctx = window.AudioContext || (window as unknown as { webkitAudioContext?: typeof AudioContext }).webkitAudioContext
    if (!audio && Ctx) audio = new Ctx()
    void audio?.resume()
  } catch {
    audio = null
  }
}

function tone(freq: number, ms: number, when = 0) {
  if (!audio || audio.state !== 'running') return
  const t = audio.currentTime + when
  const osc = audio.createOscillator()
  const gain = audio.createGain()
  osc.frequency.value = freq
  osc.type = 'sine'
  gain.gain.setValueAtTime(0.0001, t)
  gain.gain.exponentialRampToValueAtTime(0.08, t + 0.015)
  gain.gain.exponentialRampToValueAtTime(0.0001, t + ms / 1000)
  osc.connect(gain).connect(audio.destination)
  osc.start(t)
  osc.stop(t + ms / 1000 + 0.02)
}

export function cue(kind: Cue) {
  const vibrate = (p: number | number[]) => {
    try {
      navigator.vibrate?.(p)
    } catch {
      /* not supported */
    }
  }
  if (kind === 'ready') {
    vibrate(25)
    tone(660, 90)
  } else if (kind === 'start') {
    vibrate(12)
  } else if (kind === 'done') {
    vibrate([20, 40, 20])
    tone(660, 80)
    tone(880, 120, 0.09)
  } else {
    vibrate([60, 60, 60])
    tone(330, 160)
  }
}
