# Auth, route guard, and admin app shell

_Reverse-engineered from the live www.saferidekenya.com bundle (app.pretty.js in safe-ride/.local/live-reference/). Source of truth for alignment._

## `(guard component, wraps every non-auth route)` — ProtectedRoute (lr, lines 18501-18539)

## Purpose
Route guard wrapping every page except `/auth`, `/reset-password`, and the 404 catch-all. Receives `{ children, allowedRoles }`. Reads `{ user, role, loading }` from the `useAuth()` hook (la).

## CRITICAL behavior quirk — guard only active for admin routes
The very first check is: `allowedRoles?.length === 1 && allowedRoles[0] === "admin"`.
- **If FALSE (i.e. driver/parent routes, `allowedRoles: ["driver"]` or `["parent"]`)**: children are rendered IMMEDIATELY with NO auth check, NO loading state, NO redirect. Driver and parent routes are effectively unguarded at the client routing level (RLS presumably protects data).
- **If TRUE (admin-only routes)**, the full guard runs:
  1. **Loading state**: while `loading` is true, render a full-screen skeleton: `<div className="min-h-screen flex items-center justify-center">` containing `<div className="space-y-4 w-64">` with three Skeleton components (`animate-pulse rounded-md bg-muted`): `h-8 w-full`, `h-4 w-3/4`, `h-4 w-1/2`.
  2. **Not authenticated** (`user` falsy): `<Navigate to="/auth" replace />`.
  3. **Authenticated, role known, role NOT in allowedRoles**: redirect by role (all with `replace`):
     - role === "admin" → `<Navigate to="/" replace />`
     - role === "driver" → `<Navigate to="/driver" replace />`
     - anything else (parent) → `<Navigate to="/parent" replace />`
  4. **Authenticated and (role in allowedRoles OR role is null)**: render children. Note: a logged-in user whose role row is missing or still resolving (role === null) DOES see admin children.

## useAuth hook (la, lines 18447-18489) — consumed by guard, auth page, layouts
State: `user` (null), `session` (null), `role` (null), `loading` (true initially).
On mount:
- Subscribes `we.auth.onAuthStateChange((event, session) => ...)`: sets session and `session?.user ?? null`; if a user exists, `setTimeout(() => fetchRole(user.id), 0)` (deferred to avoid deadlock); otherwise role=null and loading=false.
- Then `we.auth.getSession()`: sets session/user; if no session, loading=false.
- `fetchRole(userId)`: `we.from("user_roles").select("role").eq("user_id", userId).single()`. On error: `console.error("Failed to fetch user role:", error.message)`, role=null. Else `role = data?.role ?? null`. Finally loading=false. Uses an `isMounted` flag (`h`) to skip state updates after unmount; unsubscribes the auth listener on cleanup.
- `signOut()`: `await we.auth.signOut()` then clears user, session, role locally.

## Supabase client (we, lines 18437-18445)
`createClient("https://tculxwgbbpuqphnfsttn.supabase.co", <anon key>, { auth: { storage: localStorage, persistSession: true, autoRefreshToken: true } })`.

### Data sources

supabase auth: `we.auth.onAuthStateChange`, `we.auth.getSession`, `we.auth.signOut`. Table `user_roles`: `select("role").eq("user_id", uid).single()` to resolve the current user's role (single role per user).

### Styling notes

Loading skeleton: `min-h-screen flex items-center justify-center` > `space-y-4 w-64` > 3x Skeleton (`animate-pulse rounded-md bg-muted` with h-8 w-full / h-4 w-3/4 / h-4 w-1/2). Skeleton component name in source: `qt`.

## `/auth` — Auth (Login / Signup / Forgot Password) — TL, lines 21271-21639

## Page wrapper
`<div className="min-h-screen bg-background flex items-center justify-center p-4">` > `<div className="w-full max-w-md space-y-6">`.

## Brand header (centered, `text-center space-y-2`)
- Logo tile: `inline-flex items-center justify-center h-14 w-14 rounded-2xl bg-primary text-primary-foreground` containing lucide **Bus** icon `h-7 w-7`.
- `<h1 className="text-2xl font-bold font-heading text-foreground">SafeRide</h1>`
- `<p className="text-muted-foreground text-sm">School bus management platform</p>`

