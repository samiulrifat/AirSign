// The browser's own speech, used by the voice assistant. Recognition is missing in
// some browsers (Firefox), so every use of it also has a typed alternative.

type Alternatives = { length: number; [i: number]: { transcript: string } }
type SpeechEvent = { results: { [i: number]: Alternatives } }
type Recognition = {
  lang: string
  maxAlternatives: number
  onresult: ((e: SpeechEvent) => void) | null
  onerror: ((e: { error?: string }) => void) | null
  onend: (() => void) | null
  start: () => void
  abort?: () => void
}

let active: Recognition | null = null
let lastError: string | null = null

function recognizer(): (new () => Recognition) | undefined {
  const w = window as unknown as { SpeechRecognition?: new () => Recognition; webkitSpeechRecognition?: new () => Recognition }
  return w.SpeechRecognition ?? w.webkitSpeechRecognition
}

export const canListen = () => recognizer() !== undefined
export const canSpeak = () => 'speechSynthesis' in window

/** Why the last listen() heard nothing, in words for the cashier, if it was not just silence. */
export function listenProblem(): string | null {
  if (lastError === 'not-allowed' || lastError === 'service-not-allowed') {
    return 'The microphone is blocked. Allow it for this site. Browsers only allow it on HTTPS or localhost.'
  }
  if (lastError === 'network') return 'Speech recognition needs an internet connection.'
  if (lastError === 'audio-capture') return 'No microphone was found.'
  return null
}

/** What was heard, likeliest first. Empty when nothing was understood. */
export function listen(): Promise<string[]> {
  const Speech = recognizer()
  lastError = null
  if (!Speech) return Promise.resolve([])
  return new Promise((resolve) => {
    let heard: string[] = []
    const r = new Speech()
    active = r
    r.lang = navigator.language
    r.maxAlternatives = 3
    r.onresult = (e) => {
      const alternatives = e.results[0]
      heard = Array.from({ length: alternatives.length }, (_, i) => alternatives[i].transcript)
    }
    r.onerror = (e) => {
      lastError = e.error ?? 'unknown'
      resolve([])
    }
    r.onend = () => resolve(heard)
    try {
      r.start()
    } catch {
      resolve([])
    }
  })
}

export function stopListening() {
  try {
    active?.abort?.()
  } catch {
    /* it had already stopped */
  }
  active = null
}

/** Resolves when speaking ends. Some browsers never report the end, so it also times out. */
export function speak(text: string): Promise<void> {
  if (!canSpeak()) return Promise.resolve()
  return new Promise((resolve) => {
    const timer = setTimeout(resolve, 2000 + text.length * 100)
    const u = new SpeechSynthesisUtterance(text)
    u.onend = u.onerror = () => {
      clearTimeout(timer)
      resolve()
    }
    window.speechSynthesis.cancel()
    window.speechSynthesis.speak(u)
  })
}

export function stopSpeaking() {
  if (canSpeak()) window.speechSynthesis.cancel()
}

// Spoken prompts on the customer's phone. Someone can turn them off, since they say the amount out loud.

const PROMPTS = 'airsign.spoken-prompts'

export function promptsOn(): boolean {
  try {
    return localStorage.getItem(PROMPTS) !== 'off'
  } catch {
    return true
  }
}

export function setPromptsOn(on: boolean) {
  try {
    localStorage.setItem(PROMPTS, on ? 'on' : 'off')
  } catch {
    /* the setting just lasts until the page closes */
  }
}

export function prompt(text: string) {
  if (promptsOn()) void speak(text)
}
