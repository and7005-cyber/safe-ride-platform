# SafeRide Administrator Guide

This guide is for school transport coordinators and office staff. As an administrator you set up the fleet (schools, buses, drivers, routes, students), keep daily attendance up to date, monitor live runs, and handle alerts from drivers and parents.

## Your account

Administrator accounts are **provisioned by the SafeRide operations team** — there is no self-service admin sign-up (the in-app "Create account" form only offers Parent and Driver roles). You will receive your email and initial password from the operator.

**To sign in:** open `https://saferidelive.co.ke/auth`, stay on the **Email & Password** tab, enter your email and password, and click **Sign In**. If you forget your password, use **"Forgot password?"** — you'll get a single-use reset link by email. After too many failed attempts sign-in is briefly throttled ("Too many login attempts. Try again shortly."); wait a few minutes.

## The admin console at a glance

The left sidebar lists the ten sections of the console:

| Section | What it is for |
| --- | --- |
| **Dashboard** | Today's overview: active buses, students on board, incidents, live runs |
| **Fleet Map** | Live bus positions on a map, plus the route planner |
| **Buses** | The vehicle register |
| **Routes** | Routes, their stops and stop order; messaging a route's parents |
| **Students** | Student records, parent contacts, route assignment, daily absences |
| **Run History** | Every run, with a detailed report per run |
| **Schools** | School records and gate locations |
| **Parents** | Registered parent accounts |
| **Drivers** | Driver accounts and PINs |
| **Alerts** | Incident feed from drivers and parent cancellations |

The **bell icon** in the top bar shows a red count of unacknowledged alerts and jumps to the Alerts page. The avatar menu (top right) has **Sign Out**. Screens refresh automatically — admin lists roughly every 15 seconds, the Fleet Map every 5.

## First-time setup, in order

Set things up in this order — each step depends on the previous one:

1. **Add your school** (Schools) — routes and runs need a school gate location.
2. **Add drivers** (Drivers) — each gets a 4-digit PIN to sign in with.
3. **Add buses** (Buses) — and assign a driver to each.
4. **Add students** (Students) — with parent contacts and home locations.
5. **Create routes** (Routes or the Fleet Map route planner) — assign a bus and a school, and put students on the route.
6. **Dry-run**: have a driver sign in with their PIN and confirm they see the bus and route on their Home screen.

The sections below cover each screen in detail.

## Schools

Click **Add School** and fill in **Name**, **Address**, **Phone**, and — required — the **Location**: click the map to drop the pin on the **school gate**. The gate becomes the start point of afternoon routes and the end point of morning routes, so place it accurately. Both an address and a map location are required to save.

Deleting a school leaves any routes pointing at it without a destination — reassign those routes first.

## Drivers

Click **Add Driver** and fill in **Full name**, **Email**, **Password** (min 6 characters — drivers can use this on the Email & Password tab, though most will only ever use the PIN), **Phone**, and the **Driver PIN**. Use **Generate** for a random 4-digit PIN.

**The PIN is shown exactly once.** After saving, a **"Driver PIN set"** dialog reveals the PIN with a **Copy** button — share it with the driver right there and then. It is stored encrypted and cannot be viewed again. If a driver forgets their PIN, edit their record and use **Reset PIN** (leaving it blank keeps the current one).

PINs are **unique across drivers** — if you pick one that's taken you'll see "That PIN is already in use by another driver".

The table shows each driver's PIN state (**Set** / **None**) and assigned bus. Deleting a driver removes the account and unassigns their bus.

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
- **School**, **Morning route** and **Afternoon route**. Students are assigned to routes; the bus is whatever bus runs the route.

**Parent emails matter:** the email you record here is what links the parent's app account. When a parent signs up with that exact email, their account attaches to the child automatically (up to two parent accounts per child). If a parent says the app shows no children, the email on the student record is almost always the mismatch.

### Bulk upload

**Bulk Upload** accepts a CSV or Excel file — click **Download template** for the exact format. Columns: `name, grade, parent_name, parent_phone, parent_email, parent2_name, parent_phone2, parent2_email, home_address, home_lat, home_lng, pickup_time, route_name`. Valid rows import; bad rows are reported individually and skipped.

