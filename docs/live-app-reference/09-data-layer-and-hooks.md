# Live app data layer: schema, hooks, mutations, edge functions, realtime

# SafeRide Kenya — Data Layer & Cross-Cutting Hooks Reference (reverse-engineered from live bundle)

Source: `/Users/andreanatali/Documents/GitHub/Safe Ride 1/safe-ride/.local/live-reference/app.pretty.js`. `we` = supabase client; line numbers cited per call site.

## 1. Supabase client (L18437–18445)
- URL: `https://tculxwgbbpuqphnfsttn.supabase.co`, anon JWT key inline (E4).
- Options: `auth: { storage: localStorage, persistSession: true, autoRefreshToken: true }`.

## 2. Auth + role model

### useAuth hook `la()` (L18447–18489)
- State: `user`, `session`, `role`, `loading`.
- `we.auth.onAuthStateChange((event, session) => ...)`: sets session/user; if session.user exists, defers (`setTimeout(...,0)`) a role fetch: `we.from("user_roles").select("role").eq("user_id", userId).single()` → `role` (null on error). No user → role=null, loading=false.
- On mount also calls `we.auth.getSession()` to seed state (loading stays true until role fetch completes when a session exists).
- `signOut()`: `we.auth.signOut()` then nulls user/session/role.

### ProtectedRoute `lr({children, allowedRoles})` (L18501–18539)
- ONLY enforces when `allowedRoles` is exactly `["admin"]`: while loading shows skeleton; no user → `<Navigate to="/auth" replace/>`; role known and not allowed → redirect by role (`admin→"/"`, `driver→"/driver"`, else `"/parent"`); else render children. For any other allowedRoles value it renders children unguarded (driver/parent routes are effectively NOT role-blocked client-side).

### /auth page `TL` (L21271–21656)
- Sign in (`signInWithPassword({email, password})`); afterwards `we.auth.getUser()` then `user_roles.select("role").eq("user_id", id).single()` and navigate: admin→`/`, driver→`/driver`, else `/parent`.
- Sign up: `we.auth.signUp({email, password, options: { data: { full_name, role }, emailRedirectTo: window.location.origin }})`; role select limited to `driver` | `parent` (map `uf`, L21254; default "parent"); toast "Account created! / Please check your email to verify your account." (Implication: a DB trigger on auth.users consumes `raw_user_meta_data.full_name/role` to populate `profiles` and `user_roles`.)
- Forgot password (`?forgot=1` opens it): `we.auth.resetPasswordForEmail(email, { redirectTo: `${origin}/reset-password` })`; toast "Reset link sent".
- Driver PIN tab: 4-digit OTP input → `we.functions.invoke("driver-pin-login", { body: { pin } })` → response `{ token_hash }` (or `{error}`) → `we.auth.verifyOtp({ token_hash, type: "magiclink" })` → navigate `/driver`. Errors toast "Invalid PIN".

### /reset-password `CL` (L21658–21782) + helpers (L21640–21656)
- URL parsing `kL()`: hash params (`error`, `error_code`, `type==="recovery" && access_token`) and search params (`code`, `token_hash`, `type`).
- Verification state machine ("checking"→"ready"/"missing"): if `token_hash&type=recovery` → `we.auth.verifyOtp({token_hash, type:"recovery"})`; else if `code` → `we.auth.exchangeCodeForSession(code)` (fallback: existing session + `sessionStorage.passwordRecoveryInProgress==="true"`); else subscribe `onAuthStateChange` waiting for `PASSWORD_RECOVERY` (or `SIGNED_IN`/`INITIAL_SESSION` when recovery hash present) and poll `getSession()` 20×250ms. On settle, `history.replaceState(null,"","/reset-password")` to scrub tokens.
- Submit: passwords must match, ≥6 chars; requires live session; `we.auth.updateUser({ password })`; then clears the sessionStorage flag, `we.auth.signOut()`, navigate `/auth`.

