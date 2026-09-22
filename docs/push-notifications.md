# Push notifications & PWA

SafeRide notifies parents about their child's bus in real time and installs as a
PWA on phones (Android Chrome and iOS 16.4+ Safari via "Add to Home Screen").

## Notification types

| Type | When it fires | Audience |
|---|---|---|
| `run-started` | Driver starts a **morning** run | Parents of every non-absent student on the run |
| `on-way-home` | Driver starts an **afternoon** run | Same |
| `student-boarded` | Driver marks a student as on the bus | That student's parents |
| `bus-approaching` | Driver arrives at the stop just before the child's stop (stop-order based — fired by the Arrive tap, not by the phone's position) | Parents of non-absent students at that next stop |
| `reached-school` | Bus arrives at the school gate (or a morning run ends) | Parents of every non-absent student on the run |
| `dropped-off` | An afternoon run ends | Same |
| `student-absent` | Driver marks a student absent at pickup | That student's parents only |
| `absent-call-now` | A remote absent mark ended up **uncorroborated**: the driver answered "No — I wasn't at the stop", dismissed the prompt, or left it unanswered until their next Arrive (GPS plan U10/R18). Additional to `student-absent`; never sent when the prompt is still open at End Run or force-close | That student's parents only — **at most once per child per run**, capped on the exception's event ledger before it reaches the push service, and **never retracted** by an undo |
| `boarding-corrected` | Driver undoes their own boarding (Board-tab Undo, or Undo on the far-from-stop prompt); retracts `student-boarded` | That student's parents only |
| `absence-corrected` | Driver undoes their own absent mark; retracts `student-absent` only (never `absent-call-now`). Body is neutral: the mark is withdrawn, the driver will record what happens | That student's parents only |
| `dropoff-corrected` | Driver undoes their own drop-off; retracts `dropped-off` | That student's parents only |
| `incident` | Driver reports breakdown / accident / traffic / student issue / notice | Parents of every student on that bus |
| `ride-cancelled` | A parent cancels the child's ride (Cancel-a-Ride) | That student's linked parents only |
| `admin-notice` | Office broadcasts a message to a route | Every parent with a child assigned to the route (one copy per parent) |

Run-scoped types (`run-started`, `on-way-home`, `student-boarded`,
`bus-approaching`, `reached-school`, `dropped-off`, `student-absent`,
`absent-call-now`, `boarding-corrected`, `absence-corrected`,
`dropoff-corrected`) are deduplicated per
(parent, run, student, type), so a parent gets each at most once per run.
Incident notifications are never deduplicated — every report matters. The
same goes for `ride-cancelled` and `admin-notice`: their `run_id` stays NULL
(they are not tied to a run), so every emission is a real row — the
cancellation confirmation fires only on a real scope transition, and two
identical broadcasts are deliberately two rows.

**Corrections retract.** Each `*-corrected` type first deletes the feed row it
supersedes (`student-boarded`, `student-absent` or `dropped-off`) for that
(parent, run, student), because the dedup index would otherwise suppress the
driver's genuine second confirmation as a duplicate of the message being
corrected. `absent-call-now` is the one row a correction never removes: a
mark/undo cycle must not re-arm the loudest message, and a family that was
asked to call is not stood down by the app.

**The call-now decision is durable.** The transaction that classifies the
absent mark stamps the notice *due* on the exception event; the post-commit
task sends it and stamps it *sent*; any later driver action or driver-context
poll on that run re-attempts due-but-unsent rows. The once-per-child cap
lives on that ledger, so a retry, a second prompt for the same child, or a
re-marked absence cannot send it twice.

Every notification is stored in `live_notifications` and shown in the parent
**Alerts** tab even when push is unavailable. Push delivery (FCM and/or Web
Push) is best-effort on top.

## Architecture

- `backend/app/services/push_service.py` — fan-out: resolves parents, writes
  feed rows, sends FCM + Web Push. Called from the run/incident routers via
  FastAPI `BackgroundTasks` so driver requests never wait on delivery.
- `backend/app/api/push.py` — `/api/push/config` (public client config),
  FCM token registration, web-push subscriptions, notification feed.
- `frontend/src/lib/push.ts` — client plumbing: FCM first, raw Web Push
  fallback, based on what `/api/push/config` reports.
