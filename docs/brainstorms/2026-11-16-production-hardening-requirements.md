---
date: 2026-11-16
topic: production-hardening
---

# Production Hardening for the January Go-Live — Requirements

## Summary

Before the first paying school goes live in January 2027, close the gaps the November readiness assessment found. Parents can recover a password and are verified before a child is linked. Driver sign-in is scoped to one school. Operators are told when production fails, and the database can be restored. The exposed edges close: missing security headers, public API docs, vulnerable dependencies, the demo Map ID, retention that runs only by accident, and a deploy that can regenerate live secrets. Privacy and terms surfaces land with Kuumbai's text. Two certified releases, then a code freeze on 19 December 2026.

---

## Problem Frame

SafeRide has run in production since mid-2026 with one pilot school, and has since shipped multi-school tenancy and phone-GPS tracking. On 2026-11-15 a readiness assessment read the code, the infrastructure templates and the live AWS account. It found that the platform is sound at its core (tenant isolation live and verified, encrypted private database, auto-renewing certificates, a green test suite, zero errors in the last month) but that several first-week failures are waiting for a real customer.

A parent who forgets their password sees "Check your email" and nothing arrives: the platform sends no email at all, production discards the reset link, and the office has no way to reset a parent. Sign-up shows a verification promise that nothing fulfils. Anyone who signs up with an email address a school has on file is linked to that child at once.

Drivers share one 4-digit PIN space across every school. The PIN alone signs a driver in to whichever school owns it, and the "already in use" message tells a director that a PIN exists at another school. With one school this was theoretical. Production already has two schools, and January adds a paying one.

Nothing watches production. There is no alarm and no notification topic in either region. An unexpected error answers 500 with no log line, no log level is set so info lines are dropped, the health check never touches the database, and logs are kept forever.

The database is a single-AZ micro instance with deletion protection off, seven days of backups and a restore that has never been rehearsed. The deploy script regenerates the database master password and the PIN pepper if the operator's default CLI region ever changes, which would break every driver PIN.

Smaller exposures: no security headers on the site, interactive API documentation served publicly, thirteen vulnerable production dependencies in the frontend (most in a Firebase path production does not use), Google's demo Map ID on the maps, the browser Maps key living in an untracked local file, a position-trail purge that runs only after a Start Run, notification and audit rows never purged, no privacy notice, terms or consent, and no CI gate before a deploy.

Traffic shows the pilot has not been using the app daily: most school days in September and October saw under 60 API calls, where one bus run produces several hundred. The January school will be the first sustained use. Kenyan Term 1 begins in early January, so the change window is now to mid-December.

---

## Key Decisions

- **Parent recovery has two paths: real email and an office-issued reset.** Email makes "Forgot password" true and lets sign-up be verified. The office reset covers a parent whose email is wrong on the school's record or who has no working inbox, the same way staff resets already work. *Why:* either path alone leaves some parents locked out.

- **Email verification gates child linking for new sign-ups only.** A new parent sees no child until the address is verified. Every parent already linked in production keeps their links and is never asked to verify. *Why:* the exposure is someone claiming a child with a known email, which verification closes; forcing existing families through it would break working accounts for no gain.

- **The driver's phone is bound to one school once; sign-in stays PIN-only.** The office gives each driver a school link or QR, or the driver types the school code once. The phone remembers the school, the PIN is looked up within that school, and PINs are unique per school rather than globally. *Why:* drivers keep the one-tap morning they have today, tenant isolation no longer rests on PIN secrecy, and the office can re-issue the link without touching the driver's account.

- **Hardened single-AZ, not Multi-AZ.** Deletion protection on, 35-day backups, storage autoscaling, maintenance outside route hours, and one rehearsed restore with a written runbook. *Why:* at pilot scale a hardware failure costing a short hand-done restore is acceptable; Multi-AZ roughly doubles the database bill and is revisited when more schools run.

- **Alerts reach people, not dashboards.** Two named operators receive email when errors, latency, database capacity, the retention job or a deploy's migrate step cross a threshold. *Why:* the last month's zero-error record is unverifiable without it, and a school morning cannot wait for someone to open a console.

- **A deploy fails when a secret is missing; it never generates one.** *Why:* silent regeneration of the database password or the PIN pepper is an outage that looks like a successful deploy.

- **Seeds never run against production.** The migrate step applies seed files only in a local environment. *Why:* every seed is already marked applied in production, so this is safe today only by accident; a new seed file would run live.

