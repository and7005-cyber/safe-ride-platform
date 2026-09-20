---
date: 2026-09-18
topic: gps-bus-tracking
---

# Phone GPS Bus Tracking — Requirements

## Summary

The driver's phone becomes the platform's source of bus position during runs, in two phases. Phase 1 attaches a real GPS fix to every run action and uses those fixes to raise stop exceptions — custody taps far from the stop, stops bypassed without an outcome, uncorroborated remote absent marks — that nudge the driver in the moment and flag the office. Phase 2 adds interval pings while a run is active so the bus moves between stops on every map. Every audience reads one unified latest position, GPS or geolocated checkpoint, labelled with its freshness. Phone GPS is a stopgap behind a source-agnostic feed that regulation-grade hardware trackers join later.

---

## Problem Frame

Today the map moves only when the driver taps. Start Run stamps the bus at the school gate, each Arrive stamps it at the planned coordinates of that stop, and End Run clears it. The fleet map is subtitled "Live bus positions", the parent Track page shows the same dot, and the driver guide states plainly that the app does not use the phone's GPS. Between taps nobody — office or parent — can tell whether the bus is at the stop, stuck in traffic two kilometres away, or somewhere it should not be.

A recent regulation requires every school bus to carry a GPS tracking device. Kuumbai intends to meet it with installed hardware trackers, but procurement takes time and the pilot schools need something now. Every driver already carries a GPS-capable phone running SafeRide during every run.

The school's own need is different from the regulator's: the office wants to know that the bus is really where the plan says it is, and that the taps reflect reality. Today the taps are self-reported and unverifiable. A driver can mark a child boarded from anywhere. A driver can pull away from a stop having tapped nothing, and the run simply stalls at that stop with no signal to anyone. A child marked absent from afar may be standing at the roadside, and the only counterweight is the parent noticing the absent notification in time. The office's one verification tool is a phone call to the driver.

The platform's real-time mechanism is polling — parents every five seconds, staff every fifteen — but the fleet map, the page built for this question, does not refresh at all while open.

---

## Key Decisions

- **Phone GPS is a stopgap behind a position feed that is source-agnostic within an active run.** The regulation will be met by installed hardware trackers later. Everything downstream — maps, freshness labels, exceptions — consumes "latest known position of this bus, with source and time" and never cares whether it came from a phone or a tracker. *Why:* the in-run admin side gets built once. Compliance artefacts (retention rules, inspector evidence, device certification) and the out-of-run position lifecycle a tracker brings — it reports whenever the vehicle moves — are hardware-phase decisions.

- **Taps first, pings second.** Phase 1 attaches a GPS fix to every run action, builds the unified position on those fixes, and ships the integrity checks. Phase 2 adds interval streaming, screen wake lock, and real-time missed-stop nudges. *Why:* Phase 1 rides code paths that already exist, proves the permission flow and the exception concept on real drivers, and delivers most of the integrity value before any streaming machinery exists. The between-stop live dot consciously waits for Phase 2. R10 and R15 read no fix at all and are sequenced first within Phase 1, ahead of the permission flow.

- **Tracking only while a run is in progress.** Start Run opens the window; End Run or an office close shuts it; nothing is requested or recorded outside it. *Why:* drivers use personal phones, this is when route adherence matters, and it mirrors how the checkpoint position already behaves. Pre-run staging visibility was weighed and rejected.

- **One unified latest position for every audience.** Staff console, provider view and parent Track page all read the same position: whichever of GPS-stamped action, GPS ping or planned-stop checkpoint is freshest, each carrying its source and time. *Why:* a quiet phone degrades to exactly today's behaviour, never below it, and one model needs no per-audience rules.

  ```mermaid
  flowchart TB
    T[Run action with GPS fix - Phase 1] --> P[Latest known position per bus - source, time, trail]
    G[Interval ping - Phase 2] --> P
    C[Planned-stop checkpoint - fallback, today's behaviour] --> P
    H[Hardware tracker - later] -.-> P
    P --> M[Staff fleet map and live run view]
    P --> O[Provider view of a school]
    P --> K[Parent Track page]
  ```

