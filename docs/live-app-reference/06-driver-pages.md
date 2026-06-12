# Driver pages (/driver, /driver/run, /driver/boarding, /driver/incident)

_Reverse-engineered from the live www.saferidekenya.com bundle (app.pretty.js in safe-ride/.local/live-reference/). Source of truth for alignment._

## `/driver` — Driver Home

# Driver Home (`xH`, lines 56031–56267)

Route registered at line 57900 as `<ProtectedRoute allowedRoles={["driver"]}><DriverHome/></ProtectedRoute>`. NOTE: ProtectedRoute (lr, 18501) only performs auth/role checks when `allowedRoles` is exactly `["admin"]`; for driver routes it renders children unconditionally — no client-side gate (auth redirect happens for admins only; relies on RLS + the /auth page redirecting signed-in drivers to /driver).

Wrapped in **DriverLayout** (`In`) with `title="SafeRide Driver"` (see sharedComponents).

## Data
- `useBuses()` (dn): `we.from("buses").select("*").order("name")`, queryKey ["buses"] — also provides `isLoading` used for the skeleton state.
- `useRoutes()` (da): `we.from("routes").select("*, route_stops(*)").order("name")`, route_stops sorted client-side by `stop_order`; queryKey ["routes"].
- `useStudents()` (Ji): `we.from("students").select("*").order("name")`, queryKey ["students"].
- `useRuns()` (oi): `we.from("runs").select("*, buses(name, plate_number), routes(name)").order("date", {ascending:false})`, queryKey ["runs"].
- `useStudentRoutes()` (Xl, no arg): `we.from("student_routes").select("id, student_id, route_id")`, queryKey ["student-routes","all"].
- No realtime subscriptions on any driver page (channels exist only on admin + parent pages).

## Derived state
- `bus` = buses.find(b => b.driver_id === auth user.id).
- `busRoutes` = routes filtered by `route.bus_id === bus.id`; `morning` = first with `type === "morning"`, `afternoon` = first with `type === "afternoon"`; `routesList` = [morning, afternoon].filter(Boolean).
- `activeRun` = runs.find(r => r.bus_id === bus.id && r.status !== "completed").
- `studentsForRoute(routeId)`: set of student_ids from student_routes rows with that route_id, UNION student_ids embedded on the route's route_stops (`stop.student_id`), then students filtered to that set.
- `totalStops` = sum of route_stops.length over routesList; `uniqueStudents` = Set of studentsForRoute ids over routesList; `departTime` = earliest non-null `scheduled_time` across all stops of both routes (string sort), fallback `"—"`.

## States
- **Loading** (buses query): 3 Skeletons `h-24 rounded-xl` in `space-y-4`.
- **No bus assigned**: Card with BusFront icon (`h-10 w-10 text-muted-foreground/40`), "No bus assigned" (text-sm font-medium), "Contact your administrator to be assigned to a bus." (text-xs muted).

