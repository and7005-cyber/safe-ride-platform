# SafeRide Provider Guide (Kuumbai Kenya)

This guide is for Kuumbai Kenya staff operating the SafeRide platform. A **provider account** exists outside any school: it lists every school with basic health, sets new schools up with their first director, and **steps into** a school to support it. There is no "see everything" mode — inside a school you are scoped exactly like its director, and there is no merged cross-school view of students, buses or runs.

This guide is Kuumbai-internal. Do not share it with schools; they have their own guides.

## Signing in — two steps, always

Provider sign-in is password **plus** an authenticator-app code. A correct password alone never opens a session.

1. Open `https://saferidelive.co.ke/auth`, enter your email and password on the **Email & Password** tab, and click **Sign In**.
2. Enter the **6-digit code** from your authenticator app.

Rules worth knowing:

- The code prompt is valid for **5 minutes** after the password step; take longer and you start again at the password.
- **Five wrong codes** void the attempt — you start again at the password. Wrong-code attempts are also rate-limited.
- Each code works once; wait for the app to show the next one rather than re-entering the last.

If codes stop working entirely, your second factor needs a reset by a **peer provider** — see [Provider accounts](#provider-accounts). This is why the platform always keeps at least two provider accounts.

## Your first sign-in

A new provider account starts with a **temporary password** — handed over by the peer who created your account (valid unused for **72 hours**), or retrieved from SSM for a bootstrap account (no expiry — sign in and replace it immediately) — and no second factor yet. The app walks you through both, **in this order**:

1. **Change the temporary password first.** Until you do, every screen answers "You must change your temporary password first" — only the change-password screen works. Your current (temporary) password is required to set the new one.
2. **Then enrol the second factor.** The app shows a **secret key and an `otpauth://` URI exactly once** — enter the key into your authenticator app by hand (there is no QR image yet) and confirm with the app's current code. Until you confirm, every other page answers "You must enrol your second factor first".

The order is enforced by the server, not just the screens: the temporary-password gate opens nothing but the password change, so the enrolment screens are unreachable until the password is yours. Leaving the enrolment screen and coming back regenerates the secret — an abandoned half-enrolment never leaves a usable key behind.

## The console

After sign-in you land on the school list — the provider's home surface, always one click away from anywhere:

| Page | What it is |
| --- | --- |
| **Schools** | Every school on the platform with its health: setup state, students, buses, drivers, runs today, and the **last staff activity** (your own visits and reads never count as activity). Per-school **Step in**. |
| **Provider accounts** | Kuumbai account lifecycle: create, remove, reset a peer's second factor. |
| **Audit** | The provider-side audit reader — the unmasked record of who did what, everywhere. |

The school list shows **counts only, never a roster**. To see or touch a school's actual data, you step in.

## Creating a school

**Create school** asks for the school's **name** (required), the **gate location**, the **bell times** (`HH:MM`), and its **first director** — an email and a name.

- The school gets a **generated school code** (e.g. `MSB-001`, three letters from the name plus a number). The code is **not editable, ever** — it is the school's stable identity in support requests and role offers. Quote it when talking to the school.
- If the director's email is **new**, an account is created and its **temporary password is shown to you exactly once** — pass it to the director by a channel you trust. It expires unused after **72 hours**, and the director must replace it at first sign-in.
- If the email **already has a SafeRide account** (the owner of an existing school, typically), no account and no password are created: the director role is **offered**, and the person accepts it at their next sign-in. Tell them the school code so they can check the offer is genuine.

After that, the director does the rest in their own console (staff, drivers, buses, students, routes). Step in only if they ask for help.

Nobody can delete a school from the app. A school created by mistake is removed operationally — flag it and leave it untouched.

## Stepping into a school

Step-in is the only way to act inside a school, and it is deliberately ceremonial:

- **A reason is required** (1–500 characters) and is **shown to the school** — write it for their eyes, e.g. "Support ticket #123 — fixing the morning route stops".
- **A fresh authenticator code** is required when your last one is older than **15 minutes** (the dialog asks when needed). The same freshness rule guards account management — see below.
- Inside, you have **director powers in that school only**, under a persistent **support banner** naming the school, your reason and the time left. The school-facing identity is always **"SafeRide"** — never your name, never a customer's.
- A step-in ends when you click **Step out** (on the banner), when you sign out, when you step into a different school (the first step-in is superseded — you are never in two schools at once), and in any case after a **4-hour hard limit**.

**Everything is on the record.** The step-in itself (who, which school, the reason, start and end) and every action you take inside are written to the same audit trail as staff actions, flagged as provider-originated, under your **real name**. The school sees the actions and the "SafeRide" label; your individual identity is readable only provider-side. Assume the school's director will read the reason and the audit trail — because they can see the effects, and you should be able to account for every write.

Etiquette that follows from the mechanics:

- One reason per job. Stepping in "to look around" still writes an audit row — a step-in with no changes records the provider, the school and the time.
- Prefer the smallest fix. You hold director powers, including deletes; the school's own coordinator deliberately does not.
- Step out when done. Don't ride the 4-hour timer.

## Provider accounts

The **Provider accounts** page lists every Kuumbai account with its second-factor state. Create, remove and reset-TOTP all demand a **fresh code** (15-minute rule) on top of your session.

- **Create** — email (must not already have any SafeRide account; providers never piggyback an existing identity) and a name. The **temporary password shows once** (72-hour validity); the new provider then follows [Your first sign-in](#your-first-sign-in). Never create provider accounts for anyone outside Kuumbai.
- **Remove** — ends that person's access **immediately**, open sessions included. **The last provider account cannot be removed** — the platform always keeps at least one, and should always have at least two so a lockout can be recovered by a peer.
- **Reset second factor** — for a peer whose authenticator is lost or broken: clears their enrolment so they enrol afresh at their next sign-in (password step, then straight into enrolment). Their password is untouched.

If a provider forgets their **password** (rather than the code), there is no in-app reset for providers — re-key the account through the deploy bootstrap below.

## The audit reader

The **Audit** page is the provider-side view of the one shared audit trail: staff actions and provider actions together, with **real names and emails unmasked** (school screens showing the same rows mask provider actors as "SafeRide"). Filter by **school** and by **support session** to reconstruct exactly what one step-in did. Step-ins and step-outs appear as their own entries, so the record answers "who was in which school, when, and why" without interpretation.

The audit is retained for the life of the school. There is no export yet — read it in the console.

## Operational bootstrap (before the first deploy)

Provider accounts are never created through the app's sign-up. The first accounts come from the deploy pipeline:

1. Run `scripts/provider-bootstrap.sh <email1> "<Full Name 1>" <email2> "<Full Name 2>"` — **before** `infra/scripts/deploy-backend.sh`. It creates two SSM SecureString parameters:
   - `/saferide/totp-pepper` — created once, then left alone (see the warning below);
   - `/saferide/provider-bootstrap` — the versioned two-account payload the migrate step reads;
   and stores each account's server-generated initial password at `/saferide/provider-initial-password/<email>` for the operator to retrieve out-of-band. The script prints parameter **names only** — no secrets appear on screen or in this repo.
2. Deploy. The migrate step creates (or re-keys) the two provider accounts. Each person signs in with the initial password from SSM and follows [Your first sign-in](#your-first-sign-in).

The deploy **fails fast** if `/saferide/totp-pepper` or `/saferide/provider-bootstrap` is missing — they are never minted silently (see `infra/README.md`).

**Lockout recovery:** re-running `provider-bootstrap.sh` bumps the payload version, and the next deploy **re-keys the bootstrap accounts** with fresh initial passwords — the recovery path when both providers are locked out.

**Never rotate `/saferide/totp-pepper` casually.** Every enrolled second factor is derived from it: rotating it breaks every provider's codes at once, and each account then needs a peer TOTP reset and a fresh enrolment. If every provider is broken at the same time, no one can sign in to do the resetting — recover by adding a **new** email to the bootstrap payload (a fresh, unenrolled account) and deploying, then peer-resetting the others from it. Treat rotation as an incident procedure, not maintenance.