## State
`isLogin` (default true), `email`, `password`, `fullName`, `role` (default `"parent"`), `showForgot` (initialized from URL: `useSearchParams().get("forgot") === "1"` → opens directly in forgot-password mode), `loading`, `pin` (string).

## Card (shadcn Card)
CardHeader `text-center pb-2`:
- CardTitle (`font-heading`): forgot → "Reset Password"; login → "Welcome back"; signup → "Create account".
- CardDescription: forgot → "Enter your email to receive a reset link"; login → "Sign in to your account"; signup → "Sign up for SafeRide".

## Mode 1 — Forgot password form (when showForgot)
- Field: Label "Email" (htmlFor email) + Input `id=email type=email placeholder="you@example.com" required`, bound to email state.
- Submit Button `type=submit className="w-full" disabled={loading}`: label `"Sending…"` while loading else `"Send Reset Link"`.
- Handler: `we.auth.resetPasswordForEmail(email, { redirectTo: `${window.location.origin}/reset-password` })`. Success toast: title "Reset link sent", description "Check your email for a password reset link." then exits forgot mode (back to sign-in). Error toast (variant destructive): title "Error", description err.message or "Unable to send reset link".
- Below: centered text button "Back to sign in" (`text-primary text-sm font-medium hover:underline`) → showForgot=false.

## Mode 2 — Login (isLogin true, default)
Tabs component, `defaultValue="email"`, TabsList `grid w-full grid-cols-2 mb-4`:
- Tab 1 value="email": label "Email & Password".
- Tab 2 value="pin": className `gap-1.5`, lucide **KeyRound** icon `h-3.5 w-3.5` + " Driver PIN".

### Email tab form (space-y-4)
- Label "Email" + Input id=email type=email placeholder "you@example.com" required.
- Password row: `flex items-center justify-between` with Label "Password" and a text button "Forgot password?" (`text-xs text-primary hover:underline`) → showForgot=true.
- Input id=password type=password placeholder "••••••••" required minLength=6.
- Submit Button w-full disabled={loading}: `"Please wait..."` while loading else `"Sign In"`.
- Handler: `we.auth.signInWithPassword({ email, password })` (throws on error) → `we.auth.getUser()` → if user, fetch `we.from("user_roles").select("role").eq("user_id", user.id).single()` → **post-login redirect by role**: admin → navigate("/"), driver → navigate("/driver"), anything else (incl. parent / missing role) → navigate("/parent"). Error toast destructive: title "Error", description err.message or "Authentication failed".

### Driver PIN tab form (space-y-6)
- Centered block (`space-y-3 text-center`): Label `text-sm` "Enter your 4-digit PIN"; centered InputOTP (`maxLength={4}`) with an InputOTPGroup of 4 InputOTPSlot (indices 0-3); helper `<p className="text-xs text-muted-foreground">Your PIN was provided by your administrator</p>`.
- Submit Button w-full `disabled={loading || pin.length !== 4}`: `"Signing in..."` while loading else `"Sign In with PIN"`.
- Handler (only runs if pin.length === 4): invoke edge function **`driver-pin-login`** with body `{ pin }`. Throw if invoke error; if `data.error` throw `new Error(data.error)`. Then `we.auth.verifyOtp({ token_hash: data.token_hash, type: "magiclink" })` (the edge function returns a magic-link token hash). On success navigate("/driver"). Error toast destructive: title "Invalid PIN", description err.message or "Please check your PIN and try again."

### Login footer
`mt-4 text-center text-sm`: muted span "Don't have an account? " + text button "Sign up" (`text-primary font-medium hover:underline`) → isLogin=false.

