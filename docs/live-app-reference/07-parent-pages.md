# Parent pages (/parent, /parent/track, /parent/alerts, /parent/profile)

_Reverse-engineered from the live www.saferidekenya.com bundle (app.pretty.js in safe-ride/.local/live-reference/). Source of truth for alignment._

## `/parent` — Parent Home (children status dashboard)

## Route & Access
- Path `/parent`, wrapped in `ProtectedRoute` with `allowedRoles: ["parent"]`. Rendered inside the shared mobile ParentLayout (see sharedComponents) with header title **"SafeRide Parent"**.

## Data
- `useMyStudents()` (queryKey `["my-students"]`) → children of the logged-in parent (see sharedComponents for query).
- `useBuses()` `["buses"]` → all buses.
- `useRoutes()` `["routes"]` → all routes with nested sorted `route_stops`.
- `useRuns()` `["runs"]` → all runs (with joined bus/route names), ordered by date desc.

## Realtime
- Subscribes channel **`parent-students-status`** on mount: `postgres_changes` event `UPDATE` on `public.students` → `invalidateQueries(["my-students"])` and `invalidateQueries(["students"])`. Channel removed on unmount.

## Loading state
- While `my-students` is loading: layout renders `space-y-4` with **2 Skeletons** `h-48 rounded-xl`.

## Layout (loaded), `space-y-5`
### 1. Greeting block
- `h2.font-heading.font-bold.text-lg`: **"Good morning, {firstName} 👋"** — firstName = first word of `students[0].parent_name`, fallback **"Parent"**.
- `p.text-xs.text-muted-foreground`: **"Today's bus status at a glance"**.

### 2. One Card per child (`students.map`)
For each child, derived data: `bus = buses.find(b => b.id === student.bus_id)`; `route = routes.find(r => r.bus_id === student.bus_id)`; `activeRun = runs.find(r => r.bus_id === student.bus_id && r.status !== "completed")`; `completed = activeRun ? activeRun.stops_completed : 0`; `total = activeRun ? activeRun.total_stops : (route?.route_stops.length ?? 0)`; `pct = total>0 ? completed/total*100 : 0`; `stops = route?.route_stops ?? []`.

Status badge config (keyed by `student.status`, fallback to `at-school`):
- `on-bus` → label **"On Bus"**, badge `bg-success/15 text-success border-success/30`, dot `bg-success animate-pulse-dot`
- `at-school` → **"At School"**, `bg-primary/15 text-primary border-primary/30`, dot `bg-primary`
- `dropped-off` → **"Dropped Off"**, `bg-muted text-muted-foreground border-border`, dot `bg-muted-foreground`
- `absent` → **"Absent"**, `bg-warning/15 text-warning border-warning/30`, dot `bg-warning`

Card (`overflow-hidden`, CardContent `p-0`):
- **Header row** (`flex items-center justify-between p-4 pb-3`): left = avatar circle `h-10 w-10 rounded-full bg-primary/10` containing child initials (first letter of every word in name, joined, `text-primary font-bold text-sm font-heading`) + name (`font-heading font-semibold text-sm`) + sub-line `{grade} • {bus.name}` (`text-[10px] text-muted-foreground`). Right = outline Badge `text-[10px]` with status color + a 1.5px round dot span (`mr-1 h-1.5 w-1.5 rounded-full inline-block` + dot class) + status label.
- **Live run panel** — rendered ONLY when `activeRun` exists (`mx-4 mb-3 rounded-lg bg-muted/50 p-3 space-y-3`):
  - Row: MapPin icon (3.5) + route name (`text-xs text-muted-foreground`); right side ONLY when `student.status === "on-bus"`: Clock icon + **"~{N} min"** in `text-xs font-bold text-primary` — N is a MOCK random ETA: `Math.max(3, Math.floor(Math.random()*12)+2)` (3–13, recomputed each render), null otherwise.
  - Progress: label row "Stop progress" (`text-[10px] text-muted-foreground`) vs `{completed}/{total}` (`text-[10px] font-semibold`); bar = `h-2 bg-background rounded-full overflow-hidden` with inner `h-full bg-primary rounded-full transition-all duration-700` width `{pct}%`.
  - **Horizontal stop chips** (`flex items-center gap-1.5 overflow-x-auto pb-1`): for each stop index A — connector before each chip except first (`w-4 h-0.5 rounded-full`, `bg-primary` if previous passed else `bg-border`); chip `h-5 w-5 rounded-full text-[9px] font-bold border-2`: passed (A < completed) `bg-primary border-primary text-primary-foreground` showing CircleCheck `h-3 w-3`; current (A === completed) `border-primary bg-primary/10 text-primary` showing number A+1; upcoming `border-border bg-background text-muted-foreground` with number. If stop.id === student.boarding_stop_id and not yet passed: extra `ring-2 ring-warning ring-offset-1`.
  - If the child's boarding stop exists in stops AND status is `on-bus`: note `text-[10px] text-muted-foreground`: **"🟡 Your stop: {stopName} — ETA {stop.scheduled_time}"** (stop name in `font-medium text-foreground`).