- **Nudge and flag, never gate.** A tap always completes. When GPS disagrees with a tap, the driver gets an in-the-moment prompt — confirm or undo, one prompt at a time — and the office gets a stop exception; when GPS is missing or too coarse to test, the action is recorded as unverified and listed for the office. The two safety-critical prompts, bypassed stop and remote absent, carry a tone or vibration. *Why:* a gate on the honest path pushes behaviour onto the dishonest one — skip the stop and tap nothing. GPS drift and the routine habit of tapping after pulling away would challenge honest drivers. Forgetfulness self-corrects; anything else leaves a trail.

- **Absent stays markable from anywhere, but is stamped and classified.** The guide's promise survives. Each absent mark records where the phone was and whether the bus was in the stop's vicinity, and is classified by corroboration. In-app cancellations and office absences remove the child from the driver's list before a tap can happen, so the corroborators that apply to a tap are location — within the stop's vicinity, or at the school before an afternoon departure — and the driver's own attestation: a remote mark asks the driver whether a parent or the office told them, or the child was not at the stop. An attested mark is kept in history with today's notification and stays off the live view; "not at the stop" is the one class that flags and sends the call-now notice. When the stop has no usable coordinates or the fix is missing or too coarse, the mark is unverified: stamped and listed for the office, with no nudge and no call-now. *Why:* GPS watches the phone, not the child. The attestation is an explicit, recorded claim the office can compare against the parent's account and against patterns in history — more than today's silent mark — and it keeps call-now rare enough to be acted on.

  ```mermaid
  flowchart TB
    A[Driver taps Absent] --> U{Stop coordinates usable and fix usable?}
    U -->|no| V[Unverified - stamped, listed for the office by reason, notify as today]
    U -->|yes| D{Phone within the stop's vicinity, or at the school on an afternoon run with the child's stop not yet reached?}
    D -->|yes| C[Corroborated by location - stamp only, notify as today]
    D -->|no| R{Driver attests: told by a parent or the office?}
    R -->|yes| T[Attested - kept in history, notify as today, off the live view]
    R -->|no or dismissed| E[Not at the stop - stop exception, call-now parent notice]
  ```

- **Integrity checks target custody claims.** Far-from-stop checks apply to Board and Drop-off. The existing off-route hand-over remains the declared path for a legitimate away-from-stop drop-off and is never an exception. *Why:* hand-over already captures where and to whom; flagging it would punish the disclosure the button exists for.

- **The run's position trail is kept.** Positions are retained per run, not only the latest. *Why:* bypassed-stop and vicinity checks need the history, and so do later off-route alerts and the hardware phase. Trails become eligible for deletion after a configurable period — 90 days by default, set before the regulation's retention rule is known — and are purged on the next Start Run; exceptions outlive them with the run.

- **Exceptions are office-facing.** Parents never see stop exceptions; they keep receiving the existing notifications. *Why:* exceptions are data-quality and accountability tooling. Raising them to parents before the office has looked would create panic calls.

---

## Actors

- A1. **Driver** — runs the route from their own phone in the browser app. Grants location permission, receives nudges; every tap completes regardless of how they answer.
- A2. **School office staff** — the director or transport coordinator. Watches the live run and fleet map, reviews stop exceptions, phones the driver when something is wrong.
- A3. **Kuumbai provider** — sees positions and exceptions for whichever school they have stepped into. Later owns the hardware tracker rollout.
- A4. **Parent** — sees their child's bus on the Track page and receives the existing notifications. Never sees exceptions.
- A5. **Position source** — anything that can say "this bus was here at this time": the driver's phone today (action fixes in Phase 1, interval pings in Phase 2), an installed tracker later.

---

## Key Flows

- F1. Run action with a GPS fix (Phase 1)
  - **Trigger:** A1 taps Start Run, Arrive, Board, Drop-off, Absent, Hand-over or End Run.
  - **Actors:** A1, A5, A2
  - **Steps:** The app takes a fresh fix; on the very first Start Run it first explains why location is needed, then the browser asks. The action is sent with the fix, or with the reason none was available. The platform records the action, updates the bus's latest position, appends the trail, and runs the integrity checks for that action. A failed check returns a nudge to the driver and records an exception.
  - **Outcome:** The dot moves to where the bus actually was. The office sees any exception as it happens.
  - **Covers:** R1, R2, R4, R7, R8, R12, R13