## Mode 3 — Signup (isLogin false)
Form (space-y-4), same submit handler N branch:
- Label "Full Name" + Input id=fullName placeholder "John Doe" required.
- Label "Role" + shadcn Select bound to role: options built from config object `uf` — **driver**: label "Driver", icon lucide **Truck** `h-5 w-5` (color token text-accent-foreground); **parent**: label "Parent", icon lucide **Users** `h-5 w-5` (color token text-muted-foreground). Each SelectItem renders `<span className="flex items-center gap-2">{icon}{label}</span>`. Default selected: parent. (No admin option in signup.)
- Label "Email" + Input type=email placeholder "you@example.com" required.
- Label "Password" + Input type=password placeholder "••••••••" required minLength=6.
- Submit Button w-full disabled={loading}: "Please wait..." / "Create Account".
- Handler: `we.auth.signUp({ email, password, options: { data: { full_name: fullName, role }, emailRedirectTo: window.location.origin } })`. Success toast: title "Account created!", description "Please check your email to verify your account." — stays on the page (no navigation). Error toast destructive: title "Error", description err.message or "Authentication failed".
- Footer: "Already have an account? " + "Sign in" button → isLogin=true.

## Loading/empty states
Single shared `loading` boolean disables all submit buttons and swaps their labels. No other loading UI.

### Data sources

supabase auth: `signInWithPassword`, `signUp` (user_metadata: full_name, role; emailRedirectTo origin), `resetPasswordForEmail` (redirectTo origin + /reset-password), `getUser`, `verifyOtp({type:"magiclink"})`. Table `user_roles` (select role by user_id, single) for post-login redirect. Edge function `driver-pin-login` invoked with `{ pin }`, returns `{ token_hash }` or `{ error }`.

### Styling notes

shadcn/ui: Card/CardHeader/CardTitle/CardDescription/CardContent, Tabs/TabsList/TabsTrigger/TabsContent, Label, Input (h-10 rounded-md border-input), Button, Select, InputOTP (4 slots). Headings use `font-heading` font family. Primary-colored rounded-2xl logo tile with Bus icon. Toasts via shadcn useToast (Qt), destructive variant for all errors.

## `/reset-password` — Reset Password — CL, lines 21658-21873 (+ helpers 21640-21656)

## Page wrapper & brand header
Identical wrapper to /auth (`min-h-screen bg-background flex items-center justify-center p-4` > `w-full max-w-md space-y-6`). Brand header: same Bus-icon logo tile + `<h1>SafeRide</h1>` — NO subtitle paragraph here.

## State
`password`, `confirmPassword`, `saving` (bool), `linkState`: `"checking" | "ready" | "missing"` (starts "checking").

## URL/token parsing helper (kL)
Parses BOTH the hash fragment and query string:
- `hasUrlError`: hash or search contains `error` or `error_code`.
- `hasRecoveryHash`: hash has `type=recovery` AND `access_token`.
- `code`: search param `code` (PKCE flow).
- `tokenHash`: search param `token_hash`; `searchType`: search param `type`.
Helper `df()` cleans the URL via `history.replaceState(null, "", "/reset-password")`. Helper `Ex()` removes sessionStorage key `passwordRecoveryInProgress`.

## Link-verification effect (on mount, with cancelled flag + unsubscribe cleanup)
1. If `hasUrlError` → linkState="missing", clean URL, stop.
2. If `tokenHash && searchType === "recovery"` → `we.auth.verifyOtp({ token_hash, type: "recovery" })`; error → missing (also clears sessionStorage flag); success → ready. URL cleaned either way.
3. Else if `code` → `we.auth.exchangeCodeForSession(code)`; on error: `getSession()` — if a session exists AND `sessionStorage.getItem("passwordRecoveryInProgress") === "true"` → ready, else missing; on success → ready.
4. Else (implicit/hash flow): subscribe `onAuthStateChange` — mark ready when a session exists and (event === "PASSWORD_RECOVERY", or `hasRecoveryHash` and event is "SIGNED_IN"/"INITIAL_SESSION"). Simultaneously poll `getSession()` up to 20 times at 250ms intervals; if session exists and (`hasRecoveryHash` or the sessionStorage flag is "true") → ready. If the loop exhausts → missing.

## Card
CardHeader `text-center`: CardTitle `font-heading`: missing → "Reset link invalid", else "Set New Password". CardDescription: checking → "Verifying your reset link…"; missing → "This reset link has expired or was already used."; ready → "Enter your new password below".