- **Footer row** (`flex items-center justify-between px-4 pb-4`): left = Bus icon (3.5) + "Driver: {bus.driver_name}" (`text-xs text-muted-foreground`); right = Button variant outline size sm `h-7 text-[10px] gap-1` with MapPin `h-3 w-3` + **"Track"** → `navigate("/parent/track")`.

### 3. Quick actions grid (`grid grid-cols-2 gap-3`)
- Button outline `h-auto flex-col gap-1.5 py-4`: TriangleAlert icon `h-5 w-5 text-warning` + label **"Alerts"** (`text-xs font-medium`) → `navigate("/parent/alerts")`.
- Button outline same classes, `asChild` wrapping `<a href="tel:{buses?.[0]?.driver_phone}">`: Phone icon `h-5 w-5 text-primary` + **"Call Driver"**. NOTE: uses the FIRST bus in the global buses list (ordered by name), not necessarily the child's bus — reproduce as-is or treat as known quirk.

## Empty state
- No explicit empty state: with zero children the page shows greeting ("Good morning, Parent 👋") and the quick-actions grid only.

### Data sources

Supabase: `parent_students` (select student_id eq parent_id=auth uid) + `students` (select * in ids) via my-students hook; `buses` select * order name; `routes` select `*, route_stops(*)` order name (stops sorted by stop_order client-side); `runs` select `*, buses(name, plate_number), routes(name)` order date desc. Realtime channel `parent-students-status` (UPDATE on students).

### Styling notes

Mobile-first shadcn UI: Card/CardContent, Badge (outline/secondary), Button, Skeleton. lucide icons: MapPin, Clock, CircleCheck, Bus, TriangleAlert, Phone. Custom tokens: `font-heading`, `text-success`, `text-warning`, `animate-pulse-dot` (pulsing status dot). Tiny type scale: text-[10px]/text-xs throughout. Progress bar animated with `transition-all duration-700`.

## `/parent/track` — Track Bus (live route tracking)

## Route & Access
- Path `/parent/track`, ProtectedRoute `allowedRoles: ["parent"]`, ParentLayout title **"Track Bus"**.

## Data
- `useMyStudents()` `["my-students"]`, `useBuses()` `["buses"]`, `useRoutes()` `["routes"]`, `useRuns()` `["runs"]`, and `useStudentRoutes()` with no arg → queryKey `["student-routes","all"]`, `we.from("student_routes").select("id, student_id, route_id")` (no filter).
- Derived: `myRouteIds = unique route_ids from student_routes rows whose student_id is one of my children`; `myRoutes = routes.filter(r => myRouteIds.includes(r.id))` (both useMemo).

## Realtime
- Channel **`parent-track-live`** with three listeners: (1) `UPDATE` on `public.students` → invalidate `["my-students"]` + `["students"]`; (2) event `*` on `public.runs` → invalidate `["runs"]`; (3) event `*` on `public.student_routes` → invalidate `["student-routes"]`. Removed on unmount.

## Loading state
- **3 Skeletons** `h-32 rounded-xl` in `space-y-4`.

## Empty state (no matching routes)
- Centered column `py-16 text-center space-y-3`: Bus icon `h-10 w-10 text-muted-foreground`, text `text-sm text-muted-foreground`: **"No route assigned to your children yet."**

## Main content — for each route in `myRoutes` (outer `space-y-4`, each route group also `space-y-4`)
Derived per route: `bus = buses.find(b => b.id === route.bus_id)`; `activeRun = runs.find(r => r.route_id === route.id && r.status !== "completed")`; `completedStops = activeRun?.stops_completed ?? 0`; `stops = route.route_stops ?? []`; `kidsOnRoute = my children whose id appears in student_routes rows for this route`.

