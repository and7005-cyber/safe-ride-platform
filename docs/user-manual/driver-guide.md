# SafeRide Driver Guide

This guide is for bus drivers. It explains how to sign in, run your morning and afternoon trips, record boardings and drop-offs, and report problems — all from your phone.

## Before you start

- You need a **smartphone with an internet connection** (mobile data or Wi-Fi). SafeRide runs in your phone's web browser — there is no app to install.
- You need your **driver PIN**. The school office assigns it when they create your account. If you don't have a PIN, or you forget it, ask the office — you cannot set or reset it yourself.
- The office must have **assigned you a bus** and given that bus at least one **route** with students. If the app says "No bus is assigned to you yet. Contact your administrator." or "No routes assigned to this bus yet.", the office needs to finish your setup.

> **Tip:** Open `https://saferidelive.co.ke` in your browser and add it to your home screen so it is one tap away every morning.

## Signing in

1. Open `https://saferidelive.co.ke/auth`.
2. Tap the **Driver PIN** tab.
3. Type your PIN in the **Driver PIN** field. The PIN is **4 to 6 digits**, numbers only.
4. Tap **Sign In with PIN**.

If you see **"Invalid PIN"**, check the digits and try again. After about **10 wrong attempts in a minute** the app temporarily blocks sign-in ("Too many PIN attempts. Try again shortly.") — wait a minute and retry.

You stay signed in for the whole working day: the session lasts **16 hours** and renews itself every time you use the app. If the app ever says **"Your session has expired. Please sign in again."**, just sign in again with your PIN.

**Signing out:** tap the **sign-out icon in the top-right corner** of any screen. Always sign out if you share the phone with someone else.

## The app at a glance

Four tabs sit at the bottom of every screen:

| Tab | What it is for |
| --- | --- |
| **Home** | Overview: your bus, today's run status, quick **Start Run** / **Continue Run** button |
| **Run** | Choose a route, start the run, tap **Arrive Next Stop** at each stop, end the run |
| **Board** | The student list — mark students boarded, dropped off, or absent |
| **Incident** | Report a breakdown, accident, delay, or other problem |

The Home screen greets you by name and shows your bus and plate number, plus three tiles: **Stops**, **Students**, and **Depart** (the start time of the active run).

## Running a morning trip (pick-up)

**1. Start the run.**
Go to the **Run** tab. Under **"Start a run"**, open the **Choose route** list and pick your morning route, then tap **Start Run**. Morning routes are listed first. A route you already completed today appears greyed out with "· Completed today" — each route can only be run once per day.

As soon as the run starts, parents on your route are notified that the **bus is on the way**.

If a stop you expected is missing, that student was marked absent by the office (or their parent cancelled the ride) before you started — their stop is skipped automatically today.

**2. At every stop, tap "Arrive Next Stop".**
The Run tab shows your list of stops in order, with a progress bar. When you reach a stop, tap **Arrive Next Stop**. This is important for two reasons:

- It moves the bus on the live map that parents and the school office watch. **The app does not use your phone's GPS** — the map only moves when you tap.
- It sends a "**Bus approaching**" notification to the parents at the next stop.

**3. Board the students.**
Switch to the **Board** tab (titled **Student Boarding** in the morning). Each student's **Board** button unlocks only after you have tapped **Arrive Next Stop** for their stop.

- When a student gets on, tap **Board** next to their name, then confirm in the **"Board {name}?"** dialog. Their badge turns green ("On bus") and their parent is automatically notified that the child **boarded the bus**.
- If a student does not show up, tap **Absent** and confirm. **The mark covers this trip only** — a child who missed the morning bus may still be riding home, so marking them absent here does not remove them from the afternoon route. The parent and the office are both notified. If the child is out for the whole day, tell the office so they can record it.
- **Absent is available whether or not you have arrived at their stop.** If you know a child is not coming, you do not have to drive to their stop first.
- **If you tap the wrong child,** tap **Undo** on their row. It appears on entries *you* recorded while the run is still open. Their family is told about the correction, so only undo a genuine mistake.
- **There is still no un-board button.** Undo retracts an absence, a drop-off or a hand-over — it does not take a boarded child off the bus. If a boarding is wrong, call the office.

The counters at the top (**Boarded** / **Remaining**) help you check nobody is missing before you drive off. Use the **Search students…** box on long rosters.

**4. Arrive at school and end the run.**
Tap **Arrive Next Stop** at the school gate (marked with a **Gate** badge). Parents of the children on board are notified that the bus **arrived at school**. Then tap **End Run** and confirm.