- **Plain Web Push stays; the unused Firebase path goes.** Production has no Firebase key, so every phone already uses standard Web Push. *Why:* the Firebase SDK is where most of the vulnerable dependencies sit, and removing dead code is cheaper than keeping it patched.

- **Legal surfaces ship now with text supplied by Kuumbai.** Privacy notice, terms, links from sign-in and the app footer, a consent tick at parent sign-up. The wording is the lawyer's; its arrival is a release gate. *Why:* the surfaces are cheap and the children's-data context makes their absence the first thing a school's lawyer will ask about. ODPC registration and the data-processing agreement are a parallel non-code workstream.

- **Two certified releases and a freeze.** Release 1 carries the blockers (parent recovery and email, driver scoping, alerting and logging, database and deploy safety). Release 2 carries the hardening set and the legal surfaces. Code freeze from 19 December 2026 to the school's first route day; only fixes for live incidents cross it. *Why:* the blockers must not wait on the long tail, and a quiet week before Christmas lets the freeze hold.

- **Two small policy bets.** Every signed-in user can change their own password, since today only a forced change exists. Notification feed rows are kept 90 days and then deleted; the audit log is kept for the life of the school. *Why:* both are low-carrying-cost gaps a customer will notice.

---

## Actors

- A1. Parent — signs up, verifies, recovers a password, consents to the privacy notice and terms.
- A2. Driver — binds a phone to the school once, then signs in by PIN.
- A3. School office (director or transport coordinator) — issues parent resets, shows the school link or QR, manages drivers.
- A4. Provider operator (Kuumbai) — creates schools, supplies legal text, distributes the school link to the January school's drivers.
- A5. Platform operators (two named people) — receive alerts, run deploys and the restore rehearsal.

---

## Key Flows

- F1. Parent forgets password, email path
  - **Trigger:** A1 taps "Forgot password?" and enters their email.
  - **Steps:** The platform emails a single-use, time-limited reset link to that address if an account exists; the screen says a link was sent if the address is known. A1 opens the link, sets a new password, and is signed in. Other sessions on that account end.
  - **Outcome:** A1 is back in without contacting anyone.
  - **Covered by:** R1, R2, R8

- F2. Parent forgets password, office path
  - **Trigger:** A1 phones the school; their email on record is wrong or they have no inbox.
  - **Steps:** A3 opens the parent on the Parents page and issues a reset. A temporary password is shown once, valid 72 hours. A3 passes it on by a trusted channel. A1 signs in with it and must set their own password before seeing any page. The temporary password then stops working for everyone.
  - **Outcome:** A1 is back in within one phone call; the record shows who issued the reset.
  - **Covered by:** R3

- F3. New parent sign-up with verification
  - **Trigger:** A1 creates an account with the email the school has on file.
  - **Steps:** A1 ticks consent to the privacy notice and terms, submits, and is told to verify the address. The platform emails a verification link. Until it is used, Home shows that verification is pending and offers to resend; no child is linked and no school learns of the account. On verification, children at one school link at once; at more than one school the existing pending cards appear.
  - **Outcome:** Only the holder of the inbox reaches the child.
  - **Covered by:** R4, R5, R6, R32

  ```mermaid
  flowchart TB
    S[Sign-up submitted with consent] --> P[Pending verification: no child linked, resend offered]
    P -->|verification link used| V[Verified]
    V -->|email on file at one school| L[Children linked]
    V -->|email on file at several schools| C[Pending school cards, as today]
    E[Existing parent account] --> L
  ```

- F4. Driver binds a phone and signs in
  - **Trigger:** A2 opens the app on a phone that knows no school.
  - **Steps:** The Driver PIN tab asks for the school first: open the link or QR the office gave, or type the school code. The phone remembers the school and shows its name beside the PIN field. A2 enters the PIN and is signed in to that school. Every later morning is PIN-only on that phone. A new or wiped phone repeats the first step.
  - **Outcome:** A PIN is only ever resolved within one school.
  - **Covered by:** R9, R10, R11, R12, R13

- F5. Production fails and someone is told
  - **Trigger:** The API answers errors above the threshold, latency climbs, database storage runs low, the retention job fails, or a deploy's migrate step fails.
  - **Steps:** Within five minutes A5 receives an email naming the alarm. Each unexpected error has already been logged with a request identifier that the user's error message also shows. A5 matches a school's report to the log line.
  - **Outcome:** The operator learns of a failure before the school calls.
  - **Covered by:** R14, R15, R16, R17

