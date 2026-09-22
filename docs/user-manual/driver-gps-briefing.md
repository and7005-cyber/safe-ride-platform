# SafeRide Driver Briefing — Location During Runs

**Read this with every driver before the GPS release is switched on.** This page is delivered to drivers by Kuumbai Kenya, which manages them, and confirmed by Kuumbai with the school informed. It is a go/no-go gate on the Release 3 deploy: the release does not go live for a school until the sign-off block at the bottom is complete for that school's drivers. It is also the notice to drivers, under Kenya's Data Protection Act, of what the app collects about them and why.

It takes about ten minutes. The driver should have their phone with them.

---

## What is changing

Until now the app did not use your phone's location; the bus moved on the map only when you tapped **Arrive Next Stop**. From this release, **while a run is in progress, each tap also sends your phone's position**, so the office and the parents see where the bus actually was when you tapped. Nothing else about running a trip changes: the same taps, the same order, the same rules for ending a run.

**With live tracking** (the release that follows), the app also sends the bus's position **on its own, about every 10 seconds, while the run is on and the app is on your screen** — never with the screen off or the app in the background — so the office and the parents see the bus move between stops, and it **keeps your screen awake** for the run. Still nothing outside a run.

## The notice

This is the text the app shows you before your phone asks for permission the first time, with the live-tracking lines added for this briefing.

**Share the bus's location during runs**

**What** — Your phone's position — where, how accurate, and when — taken at each tap: Start Run, Arrive, Board, Drop-off, Absent, Off-route and End Run — and, with live tracking, the same reading between taps (see **When**).

**When** — Only while a run is in progress. Nothing is asked for before Start Run or after End Run. The app does not track you between runs, at home, or when it is closed. With live tracking, also about every 10 seconds on its own while the run is on and the app is on your screen — never with the screen off, never with the app in the background.

**Who sees it** — Your school's transport office and the provider (Kuumbai Kenya), as the bus's position on their map and in the run's report. Parents see where the bus is, never your phone's details — not its accuracy, not its device, not the app's checks on your taps.

**How long** — Positions stay with the run's record for the school's retention period — 90 days unless the school sets otherwise — then they are deleted. If you leave, your past runs keep their positions for the same period and no longer; the run stops naming you.

**Questions** — Ask your transport coordinator. The coordinator can escalate to Kuumbai.

What the position is used for: showing the bus on the map, and checking whether a tap was made near the child's stop. It is not used to measure your speed, judge your route, or track you outside a run.

## Allowing location on your phone

The first time you tap **Start Run** on this phone, the app shows the notice above; tap **Continue** and your browser asks whether to allow location. Choose **Allow** — and on Android, **Precise**. **The run starts whatever you answer**, and every tap always goes through, with or without a position.

If you said no by mistake, a yellow **Location off** banner appears at the top of the driver screens with **How to turn it on**. Do this at a stop, not while driving:

**Android (Chrome)**

1. Tap the lock icon (or the tune icon) next to the address bar, open **Permissions**, and set **Location** to **Allow**.
2. If the banner says **Turn on precise location**: in Android **Settings**, open **Apps**, **Chrome**, **Permissions**, **Location**, and turn on **"Use precise location"**.
3. Come back to the page; the next stop you arrive at picks it up.

**iPhone (Safari)**

1. Open **Settings**, **Privacy & Security**, **Location Services**, **Safari Websites**, and choose **"While Using the App"**. If asked, turn on **Precise Location**.
2. In Safari, tap **"aA"** in the address bar, open **Website Settings**, and set **Location** to **Allow**.
3. Come back to the page; the next stop you arrive at picks it up.

**iPhone, app added to the home screen** — the permission is under the app's own name: **Settings**, **Privacy & Security**, **Location Services**, **the SafeRide app**, **"While Using the App"**.

## Your screen during a run

With live tracking the app **keeps your screen awake** while a run is in progress, because a phone that locks itself stops sending until you unlock it and come back to the app. If your phone will not allow it, the Run tab shows **"Keep your screen on during the run — this phone will not keep it awake by itself."** — set a longer screen timeout for the run. With the screen off nothing is sent; the office sees **"last seen"** beside the bus until you return to the app or tap.