## Layout (space-y-4)
1. **Greeting**: h2 `text-lg font-heading font-bold` → `Hello, {bus.driver_name} 👋`; subtitle `text-sm text-muted-foreground` → `{bus.name} · {bus.plate_number}`.
2. **Active run card** (if activeRun) — Card `border-primary/30 bg-primary/5`, p-4, flex justify-between: left has pulsing dot (`h-2 w-2 rounded-full bg-chart-green animate-pulse-dot`) + "Active Run" (`text-xs font-medium text-primary uppercase tracking-wider`), route name from joined `run.routes?.name` (font-heading font-semibold text-sm), and `Stop {stops_completed}/{total_stops} · {students_boarded} boarded` (text-xs muted). Right: Button size=sm "Continue" → navigate("/driver/run").
3. **No active run card** (else) — Card p-4 centered py-8: CirclePlay icon h-10 w-10 muted/40, "No active run" text-sm muted, Button "Start Run" → navigate("/driver/run").
4. **Stats grid** `grid grid-cols-3 gap-3`, each a Card p-3 text-center with icon h-4 w-4 mx-auto muted mb-1, value `text-lg font-bold font-heading`, label `text-[10px] muted uppercase tracking-wider`: MapPin/`{totalStops}`/"Stops"; Users/`{uniqueStudents.size}`/"Students"; Clock/`{departTime}`/"Depart".
5. **Empty routes**: if routesList empty → Card p-6 center text-sm muted "No routes assigned to this bus yet."
6. **Per-route cards** (morning then afternoon). `isActive` = activeRun?.route_id === route.id → card gets `border-primary/40`. CardHeader pb-2 p-4: CardTitle `text-sm font-heading flex items-center justify-between gap-2` — left span: Sun icon `h-4 w-4 text-chart-amber` for morning / Moon icon `h-4 w-4 text-primary` for afternoon, then `{Morning|Afternoon} · {route.name}`; right: outline Badge `text-[10px] border-primary text-primary` "Active" when isActive. Sub-line `text-[11px] muted`: `{stops.length} stops · {studentsForRoute(route.id).length} students`.
   CardContent p-4 pt-0 space-y-4:
   - **Mini map** (`Mp`, line 31532) when stops > 0: non-interactive Leaflet map, height 160px, `w-full rounded-lg border border-border overflow-hidden`; OSM tiles `https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png`; zoom/drag/scroll/doubleclick/touch all disabled, no controls/attribution. One divIcon marker per stop at [stop.lat, stop.lng]: 18px circle `hsl(152,55%,45%)` white 2px border showing `stop.stop_order`; LAST stop is 22px `hsl(152,55%,28%)` showing "●". Dashed polyline (color hsl(152,55%,28%), weight 3, opacity .6, dashArray "6 4") through all stops; fitBounds padding [20,20] maxZoom 15.
   - **Stop timeline** space-y-3: per stop index I — `done` = isActive && I < (activeRun.stops_completed ?? 0); `isNext` = isActive && I === stops_completed. Circle `h-6 w-6 rounded-full text-xs font-bold border-2`: done → `bg-chart-green text-white border-chart-green` with CircleCheck h-3.5; isNext → `bg-primary text-primary-foreground border-primary` with number I+1; else `bg-muted text-muted-foreground border-border` with number. Vertical connector `w-0.5 h-4 mt-0.5` (`bg-chart-green` when done, else `bg-border`) except after last stop. Right side: stop `name` text-sm font-medium truncate (line-through + muted when done), `scheduled_time` text-[10px] muted. isNext → outline Badge `text-[10px] border-primary text-primary` "Next".

## Mutations
None — read-only page. Navigation only (Continue/Start Run buttons go to /driver/run).

### Data sources

Reads: buses (all, order name → find by driver_id = auth.uid), routes + route_stops (filter bus_id, split morning/afternoon), students (all, filtered to route assignment set), runs (with buses/routes joins, find non-completed for bus), student_routes (id, student_id, route_id). No writes, no edge functions, no realtime.

### Styling notes

Mobile shell from DriverLayout (max-w-lg mx-auto border-x). shadcn Card/CardHeader/CardTitle/CardContent, Button, Badge, Skeleton. Brand greens: chart-green for completion, primary for current/next, chart-amber for morning sun. font-heading for headings, text-[10px]/[11px] micro-labels with uppercase tracking-wider. animate-pulse-dot custom animation on live dot. Leaflet map colors hard-coded hsl(152,55%,28%/45%).

## `/driver/run` — Active Run

# Active Run (`vH`, lines 56269–56570)

DriverLayout title="Active Run". Same data hooks as home: useBuses (with isLoading), useRoutes, useStudents, useRuns. Derived: `bus` = buses.find(driver_id === user.id); `route` = routes.find(r => r.bus_id === bus.id) — **first route by name order, ignores morning/afternoon and ignores activeRun.route_id**; `activeRun` = runs.find(bus_id === bus.id && status !== "completed"); `busStudents` = students.filter(s => s.bus_id === bus.id); `stops` = route.route_stops (sorted by stop_order); `isRunning` = !!activeRun; `done` = activeRun?.stops_completed ?? 0.

## States
- Loading: 3 Skeletons h-24 rounded-xl.
- No bus: identical "No bus assigned" card as home (BusFront icon).
- No route: Card with MapPin icon h-10 w-10 muted/40, "No route assigned", `{bus.name} doesn't have a route yet. Contact your administrator.`