- F2. Custody tap away from the stop (Phase 1)
  - **Trigger:** A1 taps Board or Drop-off with a fix whose distance from the student's assigned stop, less the fix's accuracy, exceeds the threshold.
  - **Actors:** A1, A2
  - **Steps:** The tap completes. The driver sees "You are 1.8 km from Wanjiru's stop — board anyway?" with confirm and undo. Confirm keeps the boarding; undo returns the child to no outcome and marks the exception retracted, with the tap-then-undo kept in history. The first such tap at a stop creates one exception for that stop and run; later taps for the same stop attach their distance and answer to it. The exception shows the distance, the driver's answer, and whether any fix on the run placed the bus within the stop's vicinity — "bus seen at stop: yes / no" — on the live run view and in history.
  - **Outcome:** An honest late tap costs one confirm and shows "seen at stop: yes". A fabricated boarding shows "no".
  - **Covers:** R13, R14, R21, R23

- F3. Bypassed stop caught at the next Arrive (Phase 1)
  - **Trigger:** An Arrive moves progress past a stop that still has students with no outcome. Absent and Hand-over never trigger it.
  - **Actors:** A1, A2
  - **Steps:** An exception names the stop and the unaccounted students. The driver is prompted — with a tone or vibration — "Kimathi Corner: Brian and Amina have no record. Mark boarded or absent?" with one-tap shortcuts. Taps made through the prompt are recorded as the exception's resolution, with their fix and distance, and are exempt from the far-from-stop check. The exception leaves the live view when every listed student has an outcome; history keeps it with its resolution.
  - **Outcome:** No student silently drops out of the day. The office can call while it still matters.
  - **Covers:** R15, R21

- F4. Remote absent (Phase 1)
  - **Trigger:** A1 taps Absent with a usable fix outside the stop's vicinity and not at the school before an afternoon departure.
  - **Actors:** A1, A2, A4
  - **Steps:** The absent is recorded immediately, like every tap, and today's absent notification goes out as it does now. The driver is prompted — with a tone or vibration — "You are not at Wanjiru's stop. Did a parent or the office tell you she is not coming?" with three answers: told me, not at the stop, undo. "Told me" records an attested absent in the run's history with the driver's fix, and nothing appears on the live view. "Not at the stop" raises the stop exception on the live view and sends the parent the additional call-now notification; dismissing the prompt, or leaving it unanswered until the next Arrive, counts as "not at the stop". Undo returns the child to no outcome, marks the exception retracted, and sends today's correction notice in neutral wording.
  - **Outcome:** The stranded-child scenario has a loud, fast parent loop and an office flag; a phoned-in absence costs the driver one honest tap.
  - **Covers:** R16, R17, R18

- F5. Permission denied, no fix, or an unverifiable check (Phase 1)
  - **Trigger:** A1 declines location, the device cannot produce a fix or produces one too coarse to test, or the stop has no usable coordinates.
  - **Actors:** A1, A2
  - **Steps:** When permission is denied the driver's screens show a persistent, non-blocking "Location off — the office sees checkpoint positions only" state with how to enable it. Every tap proceeds; without a fix the planned-stop coordinates are used exactly as today. The action is recorded as unverified with its reason and listed on the run's exception view, with no nudge and no call-now. A run whose actions carry no fixes at all shows the "no GPS for this run" exception and a GPS-off marker in staff views; a stop without usable coordinates is listed once per run as "stop position unverified" so the office fixes the pin.
  - **Outcome:** Nothing is blocked; the absence of the witness is itself visible, per action and per run.
  - **Covers:** R2, R4, R8, R13, R20

- F6. Live run with interval pings (Phase 2)
  - **Trigger:** A run is in progress and the app is open.
  - **Actors:** A1, A5, A2, A4
  - **Steps:** The phone reports a fix at the configured interval and keeps the screen awake. Each ping updates the latest position and the trail; the fleet map and Track page poll and show "updated 12 s ago". When the screen locks the pings stop, the label ages, and past the staleness threshold the dot dims with "last seen" wording. A tap or a return to the app resumes pings. End Run stops them and clears the live position.
  - **Outcome:** The office watches the bus move between stops; gaps are honest rather than hidden.
  - **Covers:** R24, R25, R27, R28

- F7. Arrival and departure nudges (Phase 2)
  - **Trigger:** The trail shows the bus entering the vicinity of the next un-actioned stop, and later leaving it.
  - **Actors:** A1, A2
  - **Steps:** On entering with no Arrive recorded, the driver is offered a one-tap Arrive. On leaving with students at that stop lacking outcomes, the driver is prompted to record them. If still unrecorded after leaving, the bypassed-stop exception is raised immediately.
  - **Outcome:** A missed stop is caught while the bus is still nearby.
  - **Covers:** R29, R30

