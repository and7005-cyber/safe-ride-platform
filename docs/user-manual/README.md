# SafeRide User Manual

SafeRide is a school transport safety platform for Nairobi private schools. It lets the school office manage buses, drivers, students and routes; lets drivers run their daily trips from a phone; and lets parents follow their child's bus and receive notifications at every step.

This manual is split by role. Each guide is self-contained — share only the relevant guide with each audience.

| Guide | Audience | What it covers |
| --- | --- | --- |
| [School Staff Guide](admin-guide.md) | School directors and transport coordinators | Staff accounts and roles, buses, drivers, students and routes; daily attendance; monitoring live runs; alerts and parent broadcasts |
| [Driver Guide](driver-guide.md) | Bus drivers | Signing in with a PIN, starting a run, recording boarding and drop-offs, the phone's location during a run, the prompts, reporting incidents |
| [Driver GPS Briefing](driver-gps-briefing.md) | Bus drivers, delivered by Kuumbai before the GPS release reaches them | One page: what location data is collected, when, who sees it, how long it is kept, whom to ask; allowing location; the three prompts; sign-off |
| [Parent Guide](parent-guide.md) | Parents and guardians | Creating your account, connecting schools, following the bus live, notifications, cancelling a ride |
| [Provider Guide](provider-guide.md) | Kuumbai Kenya staff (internal — do not share with schools) | Two-step sign-in, provisioning schools, stepping in to support a school, provider accounts, the audit reader |

Every school on SafeRide is its own walled space: staff, drivers and data belong to one school, and nothing of one school is visible to another. Parents are the one cross-school surface — a parent follows their own children wherever they are enrolled, and nothing else.

## Accessing SafeRide

SafeRide runs in a web browser — there is nothing to install from an app store. On a phone you can add it to your home screen so it behaves like an app.

- **Web address:** `https://saferidelive.co.ke`
- **School staff** (directors and transport coordinators) sign in with an email address and password at `/auth`. Staff accounts are created by the school's director — the first director is set up by SafeRide (Kuumbai Kenya) when the school joins. There is no self-service staff sign-up.
- **Drivers** sign in with their personal PIN on the **Driver PIN** tab at `/auth`. The school office creates the driver's account and assigns the PIN.
- **Parents** create their own account (email + password) at `/auth` — **using the same email address the school has on the child's record**. The in-app sign-up form is for parents only.

SafeRide works on any modern browser (Chrome, Safari, Edge, Firefox) on phones, tablets and computers. Drivers and parents will normally use it on a phone; administrators on a computer.

## The daily flow in one paragraph

The office keeps students, routes and any absences up to date. In the morning the driver signs in, starts the morning route, and taps **Arrive Next Stop** at each stop; as students board, their parents are notified, and everyone can watch the run progress live — parents on their Track page, the office on the Fleet Map and Dashboard. The afternoon run works in reverse: students are checked off as they are dropped at their stops. Incidents reported by the driver reach the office's Alerts page and the affected parents immediately.

**One rule runs through the staff, driver and parent guides:** the app only ever says what somebody actually recorded. A run will not finish while any child on the roster has nothing recorded against them — the driver has to say, for each one, that they were dropped at their stop, that they left the bus somewhere else, or that they were never aboard. When a driver genuinely cannot finish a run, the office closes it, and any child nobody could account for is recorded as **unaccounted** rather than quietly marked safe. Those families get a phone call from a person, not a notification.

## Getting help

If something in the app does not match this manual, or you are stuck, contact your school's transport coordinator. Coordinators can escalate platform problems to the SafeRide operations team.