### Card A — Map (only if stops.length > 0)
- Card `overflow-hidden`, CardContent `p-3` containing `RouteStopsMap` (shared Leaflet component, see sharedComponents) fed `stops`. Static non-interactive OSM map, 160px tall, numbered green circle markers per stop (last stop larger/darker with "●"), dashed polyline connecting stops, fitBounds maxZoom 15.
- Below map, ONLY if `bus.current_lat` is truthy: centered outline Badge `text-[10px] bg-success/10 text-success border-success/20` with pulsing dot span (`bg-success animate-pulse-dot`) + **"{bus.name} is live"**. (Bus position is NOT drawn on the map — only this badge.)

### Card B — Route info
- CardHeader `p-4 pb-2`, CardTitle `text-sm font-heading flex items-center gap-2`: MapPin `h-4 w-4`, route name, then outline Badge `text-[10px] ml-1`: **"🌅 Morning"** if `route.type === "morning"` else **"🌇 Afternoon"**.
- CardContent `p-4 pt-0 space-y-2`:
  - If bus exists, meta line `flex items-center gap-2 text-xs text-muted-foreground flex-wrap`: "Bus: **{bus.name}**" (value `font-medium text-foreground`) • "Plate: **{bus.plate_number}**" (value `font-mono text-foreground`) • "Driver: {bus.driver_name}" (• rendered as separate bullet spans).
  - Children chips: `flex flex-wrap gap-1` of secondary Badges `text-[10px]` with each kid's name.
  - If activeRun: row `flex items-center gap-1 text-xs font-semibold text-primary`: Clock `h-3.5 w-3.5` + **"{completedStops}/{activeRun.total_stops} stops completed"**.

### Card C — Stop Progress vertical timeline (only if stops.length > 0)
- CardHeader `p-4 pb-2` with CardTitle `text-sm font-heading`: **"Stop Progress"**.
- CardContent `p-4 pt-0`, list `space-y-0`; for each stop index S:
  - flags: `passed = S < completedStops`; `current = S === completedStops`; `isLast`; `isMyStop = kidsOnRoute.some(k => k.boarding_stop_id === stop.id)`; `isGate = stop.is_school_gate`.
  - **Privacy-masked label**: `label = (isMyStop || isGate) ? stop.name : stop.name.replace(/^\s*\d+[\s,.-]*/, "").trim() || "Pickup stop"` — other families' stops get any leading house-number prefix stripped; falls back to "Pickup stop".
  - Row `flex gap-3`: left column `flex flex-col items-center`: circle `h-6 w-6 rounded-full text-[10px] font-bold shrink-0 z-10` — passed: `bg-primary text-primary-foreground` with CircleCheck `h-3.5 w-3.5`; current: `bg-primary/20 text-primary border-2 border-primary` with number S+1; future: `bg-muted text-muted-foreground` with number; if isMyStop and not passed add `ring-2 ring-warning ring-offset-1`. Below circle (except last stop) a connector `w-0.5 h-8` (`bg-primary` if passed else `bg-border`).
  - Right column `pt-0.5 pb-4`: label `text-xs font-medium` (+ `text-muted-foreground line-through` when passed); if isMyStop append span `ml-1 text-warning`: **"⭐"**. Status line `text-[10px] text-muted-foreground`: passed → **"✓ Passed"**, current → **"🚌 Bus is here"**, else → **"ETA {stop.scheduled_time}"**.

### Data sources

Supabase: same hooks as /parent plus `student_routes` select `id, student_id, route_id` (all rows, queryKey ["student-routes","all"]). Buses table provides `current_lat` (live flag), `plate_number`, `driver_name`. Realtime channel `parent-track-live` (UPDATE students; * runs; * student_routes). Map tiles from openstreetmap.org via Leaflet (no Supabase).

### Styling notes

shadcn Card/Badge/Skeleton; lucide MapPin/Clock/CircleCheck/Bus. Leaflet map with all interactions disabled (no zoom/drag), divIcon markers: 18px circles hsl(152,55%,45%), final stop 22px hsl(152,55%,28%) with white 2px border + shadow, dashed polyline color hsl(152,55%,28%) weight 3 opacity .6 dashArray '6 4'. Pulsing live badge uses `animate-pulse-dot`. Vertical timeline built with flex columns, no library.

