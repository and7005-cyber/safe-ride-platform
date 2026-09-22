# SafeRide School Staff Guide

This guide is for a school's staff — the **director** and the **transport coordinator**. You set up the fleet (buses, drivers, routes, students), keep daily attendance up to date, monitor live runs, and handle alerts from drivers and parents.

Everything you see and do in the console belongs to **your school**. Another school's buses, students and runs do not appear anywhere — not in lists, not in search, not by ID. If you work at more than one school, see [Switching schools](#your-school-and-switching-schools).

## Your account

Staff accounts are personal — **never share a login**. Every change made in the console is recorded against the person who made it, so the record of "who did what" is only right if everyone uses their own account.

- The **first director** of a school is set up by SafeRide (Kuumbai Kenya) when the school is created.
- Every other staff account is created by a **director** on the [Staff page](#staff-director-only) — there is no self-service staff sign-up (the in-app "Create account" form is for parents only).

**To sign in:** open `https://saferidelive.co.ke/auth`, stay on the **Email & Password** tab, enter your email and password, and click **Sign In**. After too many failed attempts sign-in is briefly throttled ("Too many login attempts. Try again shortly."); wait a few minutes.

**First sign-in with a temporary password:** the director who created your account gives you a one-time temporary password. It is valid for **72 hours** — sign in before it expires (after that, ask for a new one). The app will not show you any page until you **set your own password**; the temporary password then stops working for everyone, including the director who set it.

**Changing your password:** you can change your own password any time from the console (your current password is required). Changing it signs out your other devices.

**Locked out?** There is no self-service e-mail reset for staff. A **director** (or SafeRide support) issues you a fresh temporary password from the Staff page — your old password and any open sessions stop working immediately, and you set a new password at your next sign-in.

## Director and transport coordinator

A school has two staff roles. Both see the whole console and do the daily work; the difference is **deletions and staff management**, and the server enforces it — hiding a button is never the only barrier.

| Action | Coordinator | Director |
| --- | --- | --- |
| View everything in the school | Yes | Yes |
| Add and edit buses, drivers, students, routes, runs | Yes | Yes |
| Mark/clear absences, force-close a stale run | Yes | Yes |
| Delete a run that is **still open** (started in error) | Yes | Yes |
| Draft, apply and restore fleet plans; message parents | Yes | Yes |
| Edit School Settings | Yes | Yes |
| Delete routes, buses, drivers, students | No | Yes |
| Delete alerts, **completed** runs, parent accounts | No | Yes |
| Staff page: create, remove, reset passwords | No | Yes |

The rule of thumb: removals that are part of the daily job (an absence, a student off a route, a run that recorded nothing yet) are **edits** and stay with the coordinator; deleting a completed record destroys history and needs the director.

## Your school, and switching schools

The bottom of the sidebar shows the **active school** — its name and its **school code** (e.g. `MSB-001`, assigned by SafeRide and never editable). Quote the code when you contact support.

Most people belong to one school and never think about this. If you hold a role at **more than one school** (for example an owner who is director at both), the school card becomes a **switcher**: click it and pick a school from **"Your schools"**. Switching replaces everything on screen with the other school's data — nothing from the first school remains — and lands you on its Dashboard. On sign-in with several schools, the app asks you to **choose a school to work in** first.

Each browser tab works in one school at a time, so a second tab left on school A keeps acting on school A even after you switch this one to school B.

## Who did what

Every create, change and removal in the console is recorded with the acting person, and the screens that show a result name them — who applied a fleet plan, who force-closed a run, who acknowledged an alert, who marked an absence.

If a name shows as **"SafeRide"**, the action was taken by SafeRide (Kuumbai) support working inside your school — for example after you asked them to fix something. While they work you'll see a support banner in the console naming the reason; their individual identity is kept on SafeRide's side by design.

## The admin console at a glance

The left sidebar lists the sections of the console (everything scoped to your active school):

| Section | What it is for |
| --- | --- |
| **Dashboard** | Today's overview: active buses, students on board, incidents, live runs |
| **Fleet Map** | Live bus positions on a map, plus the route planner |
| **Buses** | The vehicle register |
| **Routes** | Routes, their stops and stop order; messaging a route's parents |
| **Fleet Plan** | Drafting the school's whole route network from its students; review, apply, restore |
| **Students** | Student records, parent contacts, route assignment, daily absences |
| **Run History** | Every run, with a detailed report per run |
| **Parents** | Registered parent accounts |
| **Drivers** | Driver accounts and PINs |
| **Alerts** | Incident feed from drivers and parent cancellations |
| **Staff** | Staff accounts and role offers — **directors only** |
| **Settings** | The school's own record: name, contact, bell times, gate location; the **Tracking** card for the GPS thresholds |

(The old **Schools** page is gone: the console works inside one school, so its record lives under **Settings** — see [School Settings](#school-settings).)

The **bell icon** in the top bar shows a red count of unacknowledged alerts and jumps to the Alerts page. The avatar menu (top right) has **Sign Out**. Screens refresh automatically — admin lists roughly every 15 seconds, the Fleet Map every 5.

## First-time setup, in order

Set things up in this order — each step depends on the previous one:

1. **Check School Settings** — your school is created by SafeRide, but routes and runs need the **gate location** and bell times to be right. Confirm them (Settings) before anything else.
2. **Add staff** (Staff, director only) — give the coordinator their own account.
3. **Add drivers** (Drivers) — each gets a 4-digit PIN to sign in with.
4. **Add buses** (Buses) — and assign a driver to each.
5. **Add students** (Students) — with parent contacts and home locations.
6. **Create routes** (Routes, the Fleet Plan page, or the Fleet Map route planner) — assign a bus, and put students on the route.
7. **Dry-run**: have a driver sign in with their PIN and confirm they see the bus and route on their Home screen.

The sections below cover each screen in detail.

## School Settings

The **Settings** page holds your school's own record: **Name**, **Address**, **Phone**, the **bell times** (morning and afternoon, `HH:MM`), and — required — the **gate location**: click the map to drop the pin on the **school gate**. The gate is the start point of afternoon routes and the end point of morning routes, so place it accurately. Both an address and a map location are required to save. Either staff role can edit these.

The page also shows your **school code** (e.g. `MSB-001`). It is assigned by SafeRide when the school is created and cannot be changed — use it to identify the school in support requests and role offers.

There is no way to create or delete a school from the console: schools are set up by SafeRide (Kuumbai Kenya), and a school created by mistake is removed by them operationally.

### Tracking

The **Tracking** card holds the five numbers that decide how the driver app's GPS fixes are judged for your school. Every field starts **empty**, which means the system default shown beside it applies; type a number to override it for your school, or click **Use default** to go back. Either staff role can edit them, and **every change is recorded in the audit log with the old and new values**, so a threshold that was loosened is always visible.

| Field | Default | Allowed | What it does |
| --- | --- | --- | --- |
| **Custody distance** | 150 m | 25–2000 m | A Board or Drop-off further than this from the child's stop, after the phone's accuracy is subtracted, asks the driver to confirm (a **Tap far from the stop** exception) |
| **Stop vicinity** | 100 m | 25–2000 m | A phone fix this close to a stop counts as the bus being at it — an Absent marked here needs no follow-up, and the run report reads "seen at stop" |
| **Accuracy cap** | 200 m | 25–2000 m | A fix wider than this is too coarse to check a tap against; it is recorded but neither raises nor clears a question (**Check not verified — fix too coarse**) |
| **Position retention** | 90 days | 7–365 days | How long the bus position trail is kept before it is purged. Exceptions keep their decision record longer, without the coordinates |
| **Ping interval** | 10 s | 5–60 s | How often the driver app sends the bus position between stops while a run is on and the app is on the driver's screen. Shorter is a smoother track and more battery and data on the driver's phone; longer is a dot that moves in bigger hops and a missed stop noticed later. Sending drops to every third interval while the bus is standing still, and stops altogether while the screen is off |

Two related numbers are **system-wide, not per school**, and do not appear on the card: how old a position must be before the maps call it stale (**90 seconds**), and how long a tap waits for a fix before going through without one (**5 seconds**).

Leave the defaults alone for the first weeks and read the run reports before changing anything. Phone accuracy in Nairobi is often worse than the phone claims, which is why the defaults are generous; tightening them makes the driver's prompts more frequent, loosening them switches checks off. The **Ping interval** is the one number here that costs the driver something — the phone's battery and data, with the screen kept on for the whole run — so change it with the drivers, not around them; 10 s is the field-checked default.

## Staff (director only)

The **Staff** page lists the school's directors and coordinators, plus any outstanding role offers. Coordinators do not see this page.

### Creating a staff account

Click **Add Staff** and enter the person's **Email**, **Full name**, and **Role** (Coordinator or Director). What happens next depends on the email:

- **A new email** gets a fresh account with a **temporary password, shown to you exactly once** — copy it and hand it over in person or by a channel you trust. It is valid for **72 hours** and must be replaced at their first sign-in, after which it works for no one (you included).
- **An email that already has a SafeRide account** (say, the owner of another school, or one of your drivers' emails) is **offered the role** instead: nothing changes for them until they sign in and accept. Their password is untouched and no temporary password is issued. The row shows as **Offered** until they accept; you can **Cancel** an unanswered offer. The person sees the offer at sign-in with your school's **name and code** and who offered it — tell them the code through a channel you trust so they can check it before accepting.

Staff creation is rate-limited (about 20 per hour) — enough for onboarding, and a brake on mistakes.

### The staff list

- **Password set on** shows when each person last set their own password. A **"—"** means they are still on a temporary password and have not signed in yet.
- **Reset password** issues a fresh temporary password (shown to you once, same 72-hour rule). Their current password and every open session stop working immediately — use this when someone is locked out.
- **Remove** ends the person's access to this school **at once**, open sessions included. Their account survives (they may work at other schools or be a parent); only the role here is removed.

**A school always keeps at least one director.** Removing the last director — yourself included — is refused until another director exists.

## Drivers

Click **Add Driver** and fill in **Full name**, **Email**, **Password** (min 6 characters — drivers can use this on the Email & Password tab, though most will only ever use the PIN), **Phone**, and the **Driver PIN**. Use **Generate** for a random 4-digit PIN.

**The PIN is shown exactly once.** After saving, a **"Driver PIN set"** dialog reveals the PIN with a **Copy** button — share it with the driver right there and then. It is stored encrypted and cannot be viewed again. If a driver forgets their PIN, edit their record and use **Reset PIN** (leaving it blank keeps the current one).

PINs are **unique across drivers** — if you pick one that's taken you'll see "That PIN is already in use by another driver".

A driver **belongs to your school**: they are created here, sign in by PIN, and only ever see your school's buses, routes and runs. There is no driver self-signup. If you see "That email is already in use", the address already has a SafeRide account — use a different email for the driver.

The table shows each driver's PIN state (**Set** / **None**) and assigned bus. Deleting a driver (director only) removes the account and unassigns their bus. **It does not delete their runs:** past runs stay in Run History, and each run's position trail stays with it until your school's **Position retention** period has passed, then it is purged like any other. The run stops naming the driver.

## Buses

Click **Add Bus** and fill in **Name**, **Plate number**, **Assign Driver** (pick from your driver list — their name and phone sync automatically), **Capacity** (1–100, default 45) and **Availability**.

**You no longer set a bus's status — the app works it out.** A bus shows **Active** while it has a run going, **Delayed** if you marked that run delayed, and **Idle** otherwise. It used to be a field you maintained by hand, which meant a bus halfway through a route still read "Idle" until somebody remembered to change it.

**Availability** is the one thing no amount of run data can tell you: whether the bus is usable at all. Set it to **Out of service** when the bus is in the workshop — that overrides everything else and shows red wherever the bus appears. Set it back to **In service** when it returns.

## Students

This is the busiest screen: it holds each child's details, their **parent contacts**, and their **route assignments**, and it's where you record daily absences.

### Adding a student

Click **Add Student** and fill in:

- **Name** and **Grade**.
- **Parent 1** (name, phone, email) and optionally **Parent 2**. Rules: Parent 1's name is required, and **across the two parents there must be at least one phone and at least one email**. Phone numbers must be valid Kenyan mobiles (`0712 345 678` or `+254712345678`).
- **Home address** — type to search (Google address lookup), or click the map to drop the pin; the address fills in automatically. The pin is where the bus will stop.
- **Pickup time** (e.g. `06:45`).
- **Morning route** and **Afternoon route**. Students are assigned to routes; the bus is whatever bus runs the route. (The student belongs to your active school automatically — there is no school field.)

**Parent emails matter:** the email you record here is what links the parent's app account. When a parent signs up with that exact email, their account attaches to the child automatically (up to two parent accounts per child). If a parent says the app shows no children, the email on the student record is almost always the mismatch.

**If the email belongs to a parent whose children are at another school**, nothing attaches automatically: the parent sees a **pending card** naming your school (never the child) and the link takes effect only when they **accept** it. If they tap **"Not my child"**, the link is cleared, that email is removed from the student record, and your Alerts page gets a **mismatched-email alert** — re-check the address with the family.

### Bulk upload

**Bulk Upload** accepts a CSV or Excel file — click **Download template** for the exact format. Columns: `name, grade, parent_name, parent_phone, parent_email, parent2_name, parent_phone2, parent2_email, home_address, home_lat, home_lng, pickup_time`. Bad rows are reported individually and never block the good ones.

The upload happens in **two steps**, and it is **scoped to your active school**: every imported student is enrolled there, and the fleet-plan drafts and pin map only ever see the school's own students.

**Step 1 — check.** Choosing the file doesn't import anything yet. Every row's address is looked up and sorted into three tiers:

- **Located** — the row has coordinates (supplied in the file, or found with confidence). Nothing to do.
- **Confirm pin** — only a low-confidence lookup found the address. The row shows the proposed location next to the student's name; click **Confirm pin** to accept it. One click per row.
- **Not located** — the address couldn't be found at all. Click **Place on map** and drop the pin by hand — you're always placing a named student's home on a map, never editing a text string. (A row you leave unplaced still imports; the student is flagged "unresolved address" until you pin them from their record or the pin map.)

The check also flags **duplicates**: a row whose name matches a student already enrolled at that school. Choose per row — **Skip** leaves the existing record untouched, **Update** overwrites their parent contacts and address with the row's values (the new address is looked up again, same tiers). Re-uploading an identical file with every duplicate skipped imports nothing — zero new students.

**Step 2 — import.** Once every proposed pin is confirmed and every duplicate decided, click **Import**. The summary reports rows inserted, updated, and skipped, plus parent assignments created.

**The `route_name` column is retired.** Routes come from the fleet plan now, not the import. An old file that still carries the column uploads fine — the student imports, no route is assigned, and the row gets the note "route column ignored — routes come from the fleet plan".

### Daily attendance and absences

Each student row has a toggle for **today's** attendance:

- **Mark absent** (confirm dialog: "The bus won't stop at this student's stop today.") — the stop is skipped on today's runs and the badge **"Absent today"** appears.
- **Mark present** removes an office-recorded absence directly.

Parents can also cancel rides from their own app. These show up as absences with a scope badge — **"Absent (AM)"**, **"Absent (PM)"** or **"Absent today"** — and when you click **Mark present** on one, a **"Parent ride cancellation"** dialog asks whether to **"Remove the cancellation"** (child rides normally) or **"Escalate to full-day absence"** (office takes ownership of the absence). Once the office has marked a child absent, the parent can no longer change it from their side.

Mark absences **before the driver starts the run** — absent students' stops are dropped from the run at start time.

## Routes

A route is an ordered list of stops run by one bus, in one direction: **Morning** routes end at the school gate; **Afternoon** routes start there and run in reverse.

**Add Route** asks for **Name**, **Type** (Morning/Afternoon) and **Bus** — the route belongs to your active school. Students are then attached to the route from their own records (Students page), and each student's home pin becomes a stop.

Each route card shows its stops in order on a small map, plus a mode badge:

- **Auto** — the system orders stops automatically.
- **Manual order** — you've reordered stops with the up/down arrows.
- **Plan order** — the route came from an applied fleet plan (Fleet Plan page) and keeps the stop order you reviewed there, even as students are added or removed.
- **Planner** — the route came from the Fleet Map route planner with fixed custom stops.

Per stop you can: **move it up/down**, **edit the pickup time** (this re-sorts the route by time), or **cancel the stop** (removes the student from the route). **Recalculate order & times** rebuilds the ordering automatically; if you see "Order/times not recalculated — check addresses/maps key", some addresses couldn't be located. On a **Plan order** route, Recalculate asks for confirmation first — it discards the applied plan's reviewed stop order and lets the optimiser reorder the stops.

### Messaging a route's parents

The megaphone button (**Message parents**) on a route card sends a one-off notice to every parent with a child on that route — it lands in their Alerts feed and as a push notification ("School notice"). Messages are limited to **500 characters** and **12 broadcasts per hour**; the toast confirms "Sent to N parents".

## Fleet Map

The map shows a colored bus marker for every bus currently on an active run. Buses with no active run don't appear, and a bus disappears again at End Run or when you force-close its run — nothing is shown outside a run.

**Where the position comes from.** Since the GPS release, every driver tap — Arrive, Board, Drop-off, Absent, Off-route, End Run — carries the driver's phone position, and that position becomes the bus's. Since live pings, the driver app also sends the position **on its own between taps** — every **Ping interval** (10 s unless you set otherwise) while the bus is moving, every third interval while it stands still — for as long as the run is on and the app is on the driver's screen, so the dot moves along the road between stops instead of jumping from one to the next. Nothing is sent while the driver's screen is off or the app is in the background, and nothing outside a run. When a tap carries no fix (location off, no signal, a fix that took too long), the bus is placed at the **planned stop** of the last Arrive instead, as it always was. Start Run is the exception: its fix is recorded but the bus stays at the school gate until the first tap on the road, so a run started from the driver's home never puts their home on the map.

Click a marker for the bus's name, driver and plate, plus three lines about the position:

- **Source** — **Phone GPS (tap)** for a fix from the driver's phone that came with a tap, **Phone GPS (live)** for one the app sent on its own between taps, **Planned stop** for a checkpoint, **Checkpoint (older app)** for a position written by a driver app that predates the GPS release. A fix also shows its claimed accuracy, e.g. `±25 m`.
- **Freshness** — **updated 12 s ago** while the position is recent; **last seen 4 min ago** once it is older than the staleness threshold (90 seconds). A stale bus is drawn faded and never hidden: the office should see that it has gone quiet, not lose it. With live pings, a bus on the move should not go stale while the driver's screen is on — so **last seen** now usually means the driver's screen is off or the app is in the background: the phone stops sending the moment the app leaves the screen and resumes when the driver unlocks and returns to it, or at their next tap. The page header counts them — **"N last seen a while ago"** beside the active-bus count — so one quiet bus among several is not missed.
- **GPS state** — **Phone GPS off since the last tap** (dashed red ring on the marker) when the run's latest tap carried no fix for a device reason; **No GPS for this run — checkpoint positions only** when the run has taps and none of them carried a fix. Both clear on their own at the next tap that does.

Around a fix the map draws a faint **accuracy circle** — the radius the phone claimed, capped at 300 m so a kilometre-wide approximate fix cannot swallow the map. A wide circle means "somewhere around here", not "exactly here".

The **Buses** card beside the map lists the same buses with the same freshness line, so the card and the marker never disagree.

### Route planner

The planner (right-hand panel) builds an optimized route from scratch:

1. Add stops — type each **address** and a pickup **time**, or **Upload CSV** (`address, pickup_time, lat, lng`; up to 24 stops).
2. Pick a **Direction** (Morning/Afternoon). The planner works against your active school's gate.
3. Click **Get route options** — you get one or more strategies with total distance and duration (in traffic where available), and a drag-to-reorder stop list. Unlocatable addresses are flagged.
4. Click **Save to Routes**, give the route a **name** and optionally a **bus** — it lands in your active school.

The saved route appears on the Routes page with the **Planner** badge.

## Fleet Plan

The Fleet Plan page drafts a school's complete route network from its enrolled students — who rides which bus, in what order, morning and afternoon — and lets you review and adjust the proposal before making it live. **Nothing on this page changes the live routes until you click Apply.**

Work through the four steps in order:

1. **Fleet** — tick every bus the plan may use and click **Confirm fleet**. The list shows your school's own buses (a bus belongs to the school that created it and is never shared). A bus running multiple trips per period is confirmed but excluded from drafting, and a bus without a depot is planned without its depot leg — both are called out under the list.
2. **Draft** — click **Generate draft**. The system reads every enrolled student's home pin and the confirmed fleet and proposes mirrored morning/afternoon routes. Only one draft is open per school: drafting again asks first, because the new draft **supersedes** the open one and its review edits are discarded. The optional solver seed reproduces a draft exactly; leave it empty normally.
3. **Review** — the map shows each bus's legs, and the panels beside it show every child's ride time, each bus's seats used per leg, the computed stop times, and — before anything is sent — **the changes families will be told about** with the count of families that will be notified. Children the plan could not seat appear in the red **unplaceable** panel with the reason (no seats, too many stops, no located address); use **Place** to seat one by hand, choosing a bus and a position per leg. You can also reorder stops with the arrows or by dragging, **Move** a child to another bus (moving just one leg splits the pair across buses), **Pin** a child so re-solving keeps them where they are, and change a child's ridership pattern (both ways, morning only, afternoon only, split) — pattern edits live in the draft until you apply. An edit that would break a hard rule (bus capacity, the 24-stop cap) is refused with the rule named, and the draft is unchanged. **Re-solve** re-optimises the whole draft: pinned children and pattern edits are kept; other manual arrangements are discarded.
4. **Apply** — one explicit act that **replaces the school's live routes** and **notifies every affected family** (changed bus, stop moved 5 or more minutes against what they were last told, first plan, lost seat). If students enrolled, moved house, or left after you drafted, the apply dialog lists each change for individual confirmation — or offers **Discard and re-draft** when the list is long. Any still-unplaceable child must be **acknowledged by name, per leg**: they will have no seat from the next run. Applying between runs takes effect the **same day** — "next run" can mean this afternoon — and if a run is active right now, parents' live tracking will not match the bus until that run ends.

The first apply for a school notifies **every** family. That is intended: it is the first time each family is told a stop and a time.

If a draft carries the badge **"Schedule couldn't be fully optimised — times are approximate"**, live travel times couldn't be fetched and distances were estimated instead. The draft is still reviewable and appliable — treat its times as indicative, and expect the same badge language on any route whose road geometry couldn't be fetched after applying.

### After applying

- The applied routes appear on the **Routes** page with the **Plan order** badge: the stop order you reviewed is preserved through student changes. **Recalculate** on such a route asks first, because it discards the plan's reviewed order (see Routes above).
- **Restore** (on the Apply step) returns the school to its routes as they were just before the apply — including any manual edits made while those routes were live. The current plan becomes restorable in its place: **only one step back exists**. Restoring passes the same confirmations as an apply and notifies the affected families.
- **Discard draft** deletes an open draft without touching the live routes.

## Run History

Every run — in progress or finished — is listed here with progress ("X/Y stops · A/B boarded") and status (**In progress / Delayed / Completed**), plus up to four flags that tell you a run needs you:

| Flag | What it means | What to do |
| --- | --- | --- |
| **No taps** | The bus has stopped recording arrivals for 15 minutes or more. Usually a dead or lost phone. Not the same as **Delayed**, which is *your* judgement about the schedule | Call the driver |
| **Needs closing** | The run is still open and its day has passed. It has fallen out of every driver screen, so only you can end it | Force-close it |
| **N to call** | That run left children unaccounted for and their families have not been phoned yet | Open the run and work through the list |
| **N to review** | That run raised stop exceptions nobody in the office has looked at yet (see [Stop exceptions](#stop-exceptions)) | Open the run, read each one, mark it reviewed |

The same flags appear on the Dashboard's **Active Runs** card for runs still open today, so the two screens always agree.

**Click any run** to open its **Run Report**: bus, driver, date, start and end time, stops completed, students boarded (or dropped off, for afternoon runs), the list of absent students with reasons, the run's stop exceptions, and — if the run left anyone unaccounted for — the families you owe a call.

### Stop exceptions

A stop exception is the app noticing that a driver's taps and the bus's position do not agree, or that a stop was passed with nothing recorded. It is recorded on the run, shown to the driver as a prompt where a prompt is useful, and listed for you in the run's report. **A tap always completes** — an exception never blocks a boarding or an absent mark; it flags it. Parents never see exceptions.

Each exception shows its kind, the stop, the children concerned, what the driver's phone reported, the driver's answer to the prompt, and who in the office has reviewed it.

| Kind | What it means | Status |
| --- | --- | --- |
| **Stop passed without outcomes** | The driver tapped Arrive at a later stop while a child at this stop still had no outcome — not boarded, dropped off, absent or handed over. With live pings it is raised earlier: as soon as the phone's positions show the bus has **driven away from the stop** with a child there unrecorded, while the bus is still nearby, and the driver is prompted at once. One row per stop per run either way — a later Arrive finds the row and raises nothing new. The driver is prompted with a tone to record them. An undo reopens it. This one also arrives on the Alerts page | **Open** until every listed child has an outcome, **Resolved** after |
| **Absent marked away from the stop** | The driver marked a child absent from well outside the stop. The prompt asked whether a parent or the office had told them the child was not coming. The family received the standard absent notice at the tap; if the answer was **No — I wasn't at the stop**, or the driver dismissed the prompt or left it unanswered until the next Arrive, the family also received the **Call the office now** notice. This one also arrives on the Alerts page. Call the family if they have not called you | **Open** while the prompt is unanswered; **Attested by driver** after **Yes — they told me** (the row then reads **Absent attested by the driver**, below); **Uncorroborated** after a no, a dismiss or an unanswered prompt; **Retracted** if the driver undid the mark |
| **Absent attested by the driver** | The driver marked a child absent away from the stop and answered that a parent or the office had told them. Kept for history only; no alert and no call-now notice | **Attested by driver** |
| **Tap far from the stop** | A Board or Drop-off was tapped further from the child's stop than the school's **Custody distance** allows, after the phone's accuracy is subtracted. The driver was asked to confirm or undo, silently, and the answer is recorded. Several children tapped far from one stop share one row, each with its own prompt. **Bus seen at stop: yes** means some fix on the run did place the bus within the **Stop vicinity** of that stop — a driver who pulled away before tapping. **No** means nothing placed the bus there | **Open** while any prompt is unanswered, **Confirmed by driver** once any tap was confirmed, **Retracted** only when every tap was undone |
| **Check not verified** | The location check for a tap could not be made, with the reason: **no fix for this action**, **fix too coarse** (wider than the **Accuracy cap**), **stop position unverified** (the stop has no usable coordinates — fix the pin on the route; listed once per run), **fix flagged implausible** (see the next row) or **fix could not be read**. One more reason comes from live pings: **live pings refused from a second sign-in** — the run was started from another sign-in on this driver's account (the same PIN on a second phone, or an assistant who started it), so the positions this phone sent between taps were refused and the driver's Run tab says live tracking is paused. Taps are never refused, and any tap from the phone now in use re-binds the run to it and the live position resumes; if it recurs, check that one PIN is not in use on two phones at once. No prompt, no alert | — |
| **Implausible movement** | A fix on the run moved further or faster than a bus can, or reported an accuracy that cannot be trusted. One row per run lists every flagged fix; a flagged fix neither raises nor clears any other check, and is never "bus seen at stop" | — |

**What the flags on an Implausible movement row mean.** Each flagged fix names the rule it broke, in plain words:

| Flag | Meaning |
| --- | --- |
| **capture time ahead of the server clock** | The phone's clock said the fix was taken in the future |
| **implausible jump between fixes** | More than a kilometre from the previous fix in under 30 seconds |
| **implied speed too high for a bus** | Over 40 m/s (about 144 km/h) since the previous fix |
| **accuracy reported as zero** | A real GPS never claims to be exact |
| **same coordinates as the previous fix** | The exact same point twice — a frozen or replayed feed |
| **fix exactly on a planned stop's pin** | Within two metres of a stop's pin, which a phone on a bus does not do by chance |

Identical **accuracy** on consecutive fixes is deliberately *not* a flag: iPhones report accuracy in fixed steps (5, 10, 35, 65 m …), so two fixes with the same accuracy are the normal case on an iPhone, and flagging them would switch the checks off for every iPhone driver.

**Reading the phone's line.** Under the kind, a row shows what the phone reported, e.g. **"Phone reported within 20 m at 07:14 · 1.8 km from the stop · Bus seen at stop: no"**:

- **Phone reported within N m at HH:MM** — the fix's claimed accuracy and the time the phone took it. It says what the phone claimed, not where the bus was.
- **N m from the stop** (or **Moved N km between fixes** on an implausible-movement row) — the straight-line distance the check used.
- **Bus seen at stop: yes / no** — whether any fix on the whole run placed the bus within the stop's vicinity. Informational only; it never closes or downgrades a row.

Below that, the row's ledger lists each tap and prompt in order — *prompt shown to the driver at 07:15, not yet answered*, *prompt answered: confirmed by the driver*, *prompt closed unanswered*, *call-now notice sent to the family at 07:21* — so you can see what the driver was asked, when, and what they said. Once a run is older than the school's **Position retention**, the coordinates on its exceptions are removed; the kind, distance, answers and review state stay.

Two things to keep apart when you read one:

- **Open / Resolved** (and the other statuses in the table) is about the children and the driver's answers: it says whether the situation still stands, and the app works it out from the run's current records. You cannot change it here.
- **Reviewed** is about you: it means a director or coordinator has looked at the row. Click **Mark reviewed** once you have — the row stays in the report with your name and the time, and stops counting on the **N to review** flag. Reviewing an exception that is still **Open** is normal; it means you have seen it, not that it is settled.

> **A phone's position corroborates; it does not prove.** "Phone reported within 20 m" is what the driver's phone claimed, with the accuracy it claimed. It is good evidence, not a verdict. Read the exception alongside the driver's answer and, when it matters, a phone call.

**The call-now notice and the two office alerts.** Two kinds are loud enough to reach beyond the run report:

- **Stop passed without outcomes** raises an alert on the Alerts page, once per stop per run — a catch-up Arrive or a reopened row does not raise it again.
- **Absent marked away from the stop**, once it is **Uncorroborated**, raises an alert on the Alerts page (once per child per run) and sends the family the **Call the office now** notice: *"{child} was marked absent away from their stop. If {child} should be on the bus, please call the school office now."* That notice goes **at most once per child per run** and is **never retracted** — an undo of the absent mark withdraws the ordinary absent notice with a neutral correction, but a family that was asked to call is not told to stand down by the app. Expect the call, and make it yourself if it does not come. No call-now is sent when the prompt is still open at End Run or a force-close: the closure gate already has that child in hand.

**Neutral corrections.** When a driver undoes a mark, the family receives one message that says only that the mark was withdrawn — **Boarding mark withdrawn**, **Absent mark withdrawn** or **Drop-off mark withdrawn** — and that the driver will record what happens at the stop. It no longer claims the child is on the bus: an undone morning absence returns the child to "nothing recorded", an undone boarding likewise, and an undone afternoon absence restores "Expected on bus". You see the same undo as a **Driver correction** alert.

### Ending a run the driver cannot finish

A driver cannot end a run while any child on the roster has nothing recorded against them. That is deliberate, and it means a driver whose phone dies mid-route needs you.

Use the **force-close** button (the boxed ✕ in the row's actions, on any run still open). Read the confirmation carefully, because it describes two separate things:

- Children with no recorded outcome are marked **unaccounted for**. That is *not* a claim about where they are — it is the app recording that nobody knows.
- **No parent is notified.** Nothing goes out automatically about an unaccounted child.

After closing, the run report lists those children with a **Mark called** button each. **Phone every family, then mark it.** The count stays on the Run History row until the list is empty, so it survives you being pulled away mid-task — that is the whole reason it is recorded rather than shown once.

> Force-close is the right tool, and marking children absent is not. Absent tells a family their child was never on the bus. If a driver ever asks you to "just mark them absent so I can close the run", close it for them instead.

Two other admin powers live here:

- **Add/Edit Run** records a run manually or corrects its details. You **cannot** set a live run to Completed — finishing a run is a claim that every child is accounted for, and only the driver's End Run or your force-close can make it. A finished run cannot be reopened either; start a new run instead.
- **Delete run** — the recovery tool for a run started in error. A route can only be run **once per day**, so deleting the mistaken run frees the route again. Deleting a run that is **still open** records nothing about any child and is available to both staff roles; deleting a **completed** run is director-only. **A finished run from today cannot be deleted by anyone** once its children have records: those records are the only evidence of who was on that bus, and removing them would show a whole busload as never having travelled.

## Parents

This page lists every parent contact in the system. **Status** tells you where each stands:

- **Registered** — the parent has created their app account.
- **Awaiting signup** — the email exists on a student record but the parent hasn't signed up yet. Chase these before launch day so parents get notifications from day one.

You can **edit** a registered parent's name, email and phone, or **delete** the account (director only; deleting removes its student links). Parents cannot edit their own details in their app — corrections come to you. Note you can't create parent accounts here: parents self-register with the email you put on the student record.

A parent marked **Shared** also has a child at another school. You see and manage only **your own students** on that parent, and their account details are **read-only** here — a change or deletion would reach the other school too, so ask SafeRide support if the account itself needs correcting.

## Alerts

The **Driver Alerts** page is the incident feed. Entries arrive from:

- **Drivers** — Vehicle Breakdown, Road Accident, Student Issue, Heavy Traffic / Delay, Notice (also pushed to the affected bus's parents, except student-specific issues which stay office-only).
- **Driver absence markings** — a record when a driver marks a child absent, naming which trip it covers.
- **Parent ride cancellations** — logged so the office always knows.
- **Run lifecycle** — the normal rhythm of the day, and the moments worth reading:

| Alert | What happened |
| --- | --- |
| **Route started** / **Route ended** | A driver began or finished a run |
| **Route cannot close** | A driver tried to end a run and was refused. The alert names the children still outstanding. You get one per situation, not one per tap — but if the driver resolves some and is still stuck, that is a new situation and you get a fresh alert |
| **Route force-closed** | You or a colleague closed a run the driver could not, naming the children now owed a phone call |
| **Left the bus off-route** | A child left the bus away from their own stop, with the driver's note saying where and to whom |
| **Driver correction** | A driver retracted something they had recorded by mistake. The family was told about the correction |
| **Stop passed without outcomes** | The driver moved past a stop while a child there still had no record. One per stop per run. Open the run in Run History to see how it resolved — see [Stop exceptions](#stop-exceptions) |
| **Absent marked away from the stop** | A child was marked absent from well outside their stop with no word from a parent or the office; the family was asked to call the school. See [Stop exceptions](#stop-exceptions) |

Lifecycle alerts arrive pre-acknowledged and are **excluded from the incident counters** — there are several per bus per day, and counting them would turn the Dashboard's incidents tile red on a completely ordinary morning. They never reach parents: several of them name other people's children.

New items carry a **"New"** badge and count toward the bell and sidebar badges. Click **Ack** once handled — acknowledged alerts stay in the feed, dimmed, and record who acknowledged them. Delete removes an alert permanently (director only — alerts are history).

## Reference

**Statuses**

| Thing | Values |
| --- | --- |
| Bus (derived) | Active, Idle, Delayed, Out of service |
| Bus availability (you set) | In service, Out of service |
| Run | In progress, Delayed, Completed |
| Route/run type | Morning, Afternoon |
| Student (today) | At home, On bus, Expected on bus, At school, Dropped off, Absent today / Absent (AM) / Absent (PM), Unaccounted, Unassigned |
| Stop exception | Open, Resolved, Confirmed by driver, Retracted, Attested by driver, Uncorroborated (plus the **Reviewed** stamp) |
| Bus position source (Fleet Map) | Planned stop, Phone GPS (tap), Phone GPS (live), Checkpoint (older app) |
| Parent account | Registered, Awaiting signup (plus the **Shared** mark) |
| Driver PIN | Set, None |
| Staff | Active, Offered |

**Validation rules**

- Phone numbers: Kenyan mobile formats (`07…`, `01…`, `+2547…`, `+2541…`), normalized to `+254…`. Schools may also use landlines.
- Passwords: 6–72 characters. Driver PINs: 4 digits, unique per driver, viewable only once at creation/reset.
- Students: Parent 1 name required; at least one phone and one email across the two parent contacts.
- Bus capacity: 1–100. Route planner: max 24 stops. Broadcasts: 500 characters, 12 per hour.
- School Settings: address **and** map location both required; bell times `HH:MM`.
- Staff temporary passwords: server-generated, shown once, valid unused for 72 hours, replaced at first sign-in.

## Troubleshooting — calls you will get

| Report | Likely cause and fix |
| --- | --- |
| Driver: "No bus is assigned to you yet" | Assign the driver to a bus (Buses → edit → Assign Driver). |
| Driver: "No routes assigned to this bus yet" | Create a route with that bus assigned (Routes). |
| Driver: "No students are assigned to this route yet" | Assign students to the route (Students → edit → Morning/Afternoon route). |
| Driver: route shows "Completed today" but needs to run again | Delete the erroneous run in Run History. |
| Driver: boarded/absented the wrong student | While the run is open the driver can **Undo** their own entry (on the Board tab, or on the far-from-stop prompt); the family gets a neutral "mark withdrawn" message. After the run has closed, correct the record from the office and inform the affected parent. |
| Driver: yellow **Location off** or **Turn on precise location** banner | Their phone refused location, or is sharing a rough one. Taps still work; the map shows planned stops instead of the bus. The banner's **How to turn it on** lists the steps per phone (also in the Driver Guide). |
| Driver: "the app keeps asking me to confirm" | Taps made further than the **Custody distance** from the child's stop, after accuracy. Check the stop pins on the route and the phone's accuracy on the run report before loosening the threshold in Settings → Tracking. |
| Driver forgot their PIN | Drivers → edit → Reset PIN; the new PIN shows once. |
| Staff member locked out of their account | A director resets it: Staff → **Reset password**. The new temporary password shows once and must be replaced at their next sign-in. |
| A director is locked out and there is no other director | Contact SafeRide support — they can step in and issue the reset. |
| New staff member's temporary password "doesn't work" | Temporary passwords expire after **72 hours** unused. Reset it and hand over the new one. |
| Parent: "No children are linked to your account yet" | The parent's sign-up email doesn't match the student record. Fix the email on the student (or have the parent re-register with the recorded email). If the parent's other children are at another school, the link shows on their phone as a **pending card** — ask them to accept it. |
| Alerts: "mismatched email" from a parent decline | A parent answered your link with "Not my child". The email was removed from the student's record — verify the address with the family and re-enter it. |
| Parent: wants name/phone/address changed | Edit it on the Students or Parents page — parents can't self-edit. |
| Parent: no notifications | Check they've registered (Parents page shows "Registered"), and that they enabled push in their Profile tab; otherwise alerts still appear in their in-app feed. |
| Bus missing from Fleet Map | The bus only appears during an active run. Its position moves with the driver's phone — at each tap, and on its own every **Ping interval** while the app is on the driver's screen — or to the planned stop when a tap carried no fix. |
| Fleet Map: bus faded, "last seen N min ago" | No position for longer than the staleness threshold (90 s). With live pings that almost always means the driver's screen is off or the app is in the background — the phone sends nothing then. The run is fine and the dot catches up when the driver returns to the app or taps; call the driver if it persists. |
| Driver: "Live tracking is paused: this run was started from another sign-in" | The run was started from another sign-in of the same PIN. Any tap from the phone the driver is holding moves the run to it and live tracking resumes. The run report shows it as **Check not verified — live pings refused from a second sign-in**. Check that one PIN is not being used on two phones at once. |
| Driver: "the app drains my battery on a run" | Expected: the app keeps the screen awake for the whole run so the phone keeps sending. The phone should be on its charger on the bus. A longer **Ping interval** helps a little; the screen is the main cost. |
| Fleet Map: "No GPS for this run — checkpoint positions only" | None of the run's taps carried a position — location is off on the driver's phone. The run is fine; ask the driver to turn location on (Driver Guide). |
