import { useEffect, useRef, useState } from 'react'
import { post, type Invoice } from '../lib/api'
import { errorText } from '../lib/app'
import { applyDraft, describeDraft, readBack, type Draft, type Line } from '../lib/cart'
import { canListen, listen, listenProblem, speak, stopListening, stopSpeaking } from '../lib/speech'
import { Spinner } from './bits'

type Phase = 'idle' | 'speaking' | 'listening' | 'thinking'
type Said = { who: 'till' | 'heard'; text: string }

const MAX_MISSES = 3
const MAX_TURNS = 15
const YES = /\b(yes|yeah|yep|ok|okay|sure|correct|confirm|create|right|done|that's all|that is all|finish|haan|ji)\b/i
const NO = /\b(no|nope|wrong|not yet|wait)\b/i
const words = (t: string) => t.trim().split(/\s+/).length

/**
 * A spoken conversation at the till. It works on the same bill as the screen: what was
 * scanned or tapped stays, and each thing said adds, removes or changes items, or clears
 * the bill. It reads the bill back and creates it once someone says yes. It only listens
 * after a tap and stops after each bill, so the till never listens all day.
 */
export default function VoiceAssistant({ lines, setLines, onCreated }: { lines: Line[]; setLines: (l: Line[]) => void; onCreated: (inv: Invoice) => void }) {
  const [phase, setPhase] = useState<Phase>('idle')
  const [said, setSaid] = useState<Said[]>([])
  const [problem, setProblem] = useState<string | null>(null)
  const run = useRef(0)
  // The latest bill, also when the camera or a tap changes it mid-conversation.
  const cart = useRef(lines)
  cart.current = lines

  useEffect(
    () => () => {
      run.current++
      stopSpeaking()
      stopListening()
    },
    [],
  )

  const note = (who: Said['who'], text: string) => setSaid((s) => [...s.slice(-3), { who, text }])

  async function say(text: string) {
    note('till', text)
    setPhase('speaking')
    await speak(text)
  }

  /** The first thing heard, or null. */
  async function hear(id: number): Promise<string | null> {
    setPhase('listening')
    const heard = await listen()
    if (id !== run.current) return null
    if (heard[0]) note('heard', heard[0])
    return heard[0] ?? null
  }

  function update(next: Line[]) {
    cart.current = next
    setLines(next)
  }

  async function start() {
    const id = ++run.current
    setSaid([])
    setProblem(null)
    let question = cart.current.length ? `${readBack(cart.current)} What would you like to add or change?` : 'What would you like to bill?'
    let misses = 0
    try {
      for (let turn = 0; turn < MAX_TURNS && misses < MAX_MISSES; turn++) {
        await say(question)
        if (id !== run.current) return
        const heard = await hear(id)
        if (id !== run.current) return
        if (!heard) {
          const blocked = listenProblem()
          if (blocked) {
            setProblem(blocked)
            setPhase('idle')
            return
          }
          misses++
          question = "I didn't catch that. Please say it again."
          continue
        }

        if (cart.current.length && YES.test(heard) && !NO.test(heard) && words(heard) <= 4) {
          setPhase('thinking')
          const invoice = await post<Invoice>('/pos/invoices', { items: cart.current })
          if (id !== run.current) return
          update([])
          setPhase('idle')
          onCreated(invoice)
          return
        }
        if (NO.test(heard) && words(heard) <= 3) {
          question = cart.current.length ? 'Okay. What would you like to change?' : 'What would you like to bill?'
          continue
        }

        setPhase('thinking')
        const draft = await post<Draft>('/pos/ai-bill', { text: heard, cart: cart.current.map(({ name, qty }) => ({ name, qty })) })
        if (id !== run.current) return
        if (!describeDraft(draft)) {
          misses++
          question = "That doesn't match anything this shop sells. Please say it again."
          continue
        }
        misses = 0
        const next = applyDraft(cart.current, draft)
        update(next)
        question = next.length ? `${readBack(next)} Say yes to create the bill, or tell me what to change.` : 'The bill is empty. What would you like to bill?'
      }
      await say('Please use the screen for this one.')
      setPhase('idle')
    } catch (e) {
      setProblem(errorText(e))
      setPhase('idle')
    }
  }

  function stop() {
    run.current++
    stopSpeaking()
    stopListening()
    setPhase('idle')
  }

  if (!canListen()) {
    return <p className="notice">The voice assistant needs Chrome, Edge or Safari. You can still type the order below.</p>
  }

  const busy = phase !== 'idle'
  return (
    <div className="assistant">
      <div className="assistant-head">
        <h2>Voice assistant</h2>
        {busy ? (
          <button className="btn ghost small" onClick={stop}>
            Stop
          </button>
        ) : (
          <button className="btn small" onClick={() => void start()}>
            {lines.length ? 'Change it by voice' : 'Start a voice bill'}
          </button>
        )}
      </div>
      {busy && (
        <p className={`assistant-state ${phase}`} role="status" aria-live="polite">
          {phase === 'listening' ? <span className="mic-dot" aria-hidden="true" /> : <Spinner />}
          {{ speaking: 'Speaking', listening: 'Listening', thinking: 'Working on it' }[phase]}
        </p>
      )}
      {said.length > 0 && (
        <ul className="assistant-log">
          {said.map((s, i) => (
            <li key={i} className={s.who}>
              {s.text}
            </li>
          ))}
        </ul>
      )}
      {problem && <div className="notice warn" style={{ margin: '8px 0 0' }}>{problem}</div>}
    </div>
  )
}
