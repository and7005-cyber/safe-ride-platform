# SafeRide Driver Guide

This guide is for bus drivers. It explains how to sign in, run your morning and afternoon trips, record boardings and drop-offs, and report problems — all from your phone.

## Before you start

- **Your account is created by your school's office** — there is no driver sign-up in the app. The office sets up your account and assigns your PIN; everything you see in the app (your bus, your routes, your students) belongs to your school and nothing else ever appears.
- You need a **smartphone with an internet connection** (mobile data or Wi-Fi). SafeRide runs in your phone's web browser — there is no app to install.
- Have the phone's **location turned on** for your browser. The app uses it **only while a run is in progress** — see [Your phone's location during a run](#your-phones-location-during-a-run).
- You need your **driver PIN**. The school office assigns it when they create your account. If you don't have a PIN, or you forget it, ask the office — you cannot set or reset it yourself.
- The office must have **assigned you a bus** and given that bus at least one **route** with students. If the app says "No bus is assigned to you yet. Contact your administrator." or "No routes assigned to this bus yet.", the office needs to finish your setup.

> **Tip:** Open `https://saferidelive.co.ke` in your browser and add it to your home screen so it is one tap away every morning.

## Signing in

Nothing about driver sign-in has changed: your PIN identifies you, and your school follows from your account.

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

- It moves the bus on the live map that parents and the school office watch. The tap sends your phone's position with it, so the map shows where the bus actually was when you tapped — see [Your phone's location during a run](#your-phones-location-during-a-run).
- It sends a "**Bus approaching**" notification to the parents at the next stop.
- If you tap Arrive at a later stop while a child at the stop you just passed still has nothing recorded, a prompt card appears — see [Prompts the app may show you](#prompts-the-app-may-show-you).

**3. Board the students.**
Switch to the **Board** tab (titled **Student Boarding** in the morning). Each student's **Board** button unlocks only after you have tapped **Arrive Next Stop** for their stop.

- When a student gets on, tap **Board** next to their name, then confirm in the **"Board {name}?"** dialog. Their badge turns green ("On bus") and their parent is automatically notified that the child **boarded the bus**.
- If a student does not show up, tap **Absent** and confirm. **The mark covers this trip only** — a child who missed the morning bus may still be riding home, so marking them absent here does not remove them from the afternoon route. The parent and the office are both notified. If the child is out for the whole day, tell the office so they can record it.
- **Absent is available whether or not you have arrived at their stop.** If you know a child is not coming, you do not have to drive to their stop first. When you mark a child absent from well away from their stop, the app asks you one question: **"Did a parent or the office tell you {name} isn't coming?"** Answer **Yes — they told me** if a parent or the office told you, and **No — I wasn't at the stop** if you did not reach the stop. The mark stands either way; the answer decides whether the office follows up — see [Prompts the app may show you](#prompts-the-app-may-show-you).
- **If you tap the wrong child,** tap **Undo** on their row. It appears on entries *you* recorded while the run is still open — an absence, a drop-off, a hand-over, **and a boarding you recorded yourself on this run** (until that child is dropped off or handed over). Their family is told about the correction, so only undo a genuine mistake.
- **Undoing a boarding** returns the child to "nothing recorded yet" — it does not say where they are. Their family gets a neutral message ("Correction: boarding withdrawn"), and you record what actually happens at the stop. If the app asked you to confirm a boarding far from the stop, **Undo** on that prompt does the same thing.

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

## Your phone's location during a run

**While a run is in progress, the app uses your phone's location.** It takes one position — where you are, how accurate the reading is, and the time — at each tap: Start Run, Arrive, Board, Drop-off, Absent, Off-route and End Run. That position travels with the tap and becomes the bus's position on the office's map and the parents' Track page.

**Nothing outside a run.** The app asks for nothing before you tap Start Run and nothing after End Run. It does not track you between runs, and it does not record where you are when the app is closed. Who sees the positions, and for how long, is in the [driver briefing](driver-gps-briefing.md), which your transport coordinator goes through with you before this version reaches you.

**The run always starts, and every tap always completes**, whatever your phone says about location. A tap waits a few seconds for a position at most; if none arrives, the tap goes through without one and the office simply sees the planned stop instead of your phone.

### The first time: the explainer

The first time you tap **Start Run** on a phone that has not answered the location question yet, the app shows a short note before your browser asks — **"Share the bus's location during runs"** — with five lines: **What**, **When**, **Who sees it**, **How long** and **Questions**. Tap **Continue** and your browser asks whether to allow location; choose **Allow** (on Android, also **Precise**). Tap **Cancel** and nothing happens — you can start the run again when you are ready. The run starts whatever you answer to the browser.

### Allowing location on your phone

Your browser remembers your answer. If you said no by mistake, or the phone is only sharing a rough location, the app shows a yellow banner at the top of every driver screen with **How to turn it on**. The steps it shows are these:

**Android (Chrome)**

1. Tap the lock icon (or the tune icon) next to the address bar, open **Permissions**, and set **Location** to **Allow**.
2. If the banner says **Turn on precise location**: in Android **Settings**, open **Apps**, **Chrome**, **Permissions**, **Location**, and turn on **"Use precise location"**.
3. Come back to this page; the next stop you arrive at picks it up.

**iPhone (Safari)**

1. Open **Settings**, **Privacy & Security**, **Location Services**, **Safari Websites**, and choose **"While Using the App"**. If the banner says **Turn on precise location**, turn on **Precise Location** on the same screen.
2. In Safari, tap **"aA"** in the address bar, open **Website Settings**, and set **Location** to **Allow**.
3. Come back to this page; the next stop you arrive at picks it up.

**iPhone (added to the home screen)** — the permission is filed under the app's own name: open **Settings**, **Privacy & Security**, **Location Services**, **the SafeRide app**, and choose **"While Using the App"** (and **Precise Location** if asked). Then come back to the page.

### The two banners

| Banner | What it means | What to do |
| --- | --- | --- |
| **Location off** | Your phone refused location. "The office sees checkpoint positions only — each stop you arrive at, not where the bus is. Your taps still work." | Follow **How to turn it on** above when you are safely stopped. The banner disappears on its own at the next good position |
| **Turn on precise location** | "Your phone is sharing an approximate location, about a kilometre wide, which cannot confirm you are at a stop. Your taps still work." | Turn on precise location as above. Until you do, the office cannot tell whether a tap was made at the stop |

Neither banner ever blocks a tap. Do not stop driving the route to fix it — do it at a stop, or after the run.

## Prompts the app may show you

Sometimes a yellow card appears above the page, on whichever driver tab you are on. It is the app noticing something about a tap; **it never undoes or blocks your tap** — the tap has already gone through. Only one card shows at a time; the card's own **✕** dismisses it (tapping elsewhere does not), and a card you have not answered comes back if you reload or switch phones. There are three kinds.

**1. A stop passed with nothing recorded** — after an Arrive. Title: the stop (**"Stop 4: Moi Avenue"**); text: **"{names} have no record. Mark boarded or absent?"** Each child listed has two buttons: **Boarded** (in the afternoon, **Dropped off**) and **Absent**. Tap the right one for each child; the card shrinks as you go and disappears when the last child is recorded. The **✕** dismisses the card, but the stop stays flagged for the office until every child has an outcome. This card **sounds a tone and vibrates**.

**2. A boarding or drop-off far from the stop** — after a Board or Drop-off tapped well away from that child's stop. Text: **"You marked {name} boarded about 1.8 km from their stop. Confirm, or undo?"** Tap **Confirm** if it is right — a guardian brought the child to you further along, say — or **Undo** if you tapped the wrong child. Undo puts the child back to "nothing recorded", tells the family the mark was withdrawn, and lets you record what really happens. This card is **silent** and has no ✕: Confirm or Undo is the answer.

**3. An absent marked away from the stop** — after an Absent tapped well away from that child's stop. Text: **"You marked {name} absent about 1.8 km from their stop. Did a parent or the office tell you {name} isn't coming?"** Two answers:

- **Yes — they told me** — a parent or the office told you the child is not coming. The mark is kept, the family gets the usual absent notice, and that is the end of it.
- **No — I wasn't at the stop** — you did not get to the stop. The mark is kept, and the office is alerted **and the family is asked to call the office** if the child should be on the bus.

Dismissing this card with **✕**, or leaving it unanswered until your next Arrive, counts the same as **No — I wasn't at the stop**. If the mark itself was a mistake, tap **Undo** on the child's row on the Board tab. This card **sounds a tone and vibrates**.

**Where a card asks you to confirm, it is asking you, not accusing you.** A phone's position is not exact, and the office reads your answer next to it. A child who genuinely boarded at a different place, a phoned-in absence marked from the road, a stop reached before the phone caught up — these are all fine, and Confirm or **Yes — they told me** is the right answer.

## Reporting an incident

Use the **Incident** tab for any safety concern or delay — breakdown, accident, a problem with a student, heavy traffic, or anything else.

1. Pick an **Incident Type**: Vehicle Breakdown, Road Accident, Student Issue, Heavy Traffic / Delay, or Other.
2. Describe what happened in the **Description** box (required).
3. Tap **Submit Report**.

The school office sees the report immediately on their Alerts page, and for delays, breakdowns and accidents the parents on your bus are notified too. In a real emergency, follow your school's emergency procedure first — the app report does not replace a phone call.

## Staying connected

- The app needs a **live internet connection** for every action. There is no offline mode and taps do not queue up: if a tap fails you'll see a red error message — **tap it again once you have signal**. Retrying is safe: the app remembers the first attempt, sends exactly the same tap again (with the position from the first attempt, not a new one), and the server never records it twice — no double boarding, no second notification.
- **Keep the app open during the run.** The screen refreshes every few seconds while it is open. Stop the phone from sleeping (or tap the screen periodically) so you can act quickly at each stop.
- **Location is used only during a run.** Between Start Run and End Run, each tap carries your phone's position; outside a run the app asks for nothing. See [Your phone's location during a run](#your-phones-location-during-a-run).

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
| Boarded the wrong student | Tap **Undo** on their row (it shows for a boarding you recorded yourself on this run, until a drop-off or hand-over). Their family is told the boarding mark was withdrawn. After the run has ended, call the office. |
| "Not everyone is accounted for: …" when ending a run | Those children need a **Drop-off**, an **Off-route** hand-over or an **Absent** mark. See [Ending a run](#ending-a-run). |
| You cannot finish the run at all | Ring the office and ask them to force-close it. Don't mark children absent to get around it. |
| Red error after a tap | Check your signal and tap again. The same tap is re-sent; nothing is counted twice. |
| Yellow **Location off** banner | Your phone refused location. Taps still work. Follow **How to turn it on** when stopped — see [Allowing location on your phone](#allowing-location-on-your-phone). |
| Yellow **Turn on precise location** banner | Your phone is sharing a rough location. Turn on precise location in the phone's settings — steps in the banner and in this guide. |
| A yellow card appears above the page | It is a prompt about a tap you made — see [Prompts the app may show you](#prompts-the-app-may-show-you). Your tap already went through; answer the card when you can. |
| "Your session has expired." | Sign in again with your PIN. |