---

## Requirements

**Phase 1 — Position capture on run actions**

- R1. Every run action a driver performs — Start Run, Arrive, Board, Drop-off, Absent, Hand-over, End Run — captures a GPS fix from the phone at the moment of the tap and sends it with the action.
- R2. A fix carries coordinates, accuracy and capture time; when no fix can be obtained within the fix-wait budget the action is sent with the reason and completes normally. The fix-wait budget — how long a tap may wait for a fix — is set by the field check before thresholds are chosen.
- R3. Taps do not queue offline today. The app captures the fix at the first tap attempt and re-sends that same fix and capture time on any retry of the same action, so the position evidence is bound to when the driver acted, not to when the network delivered it.
- R4. Before the first location prompt the app explains why location is needed and when it is used; a denied permission leaves a persistent, non-blocking indicator on the driver's screens with how to enable it.
- R5. The app never requests or records position outside a run in progress.
- R6. The driver guide's statement that the app does not use the phone's GPS is replaced by the active-run tracking promise and what it is used for; drivers are told before rollout.

**Phase 1 — Unified position**

- R7. Each bus has one latest known position with a source and a time. Sources are the planned-stop checkpoint, a GPS-stamped action, and a GPS ping; new sources such as a hardware tracker join without changing any consumer.
- R8. When an action carries a fix, the fix becomes the position; without one, the planned-stop checkpoint is used exactly as today.
- R9. The fleet map, the provider's view of a school and the parent Track page all show the unified position with how fresh it is; staff views also show the source.
- R10. The fleet map refreshes while open at the staff polling cadence. This reads no fix and is sequenced first within Phase 1, ahead of the permission flow.
- R11. The current position clears at End Run or office close, as today; nothing is shown as current outside a run.
- R12. Every position is retained in the run's trail, not only the latest. Trail rows become eligible for deletion after a configurable period, 90 days by default, and are purged on the next Start Run in that school; no trail older than the period is ever served. The run's exceptions are kept.

**Phase 1 — Stop exceptions**