### Daily attendance and absences

Each student row has a toggle for **today's** attendance:

- **Mark absent** (confirm dialog: "The bus won't stop at this student's stop today.") — the stop is skipped on today's runs and the badge **"Absent today"** appears.
- **Mark present** removes an office-recorded absence directly.

Parents can also cancel rides from their own app. These show up as absences with a scope badge — **"Absent (AM)"**, **"Absent (PM)"** or **"Absent today"** — and when you click **Mark present** on one, a **"Parent ride cancellation"** dialog asks whether to **"Remove the cancellation"** (child rides normally) or **"Escalate to full-day absence"** (office takes ownership of the absence). Once the office has marked a child absent, the parent can no longer change it from their side.

Mark absences **before the driver starts the run** — absent students' stops are dropped from the run at start time.

## Routes

A route is an ordered list of stops run by one bus, in one direction: **Morning** routes end at the school gate; **Afternoon** routes start there and run in reverse.

**Add Route** asks for **Name**, **Type** (Morning/Afternoon), **Bus**, and **School**. Students are then attached to the route from their own records (Students page), and each student's home pin becomes a stop.

Each route card shows its stops in order on a small map, plus a mode badge:

- **Auto** — the system orders stops automatically.
- **Manual order** — you've reordered stops with the up/down arrows.
- **Planner** — the route came from the Fleet Map route planner with fixed custom stops.

Per stop you can: **move it up/down**, **edit the pickup time** (this re-sorts the route by time), or **cancel the stop** (removes the student from the route). **Recalculate order & times** rebuilds the ordering automatically; if you see "Order/times not recalculated — check addresses/maps key", some addresses couldn't be located.

### Messaging a route's parents

The megaphone button (**Message parents**) on a route card sends a one-off notice to every parent with a child on that route — it lands in their Alerts feed and as a push notification ("School notice"). Messages are limited to **500 characters** and **12 broadcasts per hour**; the toast confirms "Sent to N parents".

## Fleet Map

The map shows a colored bus marker for every bus currently on an active run — position advances as the driver taps "Arrive Next Stop" at each stop. Click a marker for the bus's name, driver and plate. Buses with no active run don't appear.

### Route planner

The planner (right-hand panel) builds an optimized route from scratch:

1. Add stops — type each **address** and a pickup **time**, or **Upload CSV** (`address, pickup_time, lat, lng`; up to 24 stops).
2. Pick a **Direction** (Morning/Afternoon) and **School**.
3. Click **Get route options** — you get one or more strategies with total distance and duration (in traffic where available), and a drag-to-reorder stop list. Unlocatable addresses are flagged.
4. Click **Save to Routes**, give the route a **name**, a **school** (required) and optionally a **bus**.

The saved route appears on the Routes page with the **Planner** badge.

## Run History

Every run — in progress or finished — is listed here with progress ("X/Y stops · A/B boarded") and status (**In progress / Delayed / Completed**), plus up to three flags that tell you a run needs you:

| Flag | What it means | What to do |
| --- | --- | --- |
| **No taps** | The bus has stopped recording arrivals for 15 minutes or more. Usually a dead or lost phone. Not the same as **Delayed**, which is *your* judgement about the schedule | Call the driver |
| **Needs closing** | The run is still open and its day has passed. It has fallen out of every driver screen, so only you can end it | Force-close it |
| **N to call** | That run left children unaccounted for and their families have not been phoned yet | Open the run and work through the list |

**Click any run** to open its **Run Report**: bus, driver, date, start and end time, stops completed, students boarded (or dropped off, for afternoon runs), the list of absent students with reasons, and — if the run left anyone unaccounted for — the families you owe a call.

### Ending a run the driver cannot finish

A driver cannot end a run while any child on the roster has nothing recorded against them. That is deliberate, and it means a driver whose phone dies mid-route needs you.