## 3. React-Query read hooks (admin-shared, L24110–24261)
| Hook | key | Query |
|---|---|---|
| `dn()` useBuses | `["buses"]` | `from("buses").select("*").order("name")` |
| `da()` useRoutes | `["routes"]` | `from("routes").select("*, route_stops(*)").order("name")`; client sorts each `route_stops` by `stop_order` asc |
| `Ji()` useStudents | `["students"]` | `from("students").select("*").order("name")` |
| `oi()` useRuns | `["runs"]` | `from("runs").select("*, buses(name, plate_number), routes(name)").order("date", {ascending:false})` |
| `Ip()` useSchools | `["schools"]` | `from("schools").select("*").order("name")` |
| `Xl(studentId?)` | `["student-routes", id??"all"]` | `from("student_routes").select("id, student_id, route_id")` (+ `.eq("student_id", id)` if given) |
| `PD()` | `["parent-students", arg]` | `from("parent_students").select("*")` |
| `Lp()` useMyStudents (parent) | `["my-students"]` | `getUser()` → `from("parent_students").select("student_id").eq("parent_id", user.id)` → `from("students").select("*").in("id", ids)`; [] if none |
| `RD()` | `["incidents-today-count"]` | `from("incidents").select("id", {count:"exact", head:true}).gte("created_at", startOfTodayISO)`; `refetchInterval: 15000` |
| sidebar unread (L22823) | `["unread-alerts"]` | `from("incidents").select("id",{count:"exact",head:true}).eq("acknowledged", false)`; returns 0 on error |
| `T3()`/`vz()` | `["admin-drivers-list"]`/`["admin-drivers"]` | `functions.invoke("admin-manage-drivers", {body:{action:"list"}})` |
| `fz()`/`mz()` | `["admin-parents"]` | `functions.invoke("admin-manage-parents", {body:{action:"list"}})` |

Dashboard stats `jD()` (L24263–24288): todayRuns = runs with `date === toISOString().split("T")[0]`; activeBuses = union of buses.status==="active" + today's runs status==="in-progress" (by bus_id); delayed likewise for "delayed"; studentsOnBus = students.status==="on-bus"; absent = "absent"; `studentsWaiting` hardcoded 0.

## 4. Mutations (L30440–30798)
All via react-query `useMutation` with invalidation:
- Buses: `insert(payload).select().single()` / `update(rest).eq("id",id).select().single()` / `delete().eq("id",id)` → invalidate `["buses"]`. Bus form payload (L30857): `{ name, plate_number, driver_name, driver_phone, capacity (int 1–100, default 45), status ("idle"|"active"|"delayed"|"offline"), driver_id: string|null }`. Picking a registered driver (from admin-manage-drivers list) syncs driver_name/driver_phone read-only from profile; "none" clears all three.
- Routes: same CRUD → invalidate `["routes"]`. Payload (L31346–31358): `{ name, type: "morning"|"afternoon", bus_id: string|null, school_id: string|null }`. NOTE: the client never inserts/updates `route_stops` — stops are produced server-side (trigger/edge) from assigned students + school gate.
- Students: same CRUD → invalidate `["students"]`. Payload (L32052–32064): `{ name, grade, parent_name, parent_phone, parent_phone2: string|null, parent_email: string|null, home_address: string|null, home_lat: number|null, home_lng: number|null, pickup_time: string|null, status }` (status default "at-school"; options at-school|on-bus|absent|dropped-off; map click sets home_lat/lng toFixed(6), default map center Nairobi −1.2921,36.8219).
- student_routes sync `g3()` (L30596): given `{studentId, routeIds}` (morning+afternoon route picks from the student form), reads existing `select("id, route_id").eq("student_id",...)`, deletes removed by id (`delete().in("id", ids)`), inserts added as `{student_id, route_id}`; invalidates `["student-routes"], ["routes"], ["students"]`.
- Runs: CRUD → invalidate `["runs"]`. Admin form payload (L53140–53162): `{ bus_id, route_id: string|null, type: "morning"|"afternoon", date (yyyy-mm-dd, default today), start_time: string|null, end_time: string|null, status: "in-progress"|"completed"|"delayed", total_stops, stops_completed, total_students, students_boarded, incidents }`.
- Schools: CRUD → invalidate `["schools"]`. Payload (L53695): `{ name, address (required), phone, lat, lng }` — lat/lng required (set by clicking Leaflet map), toFixed(6).
- parent_students: `insert({parent_id, student_id}).select().single()` / `delete().eq("id", rowId)` → invalidate `["parent-students"]`.