- R13. A stop exception is a per-run record naming the stop, the students concerned, its kind, when and where the phone was, and the driver's response to the nudge if any — confirmed, dismissed or retracted. Phase 1 kinds: custody tap away from stop, stop bypassed without outcome, remote absent uncorroborated, remote absent attested (history only, never on the live view), unverified (with its reason: no fix for this action, fix too coarse, stop position unverified), no GPS for this run. A custody-tap exception also records whether any fix on the run placed the bus within the stop's vicinity, shown as "bus seen at stop: yes / no". The driver sees one prompt at a time, queued — bypassed-stop and remote-absent prompts before routine custody confirms — never stacked.
- R14. Custody tap away from stop: a Board or Drop-off tap whose distance from the student's assigned stop, less the fix's reported accuracy, exceeds a configurable threshold. The tap completes, the driver is asked to confirm or undo, and the answer is recorded; undo returns the child to no outcome, marks the exception retracted and keeps the tap-then-undo in history. One exception is raised per stop per run: the first offending tap creates it and later taps for that stop attach their distance and answer. Taps made through a bypassed-stop resolution prompt are exempt.
- R15. Stop bypassed without outcome: when an Arrive moves progress past a stop whose students still have no outcome — boarded, dropped off, absent or handed over — an exception lists them and the driver is prompted, with a tone or vibration, to record outcomes. Absent and Hand-over never trigger it. Taps made through the prompt are recorded as the exception's resolution with their fix and distance. It leaves the live view once all listed students have an outcome. The check reads no fix and is sequenced first within Phase 1; the phone-position stamp on its exception arrives with the rest of Phase 1.
- R16. Every absent mark records the phone's position and whether it was within the stop's vicinity, measured as distance less the fix's accuracy after the coarse-fix cap. It is classified unverified when the stop has no usable coordinates or the fix is missing or too coarse to test; corroborated when a parent cancellation or office absence already exists for that student and trip, when marked within the vicinity, or — on an afternoon run — when marked within the school's vicinity while the child's stop is not yet reached; otherwise it is a remote absent, classified by the driver's attestation under R17.
- R31. Vicinity radius is configurable and tolerates GPS accuracy and stop-coordinate error.
- R17. A remote absent is recorded immediately, like every tap, and today's absent notification goes out as it does now; the driver is then asked, with a tone or vibration, whether a parent or the office told them the child is not coming, with the answers told me, not at the stop, and undo. "Told me" records an attested absent: kept in the run's history with the attestation and the fix, never shown on the live view. "Not at the stop", a dismissed prompt, or a prompt left unanswered until the driver's next Arrive raises an uncorroborated remote absent exception on the live view; a prompt still pending at End Run or office close is recorded unanswered without a call-now notice. Undo returns the child to no outcome — an afternoon undo restores the presumed boarding — and marks the exception retracted.
- R18. The call-now parent notification is an additional message with its own type: it says the driver marked the child absent away from the stop and asks the parent to call the office now if the child should be on the bus. It is sent only for the uncorroborated class, at most once per child per run, never for an attested absent; an undo sends today's correction notice, reworded neutrally, never call-now.
- R19. Off-route hand-over is never a stop exception.
- R20. An action whose check cannot be evaluated — no fix for this action, fix too coarse, or stop position unverified — is recorded as an unverified exception with its reason: stamped where a fix exists, shown on the run's exception view grouped by reason, with no nudge and no call-now; the standard notifications go out as today. A stop without usable coordinates is listed once per run as "stop position unverified" so the office fixes the pin. A run whose actions carry no fixes at all shows the "no GPS for this run" exception and a GPS-off marker in staff views for that run.
- R21. Exceptions appear on the staff live view of the run as they happen, stay with the run in history, and can be marked reviewed by the director or the transport coordinator. Positions and exceptions follow school scoping: visible only inside the school the run belongs to, and to the provider when stepped into it.
- R22. Parents never see exceptions.
- R23. Nudges never block: the tap completes before the prompt appears, and dismissing a prompt is a recorded response, not an error.
- R32. A fix is a corroborating signal, not proof. Before a fix can clear or suppress a check, a plausibility safeguard runs: a fix with anomalous accuracy, or a physically implausible jump from the previous fix or ping, is flagged for office review rather than treated as a normal fix.

**Phase 2 — Interval pings**

Phase 2's phone streaming, R24–R28, proceeds only if installed trackers are more than a stated number of months away or will not cover every pilot bus (see Dependencies). R29 and R30 are unconditional: they consume the trail from whatever source feeds it.

- R24. While a run is in progress and the app is open, the phone reports its position at a fixed interval; reporting stops within one interval of End Run, office close, or the app leaving the foreground. A ping never triggers a parent notification; the legacy proximity push is retired before pings ship.
- R25. The app keeps the screen awake while a run is in progress and the app is in the foreground.
- R26. Interval and accuracy are chosen so the pings themselves cost immaterial data and battery on a typical driver phone; the screen-awake cost is measured in the field check. The interval is adjustable without a release.
- R27. A ping older than the staleness threshold makes the dot visibly stale with "last seen" wording on every surface; the position is never hidden while the run is active.
- R28. Pings are accepted only for a run in progress and only from the driver assigned to it.

**Phase 2 — Real-time stop nudges**

- R29. When the trail shows the bus entering the vicinity of the next stop with no Arrive recorded, the driver is offered a one-tap Arrive.
- R30. When the trail shows the bus leaving a stop's vicinity while students at that stop have no outcome, the driver is prompted to record them; if still unrecorded after leaving, the bypassed-stop exception is raised immediately rather than at the next action.

---

## Acceptance Examples