Use the **force-close** button (the boxed ✕ in the row's actions, on any run still open). Read the confirmation carefully, because it describes two separate things:

- Children with no recorded outcome are marked **unaccounted for**. That is *not* a claim about where they are — it is the app recording that nobody knows.
- **No parent is notified.** Nothing goes out automatically about an unaccounted child.

After closing, the run report lists those children with a **Mark called** button each. **Phone every family, then mark it.** The count stays on the Run History row until the list is empty, so it survives you being pulled away mid-task — that is the whole reason it is recorded rather than shown once.

> Force-close is the right tool, and marking children absent is not. Absent tells a family their child was never on the bus. If a driver ever asks you to "just mark them absent so I can close the run", close it for them instead.

Two other admin powers live here:

- **Add/Edit Run** records a run manually or corrects its details. You **cannot** set a live run to Completed — finishing a run is a claim that every child is accounted for, and only the driver's End Run or your force-close can make it. A finished run cannot be reopened either; start a new run instead.
- **Delete run** — the recovery tool for a run started in error. A route can only be run **once per day**, so deleting the mistaken run frees the route again. Deleting a run that is still open records nothing about any child. **A finished run from today cannot be deleted** once its children have records: those records are the only evidence of who was on that bus, and removing them would show a whole busload as never having travelled.

## Parents

This page lists every parent contact in the system. **Status** tells you where each stands:

- **Registered** — the parent has created their app account.
- **Awaiting signup** — the email exists on a student record but the parent hasn't signed up yet. Chase these before launch day so parents get notifications from day one.

You can **edit** a registered parent's name, email and phone, or **delete** the account (which removes its student links). Parents cannot edit their own details in their app — corrections come to you. Note you can't create parent accounts here: parents self-register with the email you put on the student record.

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

Lifecycle alerts arrive pre-acknowledged and are **excluded from the incident counters** — there are several per bus per day, and counting them would turn the Dashboard's incidents tile red on a completely ordinary morning. They never reach parents: several of them name other people's children.

New items carry a **"New"** badge and count toward the bell and sidebar badges. Click **Ack** once handled — acknowledged alerts stay in the feed, dimmed. Delete removes an alert permanently.

## Reference

**Statuses**

| Thing | Values |
| --- | --- |
| Bus (derived) | Active, Idle, Delayed, Out of service |
| Bus availability (you set) | In service, Out of service |
| Run | In progress, Delayed, Completed |
| Route/run type | Morning, Afternoon |
| Student (today) | At home, On bus, Expected on bus, At school, Dropped off, Absent today / Absent (AM) / Absent (PM), Unaccounted, Unassigned |
| Parent account | Registered, Awaiting signup |
| Driver PIN | Set, None |

**Validation rules**

- Phone numbers: Kenyan mobile formats (`07…`, `01…`, `+2547…`, `+2541…`), normalized to `+254…`. Schools may also use landlines.
- Passwords: 6–72 characters. Driver PINs: 4 digits, unique per driver, viewable only once at creation/reset.
- Students: Parent 1 name required; at least one phone and one email across the two parent contacts.
- Bus capacity: 1–100. Route planner: max 24 stops. Broadcasts: 500 characters, 12 per hour.
- Schools: address **and** map location both required.

## Troubleshooting — calls you will get

| Report | Likely cause and fix |
| --- | --- |
| Driver: "No bus is assigned to you yet" | Assign the driver to a bus (Buses → edit → Assign Driver). |
| Driver: "No routes assigned to this bus yet" | Create a route with that bus assigned (Routes). |
| Driver: "No students are assigned to this route yet" | Assign students to the route (Students → edit → Morning/Afternoon route). |
| Driver: route shows "Completed today" but needs to run again | Delete the erroneous run in Run History. |
| Driver: boarded/absented the wrong student | Drivers can't undo these (parents were already notified). Correct the record from the office and inform the affected parent. |
| Driver forgot their PIN | Drivers → edit → Reset PIN; the new PIN shows once. |
| Parent: "No children are linked to your account yet" | The parent's sign-up email doesn't match the student record. Fix the email on the student (or have the parent re-register with the recorded email). |
| Parent: wants name/phone/address changed | Edit it on the Students or Parents page — parents can't self-edit. |
| Parent: no notifications | Check they've registered (Parents page shows "Registered"), and that they enabled push in their Profile tab; otherwise alerts still appear in their in-app feed. |
| Bus missing from Fleet Map | The bus only appears during an active run, and only advances as the driver taps "Arrive Next Stop". |