- F6. Deploy with a missing secret
  - **Trigger:** A5 runs a release and one required secret is absent from its expected region.
  - **Steps:** The deploy stops before any change, names the missing secret and region, and exits non-zero. Nothing is generated.
  - **Outcome:** No silent rotation; the operator fixes the parameter and reruns.
  - **Covered by:** R22

- F7. Daily retention
  - **Trigger:** The scheduled job runs each night outside route hours.
  - **Steps:** Position-trail rows older than each school's retention are purged, notification feed rows older than 90 days are deleted, expired idempotency keys and reset tokens go, the audit log is untouched. A failure raises an alert.
  - **Outcome:** The driver notice's "then they are deleted" is true without a Start Run.
  - **Covered by:** R29, R14

- F8. Restore rehearsal
  - **Trigger:** Before Release 1 ships, A5 restores the latest automated backup into a scratch instance.
  - **Steps:** Row counts and the newest run are compared with production, the time from decision to a usable database is recorded, the scratch instance is deleted, and the steps become the runbook.
  - **Outcome:** A documented recovery time and a procedure anyone on A5 can follow.
  - **Covered by:** R21

---

## Requirements

**Parent account recovery and verification**

- R1. The platform sends transactional email (reset and verification links) from the SafeRide domain, with delivery failures visible to operators.
- R2. "Forgot password" emails a single-use link that expires within an hour, and the screen never claims a mail was sent when none was.
- R3. Either staff role can issue a parent a one-time temporary password, shown once, valid 72 hours, which the parent must replace at next sign-in; issuing it ends the parent's other sessions and is recorded against the staff member.
- R4. A new parent account links no child and is unknown to any school until its email address is verified; until then the app says so and offers to resend the verification mail.
- R5. Parent accounts that exist before the release keep their links and are never asked to verify.
- R6. Cross-school pending link cards behave as they do today once the account is verified.
- R7. Any signed-in user can change their own password from the app, giving the current password; the change signs out their other sessions.
- R8. No screen or guide promises an email the platform does not send.

**Driver sign-in scoped to the school**

- R9. A driver signs in by PIN only on a phone bound to one school; the binding comes from a school link or QR the office provides, or from typing the school code once.
- R10. PINs are unique within a school, not across schools, and no message reveals whether a PIN exists at another school.
- R11. The office can show the school link, its QR and the school code on the Drivers page, and the driver guide explains the first sign-in on a new phone.
- R12. A PIN entered on a phone bound to no school is refused with the instruction to open the school link or enter the code.
- R13. Existing drivers keep their PINs; their first sign-in after the release requires the school binding, so the link reaches every active driver before the release goes live.

**Alerting, logging and health**

- R14. Two named operators are emailed within five minutes when API errors or 5xx responses, latency, database storage or CPU, the retention job, or a deploy's migrate step cross agreed thresholds.
- R15. Every unexpected error is logged with a request identifier, and the error shown to the user carries the same identifier.
- R16. Production keeps info-level request logs, and all logs expire after 90 days.
- R17. The health check verifies the database connection and reports the deployed version.
- R18. Interactive API documentation is not served in production.
- R19. Account changes are recorded (CloudTrail on) and a monthly spend alarm exists.

**Database resilience and deploy safety**

- R20. The database has deletion protection on, automated backups kept 35 days, storage autoscaling, and maintenance and backup windows outside Kenyan route hours.
- R21. A restore from backup is rehearsed once before Release 1 into a scratch instance, the time to recover is recorded, and the runbook covers point-in-time recovery and the stack change needed to use a restored instance.
- R22. A deploy fails fast, before any change, when a required secret is missing from its expected region, and never generates a replacement.
- R23. Production deploys apply no seed files.
- R24. The Lambda concurrency quota is raised to at least 100 before January.

**Closing exposed edges**

- R25. The site sends security headers (HSTS, a content-security policy, frame denial, content-type nosniff, referrer policy) without breaking maps, push or the PWA.
- R26. Frontend production dependencies carry no known high or critical vulnerability at release; the unused Firebase path is removed; phones that enabled push before the release keep receiving it without re-enabling.
- R27. Spreadsheet import uses a maintained parser, enforces a file-size and row cap, and completes or fails clearly within the request time limit.
- R28. Maps use a production Map ID; the browser Maps key is held in the parameter store, injected at build, and restricted to the production origins.
- R29. Retention runs on a nightly schedule: the position trail per school setting, notification feed rows after 90 days, idempotency keys and reset tokens after their lifetimes; the audit log is kept for the life of the school.
- R30. Every pull request runs backend unit tests, the type check and frontend unit tests, and a release deploys only a clean checkout of main.