- AE1. **Covers R1, R8.** Permission granted; the driver taps Arrive at stop 3 while parked 40 m from its planned coordinates. The bus's position is the fix, source "GPS action", and the dot moves there.
- AE2. **Covers R2, R8, R20.** Permission denied; the driver taps Arrive. The action completes, the position is the planned stop as today, the run shows "no GPS for this run", and the fleet map marks the bus GPS-off.
- AE3. **Covers R13, R14, R23.** The driver taps Board for Wanjiru 1.8 km from her stop, having driven off before tapping. Board completes; the prompt shows the distance; the driver confirms. The office sees the exception with 1.8 km, "confirmed by driver" and "bus seen at stop: yes" — her stop's Arrive fix was within the vicinity.
- AE4. **Covers R14.** The driver taps Board 60 m from the stop, inside the threshold. No prompt, no exception.
- AE5. **Covers R15.** Stop 4 has Brian and Amina; the driver taps Arrive at stop 5 with neither recorded. An exception lists both and the driver is prompted. The driver marks Brian boarded and Amina absent; the exception leaves the live view and history shows how it resolved.
- AE6. **Covers R16, R17, R18.** Wanjiru's mother phones the driver at 06:40 to say she is sick. The driver marks her absent from the road and answers "told me". No live exception; the attested absent sits in the run's history with the driver's fix; her mother receives today's standard absent notification.
- AE7. **Covers R16, R17, R18.** The driver marks Brian absent 3 km from his stop before reaching it and answers "not at the stop". An uncorroborated remote absent exception appears on the live view; Brian's parent receives the call-now notification.
- AE8. **Covers R16.** The driver waits at the stop, the child does not appear, and the driver marks absent within the vicinity. Corroborated by location: stamped, no prompt.
- AE9. **Covers R19.** The driver records an off-route hand-over 500 m from the stop with where and to whom. No exception.
- AE10. **Covers R21.** A coordinator at school A sees no positions or exceptions from school B's runs; the provider stepped into school A sees only school A's.
- AE11. **Covers R9, R10.** A coordinator keeps the fleet map open; the driver taps Arrive. Within one staff polling interval the dot moves and the freshness label shows the tap time.
- AE12. **Covers R5, R11.** The driver opens the app after the run ended. No fix is requested and nothing is recorded; no bus shows a current position.
- AE13. **Covers R24, R27.** Phase 2. Mid-run the driver locks the phone. Pings stop; after the staleness threshold the dot dims with "last seen 4 min ago". The driver unlocks and taps Arrive; pings resume and the label refreshes.
- AE14. **Covers R30.** Phase 2. The bus enters stop 6's vicinity and leaves without an outcome for Amina. The driver is prompted on leaving; nothing is recorded; the exception appears on the live view before the bus reaches stop 7.
- AE15. **Covers R28.** Phase 2. A ping arrives for a run that ended a minute earlier. It is rejected and no position changes.
- AE16. **Covers R16.** Afternoon run. The driver has tapped Arrive at the school gate and marks Brian absent there because he did not board; his own stop is further along the route. Corroborated by location: no prompt, no exception, the existing notification only.
- AE17. **Covers R14, R20.** The driver taps Board with a fix whose accuracy radius is 900 m, wider than the threshold. No prompt; the action is recorded as unverified — fix too coarse — on the run's exception view.

---

## Success Criteria

- At least nine in ten run actions on the pilot fleet carry a GPS fix within two weeks of Phase 1 — the measure of permission uptake and device reliability.
- Stop exceptions per completed run settle to a volume the coordinator reviews in minutes; if most runs produce several, check the tap-after-driving-off habit and stop-coordinate quality before thresholds — the drivers come last.
- No measurable increase in run duration attributable to nudges.
- In Phase 2, office staff answer "where is the bus now" from the console without calling the driver, and the median position age over the whole of an active run is under one minute.
- Drivers are briefed and the guide is updated before Phase 1 reaches any driver.

---

## Scope Boundaries

**Deferred for later**

- Route-corridor deviation alerts — "the bus left the planned path between stops". Stop-level exceptions are in; corridor monitoring is not.
- Trail playback or a route-history view on the map. The trail is stored; showing it is later.
- Pre-run staging visibility (bus on its way to the first stop).
- Parent-facing arrival estimates.
- Speed monitoring — free with GPS data, its own feature, and likely a regulator topic.
- Hardware tracker integration. The feed is ready for it; the integration is its own project.

**Outside this feature's identity**

- Compliance artefacts for the regulation: retention rules for inspectors, reports, device certification.
- The out-of-run position lifecycle a tracker brings — reporting whenever the vehicle moves, a position current outside any run — is a hardware-phase decision.
- Disciplinary workflow. Exceptions inform the office; what a school does with a pattern is outside the app.
- Tracking drivers outside a run.
- A native app.

---

## Dependencies / Assumptions