## `/parent/alerts` — Parent Alerts (incident notifications feed)

## Route & Access
- Path `/parent/alerts`, ProtectedRoute `allowedRoles: ["parent"]`, ParentLayout title **"Alerts"**. This is also the deep-link target (`url: "/parent/alerts"`) of the `send-arrival-push` web push sent by the driver app when the bus reaches the school gate.

## Data
- `useMyStudents()` → children; `busIds = Array.from(new Set(children.map(c => c.bus_id).filter(Boolean)))`.
- Query `["parent-alerts", busIds]`, `enabled: !studentsLoading`: if busIds empty return `[]`; else `we.from("incidents").select("id, driver_name, bus_id, bus_name, type, description, created_at").in("bus_id", busIds).order("created_at", { ascending: false })`; throws on error.

## Realtime
- Only when busIds non-empty: channel **`incidents-parent`**, `postgres_changes` event `*` on `public.incidents`; handler reads `payload.new ?? payload.old` and ONLY if its `bus_id` is in busIds → `invalidateQueries(["parent-alerts"])`. Effect deps `[queryClient, busIds.join(",")]`; channel removed on cleanup.

## Incident type label map (fallback = raw type string)
- `breakdown` → "Vehicle Breakdown"; `accident` → "Road Accident"; `student` → "Student Issue"; `traffic` → "Traffic Delay"; `arrival` → "Bus Arrived at School"; `other` → "Notice".

## Layout (`space-y-4`)
- Header row `flex items-center justify-between`: `h2.font-heading.font-bold.text-base` **"Notifications"**; if alerts exist, count Badge `bg-primary/15 text-primary border-primary/30 text-[10px]` showing `alerts.length`.
- **Loading** (alerts loading OR students loading): 3 Skeletons `h-20 rounded-xl` in `space-y-2`.
- **Empty** (no alerts): `text-center py-12`: Bell icon `h-10 w-10 mx-auto text-muted-foreground/30 mb-3` + `text-sm text-muted-foreground` **"No alerts for your children's bus"**.
- **List** (`space-y-2`), one Card per incident, Card `border-warning/20`, CardContent `flex items-start gap-3 p-3`:
  - Icon tile `h-8 w-8 rounded-lg shrink-0 bg-warning/10 text-warning` with TriangleAlert `h-4 w-4` (same icon for every type).
  - Body `flex-1 min-w-0`: top row `flex items-start justify-between gap-2` = type label (`text-xs font-semibold`, via map above) and relative timestamp (`text-[10px] text-muted-foreground shrink-0`) via date-fns `formatDistanceToNow(new Date(created_at), { addSuffix: true })`.
  - Description `text-[11px] text-foreground/80 mt-0.5 whitespace-pre-wrap`.
  - Meta row `flex items-center gap-2 mt-1 text-[10px] text-muted-foreground`: if `bus_name` → Bus icon `h-3 w-3` + bus_name; if `driver_name` → "· {driver_name}".
- No actions/mutations on this page (read-only feed; no dismiss/mark-read).

### Data sources

Supabase: `incidents` select `id, driver_name, bus_id, bus_name, type, description, created_at` filtered `.in("bus_id", childBusIds)` ordered created_at desc (queryKey ["parent-alerts", busIds]); `parent_students`+`students` via my-students hook to derive bus ids. Realtime channel `incidents-parent` on all incidents events with client-side bus_id filter. Related edge function: `send-arrival-push` (invoked from driver run page wH after inserting an `arrival` incident) with body `{ bus_id, title: "Bus Arrived at School 🚌", body: "{bus} has arrived at {stop}.", url: "/parent/alerts" }`.

### Styling notes

Warning-tinted alert cards (`border-warning/20`, `bg-warning/10 text-warning` icon tile). lucide Bell (empty state) and TriangleAlert (list). date-fns formatDistanceToNow for "x minutes ago". Count badge in primary tint.

## `/parent/profile` — Parent Profile (account, children, push notifications)

## Route & Access
- Path `/parent/profile`, ProtectedRoute `allowedRoles: ["parent"]`, ParentLayout title **"Profile"**. Uses `useAuth()` (user + signOut), `useNavigate`, `useToast`, and the `usePushNotifications` hook (see sharedComponents).