**Battery:** a screen that stays on for a whole run, plus the sending, uses battery. **Keep the phone on its charger on the bus.**

If the Run tab says **"Live tracking is paused: this run was started from another sign-in."**, the run was started from another phone with your PIN; any tap from this phone moves it here. Do not run one PIN on two phones at once.

## Prompts you may see

Sometimes a yellow card appears above the page. **Your tap has already gone through**; the card is a question, not a block, and only its own **✕** closes it.

| The card | What it asks | Your answers |
| --- | --- | --- |
| **A stop passed with nothing recorded** (tone and vibration) | *"{names} have no record. Mark boarded or absent?"* — you tapped Arrive at a later stop while a child at the previous stop had nothing recorded; with live tracking, also as soon as the bus drives away from that stop | Per child: **Boarded** (afternoon: **Dropped off**) or **Absent**. The card shrinks as you go |
| **A boarding or drop-off far from the stop** (silent) | *"You marked {name} boarded about 1.8 km from their stop. Confirm, or undo?"* | **Confirm** if it is right; **Undo** if you tapped the wrong child — the family is told the mark was withdrawn |
| **An absent marked away from the stop** (tone and vibration) | *"You marked {name} absent about 1.8 km from their stop. Did a parent or the office tell you {name} isn't coming?"* | **Yes — they told me** if a parent or the office told you; **No — I wasn't at the stop** if you did not reach the stop. Dismissing the card counts as no, and the family is asked to call the office |
| **Arrive at a stop?** (silent; live tracking only) | *"Arrive at {stop}?" — "Your phone puts the bus at stop {n} and no arrival is recorded yet."* — the bus is at a stop you have not tapped Arrive for | **Arrive** if you are there — it records the arrival, like Arrive Next Stop; **✕** if you are not. It records nothing by itself |

Answering honestly is all that is asked. A child who boarded somewhere else, a phoned-in absence marked from the road, a stop reached before the phone caught up — all normal, and **Confirm** or **Yes — they told me** is the right answer.

## Two things that do not change

- **A tap always completes.** No prompt, no banner and no missing position ever stops a boarding, a drop-off, an absent mark or an Arrive.
- **Retrying is safe.** If a tap shows a red error, tap it again once you have signal. The app sends the same tap again and the server never counts it twice.

## Checklist for the person delivering this briefing

- [ ] The driver has read the notice (What / When / Who sees it / How long / Questions) and had the chance to ask questions.
- [ ] The driver knows location is used **only between Start Run and End Run**.
- [ ] The driver has allowed location on their own phone (precise, on Android), or knows where the **How to turn it on** steps are.
- [ ] The driver has seen the prompts and knows what each answer means — and that dismissing the absent prompt counts as "not at the stop".
- [ ] With live tracking: the driver knows the app sends the bus's position about every 10 seconds while the run is on and the app is on screen — never with the screen off or the app in the background — and that the app keeps the screen awake for the run.
- [ ] The driver keeps the phone on its charger on the bus.
- [ ] The driver knows a tap always completes and that retrying is safe.
- [ ] The driver knows whom to ask: the school's transport coordinator.

## Sign-off

Complete one block per school before that school's Release 3 deploy. Do not enter drivers' personal details beyond their name.

| | Name | Date | Signature |
| --- | --- | --- | --- |
| **Delivered by Kuumbai** (person who gave the briefing) | ______________________ | ____ / ____ / ________ | ______________________ |
| **Confirmed by Kuumbai** (operations lead) | ______________________ | ____ / ____ / ________ | ______________________ |
| **School informed** (director or transport coordinator) | ______________________ | ____ / ____ / ________ | ______________________ |

Drivers briefed (names): ____________________________________________________________________________

This briefing must be delivered and confirmed **before the Release 3 deploy** for the school. Attach the completed block to the release's validation record under `docs/validation/`.