## Header row (flex justify-between)
- Left: h2 font-heading font-bold text-base `{route.name}`; sub `text-xs muted`: `{🌅 Morning | 🌇 Afternoon} pickup` (emoji by route.type).
- Right: if running → Button variant=destructive size=sm gap-1.5 with Square icon h-3 w-3, label "End Run" (handler T). Else → Button gap-1.5 with Play icon h-4 w-4, label "Start" (handler v).

## Start Run (v)
`we.from("runs").insert({ bus_id: bus.id, route_id: route.id, school_id: route.school_id ?? null, type: route.type, status: "in-progress", total_stops: stops.length, total_students: busStudents.length, stops_completed: 0, students_boarded: 0, start_time: new Date().toLocaleTimeString([], {hour:"2-digit", minute:"2-digit", hour12:false}) })` — `date` column left to DB default. On error: destructive toast "Could not start run" + error.message. On success: invalidate ["runs"], toast "Run started" / `"{route.name} — drive safe! 🚌"`.

## Arrive at stop (y) — fires from the current stop's "Arrive" button
Guard: requires activeRun and done < stops.length. Let `stop = stops[done]`, `isLast = done === stops.length-1`, `notifySchool = stop.is_school_gate || isLast`, `next = done+1`.
1. `we.from("runs").update({stops_completed: next}).eq("id", activeRun.id)`. Error → destructive toast "Update failed"+message, abort.
2. invalidate ["runs"]; toast title `Arrived at {stop.name}`, description "Tap students to board them" (omitted when isLast).
3. If notifySchool (and bus+user present): insert into **incidents**: `{driver_id: user.id, driver_name: bus.driver_name, bus_id: bus.id, bus_name: bus.name, type: "arrival", description: "{bus.name} has arrived at {stop.name}."}`. Insert error → destructive toast "Notification failed"+message. Success → fire-and-forget edge function `we.functions.invoke("send-arrival-push", {body:{bus_id: bus.id, title: "Bus Arrived at School 🚌", body: "{bus.name} has arrived at {stop.name}.", url: "/parent/alerts"}}).catch(()=>{})`.