**Legal surfaces**

- R31. A privacy notice and a terms page are reachable without signing in, linked from the sign-in screen and from the app's footer or profile.
- R32. Parent sign-up requires a consent tick naming both documents, and the time of consent is recorded on the account.
- R33. The text of both documents comes from Kuumbai; the pages do not ship with placeholder text.

**Release process and manuals**

- R34. Two releases, each certified green at its release commit with a validation record: Release 1 (R1–R24) by 4 December 2026, Release 2 (R25–R33) by 18 December 2026, then a freeze until the school's first route day, crossed only by fixes for live incidents.
- R35. The parent, driver, office and provider guides describe recovery, verification, consent, the school link and the office parent reset in the words the app uses.

---

## Acceptance Examples

- AE1. **Covers R1, R2.** Given a parent account exists for an address, when the parent requests a reset, then one email arrives with a link that works once and is dead after an hour or after use, and the screen says a link was sent.
- AE2. **Covers R2, R8.** Given no account exists for an address, when a reset is requested, then nothing is sent and the screen does not claim otherwise.
- AE3. **Covers R3.** Given a coordinator issues a parent reset, when the parent signs in with the temporary password, then they must set a new one before any page, the temporary password then fails for everyone, and the parent's earlier sessions are gone.
- AE4. **Covers R4, R32.** Given a new sign-up with an address on file at one school and the consent tick set, when the account is created, then Home shows verification pending, no child appears, and the school's Parents page does not list the account; when the verification link is used, the children appear.
- AE5. **Covers R32.** Given the same sign-up without the consent tick, when the parent submits, then the account is not created and the form says why.
- AE6. **Covers R5.** Given a parent account linked before the release, when that parent signs in after it, then their children show as before and no verification is requested.
- AE7. **Covers R6.** Given a verified new account whose address is on file at two schools, when verification completes, then two pending school cards appear and nothing links until each is accepted.
- AE8. **Covers R9, R12.** Given a phone with no school binding, when a driver enters a valid PIN, then sign-in is refused with the instruction to open the school link; when the driver opens the link and enters the same PIN, then they are signed in and the school's name shows on the sign-in screen thereafter.
- AE9. **Covers R10.** Given drivers at two schools share the same four digits, when each signs in on a phone bound to their own school, then each reaches their own school's buses; when a third school's director tries to set that PIN, then it is accepted, and when the same school tries it twice, then the second attempt is refused without reference to any other school.
- AE10. **Covers R13.** Given a driver who signed in daily before the release, when they open the app the morning after it, then they are asked for the school once and are PIN-only from then on.
- AE11. **Covers R14, R15.** Given a request hits an unexpected error, when the response is sent, then it carries a request identifier, a log line with that identifier exists, and the operators receive an email within five minutes.
- AE12. **Covers R17.** Given the database is unreachable, when the health check is called, then it reports unhealthy rather than ok.
- AE13. **Covers R22.** Given one required secret is absent from its region, when a release is run, then it stops before the build, names the secret and region, and no parameter has been created or changed.
- AE14. **Covers R23.** Given a new seed file is present in the repository, when a production deploy runs, then the file is not applied and the deploy records that seeds were skipped.
- AE15. **Covers R26.** Given a parent enabled push before the release, when a boarding notice is sent after it, then the phone receives it without the parent touching the push setting.
- AE16. **Covers R27.** Given a spreadsheet above the row cap, when it is uploaded, then the dialog refuses it with the cap stated before any geocoding starts.
- AE17. **Covers R29.** Given a trail row older than the school's retention and a notification older than 90 days, when the nightly job has run, then both are gone, the audit rows of the same age remain, and the driver notice's retention statement is true.
- AE18. **Covers R21.** Given the latest automated backup, when it is restored into a scratch instance, then row counts match production for the main tables, the time to a usable database is recorded in the runbook, and the scratch instance is deleted afterwards.
- AE19. **Covers R25.** Given the security headers are live, when a parent opens Track and a staff member opens Fleet Map, then both maps render and push can still be enabled.

---

## Success Criteria