## 5. Edge functions
- `admin-manage-drivers` (L30789, 54689–54811): bodies `{action:"list"}` → array `{id, full_name, email, phone, has_pin: bool, assigned_bus: string|null, created_at}`; `{action:"create", full_name, email, password, phone, pin}` (password required min 6 max 72 on create; pin optional 4 digits, UI "Generate" makes random 1000–9999); `{action:"update", id, full_name, email, phone, pin}`; `{action:"delete", id}` (also unassigns buses → invalidates `["admin-drivers"],["driver-profiles"],["buses"]`); `{action:"reset-password", email}` (sends reset email). create/update invalidate `["admin-drivers"]` + `["driver-profiles"]`.
- `admin-manage-parents` (L54049, 54279–54364): `{action:"list"}` → array `{id, full_name, email, phone, status: "registered"|"pending", students: string[] (names), created_at}` (pending = parent_email on students without an auth account yet; UI shows "Awaiting signup", row actions only for registered); `{action:"update", id, full_name, email, phone}`; `{action:"delete", id}` (invalidates `["admin-parents"],["parent-students"]`; copy: deletes account and removes all student assignments); `{action:"reset-password", email}`.
- `bulk-upload-students` (L52662–52694): requires session; body `{ students: [{name, grade, parent_name, parent_phone, parent_phone2, parent_email, home_address, home_lat, home_lng, pickup_time, route_name}] }` (all strings; first 4 required — validated client-side per row; headers normalized lowercase/underscores). Response `{ inserted: number, parentAssignments: number, errors: string[] }`. CSV template constant (L52592–52596) with Kenyan sample rows. Accepts .csv/.xlsx/.xls parsed via SheetJS.
- `driver-pin-login` (L21303): `{pin}` → `{token_hash}` consumed by `verifyOtp({type:"magiclink"})`.
- `send-arrival-push` (L56402): `{ bus_id, title: "Bus Arrived at School 🚌", body: "<bus> has arrived at <stop>.", url: "/parent/alerts" }`; fire-and-forget `.catch(()=>{})`.

## 6. Realtime channels (all `postgres_changes`, schema "public"; cleanup via `we.removeChannel`)
| Channel | Filters | Action |
|---|---|---|
| `incidents-sidebar` (L22837) | `*` on incidents | invalidate `["unread-alerts"]` |
| `dashboard-live` (L24462) | `*` on students / buses / runs / incidents | invalidate `["students"]` / `["buses"]` / `["runs"]` / `["incidents-today-count"]` respectively |
| `incidents-admin` (L55797) | `*` on incidents | invalidate `["incidents"]` |
| `parent-students-status` (L57035) | `UPDATE` on students | invalidate `["my-students"]`, `["students"]` |
| `parent-track-live` (L57261) | `UPDATE` students; `*` runs; `*` student_routes | invalidate `["my-students"]+["students"]` / `["runs"]` / `["student-routes"]` |
| `incidents-parent` (L57486) | `*` on incidents, payload-filtered: `(new??old).bus_id` ∈ kids' bus_ids | invalidate `["parent-alerts"]` |

