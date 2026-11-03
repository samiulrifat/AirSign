// The browser's own speech, used by the cashier screen. Recognition is missing in
// some browsers (Firefox), so every use of it also has a typed alternative.

type Alternatives = { length: number; [i: number]: { transcript: string } }
type SpeechEvent = { results: { [i: number]: Alternatives } }
type Recognition = {
  lang: string
  maxAlternatives: number
  onresult: ((e: SpeechEvent) => void) | null
  onerror: (() => void) | null
  onend: (() => void) | null
  start: () => void
}

function recognizer(): (new () => Recognition) | undefined {
  const w = window as unknown as { SpeechRecognition?: new () => Recognition; webkitSpeechRecognition?: new () => Recognition }
  return w.SpeechRecognition ?? w.webkitSpeechRecognition
}

export const canListen = () => recognizer() !== undefined

/** What was heard, likeliest first. Empty when nothing was understood. */
export function listen(): Promise<string[]> {
  const Speech = recognizer()
  if (!Speech) return Promise.resolve([])
  return new Promise((resolve) => {
    let heard: string[] = []
    const r = new Speech()
    r.lang = navigator.language
    r.maxAlternatives = 3
    r.onresult = (e) => {
      const alternatives = e.results[0]
      heard = Array.from({ length: alternatives.length }, (_, i) => alternatives[i].transcript)
    }
    r.onerror = () => resolve([])
    r.onend = () => resolve(heard)
    try {
      r.start()
    } catch {
      resolve([])
    }
  })
}

/** Resolves when speaking ends. Some browsers never report the end, so it also times out. */
export function speak(text: string): Promise<void> {
  if (!('speechSynthesis' in window)) return Promise.resolve()
  return new Promise((resolve) => {
    const timer = setTimeout(resolve, 5000)
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
  if ('speechSynthesis' in window) window.speechSynthesis.cancel()
}