### Missing-state content (space-y-4)
- `<p className="text-sm text-muted-foreground text-center">Reset links can only be opened once and expire after a short time. Please request a new link.</p>`
- Button w-full "Request a new reset link" → navigate("/auth?forgot=1") (opens /auth pre-switched to forgot mode).

### Form (checking or ready states; space-y-4)
- Label "New Password" + Input id=password type=password placeholder "••••••••" required minLength=6, `disabled={linkState !== "ready"}`.
- Label "Confirm Password" + Input id=confirmPassword same attrs/disabled.
- Submit Button w-full `disabled={saving || linkState !== "ready"}`; label: saving → "Updating…"; checking → "Verifying…"; else "Update Password".

### Submit handler validation order + toasts (all errors variant destructive)
1. linkState !== "ready" → toast title "Reset link invalid", description "Please request a new password reset link." (return)
2. password !== confirm → toast title "Error", description "Passwords do not match" (return)
3. password.length < 6 → toast title "Error", description "Password must be at least 6 characters" (return)
4. `getSession()`; if no session → throws "This reset link has expired or was already used. Please request a new reset link." (caught → toast "Error" + that message)
5. `we.auth.updateUser({ password })`; throw on error.
6. Success: toast title "Password updated", description "You can now sign in with your new password."; clear sessionStorage flag; `await we.auth.signOut()`; navigate("/auth").
Generic catch: toast "Error" / err.message or "Unable to update password".

### Footer (always shown, `mt-4 text-center`)
Text button → navigate("/auth"): `text-primary text-sm font-medium hover:underline inline-flex items-center gap-1`, lucide **ArrowLeft** icon `h-3 w-3` + " Back to sign in".

### Data sources

supabase auth only: `verifyOtp({type:"recovery"})`, `exchangeCodeForSession`, `getSession` (polled up to 20x/250ms), `onAuthStateChange` (PASSWORD_RECOVERY / SIGNED_IN / INITIAL_SESSION events), `updateUser({password})`, `signOut`. sessionStorage key `passwordRecoveryInProgress` acts as a cross-page recovery flag (set elsewhere, read+cleared here).

### Styling notes

Same auth-page shell styling. Three-state UI driven by linkState; inputs and submit hard-disabled until "ready". shadcn Card/Label/Input/Button, useToast destructive errors.

## `(layout) used by all 11 admin pages` — AdminLayout (Fn, lines 24038-24108) + header

## Composition
`AdminLayout({ children, title, subtitle })` renders:
`<SidebarProvider>` > `<div className="min-h-screen flex w-full">` > [ `<AdminSidebar/>` , `<div className="flex-1 flex flex-col min-w-0">` [ header, main ] ].
Every admin page passes its own `title` (and optional `subtitle`) into this layout.

## Header bar
`<header className="h-14 flex items-center justify-between border-b bg-card px-4">`
**Left** (`flex items-center gap-3`):
- SidebarTrigger: shadcn Button variant=ghost size=icon `h-7 w-7`, lucide **PanelLeft** icon, `<span className="sr-only">Toggle Sidebar</span>`; onClick toggles sidebar (desktop collapse-to-icon / mobile sheet).
- Title block: `<h2 className="text-base font-heading font-semibold">{title}</h2>`; if subtitle: `<p className="text-xs text-muted-foreground">{subtitle}</p>`.
**Right** (`flex items-center gap-2`):
- Notification bell button: `<button className="relative p-2 rounded-lg hover:bg-muted transition-colors">` with lucide **Bell** `h-4 w-4 text-muted-foreground`. **No onClick handler, no badge, no dropdown — purely decorative.**
- Avatar dropdown (shadcn DropdownMenu): trigger `<button className="h-8 w-8 rounded-full bg-primary flex items-center justify-center text-primary-foreground text-xs font-semibold hover:opacity-90 transition-opacity">{initials}</button>`.
  - Initials computed: `user.user_metadata.full_name` → split on spaces, take first letter of each word, join, slice(0,2), uppercase; else first 2 chars of email uppercased; else "??".
  - DropdownMenuContent align="end": (1) DropdownMenuItem `disabled` `text-xs text-muted-foreground` showing `user.email`; (2) DropdownMenuItem `text-destructive` with lucide **LogOut** `h-3.5 w-3.5 mr-2` + " Sign Out" → `await signOut()` then `navigate("/auth")`.