## 7. Incidents / alerts flows
- Admin /alerts (L55775): `from("incidents").select("*").order("created_at",{ascending:false})`; Ack: `update({acknowledged:true, acknowledged_at: ISO now, acknowledged_by: user.id}).eq("id",id)`; Delete: `delete().eq("id",id)`. Type labels (L55767): breakdown→"Vehicle Breakdown", accident→"Road Accident", student→"Student Issue", traffic→"Heavy Traffic / Delay", other→"Other".
- Driver /driver/incident (L56803–56850): type from same 5 options; fetches `profiles.select("full_name").eq("id",uid).maybeSingle()` + `buses.select("id, name").eq("driver_id",uid).maybeSingle()` in parallel, then `incidents.insert({driver_id, driver_name, bus_id, bus_name, type, description})`.
- Parent /parent/alerts (L57459): `incidents.select("id, driver_name, bus_id, bus_name, type, description, created_at").in("bus_id", kidsBusIds).order("created_at", desc)`; label map adds `arrival→"Bus Arrived at School"`, `other→"Notice"`.

## 8. Driver run lifecycle (/driver/run `vH`, L56269–56462)
- Resolution: bus = buses.find(driver_id===user.id); route = routes.find(bus_id===bus.id); activeRun = runs.find(bus_id===bus.id && status!=="completed"); students = students.filter(bus_id===bus.id).
- Start: `runs.insert({ bus_id, route_id, school_id: route.school_id??null, type: route.type, status:"in-progress", total_stops: stops.length, total_students: students.length, stops_completed:0, students_boarded:0, start_time: HH:mm (24h locale) })`.
- Arrive at next stop: `runs.update({stops_completed: n+1}).eq("id", run.id)`. If the stop `is_school_gate` OR is the last stop: `incidents.insert({driver_id, driver_name: bus.driver_name, bus_id, bus_name: bus.name, type:"arrival", description:"<bus> has arrived at <stop>."})` then invoke `send-arrival-push` (above).
- End run: `runs.update({status:"completed", stops_completed: stops.length, end_time: HH:mm}).eq("id", run.id)`; then bulk student status: `students.update({status: route.type==="afternoon" ? "dropped-off" : "at-school"}).in("id", busStudentIds)`; clears `localStorage["driver-boarded-<busId>"]` and dispatches `CustomEvent("driver-run-completed", {detail:{busId}})`.
- /driver/boarding `_H` (L56572): eligible students = union of student_routes rows for the route + `route_stops.student_id` values; boarding gated by stop progress (`route_stops` keyed by `student_id`→`stop_order`; allowed once `stop_order <= run.stops_completed`); toggle writes `students.update({status: "on-bus"|"at-school"}).eq("id", studentId)` with optimistic Set state persisted in `localStorage["driver-boarded-<busId>"]`; invalidates `["students"]`, `["my-students"]`.

