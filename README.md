# AirSign

**Pay in a shop with a move of your phone.** AirSign works out which shop you are in, you approve the payment in PayPal, and you confirm it by moving your phone in a personal *air signature*, recorded by the phone's gyroscope and accelerometer. AI models check the motion, catch replays and fake sensor data, and decide how strict the check should be. Only a passing signature lets the server capture the payment through PayPal.

No card, no PIN, no terminal: just a mobile browser. Built for the PayPal x AI hackathon *Build What's Next with PayPal and AI*. Everything runs on the **PayPal sandbox**, and no real money moves.

> Status: working prototype. The evaluation on real recordings (FAR/FRR/EER) is in progress; numbers will be added here when measured, including misses.

## Try it

1. Open the app on a phone (HTTPS is needed for motion sensors and location; see [Run on a phone](#run-on-a-phone)).
2. Enter a name, give consent, and **set up your air signature**: 7 repetitions of a 2–4 second gesture, like your initials drawn in the air.
3. On a second screen (laptop), open **`/pos`**, pick **Lumen Books**, PIN **`1234`**, and create a bill.
4. On the phone tap **Pay in a shop** → *Find my shop* (or a simulated location) → **"Are you at Lumen Books, 4th floor?"** → approve in PayPal → **press and hold the pad, wait for green, sign in the air, release**.
5. The receipt appears, and the cashier screen flips to *Paid*. The PayPal webhook confirms it a moment later.

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

Never commit `.env`.

## How it works

```
Cashier POS ──invoice──▶ ┌──────────────── AirSign backend (FastAPI) ────────────────┐ ──▶ PayPal sandbox
Shop QR / code ─token──▶ │ shop ranking · risk engine · gesture match · replay check │     Orders v2 (create,
Customer phone ◀──API──▶ │ intent state machine · PayPal client · DB + audit log     │ ◀── get, capture),
                         └────────────────────────────────────────────────────────────┘     webhooks
```

1. **Invoice.** The cashier creates a bill. Amount and recipient are stored on the server, and the phone never supplies them.
2. **Shop detection.** Browser location finds the *building*; the shop ranker picks the *shop* (shops on floors 3, 4 and 5 share coordinates). The customer confirms, picks from the list, or types the counter's rotating code.
3. **PayPal approval.** The server creates an Orders v2 order (intent `CAPTURE`). The buyer approves with the **PayPal JavaScript SDK v6**, and the server checks the approval and amount with PayPal. Nothing is captured yet.
4. **Risk tier and nonce.** The risk engine picks tier 1, 2 or 3, and the server issues a 128-bit single-use nonce valid for 120 s.
5. **Sign in the air.** Press and hold the pad. It asks you to hold still for about 0.5 s (to measure the sensor noise floor), then you sign and release.
6. **Verify.** Preprocess, then score against the template, then run the replay and sensor checks, which gives ACCEPT, STEP_UP or REJECT.
7. **Capture.** Only a VERIFIED intent is captured, with a `PayPal-Request-Id` idempotency key. The receipt comes from the capture response, and the `PAYMENT.CAPTURE.COMPLETED` webhook (signature verified with PayPal) reconciles it.

**The one rule:** the only code path that can capture money is a passing sign-off (`backend/airsign/intents.py::_capture`, called only from `signoff`). A test enforces it.

### Where the AI is

| Component | What it does | Code |
|---|---|---|
| **Gesture match** | 6-channel IMU signal → resample to 50 Hz → 10 Hz Butterworth (zero-phase) → segmentation → per-channel z-norm. Dynamic Time Warping (Sakoe-Chiba band) against the enrolled repetitions; a personal threshold learned from the user's own variation. A Siamese 1D CNN (NumPy inference) fuses in when trained weights are present. | `signal/pipeline.py`, `dtw.py`, `enroll.py`, `verify.py`, `siamese.py` |
| **Replay and fake-data check** | Near-exact match against enrollment and earlier accepted signatures (compact encrypted fingerprints, not raw data); sensor-plausibility features: high-frequency noise floor, timestamp jitter, autocorrelation, cross-channel coherence, declared vs measured rate. | `signal/replay.py`, `fingerprint.py` |
| **Risk engine** | Transparent weighted rules (amount vs your median, new shop, unusual hour, velocity, distance, new device), plus an **isolation forest** on your own history, calibrated to your history's quantiles. Template-built explanation sentences, so the model cannot invent reasons. | `risk.py` |
| **Shop ranking** | Softmax over distance likelihood, floor hint (when altitude accuracy allows), visit history, opening hours, time of day, and *which shop just opened a bill*. It suggests a shop only if p ≥ 0.6 and leads the next by ≥ 0.2. | `shops.py` |

### Security and privacy

- Templates are encrypted with **AES-256-GCM** under a per-user data key, wrapped by a master key from the environment. Raw sensor streams are discarded after processing and never logged.
- Single-use, hashed nonce bound to the intent; it is consumed atomically. A payment intent allows 3 attempts, and a replay flag or the third failure locks the **intent, not the account**.
- Shop check-in QR tokens are HMAC-SHA256 signed, expire after 60 s and are single use; a 6-digit code is the fallback.
- PayPal webhooks are verified through PayPal's verification endpoint and checked against order id and amount.
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
  paypal/      PayPal gateway: sandbox (official Python Server SDK) and mock
  routers/     API: customer, shops, payments, POS, simulation
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
| **PayPal Orders API v2** | Create, verify approval of and capture the order for each in-store bill; idempotent capture with `PayPal-Request-Id` |
| **PayPal Python Server SDK** (`paypal-server-sdk`) | Server-side Orders calls in sandbox mode |
| **PayPal JavaScript SDK v6** | Buyer approval (`createPayPalOneTimePaymentSession`) for the server-created order |
| **PayPal Webhooks** | `PAYMENT.CAPTURE.COMPLETED` / `DENIED`, verified with `verify-webhook-signature` |
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