## Data
- Query `["parent-children", user.id]` (enabled when user.id): 1) `we.from("parent_students").select("student_id").eq("parent_id", user.id)` — if none → `[]`; 2) `we.from("students").select("id, name, grade, boarding_stop_name, bus_id, school_id").in("id", studentIds)`; 3) batch-resolve names: `we.from("buses").select("id, name").in("id", uniqueBusIds)` and `we.from("schools").select("id, name").in("id", uniqueSchoolIds)`; returns students decorated with `busName`/`schoolName` (null when absent).
- Query `["parent-profile", user.id]` (enabled when user.id): `we.from("profiles").select("full_name").eq("id", user.id).single()`.
- Display name = `profiles.full_name || user.user_metadata.full_name || "Parent"`; email = `user.email ?? ""`; phone = `user.phone ?? ""` (from the auth user, not a table).

## Layout (`space-y-4`)
### 1. Identity Card
- CardContent `p-4 flex items-center gap-4`: avatar `h-14 w-14 rounded-full bg-primary/10` with User icon `h-7 w-7 text-primary`; right: name `h2.font-heading.font-bold.text-base`; if email: line `text-xs text-muted-foreground flex items-center gap-1` with Mail icon `h-3 w-3`; if phone: same style line with Phone icon `h-3 w-3`. (No edit capability.)

### 2. "My Children" Card
- CardHeader `p-4 pb-2`, CardTitle `text-sm font-heading flex items-center gap-2`: GraduationCap `h-4 w-4` + **"My Children"**.
- CardContent `p-4 pt-0 space-y-3`:
  - Loading: two Skeletons `h-16` in `space-y-2`.
  - Empty: `text-sm text-muted-foreground text-center py-4`: **"No children assigned yet."**
  - Per child row `flex items-center gap-3 p-3 rounded-lg bg-muted/50`: avatar `h-9 w-9 rounded-full bg-primary/10 text-primary font-bold text-xs font-heading` with initials (first letters of words, `.slice(0,2)` — max 2 chars, unlike home page); body: name `text-sm font-medium`; meta row `flex items-center gap-2 text-[10px] text-muted-foreground`: grade, then if busName: "•" + Bus icon `h-2.5 w-2.5` + busName; if schoolName: outline Badge `mt-1 text-[10px]` with schoolName; if `boarding_stop_name`: line `text-[10px] text-muted-foreground flex items-center gap-0.5 mt-0.5` with MapPin `h-2.5 w-2.5` + stop name.

### 3. Action buttons (`space-y-2`)
- **Push notification toggle**: Button variant outline `w-full justify-start gap-2 text-xs h-10`, `disabled` when `!supported || status === "denied"`. Icon: BellOff `h-4 w-4` when subscribed, else BellRing `h-4 w-4`. Label logic: not supported → **"Push not supported on this device"**; status denied → **"Notifications blocked in browser"**; subscribed → **"Disable Push Notifications"**; else → **"Enable Push Notifications"**.
  - onClick: if subscribed → `await unsubscribe()` then toast `{ title: "Push notifications disabled" }`. Else → `ok = await subscribe()`; toast: success → `{ title: "Push notifications enabled", description: "You'll be notified when the bus arrives at school." }`; failure → `{ title: "Permission required", description: "Allow notifications in your browser settings.", variant: "destructive" }`.
- **Sign Out**: Button outline `w-full justify-start gap-2 text-xs h-10 text-destructive hover:text-destructive` with LogOut `h-4 w-4` + **"Sign Out"** → `await signOut()` then `navigate("/auth")`.

