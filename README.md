# AirSign

**Pay in a shop with a move of your phone.** The cashier snaps a photo of the items or says the order, and **Google Gemini** drafts the bill. Your phone alerts you, and you confirm by moving it in a personal *air signature*, recorded by its gyroscope and accelerometer. Save your **PayPal** account once with **PayPal Vault**, and the air signature is all a normal payment needs. AI models check the motion, catch replays and fake sensor data, and decide how strict the check should be. Only a passing signature lets the server take the payment through PayPal.

No card, no PIN, no terminal: just a mobile browser. Built for the PayPal x AI hackathon *Build What's Next with PayPal and AI*. Everything runs on the **PayPal sandbox**, and no real money moves.

> Status: working prototype. The evaluation on real recordings (FAR/FRR/EER) is in progress; numbers will be added here when measured, including misses.

## Try it

1. Open the app on a phone (HTTPS is needed for motion sensors and location; see [Run on a phone](#run-on-a-phone)).
2. Enter a name, give consent, and **set up your air signature**: 7 repetitions of a 2–4 second gesture, like your initials drawn in the air.
3. Tap **Save my PayPal** once, then **I'm at a shop**. Your name and a short code appear on the phone.
4. On a second screen (laptop), open **`/pos`**, pick **Lumen Books**, PIN **`1234`**. Say or type the order (or snap a photo, with a Gemini key), and create the bill.
5. Send it: in **Person** mode tap your name. In **AI auto** mode the till asks your name out loud; answer it, or type it.
6. The phone buzzes. Tap **Sign to pay**, wait for green, and sign in the air.
7. The receipt shows your signature and what the AI checked, and the cashier screen flips to *Paid*. The PayPal webhook confirms it a moment later.

**No phone, or not in the shop?** Simulation mode is built in and clearly labeled:
- *Simulated location*: "I am in Meridian Arcade", or "Place this building here" to move a demo building to where you stand.
- *Simulated signature*: enroll and sign with a synthetic signature that has natural variation, sensor noise and timing jitter.
- *Simulated attackers*: someone who watched you, a different person, generated sensor data, and a replay of your last good signature.

Simulated recordings go through exactly the same pipeline, verifier and replay checks as real ones. Nothing is bypassed.

Things to try:

| Scenario | What happens |
|---|---|
| Coffee or a book at a shop you know | Tier 1: one signature |
| $400 at Volt Phone Repair (a shop you have never paid) | Tier 2 or 3: stricter threshold or two signatures, with a plain-language reason |
| "Someone who watched me" | Rejected, and the distance meter shows how far off it was |
| "Replay my last good signature" | Recognised as a recording, so the payment intent locks (your account does not) |
| "Generated sensor data" | Rejected: no sensor noise, perfectly regular timestamps |
| Three wrong signatures | The payment intent locks |
| Two bills open in one building | The popup won't guess and shows the shop list instead |

## The cashier's two modes

Customers tap **I'm at a shop**, and appear on the cashier's screen with their first name and a short code. The bill then goes to one phone, so nobody else sees it or can pay it:

- **Person:** the cashier asks the name and taps it in the list.
- **AI auto:** the till asks out loud, hears the name, and sends the bill on one clear match, allowing for spellings like Rahim and Raheem. Same names or unclear answers go back to the cashier with the likely people listed.

**Alert everyone nearby** is the fallback for when no name matches. Those phones see only "A bill is ready at Lumen Books" until someone opens it. The QR code and six-digit code still work without alerts.

Location finds the building, not the floor, so the list can include people on other floors. That's why it says *nearby* and shows a code to check against the phone. Presence stores the building and the code, never a position, and disappears within 45 seconds of the phone going quiet.

## Keys to set

The app runs with none of these (mock PayPal, keyword bills), so judges and teammates can always start it. Put them in `.env` at the repo root when running locally, or under **Environment** on the Render service. Never commit them.

| Variable | Where to get it | What it turns on |
|---|---|---|
| `AIRSIGN_GEMINI_API_KEY` | [Google AI Studio](https://aistudio.google.com/apikey) → *Create API key* (free tier works) | Photo bills, Gemini-read orders, Gemini-worded risk reasons |
| `AIRSIGN_PAYPAL_MODE` | Set to `sandbox` | Real PayPal sandbox instead of the built-in mock |
| `AIRSIGN_PAYPAL_CLIENT_ID`, `AIRSIGN_PAYPAL_CLIENT_SECRET` | [PayPal Developer Dashboard](https://developer.paypal.com/dashboard/) → *Apps & Credentials* → your sandbox app | PayPal sandbox |
| `AIRSIGN_PAYPAL_WEBHOOK_ID` | Same app → *Sandbox Webhooks* (see [Use the PayPal sandbox](#use-the-paypal-sandbox)) | Webhook confirmation on the receipt |

The Gemini models default to `gemini-3.8-flash` for reading orders, falling back to `gemini-3.5-flash-lite`, which also writes the risk reasons. `AIRSIGN_GEMINI_MODEL` and `AIRSIGN_GEMINI_FAST_MODEL` change them. Current free limits are on the [AI Studio rate-limit page](https://aistudio.google.com/rate-limit). On the free tier Google may use requests to improve its products, so AirSign sends only shop items, orders, counter photos and computed risk reasons: never motion data, signature templates, customer names or locations. In AI auto mode the browser's own speech recognition hears the name, and browsers such as Chrome send that audio to their maker to transcribe it.

## Run locally

Requirements: Python 3.11+ and Node 20+.

```bash
# backend
python3 -m venv .venv
.venv/bin/pip install -r backend/requirements-dev.txt

# frontend
cd frontend && npm install && npm run build && cd ..

# run (serves the API and the built frontend on :8000)
.venv/bin/uvicorn airsign.main:app --app-dir backend --host 0.0.0.0 --port 8000
```

Open http://localhost:8000. Without configuration it uses a **built-in PayPal mock** (labeled in the UI) and generates development secrets in `data/dev-secrets.json`.

For frontend development with hot reload, run `npm run dev` in `frontend/`; it proxies `/api` to port 8000.

Tests:

```bash
cd backend && ../.venv/bin/python -m pytest -q
```

The tests drive the whole flow through the API: happy path, single-use nonce, imitation lockout, replay of an earlier signature and of an enrollment recording, injected sensor data, tier 3 needing two signatures, check-in codes, forged QR tokens, invoice hijacking, unverified webhooks, and a check that **PayPal capture has exactly one call site**.

### Run on a phone

Browsers only give motion sensors and location to HTTPS pages. Either deploy (any host with HTTPS), or tunnel your local server, for example:

```bash
cloudflared tunnel --url http://localhost:8000
```

Then set `AIRSIGN_SECURE_COOKIES=true`. On iPhone the app asks for motion access when you tap *Allow motion sensors*.

### Deploy for free (Render + Neon)

The hosted demo needs HTTPS and has to stay up through judging. Both services below have free plans that don't need a card:

1. **Database: [Neon](https://neon.com) free Postgres.** Create a project and copy its connection string (`postgresql://…?sslmode=require`). Render's own free Postgres expires after 30 days, and Render's free disk is wiped on every restart, so the data lives on Neon.
2. **App: [Render](https://render.com) free web service.** In the dashboard choose *New → Blueprint* and pick this repo; [`render.yaml`](render.yaml) builds the [`Dockerfile`](Dockerfile). When asked, paste the Neon URL into `AIRSIGN_DATABASE_URL` and choose a team code for `AIRSIGN_LAB_CODE`. Render generates `AIRSIGN_SECRET_KEY` and `AIRSIGN_MASTER_KEY`. **Save a copy of the master key**: stored signatures can't be decrypted without it.
3. **Optional keep-alive.** Free services sleep after 15 idle minutes, and waking takes about a minute. To keep the app awake for judges, add the repository variable `AIRSIGN_URL` (your `https://….onrender.com`). The [`keep-alive`](.github/workflows/keepalive.yml) workflow then pings `/api/health` every 10 minutes. That endpoint doesn't touch the database, so Neon still scales to zero.

### Recording lab (for the team)

`/lab` collects labeled recordings on real phones for the evaluation. It is off unless `AIRSIGN_LAB_CODE` is set, and protected by that code.

- **Protocol per person:** 7 enrollment takes in one sitting; 15 genuine takes over two sessions on different days, standing and sitting; and 10 imitations of someone else after watching them sign.
- **After every take:** whether it's usable, the phone's real sampling rate, timestamp jitter and sensor noise floor, any sensor-check flags, and (once the person has enrolled) the distance score with the tier 1 and tier 2 decisions. A bad take can be deleted.
- **Results:** FAR/FRR per tier with 95% intervals, EER against imitations and against other people, replay detection, and sensor-check false positives per platform.
- **Export:** the data downloads as pseudonymous JSON (P1, P2, …). Offline:

  ```bash
  .venv/bin/python backend/scripts/evaluate.py airsign-lab.json   # or --synthetic 5
  ```

Participants consent before recording. Deleting a participant removes all their recordings. Lab data is kept apart from the payment system and is never used to authorize a payment.

### Tune it with real recordings

After the team has recorded in the lab, export the data and run:

```bash
# 1. pick the scoring method, sigma floor and tier thresholds; prints leave-one-person-out rates
.venv/bin/python backend/scripts/calibrate.py airsign-lab.json            # dry run
.venv/bin/python backend/scripts/calibrate.py airsign-lab.json --write    # writes backend/models/thresholds.json

# 2. optional: train the Siamese model (CPU is fine; about 4 minutes for 400 synthetic people)
.venv/bin/pip install -r ml/requirements.txt --index-url https://download.pytorch.org/whl/cpu
.venv/bin/python ml/train_siamese.py --lab airsign-lab.json --synthetic 400 --install
.venv/bin/python backend/scripts/calibrate.py airsign-lab.json --write    # recalibrate with the model
```

Commit `backend/models/` and redeploy. The thresholds apply at once. Neural scoring applies to signatures enrolled after the model is installed; older templates keep using DTW.

How the calibration chooses: it compares DTW, neural, fused and "both must pass", and keeps the lowest equal error rate against observed imitations. Tier 1 then accepts 97% of genuine attempts unless that would let more than 10% of imitations through; tiers 2 and 3 aim for 90%, with a 2% cap. Rates are reported **leave-one-person-out**: each person is judged with thresholds chosen on everyone else.

On synthetic people only (8 people, leave-one-person-out), the tier 1 results were:

| Method | Genuine accepted | Imitations accepted |
|---|---|---|
| DTW | 96.7% | 0% |
| Siamese CNN | 94.2% | 0% |

These numbers demonstrate the pipeline, not real-world accuracy. Real numbers come from the lab.

### Use the PayPal sandbox

1. In the [PayPal Developer Dashboard](https://developer.paypal.com/dashboard/), create a sandbox REST app and note the client ID and secret.
2. Add a webhook pointing to `https://<your-host>/api/webhooks/paypal` for `PAYMENT.CAPTURE.COMPLETED` and `PAYMENT.CAPTURE.DENIED`, and note its webhook ID.
3. Copy `.env.example` to `.env` and set:
   ```
   AIRSIGN_PAYPAL_MODE=sandbox
   AIRSIGN_PAYPAL_CLIENT_ID=...
   AIRSIGN_PAYPAL_CLIENT_SECRET=...
   AIRSIGN_PAYPAL_WEBHOOK_ID=...
   ```
4. Approve payments with a sandbox **personal** (buyer) account.
5. For **Save my PayPal**: in your app's *Sandbox App Settings* → *App Feature Options*, enable *Accept payments*, open *Advanced options*, and confirm **Vault** is selected. PayPal's docs also say saving PayPal needs reference-transaction approval on the account. If PayPal refuses, the app says so, and payments keep working with one PayPal approval each.

Never commit `.env`.

## How it works

```
Cashier POS ──invoice──▶ ┌──────────────── AirSign backend (FastAPI) ────────────────┐ ──▶ PayPal sandbox
Shop QR / code ─token──▶ │ shop ranking · risk engine · gesture match · replay check │     Orders v2 (create,
Customer phone ◀──API──▶ │ intent state machine · PayPal client · DB + audit log     │ ◀── get, capture),
                         └────────────────────────────────────────────────────────────┘     webhooks
```

1. **Invoice.** The cashier taps items, or lets the AI cashier draft them from a photo or a spoken order, and confirms. Amount and recipient are stored on the server, and the phone never supplies them.
2. **Sending the bill or shop detection.** A customer who tapped *I'm at a shop* gets the bill on their phone once the cashier picks them, or the till matches their spoken name. Otherwise browser location finds the *building* and the shop ranker picks the *shop* (shops on floors 3, 4 and 5 share coordinates); the customer confirms, picks from the list, or types the counter's rotating code.
3. **PayPal.** With a saved account (PayPal Vault) and a bill up to $200, this step is skipped: no PayPal order exists yet, because creating one with a saved account captures it at once. Otherwise the server creates an Orders v2 order (intent `CAPTURE`), the buyer approves with the **PayPal JavaScript SDK v6**, and the server checks the approval and amount with PayPal. Nothing is captured yet.
4. **Risk tier and nonce.** The risk engine picks tier 1, 2 or 3, and the server issues a 128-bit single-use nonce valid for 120 s.
5. **Sign in the air, hands-free.** Hold the phone still: the pad turns green, buzzes and plays a tone where the browser allows it. Moving the phone starts the recording, and holding still again ends it; the half-second of stillness before the first stroke is kept, because the server measures the sensor noise there. The signature appears as pen ink, reconstructed from the phone's in-plane movement (accelerometer) and wrist rotation (gyroscope), with a short Redo window before it is sent. Twitches that are too short or weak to be a signature are discarded without using up an attempt. The ink is only a picture for the person; the verifier works on the raw sensor data.
6. **Verify.** Preprocess, then score against the template, then run the replay and sensor checks, which gives ACCEPT, STEP_UP or REJECT.
7. **Capture.** Only a VERIFIED intent takes money: the approved order is captured, or the saved account is charged, with a `PayPal-Request-Id` idempotency key either way. The receipt comes from PayPal's response, and the `PAYMENT.CAPTURE.COMPLETED` webhook (signature verified with PayPal) reconciles it.

**The one rule:** the only code path that can move money is a passing sign-off (`backend/airsign/intents.py::_capture`, called only from `signoff`). Tests enforce it for both the capture and the saved-account charge.

### Where the AI is

| Component | What it does | Code |
|---|---|---|
| **AI cashier (Gemini)** | Reads a counter photo or a typed or spoken order with structured output, and picks catalog items and quantities. It cannot set prices or invent items: the server drops unknown ids and takes prices from the catalog, and the cashier confirms every draft. Typed orders fall back to a keyword match without Gemini. | `ai/cashier.py`, `ai/gemini.py` |
| **Name matching** | Matches a spoken name to the customers nearby, by spelling and by consonants, and sends only on one clear match. It runs on our server, so names never go to Gemini. | `ai/names.py` |
| **Risk reasons (Gemini)** | Rewrites why a payment needs a stricter check in plain words, from the reasons the risk engine already computed. The decision is committed first and never depends on Gemini, and any failure keeps the template sentence. | `ai/explain.py` |
| **Gesture match** | 6-channel IMU signal → resample to 50 Hz → 10 Hz Butterworth (zero-phase) → segmentation → per-channel z-norm. Dynamic Time Warping (Sakoe-Chiba band) against the enrolled repetitions; a personal threshold learned from the user's own variation. A Siamese 1D CNN (NumPy inference) fuses in when trained weights are present. | `signal/pipeline.py`, `dtw.py`, `enroll.py`, `verify.py`, `siamese.py` |
| **Replay and fake-data check** | Near-exact match against enrollment and earlier accepted signatures (compact encrypted fingerprints, not raw data); sensor-plausibility features: high-frequency noise floor, timestamp jitter, autocorrelation, cross-channel coherence, declared vs measured rate. | `signal/replay.py`, `fingerprint.py` |
| **Risk engine** | Transparent weighted rules (amount vs your median, new shop, unusual hour, velocity, distance, new device), plus an **isolation forest** on your own history, calibrated to your history's quantiles. Template-built explanation sentences, so the model cannot invent reasons. | `risk.py` |
| **Shop ranking** | Softmax over distance likelihood, floor hint (when altitude accuracy allows), visit history, opening hours, time of day, and *which shop just opened a bill*. It suggests a shop only if p ≥ 0.6 and leads the next by ≥ 0.2. | `shops.py` |

### Security and privacy

- Templates are encrypted with **AES-256-GCM** under a per-user data key, wrapped by a master key from the environment. Raw sensor streams are discarded after processing and never logged.
- Single-use, hashed nonce bound to the intent; it is consumed atomically. A payment intent allows 3 attempts, and a replay flag or the third failure locks the **intent, not the account**.
- Shop check-in QR tokens are HMAC-SHA256 signed, expire after 60 s and are single use; a 6-digit code is the fallback.
- PayPal webhooks are verified through PayPal's verification endpoint, using the raw request body exactly as received (re-serialized JSON would fail the signature check), and checked against order id and amount. Events are deduplicated by id.
- *Delete my signature* and *Delete all my data* are in the app. Location is read only when you tap, and only the matched shop is kept.

**Honest limits.** A motion signature is a behavioral biometric: one factor, not a replacement for Face ID or passkeys. A close observer might copy a simple gesture (enrollment rejects trivial ones), signing while walking is harder, and phones differ. Malware that injects realistic sensor data is out of scope. Verification runs on the server so it can gate the capture; the production path is on-device matching with device-bound keys. The demo returns match scores to show the model working; a production system would not. This prototype makes no regulatory compliance claims.

### Deliberate changes from our design report

We found these while building, and the code comments explain each one:

- **Threshold statistics.** Enrollment μ/σ are computed *leave-one-out with the same mean-of-3-nearest statistic* used to score a candidate. The all-pairs statistic in the report is not comparable and makes the threshold far too lenient. σ also has a floor, because it comes from only 5–10 scores.
- **Isolation forest calibration.** The report's `R_ml = 100 · s` would put almost every ordinary payment in tier 2 (normal points score about 0.5), so we use the quantile against the user's own history instead.
- **Open-bill feature in shop ranking.** Without it, a first-time visitor in a multi-floor building always ties, and the popup could never suggest a shop.
- **Separate replay bounds** for DTW distances and fingerprint distances, instead of one bound for two different scales.
- **The receipt comes from the capture response**, and the webhook reconciles it. Sandbox webhooks can lag by seconds.
- **Thresholds live in a config file** (`backend/models/thresholds.json`) and are applied when scoring, not baked into templates, so they can be retuned without anyone enrolling again. With only 5–10 enrollment repetitions the personal σ is unreliable, so σ has a relative floor (a fraction of μ) and an absolute one. Channels are standardized, so DTW distances share one scale across people, and the absolute floor stops one lucky, very consistent enrollment from making the threshold impossibly tight. Calibration picks both from data.
- **Three *failed* signatures lock a payment** (FR-10), not three signatures. Otherwise a tier 3 payment that went fail, fail, pass would wait forever for a second signature it could never submit.
- **Rate limits** per IP and per user on sign-up, enrollment, payments, sign-off, check-in codes, the cashier PIN and the lab code (in memory, enough for one instance).
- **The outlier gate** at enrollment also requires 1.5× the median, and drops at most two repetitions. With 7 samples the MAD alone drops normal repetitions.

## Project layout

```
backend/airsign/
  signal/      pipeline, DTW, enrollment, verification, replay checks, Siamese inference, synthesizer
  ai/          Gemini client, AI cashier, risk-reason wording
  paypal/      PayPal gateway: sandbox (official Python Server SDK, Vault over REST) and mock
  routers/     API: customer, shops, presence, payments, POS, saved PayPal, simulation
  presence.py  who is nearby: customers with "I'm at a shop" on, for the cashier's list
  intents.py   payment state machine and the single capture path
  risk.py      risk engine      shops.py  shop ranking      security.py  crypto
  evaluation.py  FAR/FRR/EER, replay and sensor-check rates with 95% intervals
backend/scripts/evaluate.py  offline evaluation of a lab export or synthetic people
backend/scripts/calibrate.py thresholds and method from labeled data (leave-one-person-out)
backend/models/              thresholds.json and siamese.npz once calibrated or trained
ml/train_siamese.py          PyTorch training of the Siamese CNN, exported for NumPy inference
backend/tests/ end-to-end API tests (SQLite by default; AIRSIGN_TEST_DATABASE_URL for Postgres)
frontend/src/  React app: customer pages, cashier POS, recording lab, sign pad, PayPal v6 loader
Dockerfile, render.yaml, .github/workflows/  free deployment, keep-alive, CI
```

## Tools used

| Tool | How we used it |
|---|---|
| **Google Gemini API** (`gemini-3.8-flash`, `gemini-3.5-flash-lite`) | AI cashier (photo and order to draft bill) and plain-language risk reasons |
| **PayPal Orders API v2** | Create, verify approval of and capture the order for each in-store bill; idempotent capture with `PayPal-Request-Id` |
| **PayPal Vault** (Payment Method Tokens v3) | Save a PayPal account once, then charge it with `vault_id` only after a passing air signature |
| **PayPal Python Server SDK** (`paypal-server-sdk`) | Server-side Orders calls in sandbox mode |
| **PayPal JavaScript SDK v6** | Buyer approval (`createPayPalOneTimePaymentSession`) for the server-created order |
| **PayPal Webhooks** | `PAYMENT.CAPTURE.COMPLETED` / `DENIED`, verified with `verify-webhook-signature` |
| **PayPal AI Toolkit** ([github.com/paypal/AI-Toolkit](https://github.com/paypal/AI-Toolkit)) | Audited the integration with its `paypal-best-practices` skill and *PayPal Doctor* checklist. Fixes that came out of it: webhook signatures verified against the raw request body, `PayPal-Request-Id` on every POST, retries with backoff for 429/5xx, `debug_id` logging, `INSTRUMENT_DECLINED` handled separately, `findEligibleMethods()` before showing PayPal's own `<paypal-button>` web component |
| **PayPal sandbox** | All payments, with sandbox business and buyer accounts |
| FastAPI, SQLAlchemy, SQLite / Postgres (Neon) | API, state machine, storage |
| NumPy, SciPy | Signal processing (resampling, Butterworth filtering), DTW, NumPy CNN inference |
| scikit-learn | Isolation forest for the learned risk score |
| PyTorch | Offline training of the Siamese CNN (triplet loss, semi-hard mining); the server runs it in NumPy |
| cryptography | AES-256-GCM envelope encryption of templates |
| React, Vite, TypeScript | Mobile web app and cashier screen; DeviceMotion and Geolocation APIs |
| qrcode | Rotating check-in QR on the cashier screen |
| Render, Neon | Free hosting for the demo (Render is a hackathon sponsor) and its Postgres database |
| Playwright | Headless browser runs of the full flow during development |
| Claude Code | AI pair programmer used to build and test the project |

## License

[MIT](LICENSE)