## Main
`<main className="flex-1 p-4 md:p-6 overflow-auto">{children}</main>`.

## Sidebar primitive behavior (shadcn sidebar, lines 22288-22463)
- SidebarProvider: defaultOpen=true; persists state to cookie `sidebar:state` (max-age 604800 = 7 days); CSS vars `--sidebar-width: 16rem`, `--sidebar-width-icon: 3rem` (mobile sheet width `18rem`); keyboard shortcut **Cmd/Ctrl+B** toggles; wraps children in TooltipProvider delayDuration=0.
- Mobile (<768px via useIsMobile matchMedia hook): sidebar renders inside a Sheet, side="left", `w-[--sidebar-width] bg-sidebar p-0 text-sidebar-foreground [&>button]:hidden` (built-in close X hidden) — toggled by the same SidebarTrigger.
- Desktop: fixed inset-y-0 left sidebar, width animates 16rem ↔ 3rem (`collapsible="icon"` mode is used by AdminSidebar), border-r, `bg-sidebar`.
- SidebarMenuButton shows a right-side tooltip with the item title only while collapsed (hidden when expanded or mobile).

### Data sources

useAuth (la) for `user` (email + user_metadata.full_name for initials) and `signOut`. No direct table queries in the layout itself (sidebar has its own — see AdminSidebar entry).

### Styling notes

Header: h-14 border-b bg-card px-4. Content padding p-4 md:p-6. Tokens: bg-sidebar / sidebar-foreground / sidebar-primary / sidebar-accent CSS-variable theme. font-heading for the page title. Avatar is a plain initials circle (no image support).

## `(component) admin sidebar` — AdminSidebar (pF, lines 22811-22925) with nav + alerts badge

## Container
`<Sidebar collapsible="icon">` — collapses to a 3rem icon rail on desktop; `collapsed = useSidebar().state === "collapsed"` hides all text labels.

## SidebarHeader (`p-4`)
`flex items-center gap-3`:
- Logo tile: `flex h-9 w-9 shrink-0 items-center justify-center rounded-lg bg-sidebar-primary` with lucide **Shield** icon `h-5 w-5 text-sidebar-primary-foreground`.
- When NOT collapsed: `<h1 className="text-sm font-bold text-sidebar-accent-foreground font-heading tracking-tight">SafeRide</h1>` + `<p className="text-[10px] text-sidebar-foreground/60 uppercase tracking-widest">Kenya</p>`.

## SidebarContent — single group
SidebarGroupLabel: `text-sidebar-foreground/50 text-[10px] uppercase tracking-widest` → **"Management"**.
SidebarMenu of 11 items, EXACT order / titles / urls / lucide icons:
1. **Dashboard** — `/` — LayoutDashboard
2. **Fleet Map** — `/fleet-map` — MapPin
3. **Buses** — `/buses` — Bus
4. **Routes** — `/routes` — MapPin (yes, same icon as Fleet Map)
5. **Students** — `/students` — Users
6. **Run History** — `/runs` — Clock
7. **Schools** — `/schools` — School
8. **Parent Assignments** — `/parent-assignments` — UserCheck
9. **Parents** — `/parents` — UserCog
10. **Drivers** — `/drivers` — UserPlus
11. **Alerts** — `/alerts` — Bell

Each item: SidebarMenuItem > SidebarMenuButton asChild > custom NavLink (react-router NavLink wrapper accepting activeClassName/pendingClassName): `to={url}`, `end` only when url === "/", base className `hover:bg-sidebar-accent/50`, activeClassName `bg-sidebar-accent text-sidebar-primary font-medium`. Inside: `<Icon className="mr-2 h-4 w-4"/>`; when not collapsed `<span className="flex-1">{title}</span>`.