## Push subscribe flow (usePushNotifications hook, exact behavior)
- Support check: `"serviceWorker" in navigator && "PushManager" in window && "Notification" in window`. `status` state initialized to `Notification.permission` or `"unsupported"`; `subscribed` boolean state.
- On mount (if supported): `navigator.serviceWorker.register("/sw.js").catch(noop)`; then `serviceWorker.ready` → `pushManager.getSubscription()` → set `subscribed = !!sub`.
- `subscribe()`: set status `"subscribing"`; `Notification.requestPermission()` — if not "granted", set status to result and return false. Get/create subscription via `pushManager.subscribe({ userVisibleOnly: true, applicationServerKey: urlBase64ToUint8Array(VAPID_PUBLIC_KEY) })` where VAPID public key = `"BJAyMFuKknBtQNiw5ERL-hmSIfNEYgwA_c44rOg62lNntsP5NgKH9S1tx44ANMekGE6U2XeJGpCA3Tzfb8SO7aY"` (base64url→Uint8Array helper included in bundle). Then `we.auth.getUser()`; if no user return false; else `we.from("push_subscriptions").upsert({ user_id, endpoint, p256dh: keys.p256dh, auth: keys.auth, user_agent: navigator.userAgent }, { onConflict: "endpoint" })`; set status "granted", subscribed true, return true.
- `unsubscribe()`: get current subscription; if present `we.from("push_subscriptions").delete().eq("endpoint", sub.endpoint)` then `sub.unsubscribe()`; set subscribed false.
- Delivery side: driver app's school-gate arrival inserts an `arrival` incident then invokes edge function **`send-arrival-push`** with `{ bus_id, title: "Bus Arrived at School 🚌", body, url: "/parent/alerts" }` (failures swallowed with `.catch(() => {})`) — the edge function fans out to `push_subscriptions` of that bus's parents.

### Data sources

Supabase: `parent_students` (student_id by parent_id), `students` (id,name,grade,boarding_stop_name,bus_id,school_id), `buses` (id,name) and `schools` (id,name) for name lookups, `profiles` (full_name by id, .single()), `push_subscriptions` (upsert onConflict endpoint; delete by endpoint), auth user via `we.auth.getUser()` / useAuth. Edge function `send-arrival-push` (consumer side). Service worker at `/sw.js`.

### Styling notes

shadcn Card/Badge/Button/Skeleton; lucide User, Mail, Phone, GraduationCap, Bus, MapPin, BellOff, BellRing, LogOut. Children rows on `bg-muted/50` rounded-lg. Full-width left-aligned outline action buttons h-10 text-xs; destructive styling on sign-out text only (outline variant).

## Shared components/hooks in this section

## ParentLayout (`Ii`, line ~56956)
All four parent pages render inside `Ii({ children, title, showBack })`:
- Root: `min-h-screen flex flex-col bg-background max-w-lg mx-auto border-x border-border` — phone-width column centered on desktop.
- **Header** `h-14 flex items-center gap-3 border-b bg-card px-4 shrink-0`: optional back button (only when `showBack`; ArrowLeft `h-5 w-5`, `navigate(-1)`; none of the 4 parent pages pass it); logo square `h-8 w-8 rounded-lg bg-accent` with Bus icon `h-4 w-4 text-accent-foreground`; title `h1.font-heading.font-semibold.text-sm`; right-aligned sign-out icon button (LogOut `h-4 w-4`, `p-2 rounded-md hover:bg-muted text-muted-foreground`) → `await signOut(); navigate("/auth")`.
- **Main** `flex-1 overflow-auto p-4`.
- **Bottom nav** `h-16 border-t bg-card flex items-center justify-around shrink-0` from items array `bH`: Home `/parent` (House icon), Track `/parent/track` (MapPin), Alerts `/parent/alerts` (Bell), Profile `/parent/profile` (User). Each is a react-router `NavLink` with `end` prop only for `/parent`; classes `flex flex-col items-center gap-0.5 px-3 py-1.5 rounded-lg text-muted-foreground transition-colors` + `text-primary` when active; icon `h-5 w-5` above label `text-[10px] font-medium`.

## Data hooks (defined ~24110–24241, shared with admin)
- `useBuses` (`dn`): `["buses"]`, `from("buses").select("*").order("name")`.
- `useRoutes` (`da`): `["routes"]`, `from("routes").select("*, route_stops(*)").order("name")`, route_stops sorted by `stop_order` ascending client-side.
- `useRuns` (`oi`): `["runs"]`, `from("runs").select("*, buses(name, plate_number), routes(name)").order("date", { ascending: false })`.
- `useStudentRoutes` (`Xl(studentId?)`): `["student-routes", id ?? "all"]`, `from("student_routes").select("id, student_id, route_id")`, optional `.eq("student_id", id)`.
- `useMyStudents` (`Lp`): `["my-students"]` — `we.auth.getUser()` (throws "Not authenticated" if absent) → `from("parent_students").select("student_id").eq("parent_id", user.id)` → empty array if none → `from("students").select("*").in("id", ids)`. Student rows carry: name, grade, status (`on-bus|at-school|dropped-off|absent`), bus_id, school_id, boarding_stop_id, boarding_stop_name, parent_name.