**A run will not end while any child is unaccounted for.** See [Ending a run](#ending-a-run) below — this is the one part of the app that will refuse you, and it is worth reading before your first trip.

## Running an afternoon trip (drop-off)

The afternoon flow is the reverse of the morning, with one big difference: **when you start an afternoon route, every student on the roster is automatically marked "on bus"**. Your job is to confirm each drop-off.

1. On the **Run** tab, choose the afternoon route and tap **Start Run**. Parents are notified the bus is **on the way home**.
2. Every child on the roster shows as **Expected on bus**. That is the app saying it assumed they got on, not that anyone checked — it turns into a confirmed state only when you record what happened to them.
3. If a student never got on the bus at school, mark them **Absent** on the **Board** tab (titled **Student Drop-off** in the afternoon), so their parent is not expecting a drop-off. As in the morning, the mark covers **this trip only**.
4. At each stop, tap **Arrive Next Stop**, then on the Board tab tap **Drop-off** next to each child who gets off, and confirm. The **Drop-off** button only unlocks once you have arrived at that child's stop. Each confirmed drop-off sends the parent a "**Dropped off**" notification.
5. After the last stop, tap **End Run**.

## Ending a run

**The app will not let you end a run while any child on the roster has nothing recorded against them.** This is deliberate: before, a run could be closed with children still showing as on the bus, and the app then quietly recorded them as safely dropped off. Nobody had checked, and their families were told nothing.

When children are still outstanding, the **Run** tab shows a red panel — **"Still to account for (N)"** — listing them by name. **Tap a name** to jump straight to that child on the Board screen. If you tap **End Run** anyway, the app refuses and tells you who is left.

Every child needs **one** of these:

| What happened | What to tap |
| --- | --- |
| They got off at their own stop | **Drop-off** (morning: **Board**) |
| They left the bus somewhere else — breakdown, closed road, a guardian met you at the roadside | **Off-route** |
| They were never on the bus | **Absent** |

**Off-route hand-over** asks *where and to whom* before it will save. That note goes to the office and to the child's family, along with a message saying the child left the bus away from their usual stop. Use this rather than marking a child absent when they really were on your bus — telling a family their child was never aboard, when they were, is the mistake this button exists to prevent.

Once the red panel is empty, **End Run** closes the run.

> **If you genuinely cannot finish a run** — your phone died, your shift ended, something happened — the office can close it for you. Ring them. They will record the children nobody could account for as **unaccounted**, which is the app saying plainly that it does not know, and the office then phones those families themselves. Do **not** mark children absent just to get the run to close: it tells their parents they were never on the bus.

## Reporting an incident

Use the **Incident** tab for any safety concern or delay — breakdown, accident, a problem with a student, heavy traffic, or anything else.

1. Pick an **Incident Type**: Vehicle Breakdown, Road Accident, Student Issue, Heavy Traffic / Delay, or Other.
2. Describe what happened in the **Description** box (required).
3. Tap **Submit Report**.

The school office sees the report immediately on their Alerts page, and for delays, breakdowns and accidents the parents on your bus are notified too. In a real emergency, follow your school's emergency procedure first — the app report does not replace a phone call.

## Staying connected

- The app needs a **live internet connection** for every action. There is no offline mode: if a tap fails you'll see a red error message — **retry once you have signal again**. Retrying is safe; nothing is counted twice.
- **Keep the app open during the run.** The screen refreshes every few seconds while it is open. Stop the phone from sleeping (or tap the screen periodically) so you can act quickly at each stop.
- The app never uses your phone's GPS and never asks for location permission. The bus's position on the map comes entirely from your **Arrive Next Stop** taps.

## Quick fixes

| Problem | What to do |
| --- | --- |
| "Invalid PIN" | Re-check the digits. Still failing? Ask the office to confirm or change your PIN. |
| "Too many PIN attempts. Try again shortly." | Wait about a minute, then try again. |
| "No bus is assigned to you yet." | The office must assign you a bus. |
| Route shows "· Completed today" | That route already ran today. If it was started by mistake, the office can delete the run. |
| "No students are assigned to this route yet." | The office must assign students to the route before you can start. |
| Board / Drop-off button greyed out | You haven't tapped **Arrive Next Stop** for that student's stop yet. **Absent** and **Off-route** work regardless. |
| Marked absent, dropped off or handed over by mistake | Tap **Undo** on that child's row, while the run is still open. Their family is told about the correction. |
| Boarded the wrong student | Call the office. **Undo** does not un-board — the parent has already been told the child is aboard. |
| "Not everyone is accounted for: …" when ending a run | Those children need a **Drop-off**, an **Off-route** hand-over or an **Absent** mark. See [Ending a run](#ending-a-run). |
| You cannot finish the run at all | Ring the office and ask them to force-close it. Don't mark children absent to get around it. |
| Red error after a tap | Check your signal and tap again. |
| "Your session has expired." | Sign in again with your PIN. |