## Alerts unread badge
On the Alerts item only, when `unreadCount > 0`: shadcn Badge `className="ml-auto h-5 min-w-5 px-1.5 text-[10px] bg-destructive text-destructive-foreground"` showing the raw count.
- Query (react-query, key `["unread-alerts"]`): `we.from("incidents").select("id", { count: "exact", head: true }).eq("acknowledged", false)` → returns count; returns 0 on error; default 0.
- Realtime: `we.channel("incidents-sidebar").on("postgres_changes", { event: "*", schema: "public", table: "incidents" }, () => queryClient.invalidateQueries({ queryKey: ["unread-alerts"] })).subscribe()`; `we.removeChannel(channel)` on unmount. So the badge live-updates on any incident insert/update/delete.

## SidebarFooter (`p-4 space-y-2`)
- When expanded only — hardcoded school card: `<div className="rounded-lg bg-sidebar-accent/50 p-3">` with `<p className="text-[10px] text-sidebar-foreground/50 uppercase tracking-wider">School</p>`, `<p className="text-sm text-sidebar-accent-foreground font-medium truncate">Greenfield Academy</p>`, `<p className="text-[10px] text-sidebar-foreground/40">Beta Programme</p>`. (Static strings, not fetched.)
- Sign Out button (always rendered; label hidden when collapsed): `<button className="flex items-center gap-2 w-full rounded-md px-3 py-2 text-sm text-sidebar-foreground/70 hover:bg-sidebar-accent/30 transition-colors">` with lucide **LogOut** `h-4 w-4` + "Sign Out". onClick: `await signOut()` then `navigate("/auth")`.

### Data sources

Table `incidents`: head count query filtered `acknowledged = false` (react-query key ["unread-alerts"]). Realtime channel `incidents-sidebar` on all postgres_changes for public.incidents → invalidates the count query. useAuth signOut.

### Styling notes

Sidebar theme tokens (bg-sidebar, sidebar-primary, sidebar-accent, sidebar-foreground with /50 /60 /40 opacities). Tiny 10px uppercase tracking-widest labels for branding/group/footer. Active nav = bg-sidebar-accent + text-sidebar-primary + font-medium. Destructive (red) pill badge on Alerts. Collapsed mode shows icons only + tooltips on the right.

## `/ (root app + router)` — App shell / route table (WH, lines 57820-57955) + ErrorBoundary (kR, 9932-9985)

## Provider tree (outer → inner)
`QueryClientProvider` (a single `new QueryClient()` — default options, no custom config) → `TooltipProvider` → **both** toast outlets mounted side by side: shadcn `<Toaster/>` (radix toast, used by all useToast calls in auth/layout) and Sonner `<Toaster/>` (theme from next-themes `useTheme()` default "system", `className="toaster group"` with grouped toastOptions classNames) → `BrowserRouter` → **ErrorBoundary** → `Routes`.

## ErrorBoundary (kR)
Class component; `componentDidCatch` logs `"ErrorBoundary caught:"`. Fallback UI (unless a `fallback` prop is given): `flex flex-col items-center justify-center min-h-[300px] p-8 text-center` — circle `rounded-full bg-destructive/10 p-4 mb-4` with lucide **TriangleAlert** `h-8 w-8 text-destructive`; `<h2 className="text-lg font-semibold text-foreground mb-2">Something went wrong</h2>`; `<p className="text-sm text-muted-foreground mb-4 max-w-md">An unexpected error occurred. Please try again or refresh the page.</p>`; buttons row `flex gap-2`: outline Button with lucide **RefreshCw** `h-4 w-4 mr-2` + "Try Again" (resets boundary state) and default Button "Refresh Page" (`window.location.reload()`).