- **Browser geolocation is sufficient.** The standard web geolocation capability reads the phone's GPS chip; it needs a secure origin, which the app already has for push, and a one-time permission per device. It works only while the app is open on screen — browsers have no background tracking — which the screen wake lock (Android Chrome, iOS 16.4+) and the checkpoint fallback mitigate. A field check on one or two representative driver phones early in Phase 1 confirms the permission flow, fix quality on real routes, the fix-wait budget, wake-lock behaviour, per-run battery drain and drivers' willingness to keep the screen on.
- **Android dominates the driver fleet.** iOS works with more re-prompting; not a design driver.
- **Nothing runs on a timer.** The deployment has no scheduler; every check fires on an arriving action or ping. "Unresolved after leaving" in Phase 2 is detected on the pings that follow, not by a clock. R12's purge rides Start Run for the same reason.
- **This lands on top of school scoping.** The multi-tenant work supplies the roles and the per-school visibility that R21 inherits; both director and coordinator see exceptions.
- **Corroboration sources exist.** The parent Cancel-a-Ride flow and office absence records carry the per-trip absence state R16 reads; because both remove the child from the driver's list before a tap can happen, the corroborators that apply to a tap are location and the driver's attestation.
- **Stop coordinates are geocoded, not surveyed.** Thresholds and vicinity radii must tolerate geocoding error as well as GPS drift. Some stops have no coordinates at all and some carry low-confidence geocodes; checks on them degrade to unverified rather than classify.
- **The regulation's device and retention details are unknown** and do not constrain this feature; they belong to the hardware phase. R12's 90-day default was set before the retention rule is known and is revisited when it is.
- **Hardware tracker timeline.** Kuumbai supplies the expected date and bus coverage of installed trackers; Phase 2's phone streaming proceeds only if trackers are more than a stated number of months away or will not cover every pilot bus.
- **Kuumbai communicates the change to drivers** before Phase 1 rollout; the guide promise changes.

---

## Outstanding Questions

**Deferred to Planning**

- Starting values for the custody-tap distance threshold and the vicinity radius, accounting for GPS accuracy, geocoding error and the tap-after-driving-off habit.
- Ping interval, accuracy mode and staleness threshold for Phase 2.
- Whether exceptions ride the existing alerts feed or a dedicated list on the run view.
- How a nudge is delivered when the driver is on another screen of the app.
- Where the location explanation lives: at first Start Run, or at sign-in.

---

## Sources

- `backend/app/dao/run_dao.py:772`, `:821`, `:1062` — the bus position is stamped with planned coordinates at Start Run and each Arrive; `:939`, `:1006` clear it at End Run. The natural attachment point for action fixes.
- `backend/app/dao/fleet_dao.py:1046` — comment documenting today's checkpoint semantics.
- `frontend/src/features/admin/FleetMapPage.tsx` — reads the current position; `frontend/src/lib/queries.ts:9-12` — staff poll 15 s, parents 5 s, and the note that the fleet map intentionally has no refresh interval.
- `frontend/src/features/parent/ParentTrackPage.tsx` — the parent dot reads the same bus position.
- `frontend/src/features/driver/DriverBoardingPage.tsx` — Absent, hand-over and outcome model on the driver side.
- `docs/user-manual/driver-guide.md:54` — "The app does not use your phone's GPS"; `:62` — Absent available without arriving at the stop; `:97` — off-route hand-over; `:101` — unaccounted children on office close.
- `docs/brainstorms/2026-07-28-status-lifecycle-consistency-requirements.md:238` — the "there is no GPS" framing this document supersedes.
- `docs/brainstorms/2026-08-21-multi-tenant-schools-requirements.md` — roles vocabulary and AE7, fleet map scoping per school.
- `docs/brainstorms/2026-07-06-ops-refinement-requirements.md` — parent Cancel-a-Ride, a corroboration source for R16.
- `backend/app/services/geo_service.py` — great-circle distance helper already present.
- `backend/app/api/runs_live.py:224` — a dormant `POST /driver/position` endpoint, unused by the frontend, already writes the bus position with the assigned-driver check but still fans out the legacy proximity "Bus approaching" push via `push_service.notify_bus_position`, and stores no source, time or trail. Retired or gutted before pings ship (R24).
- `docs/live-app-reference/02-admin-dashboard-fleetmap.md`, `docs/live-app-reference/06-driver-pages.md` — origin of the checkpoint model and the no-refresh quirk.
- Web platform: Geolocation API and Screen Wake Lock API — foreground-only constraint on browser tracking.