- `frontend/public/sw.js` — service worker: PWA shell cache + push display +
  notification click deep-link to `/parent/alerts`.

With no push env vars set (the local default), everything still works: feed
rows are written, the UI shows them, and delivery is logged as simulated.

## Enabling Firebase Cloud Messaging (production)

1. Create a Firebase project at <https://console.firebase.google.com> (no
   Google Analytics needed).
2. **Project settings → General → Your apps → Add app → Web.** Register the
   app and copy the config object (`apiKey`, `authDomain`, `projectId`,
   `storageBucket`, `messagingSenderId`, `appId`).
3. **Project settings → Cloud Messaging → Web configuration → Web Push
   certificates → Generate key pair.** Copy the public key.
4. **Project settings → Service accounts → Generate new private key.**
   Download the JSON file. Keep it secret.
5. Set in `backend/.env`:

   ```bash
   FIREBASE_WEB_CONFIG_JSON={"apiKey":"...","authDomain":"...","projectId":"...","storageBucket":"...","messagingSenderId":"...","appId":"..."}
   FIREBASE_VAPID_KEY=<web push certificate public key from step 3>
   FIREBASE_SERVICE_ACCOUNT_JSON=/path/to/service-account.json   # or the inline JSON
   ```

6. Restart the API. Parents enable push from **Profile → Enable Push
   Notifications** (the app must be served over HTTPS or localhost).

## Deployment (live)

The Firebase project **`safe-ride-kenya`** is provisioned; `backend/.env`
holds the working credentials locally (gitignored). On the AWS deployment the
five push values are injected the same way as the Google Maps key — SSM
SecureString → deploy-time CloudFormation parameter → Lambda env var, wired in
`infra/backend/template.yaml` and seeded by `infra/scripts/deploy-backend.sh`:

| SSM parameter | Env var | Purpose |
|---|---|---|
| `/saferide/firebase-service-account-json` | `FIREBASE_SERVICE_ACCOUNT_JSON` | Admin SDK — backend FCM sends |
| `/saferide/firebase-web-config-json` | `FIREBASE_WEB_CONFIG_JSON` | Web SDK config served to the browser |
| `/saferide/firebase-vapid-key` | `FIREBASE_VAPID_KEY` | FCM web-push certificate (see below) |
| `/saferide/vapid-public-key` | `VAPID_PUBLIC_KEY` | Plain Web Push public key |
| `/saferide/vapid-private-key` | `VAPID_PRIVATE_KEY` | Plain Web Push private key |
| `/saferide/vapid-subject` | `VAPID_SUBJECT` | Web Push contact (mailto:) |

`deploy-backend.sh` seeds each SSM parameter from `backend/.env` on first
deploy, then re-reads SSM on subsequent deploys. The Lambda env stays under the
4 KB limit (~3.3 KB with all five set). Empty values degrade gracefully — the
in-app **Alerts** feed always works regardless.

**FCM web tokens vs. plain Web Push.** `FIREBASE_VAPID_KEY` (the Cloud
Messaging → Web Push certificate) is console-generated and currently unset, so
browsers use the **plain Web Push (VAPID)** path — standard, and it delivers to
installed PWAs on Android Chrome and iOS 16.4+ Safari all the same. To switch
web clients to native FCM tokens, generate the web-push certificate (step 3
above), then `aws ssm put-parameter --name /saferide/firebase-vapid-key
--type SecureString --value <key>` and redeploy — no code change.

## Plain Web Push (no Firebase)

Generate a VAPID key pair (`npx web-push generate-vapid-keys` or
`python -m py_vapid`) and set:

```bash
VAPID_PUBLIC_KEY=...
VAPID_PRIVATE_KEY=...
VAPID_SUBJECT=mailto:ops@yourdomain.com
```

When both Firebase and VAPID are configured, FCM wins for new device
registrations; existing web-push subscriptions keep working.

## PWA install

- Manifest: `frontend/public/manifest.webmanifest` (forest-green theme,
  maskable + any icons, standalone display).
- Icons are generated by `frontend/scripts/generate-icons.mjs` (Playwright).
- Android Chrome shows an install prompt automatically; iOS users use
  Share → Add to Home Screen. Push on iOS requires the installed app.