## Route table (exact order)
Public: `/auth` → TL; `/reset-password` → CL.
Admin (each wrapped `<ProtectedRoute allowedRoles={["admin"]}>`): `/` Dashboard (UD), `/fleet-map` (HD), `/buses` (N3), `/routes` (j3), `/students` (rz), `/runs` (iz), `/schools` (hz), `/parent-assignments` (pz), `/parents` (gz), `/drivers` (_z), `/alerts` (pH).
Driver (wrapped `<ProtectedRoute allowedRoles={["driver"]}>` — note: guard is a no-op for non-admin roles, see ProtectedRoute entry): `/driver` (xH), `/driver/run` (vH), `/driver/boarding` (_H), `/driver/incident` (wH).
Parent (wrapped `<ProtectedRoute allowedRoles={["parent"]}>` — same no-op caveat): `/parent` (EH), `/parent/track` (SH), `/parent/alerts` (kH), `/parent/profile` (PH).
Catch-all: `path="*"` → 404 page (mH).

## Role-based redirect rules summary (combining /auth + ProtectedRoute)
- After password login: role from user_roles → admin→"/", driver→"/driver", else→"/parent".
- After PIN login: always → "/driver".
- Visiting an admin route while logged in with the wrong role: admin→"/", driver→"/driver", else→"/parent" (replace).
- Visiting an admin route logged out: → "/auth" (replace).
- After sign-out (sidebar, header dropdown, or parent profile): → "/auth".

## Driver/parent layout headers
No driver- or parent-specific layout/header components exist in the 21780-24450 range — the only layout there is AdminLayout (Fn). Driver/parent shells live adjacent to their pages (56031+/57017+, out of this scope). The parent profile page (~57760-57819) ends with its own Sign Out outline button (LogOut icon, `text-destructive`, → signOut + navigate "/auth") and a push-notification toggle, referenced here only for the redirect-rule inventory.

### Data sources

None directly at the shell level beyond what ProtectedRoute/useAuth perform (supabase auth session + user_roles role fetch). QueryClient is shared app-wide for all react-query hooks.

### Styling notes

Two toast systems coexist (shadcn radix Toaster + Sonner); the code in this scope only fires shadcn useToast toasts. ErrorBoundary fallback uses destructive token styling. Router is plain BrowserRouter (no basename).

## Shared components/hooks in this section