- No parent is locked out without a path: every forgotten password ends in a reset by email or by the office within one school day.
- An operator hears of a production error before the school does; verified by a drill that raises each alarm once before Release 1 ships.
- The restore rehearsal records a time to recover of one hour or less.
- Production dependency audits show zero high or critical findings in the frontend and backend at each release.
- Both releases are certified at their release commits, and the freeze holds from 19 December to the first route day.
- Every driver at the January school signs in on day one with the school link and their PIN, with no office intervention beyond handing out the link.
- The parent, driver and office guides pass the vocabulary test that ties their wording to the app's.

---

## Scope Boundaries

**Deferred for later**

- Multi-AZ database; revisit when more than a handful of schools run.
- SMS, WhatsApp or USSD for parents without smartphones.
- School offboarding, data export, and account self-deletion.
- Billing, school branding, bulk import of buses, drivers or staff.
- Swahili and other languages; an offline tap queue.
- A staging environment, API access logs, a web application firewall, a shared store for rate limits.
- Hardware trackers.

**Outside the code, run alongside**

- ODPC registration for Kuumbai and the data-processing agreement with each school.
- The wording of the privacy notice and terms.
- Delivery of the driver GPS briefing, the field-check record, the onboarding rehearsal on the test school, and the support rota.

---

## Dependencies / Assumptions

- Email sending depends on the SafeRide domain being verified with the email service and the account leaving its sending sandbox; the sandbox exit is a support request with days of lead time and starts before Release 1 code.
- The email service is assumed available in the backend's region; if not, a nearby region is used and the choice is recorded.
- Both operators supply an email address for alerts and confirm the subscription.
- Kuumbai supplies approved privacy and terms text before Release 2 is certified; without it Release 2 ships without the legal pages and they follow in a fix release.
- The Google Cloud console is available to create the production Map ID and restrict the browser key.
- Kuumbai sends the school link to every active driver at both production schools before Release 1 goes live, and to the January school's drivers before their first day.
- Parents at the January school have a working email address on the school's record; where they do not, the office reset is the path and the office corrects the record.
- Production already has two schools, so the per-school PIN rule and the cross-school collision check apply from the first release.
- Kenyan Term 1 begins in early January 2027; the school's first route day is confirmed with the school.

---

## Outstanding Questions

**Resolve Before Planning**

- None.

**Deferred to Planning**

- How the phone's school binding is represented (link token or code) and how long it persists.
- Whether the reset link and verification link share one mechanism.
- Alarm thresholds for errors, latency and database capacity, and the alert email addresses.
- How strict the content-security policy can be while Google Maps, push and the PWA keep working.
- Whether any cross-school PIN collision already exists in production and how the per-school rule is introduced over it.
- The CI provider and the exact clean-checkout check at deploy time.
- Batch sizes and timing for the nightly retention job.
- Whether existing driver sessions are ended at Release 1 or allowed to run out.

---

## Sources

- Readiness assessment of 2026-11-15: two read-only code audits and the live account. Observed in the account: no CloudWatch alarms or SNS topics in either region, Lambda concurrency limit 10, no CloudTrail, no budget, log retention unlimited, database single-AZ with deletion protection off and 7-day backups, certificates renewal-eligible to 2026-12-31, domain registered to 2027-06-14, interactive API docs public, push configured for plain Web Push only.
- `backend/app/api/auth.py` — forgot-password discards the link outside local; `backend/app/services/auth_service.py` — global PIN lookup and immediate child linking at sign-up; `backend/app/api/_helpers.py` — unexpected errors become 500 without a log line; `backend/app/api/health.py` — static health.
- `infra/backend/template.yaml` — database settings, no alarms, no log retention; `infra/scripts/deploy-backend.sh` — secret lookup that generates on a miss; `backend/app/migrate_handler.py` — seeds applied on every deploy; `infra/frontend/template.yaml` — no response-headers policy.
- `frontend/src/lib/googleMaps.ts` — demo Map ID; `frontend/src/lib/push.ts` — Firebase path unused without a Firebase key; `frontend/src/features/auth/AuthPage.tsx` — "check your email" copy.
- `docs/plans/2026-07-06-001-feat-ops-refinement-plan.md` — email verification deferred at the time; `docs/user-manual/parent-guide.md` — promises an emailed reset link; `docs/user-manual/provider-guide.md` — school deletion handled operationally.
- `docs/validation/2026-09-20-gps-release-3.md`, `docs/validation/2026-09-22-gps-release-4.md` — open human gates (briefing, field check, Map ID).