## End Run (T)
1. If activeRun: `we.from("runs").update({status:"completed", stops_completed: stops.length, end_time: <HH:mm 24h local>}).eq("id", activeRun.id)`. Error → destructive toast "Could not end run", abort. Success → invalidate ["runs"].
2. Bulk student status: newStatus = route.type === "afternoon" ? "dropped-off" : "at-school"; ids = busStudents ids; if any: `we.from("students").update({status:newStatus}).in("id", ids)`. Error → destructive toast "Status update failed"; success → invalidate ["students"] and ["my-students"].
3. `localStorage.removeItem("driver-boarded-"+bus.id)` and `window.dispatchEvent(new CustomEvent("driver-run-completed", {detail:{busId: bus.id}}))` (clears the boarding page's local set).
4. Toast "Run completed ✅" / "All students delivered safely". (Note: end-run proceeds with steps 2–4 even if there was no activeRun object — button is only visible when running though.)

## Progress card (only while running)
Card border-primary/20 p-4: row "Progress" (`text-xs font-medium muted uppercase tracking-wider`) vs `{done}/{stops.length} stops` (`text-xs font-bold text-primary`); track `h-2 bg-muted rounded-full overflow-hidden` with fill `h-full bg-primary rounded-full transition-all duration-500` width `{done/stops.length*100}%`.

## Route Stops card
CardHeader p-4 pb-2, CardTitle text-sm font-heading with MapPin h-4 w-4 + " Route Stops". CardContent p-4 pt-0 space-y-3. Per stop (index b): `isDone` = b < done; `isCurrent` = b === done && isRunning; `stopStudents` = busStudents.filter(s => s.boarding_stop_id === stop.id).
- Row: `flex items-start gap-3 p-3 rounded-lg border transition-colors`; isCurrent → `border-primary bg-primary/5`; isDone → `border-chart-green/30 bg-chart-green/5`; else `border-border`.
- Number circle h-7 w-7 rounded-full text-xs font-bold shrink-0 mt-0.5: done → bg-chart-green text-white with CircleCheck h-4; current → bg-primary text-primary-foreground; else bg-muted muted; content = check or b+1.
- Body: stop.name text-sm font-medium (line-through muted when done); `ETA {scheduled_time}` text-[10px] muted; if stopStudents>0: Users icon h-3 w-3 + first names (`name.split(" ")[0]`) joined ", " at text-[10px] muted.
- isCurrent → Button size=sm variant=outline text-xs "Arrive" (calls y). isDone → Badge `bg-chart-green/15 text-chart-green border-chart-green/30 text-[10px]` "Done".

## GPS
No geolocation API use anywhere in driver code. The app never sends device GPS; buses.current_lat/current_lng (read by admin fleet map line 29982 and parent track line 57325) are not written by the driver app.

### Data sources

Reads: buses, routes+route_stops, students, runs (react-query hooks, no realtime). Writes: runs INSERT (start), runs UPDATE stops_completed (arrive), runs UPDATE status/stops_completed/end_time (end), students bulk UPDATE status in(ids) (end), incidents INSERT type "arrival" (school-gate/last-stop arrival). Edge function: `send-arrival-push` invoked fire-and-forget with {bus_id, title, body, url:"/parent/alerts"}. localStorage: removes `driver-boarded-{busId}`; dispatches window CustomEvent `driver-run-completed` {busId}.

### Styling notes

Same mobile Card stack. Destructive red End Run vs primary Start with Play icon. Progress bar primary on muted with 500ms width transition. Current stop highlighted border-primary bg-primary/5; completed chart-green tints with line-through. Emojis used in copy (🚌, ✅, 🌅, 🌇).

## `/driver/boarding` — Student Boarding

# Student Boarding (`_H`, lines 56572–56776)

DriverLayout title="Student Boarding". Hooks: useBuses (isLoading), useStudents (isLoading), useRoutes, useRuns, useStudentRoutes (all).

## Derived
- `bus` = buses.find(driver_id === user.id); `activeRun` = runs.find(bus_id === bus.id && status !== "completed").
- `route` = activeRun ? routes.find(id === activeRun.route_id) : routes.find(bus_id === bus.id) (first by name).
- Student set: ids from student_routes where route_id === route.id, plus `student_id` on each route_stop; `roster` = students in that set (empty if no route).
- `done` = activeRun?.stops_completed ?? 0. `stopOrderByStudent` Map built from route_stops that carry a `student_id` → stop_order.
- `stopReached(studentId)`: false without activeRun; true iff student's mapped stop_order exists and ≤ done. (Students assigned only via student_routes with no personal route_stop never become "reached".)

## Boarded-set state + offline persistence
- `boarded: Set<string>` in useState; storage key `driver-boarded-{bus.id}`.
- Hydration effect (once, gated by `hydrated` flag and students loaded): read localStorage key; if present parse JSON array into Set (on parse error fall back); fallback/absent → initialize from roster students whose `status === "on-bus"`. Then `hydrated = true`.
- Persist effect: after hydration, every change writes `JSON.stringify([...boarded])` to the key.
- Listener effect: window `driver-run-completed` event — if `detail.busId === bus.id`, clear the Set (fired by End Run on /driver/run, which also removes the key).
- Search state `query`; `filtered` = roster filtered by `name.toLowerCase().includes(query.toLowerCase())`.

## Toggle boarding (Z(studentId, firstName)) — tapping a student row
1. If NOT currently boarded and !stopReached: destructive toast — with activeRun: title "Stop not reached yet", description `Mark "{firstName}'s" stop as arrived before boarding.`; without: title "Start the run first", description "Begin the run before boarding students." Abort. (Un-boarding is always allowed.)
2. newStatus = wasBoarded ? "at-school" : "on-bus" (un-board sets status back to "at-school").
3. Optimistically add/remove id in Set.
4. `we.from("students").update({status:newStatus}).eq("id", studentId)`. On error: roll back the Set mutation, destructive toast "Update failed"+message, abort.
5. Invalidate ["students"] and ["my-students"]; toast: un-board → `{firstName} removed` / "Student un-boarded"; board → `{firstName} boarded ✅` / "Tap again to undo".

## States
- Loading (students OR buses): 4 Skeletons h-16 rounded-xl.
- No bus: same BusFront "No bus assigned" card.

## UI (space-y-4)
1. **Counters row** flex gap-3: tile `flex-1 rounded-lg bg-chart-green/10 p-3 text-center` — boarded count `text-xl font-bold font-heading text-chart-green`, label "Boarded" `text-[10px] muted uppercase tracking-wider`; tile `bg-destructive/10` — `roster.length - boarded.size` in `text-destructive`, label "Remaining".
2. **Search**: relative wrapper, Search icon absolute left-3 centered h-4 w-4 muted, Input placeholder "Search students..." className pl-9, controlled.
3. **Students card**: CardHeader p-4 pb-2, CardTitle text-sm font-heading `Students ({roster.length})`. CardContent p-4 pt-0 space-y-2; one `<button>` per filtered student:
   - className: `w-full flex items-center gap-3 p-3 rounded-lg border text-left transition-all active:scale-[0.98]`; boarded → `border-chart-green/30 bg-chart-green/5`, else `border-border hover:border-primary/30`; rows that are neither boarded nor reached get `opacity-60`.
   - Avatar circle h-9 w-9 rounded-full shrink-0: boarded → bg-chart-green text-white with UserCheck h-4; else bg-muted muted with UserX h-4.
   - Body: `name` text-sm font-medium truncate; `{grade} · {boarding_stop_name}` text-[10px] muted.
   - Status Badge variant=outline text-[10px]: boarded → "On Bus" (`border-chart-green text-chart-green`); else if activeRun: reached → "Waiting", not reached → "Stop not reached" (both border-border muted, slightly fainter when unreachable); no run → "Run not started".
   - onClick → Z(student.id, name.split(" ")[0]). No empty-state element when search yields nothing (list just renders empty).

## Offline behavior
Only the boarded Set survives reloads via localStorage (per-bus key). Supabase writes are optimistic with rollback on error — there is no queue/retry, so a failed network write reverts the tap. End Run clears both the key and in-memory set via the CustomEvent.

### Data sources

Reads: buses, students, routes+route_stops, runs, student_routes. Writes: students UPDATE status ("on-bus" on board, "at-school" on un-board) by id, with invalidation of ["students"] and ["my-students"]. localStorage key `driver-boarded-{busId}` (JSON array of student ids). Listens for window CustomEvent `driver-run-completed`. No edge functions, no realtime.

### Styling notes

chart-green = boarded affordance (tints /5, /10, /15 and solid avatar), destructive red for Remaining counter. Tap feedback via active:scale-[0.98]. shadcn Input with icon inset (pl-9). Badges all text-[10px] outline.

## `/driver/incident` — Report Incident

# Report Incident (`wH`, lines 56803–56937; type list `yH` 56786; Textarea `xS` 56777)

DriverLayout title="Report Incident". Local state only: `type` (string), `description` (string), `submitted` (bool), `submitting` (bool). No query hooks.

## Incident types (Select options, in order)
- breakdown → "Vehicle Breakdown"
- accident → "Road Accident"
- student → "Student Issue"
- traffic → "Heavy Traffic / Delay"
- other → "Other"

**There is NO severity field in the live app** — the form is exactly type + description.

## Submit flow (h)
1. Validation: if !type or !description.trim() → destructive toast "Please fill all fields", abort.
2. submitting=true. `we.auth.getUser()`; if no user throw "Not authenticated".
3. Parallel lookups: `we.from("profiles").select("full_name").eq("id", user.id).maybeSingle()` and `we.from("buses").select("id, name").eq("driver_id", user.id).maybeSingle()`.
4. `we.from("incidents").insert({ driver_id: user.id, driver_name: profile?.full_name ?? null, bus_id: bus?.id ?? null, bus_name: bus?.name ?? null, type, description: description.trim() })`. (No severity, no lat/lng, no run_id.)
5. Success: submitted=true; toast "Incident reported ✅" / "Admin has been notified". Catch: destructive toast "Failed to submit" + error.message. Finally submitting=false.
6. "Report Another" (f) resets type/description/submitted.

## Success screen (submitted=true)
Centered column `py-16 space-y-4`: circle `h-16 w-16 rounded-full bg-chart-green/15` containing CircleCheck `h-8 w-8 text-chart-green`; h2 font-heading font-bold text-lg "Report Submitted"; copy `text-sm muted text-center max-w-xs` "The school admin has been notified. They will follow up if needed."; Button variant=outline "Report Another".

## Form screen
1. **Info banner**: `flex items-center gap-2 p-3 rounded-lg bg-chart-amber/10 border border-chart-amber/20` — TriangleAlert `h-4 w-4 text-chart-amber shrink-0` + text-xs muted: "Use this form to report any safety concern or delay during the run."
2. **Card "New Incident Report"** (CardTitle text-sm font-heading; header p-4 pb-2; content p-4 pt-2 space-y-4):
   - Field "Incident Type" (label text-xs font-medium muted): shadcn Select, trigger placeholder "Select type...", items from the 5 types above.
   - Field "Description": Textarea (custom xS, min-h-[80px] standard shadcn styling) placeholder "Describe what happened...", rows=4, controlled.
   - Submit: Button `w-full gap-2` with Send icon h-4 w-4; label "Submitting..." while submitting else "Submit Report"; disabled while submitting.

Admin side consumes these rows via realtime channels ("incidents-admin" line 55797, "incidents-sidebar" 22837) — out of scope here but explains why no push/edge call is made on submit. Note the runs page also inserts incidents with type "arrival" for school-gate arrival notifications, so the incidents table doubles as an alerts feed.

### Data sources

Reads: supabase.auth.getUser(); profiles (full_name by id, maybeSingle); buses (id, name by driver_id, maybeSingle). Writes: incidents INSERT {driver_id, driver_name, bus_id, bus_name, type, description}. No react-query, no realtime, no edge functions on this page.

### Styling notes

chart-amber warning banner, chart-green success state. Uppercase micro-labels not used here; labels are text-xs font-medium muted. Full-width primary submit with Send icon. shadcn Select + custom Textarea matching shadcn input ring styles.

## Shared components/hooks in this section

## DriverLayout `In` (lines 55970–56029) + nav config `gH` (55952–55968)\nMobile app shell used by all 4 driver pages: `min-h-screen flex flex-col bg-background max-w-lg mx-auto border-x border-border`.\n- **Header** `h-14 flex items-center gap-3 border-b bg-card px-4 shrink-0`: optional back button when `showBack` prop (ArrowLeft h-5 w-5, `p-1 -ml-1 rounded-md hover:bg-muted`, navigate(-1)) — none of the 4 driver pages pass showBack; logo block `h-8 w-8 rounded-lg bg-primary` with Bus icon `h-4 w-4 text-primary-foreground`; `title` prop in h1 `font-heading font-semibold text-sm`; right-aligned sign-out button (LogOut icon h-4 w-4, `p-2 rounded-md hover:bg-muted text-muted-foreground`) → `await signOut(); navigate(\"/auth\")`.\n- **Main**: `flex-1 overflow-auto p-4` wrapping page children.\n- **Bottom nav** `h-16 border-t bg-card flex items-center justify-around shrink-0`, 4 react-router NavLinks (`end` only for \"/driver\"): Home(/driver, House icon), Run(/driver/run, MapPin), Board(/driver/boarding, Bus), Incident(/driver/incident, TriangleAlert). Link class: `flex flex-col items-center gap-0.5 px-3 py-1.5 rounded-lg text-muted-foreground transition-colors`, active adds `text-primary`; icon h-5 w-5, label `text-[10px] font-medium`.\nParent layout `Ii` (56956) is a structural twin (logo bg-accent instead of bg-primary) — keep them as one parameterized component when rebuilding if desired.\n\n## ProtectedRoute `lr` (18501–18539)\nOnly enforces anything when allowedRoles === [\"admin\"]: loading → skeleton screen; unauthenticated → redirect /auth; wrong role → redirect to role home (admin → \"/\", driver → \"/driver\", else \"/parent\"). For `[\"driver\"]`/`[\"parent\"]` it renders children unconditionally — driver routes have no client-side guard; data isolation must come from Supabase RLS. Routes registered 57899–57923 with allowedRoles [\"driver\"] for all four driver paths.\n\n## Data hooks (24110–24199, all react-query `useQuery` via `wr`)\n- `dn` useBuses → [\"buses\"], `buses.select(\"*\").order(\"name\")`. Bus rows carry: id, name, plate_number, driver_id, driver_name, current_lat, current_lng (+ more).\n- `da` useRoutes → [\"routes\"], `routes.select(\"*, route_stops(*)\")` ordered by name, stops sorted by stop_order client-side. route fields used: id, name, type (\"morning\"|\"afternoon\"), bus_id, school_id; route_stop fields used: id, name, scheduled_time, stop_order, lat, lng, is_school_gate, student_id.\n- `Ji` useStudents → [\"students\"], select * order name. Student fields used: id, name, grade, status (\"on-bus\"|\"at-school\"|\"dropped-off\"|waiting-ish default), bus_id, boarding_stop_id, boarding_stop_name.\n- `oi` useRuns → [\"runs\"], `runs.select(\"*, buses(name, plate_number), routes(name)\").order(\"date\", desc)`. Run fields: id, bus_id, route_id, school_id, type, status (\"in-progress\"/\"completed\"), total_stops, total_students, stops_completed, students_boarded, start_time, end_time, date(DB default).\n- `Xl` useStudentRoutes(studentId?) → [\"student-routes\", id??\"all\"], `student_routes.select(\"id, student_id, route_id\")` optional eq student_id.\n\n## RouteStopsMiniMap `Mp` (31532–31584)\nShared with admin /routes page. Leaflet (`pn`) static preview: 160px tall, all interaction disabled, OSM tile layer, numbered green divIcon circles (last stop = larger dark \"●\"), dashed polyline, fitBounds maxZoom 15. Props: `stops` (uses lat, lng, stop_order), optional className. Returns null when stops empty.\n\n## UI primitives (shadcn): `ot/ct/yn/wn` Card/CardContent/CardHeader/CardTitle, `De` Button, `Ot` Badge, `qt` Skeleton, `tt` Input (18540), `xS` Textarea (56777), `Nr/vr/Pr/_r/_t` Select/Trigger/Value/Content/Item, `Qt` useToast, `Et` useQueryClient, `Mn` useNavigate, `$0` NavLink, `_e` cn(). Icons (lucide): q1 House, _n MapPin, $r Bus, ea TriangleAlert, Ml LogOut, q0 ArrowLeft, G0 BusFront, LA CirclePlay, Bi Users, Mi Clock, Gi CircleCheck, JA Sun, VA Moon, GA Play, YA Square, ZA Send, K1 Search, X0 UserCheck, tN UserX.\n\n## Cross-page contracts\n- localStorage `driver-boarded-{busId}`: written by /driver/boarding, cleared by /driver/run End Run.\n- window CustomEvent `driver-run-completed` {detail:{busId}}: dispatched by End Run, consumed by boarding page to reset its set.\n- incidents table doubles as alert feed: boarding-run arrival rows (type \"arrival\") + driver incident reports; admin and parent pages subscribe via realtime channels.\n- Edge function `send-arrival-push` (only driver-side function call) — body {bus_id, title, body, url}.\n- No navigator.geolocation usage in app code (only inside Leaflet's unused locate()); the driver app never publishes GPS positions.

## Agent notes

All findings from /Users/andreanatali/Documents/GitHub/Safe Ride 1/safe-ride/.local/live-reference/app.pretty.js. Minified-name key: In=DriverLayout(55970), gH=driver nav items(55952), xH=DriverHome(56031), vH=DriverRun(56269), _H=DriverBoarding(56572), wH=DriverIncident(56803), yH=incident type list(56786), xS=Textarea(56777), Mp=RouteStopsMiniMap(31532), lr=ProtectedRoute(18501), hooks dn/da/Ji/oi/Xl at 24110-24199. Icon map confirmed via lucide factory calls (~lines 5133-6325). Notable: (1) the live incident form has NO severity field — only type + description; (2) driver pages never use navigator.geolocation — the only geolocation hits in the bundle are inside Leaflet's unused locate(); buses.current_lat/current_lng are read by admin fleet map and parent track but nothing in the driver app writes them; (3) ProtectedRoute only enforces auth/role for admin-only routes — for allowedRoles ["driver"] it renders children unconditionally (client-side driver routes are effectively ungated; security rests on RLS); (4) runs.students_boarded is inserted as 0 and never updated by the driver app; boarding only flips students.status; (5) offline-ish behavior is limited to localStorage persistence of the boarded set per bus.