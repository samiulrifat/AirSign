import { Link } from 'react-router-dom'
import { TopBar } from '../components/bits'
import { useApp } from '../lib/app'

export default function How() {
  const { config } = useApp()
  return (
    <div className="app">
      <TopBar links={[{ to: '/', label: 'Home' }]} />
      <div className="card">
        <h1>How AirSign works</h1>
        <ol className="muted" style={{ paddingLeft: 18 }}>
          <li>You open AirSign and tap "I'm at a shop". The cashier sees your first name.</li>
          <li>The cashier snaps a photo of the items or says the order, and the AI drafts the bill for the cashier to check.</li>
          <li>The cashier sends the bill to you by name, or in AI auto mode the till asks your name out loud and sends it.</li>
          <li>Your phone buzzes. If you saved your PayPal, you go straight to signing. Otherwise, or for bills over $200, you approve in PayPal first. Nothing is charged yet.</li>
          <li>A risk engine decides how strict the check is.</li>
          <li>You hold the phone still, then sign in the air. No need to touch the screen: the app starts and stops recording by itself and draws your signature as ink.</li>
          <li>Only if the signature passes does the server take the payment through PayPal.</li>
        </ol>
      </div>
      <div className="card">
        <h2>Where the AI is</h2>
        <ul className="muted small" style={{ paddingLeft: 18 }}>
          <li>
            <strong>AI cashier (Google Gemini):</strong> reads a counter photo or a spoken order and matches it to the shop's
            catalog. It only picks items and quantities: prices come from the catalog, and the cashier confirms the bill.
          </li>
          <li>
            <strong>Name matching:</strong> finds the spoken name among the people nearby, allowing for spellings like
            Rahim and Raheem. It sends only on one clear match, and runs on our server, not on Gemini.
          </li>
          <li>
            <strong>Risk reasons (Google Gemini):</strong> rewrites why a payment needs a stricter check in plain words. The
            decision itself never comes from Gemini.
          </li>
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
          and makes no compliance claims. Photos and typed orders go to Google Gemini; motion data, customer names and
          locations never do. The browser's speech recognition may send what the till hears to its maker, such as Google.
        </p>
      </div>
      <Link to="/" className="btn block">
        Try it
      </Link>
    </div>
  )
}
