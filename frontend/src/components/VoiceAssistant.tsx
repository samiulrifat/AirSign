import { useEffect, useRef, useState } from 'react'
import { post, type Invoice } from '../lib/api'
import { errorText, money } from '../lib/app'
import { canListen, listen, listenProblem, speak, stopListening, stopSpeaking } from '../lib/speech'
import { Spinner } from './bits'

type Line = { name: string; qty: number; price: string }
type Draft = { lines: Line[]; unmatched: string[]; source: 'gemini' | 'keywords' }
type Phase = 'idle' | 'speaking' | 'listening' | 'thinking'
type Said = { who: 'till' | 'heard'; text: string }

const MAX_TRIES = 3
const YES = /\b(yes|yeah|yep|ok|okay|sure|correct|confirm|create|right|haan|ji)\b/i
const NO = /\b(no|nope|wrong|cancel|stop|again)\b/i

function total(lines: Line[]): number {
  return lines.reduce((t, l) => t + l.qty * Math.round(parseFloat(l.price) * 100), 0)
}

/**
 * A spoken conversation at the till: hears the order, reads it back, and creates the bill
 * once someone says yes. It only listens after a tap and stops after each bill, so the
 * till never listens all day. The name question then comes from the bill panel.
 */
export default function VoiceAssistant({ onCreated }: { onCreated: (inv: Invoice) => void }) {
  const [phase, setPhase] = useState<Phase>('idle')
  const [said, setSaid] = useState<Said[]>([])
  const [problem, setProblem] = useState<string | null>(null)
  const run = useRef(0)

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

  /** The first thing heard, or null. Stops the conversation if the microphone cannot work. */
  async function hear(id: number): Promise<string | null> {
    setPhase('listening')
    const heard = await listen()
    if (id !== run.current) return null
    if (heard[0]) note('heard', heard[0])
    return heard[0] ?? null
  }

  async function start() {
    const id = ++run.current
    setSaid([])
    setProblem(null)
    let order: string | null = null
    let question = 'What would you like to bill?'
    try {
      for (let tries = 0; tries < MAX_TRIES; tries++) {
        if (!order) {
          await say(question)
          if (id !== run.current) return
          order = await hear(id)
          if (id !== run.current) return
          if (!order) {
            const blocked = listenProblem()
            if (blocked) {
              setProblem(blocked)
              setPhase('idle')
              return
            }
            question = "I didn't catch that. What would you like to bill?"
            continue
          }
        }

        setPhase('thinking')
        const draft: Draft = await post<Draft>('/pos/ai-bill', { text: order })
        if (id !== run.current) return
        order = null
        if (!draft.lines.length) {
          question = "That doesn't match anything this shop sells. Please say the order again."
          continue
        }

        const items = draft.lines.map((l) => `${l.qty} ${l.name}`).join(', ')
        await say(`${items}. That's ${money(total(draft.lines))}. Shall I create the bill?`)
        if (id !== run.current) return
        const reply = await hear(id)
        if (id !== run.current) return
        if (reply && YES.test(reply) && !NO.test(reply)) {
          setPhase('thinking')
          const invoice = await post<Invoice>('/pos/invoices', { items: draft.lines })
          if (id !== run.current) return
          setPhase('idle')
          onCreated(invoice)
          return
        }
        if (reply && !NO.test(reply)) {
          order = reply // a correction: treat what they said as the new order
        } else {
          question = 'Okay. What would you like to bill?'
        }
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
            Start a voice bill
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
