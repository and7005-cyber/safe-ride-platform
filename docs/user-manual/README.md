# SafeRide User Manual

SafeRide is a school transport safety platform for Nairobi private schools. It lets the school office manage buses, drivers, students and routes; lets drivers run their daily trips from a phone; and lets parents follow their child's bus and receive notifications at every step.

This manual is split by role. Each guide is self-contained — share only the relevant guide with each audience. It describes the app from the **December 2026** hardening release on (password recovery by email and by the school office, parent sign-up confirmed by email, the school's driver code and six-digit driver PINs), which keeps the phone-GPS bus tracking live since **22 September 2026**: the bus's position comes from the driver's phone during a run, at each tap and on its own between stops.

| Guide | Audience | What it covers |
| --- | --- | --- |
| [School Staff Guide](admin-guide.md) | School directors and transport coordinators | Staff accounts and roles, buses, drivers and the driver code, students and routes; daily attendance; monitoring live runs; alerts and parent broadcasts; resetting a parent's password and confirming a sign-up |
| [Driver Guide](driver-guide.md) | Bus drivers | Setting up a phone with the school's driver code, signing in with a PIN, starting a run, recording boarding and drop-offs, the phone's location during a run, the prompts, reporting incidents |
| [Driver GPS Briefing](driver-gps-briefing.md) | Bus drivers, delivered by Kuumbai before each driver's first run with location on | One page: what location data is collected, when, who sees it, how long it is kept, whom to ask; allowing location; the screen during a run; the four prompts; sign-off |
| [Parent Guide](parent-guide.md) | Parents and guardians | Creating your account and confirming your email, a forgotten password, connecting schools, following the bus live, notifications and the 90-day alert history, cancelling a ride |
| [Provider Guide](provider-guide.md) | Kuumbai Kenya staff (internal — do not share with schools) | Two-step sign-in and your password, provisioning schools (and their driver code), stepping in to support a school, provider accounts, the audit reader |

Every school on SafeRide is its own walled space: staff, drivers and data belong to one school, and nothing of one school is visible to another. Parents are the one cross-school surface — a parent follows their own children wherever they are enrolled, and nothing else.

## Accessing SafeRide

SafeRide runs in a web browser — there is nothing to install from an app store. On a phone you can add it to your home screen so it behaves like an app.

- **Web address:** `https://saferidelive.co.ke`
- **School staff** (directors and transport coordinators) sign in with an email address and password at `/auth`. Staff accounts are created by the school's director — the first director is set up by SafeRide (Kuumbai Kenya) when the school joins. There is no self-service staff sign-up.
- **Drivers** sign in with their personal six-digit PIN on the **Driver PIN** tab at `/auth`. The school office creates the driver's account, assigns the PIN and gives the school's **driver code**, which each phone needs once: a PIN works only at the school the phone is set up for, and a driver signs in by PIN at one school only.
- **Parents** create their own account (email + password) at `/auth` — **using the same email address the school has on the child's record** — and confirm it from the link SafeRide emails them; their children appear once it is confirmed. The in-app sign-up form is for parents only.
- **A forgotten password:** **Forgot password?** on the sign-in page emails a reset link once SafeRide's email is switched on. Until then, and whenever an email does not arrive, the school office resets a parent's password and confirms a parent's sign-up (Parents page), and a director resets a staff member's (Staff page). Anyone signed in with a password can change it from their own menu or Profile (**Change password**).
- **An error message ending "(Reference: …)"** means something failed on SafeRide's side. Quote the Reference, with the time, when you report it: it finds the exact error.

SafeRide works on any modern browser (Chrome, Safari, Edge, Firefox) on phones, tablets and computers. Drivers and parents will normally use it on a phone; administrators on a computer.

## The daily flow in one paragraph

The office keeps students, routes and any absences up to date. In the morning the driver signs in, starts the morning route, and taps **Arrive Next Stop** at each stop; as students board, their parents are notified, and everyone can watch the run progress live — parents on their Track page, the office on the Fleet Map and Dashboard — with the bus's position coming from the driver's phone, at each tap and on its own between stops while the app is on the driver's screen. The afternoon run works in reverse: students are checked off as they are dropped at their stops. Incidents reported by the driver reach the office's Alerts page and the affected parents immediately.

**One rule runs through the staff, driver and parent guides:** the app only ever says what somebody actually recorded. A run will not finish while any child on the roster has nothing recorded against them — the driver has to say, for each one, that they were dropped at their stop, that they left the bus somewhere else, or that they were never aboard. When a driver genuinely cannot finish a run, the office closes it, and any child nobody could account for is recorded as **unaccounted** rather than quietly marked safe. Those families get a phone call from a person, not a notification.

## Getting help

If something in the app does not match this manual, or you are stuck, contact your school's transport coordinator. Coordinators can escalate platform problems to the SafeRide operations team.