## Shared hooks & components found in this section (with bundle identifiers)\n\n- **`we`** — supabase client singleton (18437-18445): URL `https://tculxwgbbpuqphnfsttn.supabase.co`, anon key embedded, `auth: { storage: localStorage, persistSession: true, autoRefreshToken: true }`.\n- **`la` = useAuth** (18447-18489): `{ user, session, role, loading, signOut }`; role fetched from `user_roles` table (select role, eq user_id, single), deferred via setTimeout(0) from onAuthStateChange.\n- **`lr` = ProtectedRoute** (18501): guard only enforces when allowedRoles === [\"admin\"].\n- **`qt` = Skeleton** (18491): `animate-pulse rounded-md bg-muted`.\n- **shadcn primitives** in range: `tt` Input (18540), `Be` Label (18562), `ot/yn/wn/xp/ct/k4` Card family (18572-18625), `Ot` Badge (22753, variants default/secondary/destructive/outline), `kw` Separator, Dialog/Sheet family (21926-22287), DropdownMenu family (`ai` DropdownMenu, `ii` Trigger, `ua` Content, `fr` Item, 23932-24036), `s2` Progress (24375), InputOTP slots (`bw/Ew/zc`, ~21200s), `De` Button (defined earlier, used everywhere).\n- **`AL` = useIsMobile** (21876): matchMedia breakpoint 768px.\n- **Sidebar kit** (22288-22722): `Gw` SidebarProvider (cookie `sidebar:state` 7d, Cmd/Ctrl+B shortcut, --sidebar-width 16rem / icon 3rem / mobile 18rem), `Kw` Sidebar, `Zw` SidebarTrigger (PanelLeft ghost icon h-7 w-7), `Gl` useSidebar, `Xw` Header, `Jw` Content, `Yw` Footer, `Qw` Group, `eb` GroupLabel, `tb` GroupContent, `rb` Menu, `nb` MenuItem, `sb` MenuButton (tooltip-when-collapsed), `eF` SidebarRail, `tF` SidebarInset.\n- **`ab` = NavLink wrapper** (22723): react-router NavLink accepting `activeClassName`/`pendingClassName` merged via the isActive/isPending render-prop.\n- **`pF` = AdminSidebar** (22811), **`Fn` = AdminLayout** (24038).\n- **`kR` = ErrorBoundary** (9932) wrapping the whole Routes tree.\n- **Toast plumbing**: `Qt` = useToast (shadcn), `DN` = shadcn Toaster outlet (7641), `PC` = Sonner outlet (3733).\n- **Signup role config `uf`** (21254): driver → Truck icon / \"Driver\", parent → Users icon / \"Parent\".\n\n## Shared react-query data hooks (24110-24288, used by admin pages; queryKeys matter for invalidation)\n- `dn` useBuses → [\"buses\"]: `buses.select(\"*\").order(\"name\")`.\n- `da` useRoutes → [\"routes\"]: `routes.select(\"*, route_stops(*)\").order(\"name\")`, route_stops client-sorted by stop_order.\n- `Ji` useStudents → [\"students\"]: `students.select(\"*\").order(\"name\")`.\n- `oi` useRuns → [\"runs\"]: `runs.select(\"*, buses(name, plate_number), routes(name)\").order(\"date\", desc)`.\n- `Ip` useSchools → [\"schools\"]: `schools.select(\"*\").order(\"name\")`.\n- `Xl` useStudentRoutes(studentId?) → [\"student-routes\", id|\"all\"]: `student_routes.select(\"id, student_id, route_id\")` optional eq student_id.\n- `PD` useParentStudents → [\"parent-students\", id]: `parent_students.select(\"*\")`.\n- `Lp` useMyStudents → [\"my-students\"]: getUser → `parent_students.select(\"student_id\").eq(\"parent_id\", uid)` → `students.select(\"*\").in(\"id\", ids)`.\n- `RD` useIncidentsTodayCount → [\"incidents-today-count\"]: head count of incidents gte today-midnight ISO, refetchInterval 15000ms.\n- `jD` useDashboardStats: derives totalBuses, activeBuses (union of buses.status==\"active\" + today's in-progress runs' bus_ids), delayedBuses (same union for \"delayed\"), totalStudents, studentsOnBus (status==\"on-bus\"), studentsWaiting (hardcoded 0), absentStudents (status==\"absent\"), todayRuns (runs where date === today YYYY-MM-DD), incidentsToday.\n- `Bu` StatCard (24392): uppercase 10-ish px label, text-2xl font-heading value, optional subtitle, 10x10 rounded-xl icon tile with variant tints (default primary/10, success, warning, destructive). `Ax` BusStatusBadge (24432): outline badge text-[10px], active (success tint + pulsing 1.5px dot `animate-pulse-dot`), delayed (warning), offline (destructive), idle (muted fallback); label = capitalized status.

## Agent notes

All findings verified directly against /Users/andreanatali/Documents/GitHub/Safe Ride 1/safe-ride/.local/live-reference/app.pretty.js. Key line anchors: supabase client 18437-18445, useAuth (la) 18447-18489, ProtectedRoute (lr) 18501-18539, role-select config (uf) 21254-21269, Auth page (TL) 21271-21639, reset-link helpers 21640-21656, ResetPassword (CL) 21658-21873, useIsMobile 21874-21885, sidebar primitives 22288-22722, NavLink wrapper 22723-22738, Badge 22739-22764, admin nav items (fF) 22765-22809, AdminSidebar (pF) 22811-22925, AdminLayout (Fn) 24038-24108, data hooks 24110-24288, ErrorBoundary (kR) 9932-9985, root App + route table (WH) 57820-57955. Icon identifications confirmed via $e("IconName") factory definitions at lines 5133-6325. Notable quirks worth preserving (or consciously fixing) in a rebuild: (1) ProtectedRoute only enforces auth when allowedRoles is exactly ["admin"] — driver/parent routes render children unconditionally with no client-side guard; (2) admin routes render children when user exists but role is still null; (3) the header notification bell button has no onClick and no badge — purely decorative; (4) sidebar school card "Greenfield Academy / Beta Programme" is hardcoded; (5) Fleet Map and Routes share the same MapPin icon; (6) both shadcn Toaster and Sonner are mounted at root, but auth/layout code uses the shadcn useToast.