## RouteStopsMap (`Mp`, line 31532)
`Mp({ stops, className })` — static Leaflet mini-map, returns null when stops empty. Container div `w-full rounded-lg border border-border overflow-hidden`, inline height 160px. Map options: zoomControl/attributionControl/dragging/scrollWheelZoom/doubleClickZoom/touchZoom all disabled. Tile layer `https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png`. Per stop: divIcon HTML circle — regular stops 18px `hsl(152,55%,45%)` showing `stop_order`, last stop 22px `hsl(152,55%,28%)` showing "●"; white 2px border, box-shadow, white bold text (8px/10px). Polyline through all stops: color `hsl(152,55%,28%)`, weight 3, opacity 0.6, dashArray "6 4". `fitBounds(padding [20,20], maxZoom 15)`. Map rebuilt on every `stops` change; markers `interactive: false`.

## usePushNotifications (`NH`, line 57581) + helpers
Returns `{ supported, status, subscribed, subscribe, unsubscribe }` — full behavior documented in the /parent/profile page spec. Constants: VAPID public key `CH = "BJAyMFuKknBtQNiw5ERL-hmSIfNEYgwA_c44rOg62lNntsP5NgKH9S1tx44ANMekGE6U2XeJGpCA3Tzfb8SO7aY"`; `AH(base64url)` = standard urlBase64→Uint8Array converter. Service worker path `/sw.js`. Table `push_subscriptions` columns used: `user_id, endpoint, p256dh, auth, user_agent` (unique on `endpoint`).

## Incident type label map (`TH`, line 57450)
`{ breakdown: "Vehicle Breakdown", accident: "Road Accident", student: "Student Issue", traffic: "Traffic Delay", arrival: "Bus Arrived at School", other: "Notice" }` — shared between parent alerts feed and driver incident form types.

## UI primitives / libs (minified aliases)
`ot/ct/yn/wn` = Card/CardContent/CardHeader/CardTitle; `Ot` = Badge; `De` = Button; `qt` = Skeleton; `_e` = cn(); `wr` = useQuery; `Et` = useQueryClient; `Mn` = useNavigate; `$0` = NavLink; `la` = useAuth context (user, signOut); `Qt` = useToast; `gS` = date-fns formatDistanceToNow; `we` = supabase client; `pn` = Leaflet. Icons (lucide): `q1` House, `_n` MapPin, `Dl` Bell, `Y0` User, `$r` Bus, `Ml` LogOut, `q0` ArrowLeft, `Mi` Clock, `Gi` CircleCheck, `ea` TriangleAlert, `Bl` Phone, `Z0` Mail, `$A` GraduationCap, `NA` BellOff, `PA` BellRing.

## Realtime channel summary (parent scope)
- `parent-students-status` (/parent): UPDATE on students → invalidate my-students, students.
- `parent-track-live` (/parent/track): UPDATE students → my-students+students; * runs → runs; * student_routes → student-routes.
- `incidents-parent` (/parent/alerts): * incidents, client-filtered by child bus_ids → invalidate parent-alerts.

## Agent notes

Source: /Users/andreanatali/Documents/GitHub/Safe Ride 1/safe-ride/.local/live-reference/app.pretty.js — EH /parent 57017-57240, SH /parent/track 57242-57449, TH type map 57450-57457, kH /parent/alerts 57459-57569, CH/AH/NH push hook 57570-57630, PH /parent/profile 57632-57819, bH nav items 56938-56954, Ii ParentLayout 56956-57015, data hooks 24110-24241, Mp map 31532-31584, send-arrival-push invocation (driver side) 56402-56409, route registrations 57923-57947. Quirks worth preserving or consciously fixing in the rebuild: (1) the "~N min" ETA on /parent is pure Math.random() mock (3–13 min), regenerated per render; (2) "Call Driver" quick action dials buses[0].driver_phone from the globally-sorted bus list, not the child's assigned bus; (3) /parent/track shows a "bus is live" badge when bus.current_lat exists but never plots the live bus position on the map; (4) stop names on /parent/track are privacy-masked (leading digits stripped) for stops that aren't the family's own or the school gate.