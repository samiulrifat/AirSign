import { Link } from 'react-router-dom'
import { TopBar } from '../components/bits'
import { useApp } from '../lib/app'

export default function How() {
  const { config } = useApp()
  return (
    <div className="app">
      <TopBar links={[{ to: '/', label: 'Home' }, { to: '/how', label: 'How it works' }]} />
      <div className="card">
        <h1>How AirSign works</h1>
        <ol className="muted" style={{ paddingLeft: 18 }}>
          <li>The cashier creates a bill on the shop screen.</li>
          <li>Your phone finds the building and suggests the shop: "Are you at Lumen Books, 4th floor?"</li>
          <li>You approve the order in PayPal. Nothing is charged yet.</li>
          <li>A risk engine decides how strict the check is.</li>
          <li>You hold the phone still, then sign in the air. No need to touch the screen: the app starts and stops recording by itself and draws your signature as ink.</li>
          <li>Only if the signature passes does the server capture the payment in PayPal.</li>
        </ol>
      </div>
      <div className="card">
        <h2>Where the AI is</h2>
        <ul className="muted small" style={{ paddingLeft: 18 }}>
          <li>
            <strong>Gesture match:</strong> dynamic time warping against your enrolled repetitions
            {config?.neural_model ? ', fused with a Siamese 1D CNN embedding' : ''}, with a threshold learned from your
            own natural variation.
          </li>
          <li>
            <strong>Replay and fake-data check:</strong> rejects near-exact copies of earlier signatures and streams
            without real sensor noise or timing jitter.
          </li>
          <li>
            <strong>Risk engine:</strong> transparent rules plus an isolation forest on your payment history choose
            between one signature, a stricter one, or two.
          </li>
          <li>
            <strong>Shop ranking:</strong> scores the shops in a building from location, floor hint, opening hours, your
            visits and which shop just opened a bill.
          </li>
        </ul>
      </div>
      <div className="card">
        <h2>The one rule</h2>
        <p className="muted small">
          The amount and shop come from the bill stored on the server, never from the phone, and the only code path that
          can capture money is a passing sign-off. Every attempt is logged without raw sensor data.
        </p>
      </div>
      <div className="card">
        <h2>Honest limits</h2>
        <p className="muted small">
          A motion signature is a behavioral biometric: one factor, not a replacement for Face ID. A close observer
          might copy a simple gesture, and signing while walking is harder. This prototype runs on the PayPal sandbox
          and makes no compliance claims.
        </p>
      </div>
      <Link to="/" className="btn block">
        Try it
      </Link>
    </div>
  )
}