## 9. GPS / fleet map
- /fleet-map `HD` (L29979): Leaflet, OSM tiles, center Nairobi [-1.286389, 36.817223] z12; markers only for buses with truthy `current_lat && current_lng`; popup shows name/plate/driver/phone/capacity/status; fitBounds maxZoom 14. Parent track shows "<bus> is live" badge when `bus.current_lat` set (L57325).
- IMPORTANT: nothing in this bundle ever WRITES `buses.current_lat/current_lng` (no `watchPosition` outside Leaflet's locate control, no bus position update/insert). Live positions must come from elsewhere (seed data / another writer); parent ETA "~N min" is `Math.max(3, floor(random()*12)+2)` — fake (L57101).

## 10. Push notifications (L57570–57630)
- VAPID public key constant `CH = "BJAyMFuKknBtQNiw5ERL-hmSIfNEYgwA_c44rOg62lNntsP5NgKH9S1tx44ANMekGE6U2XeJGpCA3Tzfb8SO7aY"`; `AH()` = base64url→Uint8Array.
- Hook `NH()`: supported = SW+PushManager+Notification; registers `/sw.js` on mount and reflects existing subscription. subscribe(): `Notification.requestPermission()` → `pushManager.subscribe({userVisibleOnly:true, applicationServerKey})` → `from("push_subscriptions").upsert({user_id, endpoint, p256dh: keys.p256dh, auth: keys.auth, user_agent: navigator.userAgent}, {onConflict:"endpoint"})`. unsubscribe(): `from("push_subscriptions").delete().eq("endpoint", sub.endpoint)` then `sub.unsubscribe()`. Used only on /parent/profile (enable/disable button; "denied" shows "Notifications blocked in browser").

## 11. Parent profile data (L57632–57693)
- `["parent-children", uid]`: parent_students→student_ids; `students.select("id, name, grade, boarding_stop_name, bus_id, school_id").in("id", ids)`; then `buses.select("id, name").in("id", busIds)` and `schools.select("id, name").in("id", schoolIds)` to decorate busName/schoolName.
- `["parent-profile", uid]`: `profiles.select("full_name").eq("id", uid).single()`; fallback `user.user_metadata.full_name`, then "Parent"; email/phone from auth user.

## 12. Implied database schema
- **profiles**: id (= auth.users.id, PK), full_name, phone, created_at. (email lives in auth; admin lists merge it.)
- **user_roles**: user_id (FK auth.users), role ∈ {admin, driver, parent}; one row per user (`.single()`).
- **buses**: id, name, plate_number, driver_id (FK profiles, nullable), driver_name, driver_phone (denormalized), capacity int, status ∈ {idle, active, delayed, offline}, current_lat, current_lng (nullable; not written by client).
- **schools**: id, name, address, phone, lat, lng.
- **routes**: id, name, type ∈ {morning, afternoon}, bus_id (nullable FK buses), school_id (nullable FK schools). FK named so `runs.routes(name)` join works.
- **route_stops**: id, route_id (FK), name, stop_order int, scheduled_time (HH:mm string), lat, lng, is_school_gate bool, student_id (nullable FK students — per-student pickup stops). Server-generated only.
- **students**: id, name, grade, parent_name, parent_phone, parent_phone2?, parent_email?, home_address?, home_lat?, home_lng?, pickup_time?, status ∈ {at-school, on-bus, absent, dropped-off}, bus_id?, school_id?, boarding_stop_id? (FK route_stops), boarding_stop_name? (denormalized; bus/school/boarding fields set server-side, never by client forms).
- **student_routes**: id, student_id, route_id (join; at most one morning + one afternoon per student by UI convention).
- **parent_students**: id, parent_id (FK auth user), student_id.
- **runs**: id, bus_id, route_id?, school_id?, type ∈ {morning, afternoon}, date (yyyy-mm-dd), start_time?, end_time? (HH:mm strings), status ∈ {in-progress, completed, delayed}, total_stops, stops_completed, total_students, students_boarded, incidents int, created_at.
- **incidents**: id, driver_id?, driver_name?, bus_id?, bus_name?, type ∈ {breakdown, accident, student, traffic, arrival, other}, description, acknowledged bool (default false), acknowledged_at?, acknowledged_by?, created_at.
- **push_subscriptions**: user_id, endpoint (UNIQUE — upsert onConflict), p256dh, auth, user_agent.
- RLS implications: parents can read buses/routes/route_stops/runs/students broadly (parent pages query them with anon role filtering only client-side); drivers can update runs/students/incidents; incidents readable by parents `.in("bus_id",...)`; admin edge functions use service role for auth.users management (create/delete/reset emails, PIN-hash storage signalled by `has_pin`).

## Notes

All findings verified against exact call sites in app.pretty.js (line refs inline). Notable gotchas for the rebuild: (1) client never writes bus GPS coords or route_stops rows — both must be produced server-side or seeded; (2) parent ETA minutes are randomized client-side; (3) ProtectedRoute only enforces the admin case; (4) push VAPID public key and supabase URL/anon key are hardcoded in the bundle; (5) driver boarding state is cached in localStorage key `driver-boarded-<busId>` and cleared via a `driver-run-completed` CustomEvent.
