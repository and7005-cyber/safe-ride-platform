# Admin dashboard (/) and Fleet Map

_Reverse-engineered from the live www.saferidekenya.com bundle (app.pretty.js in safe-ride/.local/live-reference/). Source of truth for alignment._

## `/` — Admin Dashboard

# Admin Dashboard (`/`)

Component `UD` (app.pretty.js:24450-24655). Wrapped in `ProtectedRoute` (`lr`, line 18501) with `allowedRoles: ["admin"]` (route registration at line 57833). Rendered inside the shared `AdminLayout` (`Fn`) — see sharedComponents.

## Page chrome
- AdminLayout header title: **"Dashboard"**.
- Header subtitle: today's date formatted `new Date().toLocaleDateString("en-US", { weekday: "long", day: "numeric", month: "long", year: "numeric" })` → e.g. "Thursday, June 12, 2026".

## Loading state
While EITHER the buses query (`useBuses`) or runs query (`useRuns`) is loading: AdminLayout with title "Dashboard", subtitle **"Loading..."**, body = `div.grid.grid-cols-2.lg:grid-cols-4.gap-4` containing 4 `Skeleton` components with `className="h-24 rounded-xl"`.

## Realtime: `dashboard-live` channel
On mount, subscribes to one Supabase channel `"dashboard-live"` with four `postgres_changes` listeners, all `{ event: "*", schema: "public" }`:
- table `students` → `queryClient.invalidateQueries({ queryKey: ["students"] })`
- table `buses` → invalidate `["buses"]`
- table `runs` → invalidate `["runs"]`
- table `incidents` → invalidate `["incidents-today-count"]`
Channel removed (`we.removeChannel`) on unmount. (Additionally the sidebar, always mounted, runs its own `incidents-sidebar` channel — see sharedComponents.)

## Stats computation — `useDashboardStats` (`jD`, line 24263)
Derives from `useBuses` (`dn`), `useStudents` (`Ji`), `useRuns` (`oi`), and `useIncidentsTodayCount` (`RD`):
- `today` = `new Date().toISOString().split("T")[0]` (UTC date string YYYY-MM-DD).
- `todaysRuns` = runs where `run.date === today`.
- `activeBuses` = size of a Set containing: ids of buses with `status === "active"` PLUS `bus_id` of today's runs with `status === "in-progress"` (when bus_id non-null).
- `delayedBuses` = size of Set: ids of buses with `status === "delayed"` PLUS `bus_id` of today's runs with `status === "delayed"`.
- `totalBuses` = buses.length (0 fallback). `totalStudents` = students.length.
- `studentsOnBus` = count of students with `status === "on-bus"`.
- `studentsWaiting` = hardcoded `0` (never shown). `absentStudents` = students with `status === "absent"` (computed, never shown on this page).
- `todayRuns` = todaysRuns.length. `incidentsToday` = result of incidents count query (default 0).

`useIncidentsTodayCount` (`RD`, line 24243): queryKey `["incidents-today-count"]`; `we.from("incidents").select("id", { count: "exact", head: true }).gte("created_at", <local midnight today as ISO>)`; returns `count ?? 0`; **`refetchInterval: 15000`** (polls every 15s).

## Layout
Body: `div.space-y-6` containing (1) stat-card grid, (2) two-card grid.

### 1. Stat cards — `div.grid.grid-cols-2.lg:grid-cols-4.gap-4`, four `StatCard` (`Bu`):
1. **"Active Buses"** — value `activeBuses`, subtitle `` `of ${totalBuses} total` ``, icon Bus, variant `success`.
2. **"Delayed"** — value `delayedBuses`, subtitle "buses behind schedule", icon TriangleAlert, variant `warning`.
3. **"Students on Bus"** — value `studentsOnBus`, subtitle `` `of ${totalStudents} enrolled` ``, icon Users, variant `default`.
4. **"Incidents Today"** — value `incidentsToday`, subtitle `` `across ${todayRuns} runs` ``, icon TriangleAlert, variant `destructive` when `incidentsToday > 0` else `default`.

StatCard (`Bu`, line 24392): Card > CardContent `p-5` > `flex items-start justify-between`. Left: title `p.text-xs.text-muted-foreground.font-medium.uppercase.tracking-wider`; value `p.text-2xl.font-heading.font-bold.mt-1`; optional subtitle `p.text-xs.text-muted-foreground.mt-0.5`. Right: `div.h-10.w-10.rounded-xl.flex.items-center.justify-center` tinted per variant — default `bg-primary/10 text-primary`, success `bg-success/10 text-success`, warning `bg-warning/10 text-warning`, destructive `bg-destructive/10 text-destructive`; icon `h-5 w-5`.

### 2. Two-card grid — `div.grid.lg:grid-cols-3.gap-6`

#### Active Runs card (Card with `lg:col-span-2`)
- CardHeader `pb-3`; CardTitle `text-sm font-heading font-semibold flex items-center gap-2`: Clock icon `h-4 w-4 text-primary` + " Active Runs".
- CardContent `space-y-3`. Data source: `activeRuns = runs.filter(r => r.status !== "completed")` — note: ALL non-completed runs from the full runs query (any date), ordered by `date` descending (query ordering).
- **Empty state** (no active runs): `div.py-8.text-center` with Clock icon `h-8 w-8 mx-auto text-muted-foreground/40 mb-2`, `p.text-sm.text-muted-foreground` "No active runs right now", `p.text-xs.text-muted-foreground/70.mt-1` "Runs will appear here once started".
- **Run row** (key = run.id): `div.flex.items-center.gap-4.p-3.rounded-lg.bg-muted/50.hover:bg-muted.transition-colors`:
  - Left `div.flex-1.min-w-0`: row `flex items-center gap-2` with bus name (`run.buses?.name`, `p.text-sm.font-medium.truncate`) + status badge `Ax` with status = `run.status === "delayed" ? "delayed" : "active"`. Below: route name (`run.routes?.name`, `p.text-xs.text-muted-foreground.mt-0.5`).
  - Middle `div.text-right.shrink-0`: `flex items-center gap-1.5` with MapPin icon `h-3 w-3 text-muted-foreground` + span `text-xs text-muted-foreground` "{stops_completed}/{total_stops} stops"; under it a Progress bar (`s2`) value = `total_stops > 0 ? stops_completed/total_stops*100 : 0`, `className="h-1.5 w-24 mt-1"`.
  - Right `div.text-right.shrink-0.hidden.sm:block` (hidden on mobile): `flex items-center gap-1.5` with Users icon `h-3 w-3 text-muted-foreground` + span `text-xs text-muted-foreground` "{students_boarded}/{total_students}".
- No click action on rows; no "view all" link.

#### Fleet Status card (1 column)
- CardHeader `pb-3`; CardTitle same style: Bus icon `h-4 w-4 text-primary` + " Fleet Status".
- CardContent `space-y-2`. Data: full buses list (ordered by name).
- **Empty state** (`!buses || buses.length === 0`): `div.py-6.text-center` Bus icon `h-8 w-8 mx-auto text-muted-foreground/40 mb-2` + `p.text-sm.text-muted-foreground` "No buses configured".
- **Bus row** (key = bus.id): `div.flex.items-center.justify-between.py-2.border-b.border-border/50.last:border-0`. Left: `p.text-sm.font-medium` bus.name; `p.text-[11px].text-muted-foreground` "{plate_number} · {driver_name}". Right: status badge `Ax` with raw `bus.status`.

### Status badge `Ax` (line 24432)
shadcn Badge `variant="outline"`, `text-[10px] font-medium` + per-status classes: active `bg-success/10 text-success border-success/20`; delayed `bg-warning/10 text-warning border-warning/20`; offline `bg-destructive/10 text-destructive border-destructive/20`; idle (and fallback) `bg-muted text-muted-foreground border-border`. When status === "active" prepends pulsing dot `span.mr-1.h-1.5.w-1.5.rounded-full.bg-success.animate-pulse-dot.inline-block`. Label = status with first letter capitalized.

## Mutations / toasts / dialogs
None on this page (read-only dashboard).

### Data sources

- `useBuses` (`dn`, 24110): queryKey `["buses"]` — `we.from("buses").select("*").order("name")`. Fields used: id, name, plate_number, driver_name, status.
- `useRuns` (`oi`, 24155): queryKey `["runs"]` — `we.from("runs").select("*, buses(name, plate_number), routes(name)").order("date", { ascending: false })`. Fields used: id, status, date, bus_id, stops_completed, total_stops, students_boarded, total_students, buses.name, routes.name.
- `useStudents` (`Ji`, 24141): queryKey `["students"]` — `we.from("students").select("*").order("name")`. Fields used: status ("on-bus", "absent").
- `useIncidentsTodayCount` (`RD`, 24243): queryKey `["incidents-today-count"]` — `we.from("incidents").select("id", { count: "exact", head: true }).gte("created_at", localMidnightISO)`; refetchInterval 15000.
- Realtime channel `"dashboard-live"`: postgres_changes event "*" on public.students/buses/runs/incidents → invalidates ["students"]/["buses"]/["runs"]/["incidents-today-count"] respectively.
- (Sidebar, always mounted) queryKey `["unread-alerts"]`: count of `incidents` where `acknowledged = false`; realtime channel `"incidents-sidebar"` invalidates it.

### Styling notes

shadcn/ui Card, CardHeader, CardTitle, CardContent, Badge (outline), Progress (Radix, track `bg-secondary rounded-full`, indicator `bg-primary transition-all` translated by `-${100-value}%`), Skeleton (`animate-pulse rounded-md bg-muted`). Lucide icons: Bus, TriangleAlert, Users, Clock, MapPin. Semantic color tokens: primary, success, warning, destructive, muted. Headings use `font-heading`. Stat grid is 2-col on mobile, 4-col on lg; main grid stacks to 1-col below lg with Active Runs spanning 2/3 at lg. Run rows: `bg-muted/50 hover:bg-muted` pills; the students-boarded column hides below `sm`.

## `/fleet-map` — Fleet Map

# Fleet Map (`/fleet-map`)

Component `HD` (app.pretty.js:29979-30052). `ProtectedRoute` `allowedRoles: ["admin"]` (line 57839). Rendered inside `AdminLayout` (`Fn`) with title **"Fleet Map"** and subtitle **"Live bus positions"**.

## Map technology
**Leaflet 1.9.4** (full library bundled inline, lines 24656-29942; imported as `pn` ≈ `L`). Tile layer: **OpenStreetMap raster tiles** `https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png` with attribution `&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a>`. No API key, no Mapbox/Google.

## Data
Single hook: `useBuses` (`dn`) — queryKey `["buses"]`, `we.from("buses").select("*").order("name")`. `positionedBuses = buses.filter(b => b.current_lat && b.current_lng)` (truthy check — lat/lng of exactly 0 would be excluded). **No realtime channel and no polling on this page** — despite the "Live bus positions" subtitle, data refreshes only via React Query defaults (window-focus refetch) or invalidations triggered while other pages/sidebar are mounted. Replicate as-is or improve consciously.

## Map initialization (useEffect, run once)
- `L.map(containerRef, { center: [-1.286389, 36.817223] /* Nairobi CBD */, zoom: 12, zoomControl: true })`.
- Adds OSM tile layer. Cleanup on unmount: `map.remove()`.

## Markers effect (re-runs every render — dep is the freshly-filtered array)
1. Removes all existing markers (`marker.remove()`), clears the marker ref array.
2. If no positioned buses → stop (empty Nairobi map remains).
3. For each positioned bus, `L.marker([current_lat, current_lng], { icon: busIcon(status) }).addTo(map)` and pushes to ref array.
4. Binds an HTML popup per marker (see below).
5. `map.fitBounds(L.latLngBounds(allPositions), { padding: [50, 50], maxZoom: 14 })` — so the map auto-fits all buses and re-fits on every data change/render.

### Custom marker icon — `busIcon` (`$D`, line 29951)
`L.divIcon` with `className: ""`, `iconSize: [36, 36]`, `iconAnchor: [18, 18]`, `popupAnchor: [0, -20]`. HTML: a 36×36px div, `border-radius: 10px`, `background: <statusColor>`, `border: 3px solid white`, `box-shadow: 0 2px 8px rgba(0,0,0,0.3)`, flex-centered, containing an inline white bus SVG (18×18, viewBox 0 0 24 24, stroke white, stroke-width 2.5, round caps/joins — the lucide "bus-front"-style paths: `M8 6v6`, `M15 6v6`, `M2 12h19.6`, body path `M18 18h3s.5-1.7.8-2.8...`, two wheel circles at (7,18) r2 and (16,18) r2, `M9 18h5`).
Status colors (`Nx`, line 29944): active `#1a6b44` (green), delayed `#d4910a` (amber), idle `#6b7280` (gray), offline `#dc2626` (red); unknown → idle gray.

### Marker popup (inline-styled HTML, min-width 180px, font-family system-ui,sans-serif)
- Bus name: `font-weight:600; font-size:14px; margin:0`.
- Plate number: `font-size:12px; color:#6b7280; margin:2px 0`.
- `<hr style="margin:6px 0;border-color:#e5e7eb"/>`.
- Three 12px rows, label in `#6b7280`: "Driver: {driver_name}", "Phone: {driver_phone}", "Capacity: {capacity} seats".
- Status pill: inline-block, `padding:2px 6px; border-radius:4px; font-size:10px; font-weight:500`; background/color by status — active `#dcfce7`/`#15803d`, delayed `#fef9c3`/`#a16207`, offline `#fee2e2`/`#dc2626`, else `#f3f4f6`/`#4b5563`. Label from map `zD` = { active: "Active", delayed: "Delayed", idle: "Idle", offline: "Offline" }, falling back to the raw status string.

## Page body — `div.space-y-4`
### 1. Bus legend chips row — `div.flex.flex-wrap.gap-2`
One outline Badge per bus in the FULL fleet (including buses without coordinates; key=bus.id): `text-xs` + status classes — active `bg-success/10 text-success border-success/20`, delayed `bg-warning/10 text-warning border-warning/20`, offline `bg-destructive/10 text-destructive border-destructive/20`, else (idle) `bg-muted text-muted-foreground border-border`. Each chip starts with a dot `span.mr-1.5.h-1.5.w-1.5.rounded-full.inline-block` colored `bg-success animate-pulse` (active) / `bg-warning` (delayed) / `bg-destructive` (offline) / `bg-muted-foreground` (idle), followed by the bus name. Chips are not clickable/filters — purely a legend. Renders nothing while buses are loading (no skeleton on this page).
### 2. Map card
`Card.overflow-hidden` > `CardContent.p-0` > map container `div` with `ref`, `className="h-[calc(100vh-260px)] min-h-[400px] w-full"`, inline `style={{ zIndex: 0 }}` (keeps Leaflet panes under the app header/dropdowns).

## States
- No explicit loading state (no isLoading usage): legend row empty, map shows default Nairobi view at zoom 12.
- No positioned buses: map stays at Nairobi center, no markers; legend may still show chips for buses lacking coordinates.
- No error UI.

## Actions / mutations
None — read-only. Only interactions: Leaflet pan/zoom (zoom control top-left), marker click → popup.

### Data sources

- `useBuses` (`dn`): queryKey `["buses"]` — `we.from("buses").select("*").order("name")`. Fields used: id, name, plate_number, driver_name, driver_phone, capacity, status, current_lat, current_lng.
- No realtime subscription on this page; no refetchInterval. Bus positions update only through query invalidation elsewhere (e.g. dashboard's `dashboard-live` channel while that page is mounted) or refetch-on-window-focus. Sidebar's `incidents-sidebar` channel runs concurrently (unread alerts badge only).

### Styling notes

Leaflet 1.9.4 + OSM tiles; custom L.divIcon markers (36px rounded-square colored by status, white border, shadow, white bus SVG); popups are raw inline-styled HTML with hex colors (NOT tailwind tokens): green #1a6b44/#dcfce7/#15803d, amber #d4910a/#fef9c3/#a16207, red #dc2626/#fee2e2, gray #6b7280/#f3f4f6/#4b5563. Page UI uses shadcn Badge (outline) with semantic tokens success/warning/destructive/muted; active-status dots use `animate-pulse`. Map height responsive: `calc(100vh-260px)` with 400px minimum. Remember to include Leaflet CSS for proper tile/marker rendering.

## Shared components/hooks in this section

## Shared layout & navigation (used by both pages)

### `AdminLayout` (`Fn`, app.pretty.js:24038)
Props: `{ children, title, subtitle }`. Structure: `SidebarProvider` (`Gw`) > `div.min-h-screen.flex.w-full` > [`AdminSidebar` (`pF`), right column `div.flex-1.flex.flex-col.min-w-0`].
- **Header**: `header.h-14.flex.items-center.justify-between.border-b.bg-card.px-4`. Left: `SidebarTrigger` (`Zw`) + `h2.text-base.font-heading.font-semibold` (title) + optional `p.text-xs.text-muted-foreground` (subtitle). Right (`flex items-center gap-2`):
  - Bell button: `button.relative.p-2.rounded-lg.hover:bg-muted.transition-colors` with Bell icon `h-4 w-4 text-muted-foreground` — **no onClick handler, no badge** (decorative only).
  - Avatar dropdown (Radix DropdownMenu): trigger = `button.h-8.w-8.rounded-full.bg-primary.flex.items-center.justify-center.text-primary-foreground.text-xs.font-semibold.hover:opacity-90` showing initials — from `user.user_metadata.full_name` (first letter of each word, max 2, uppercased), else first 2 chars of email uppercased, else "??". Content `align="end"`: a disabled item (`text-xs text-muted-foreground`) showing the user email, then a destructive item with LogOut icon `h-3.5 w-3.5 mr-2` labelled " Sign Out" → `await signOut(); navigate("/auth")`.
- **Main**: `main.flex-1.p-4.md:p-6.overflow-auto` containing children.

### `AdminSidebar` (`pF`, 22811)
shadcn sidebar, `collapsible="icon"` (CSS vars: expanded 16rem, icon-collapsed 3rem, mobile sheet 18rem; provider also wires cookie persistence `sidebar:state` and Ctrl/Cmd+B toggle — standard shadcn implementation at 22290-22760).
- **Header** (`p-4`): `div.flex.items-center.gap-3`: logo tile `div.h-9.w-9.rounded-lg.bg-sidebar-primary` with Shield icon `h-5 w-5 text-sidebar-primary-foreground`; when expanded: `h1.text-sm.font-bold.font-heading` "SafeRide" + `p.text-[10px].uppercase.tracking-widest.text-sidebar-foreground/60` "Kenya".
- **Group label**: "Management" (`text-sidebar-foreground/50 text-[10px] uppercase tracking-widest`).
- **Menu items** (array `fF`, 22765; NavLink with `end` only for "/"; classes `hover:bg-sidebar-accent/50`, active `bg-sidebar-accent text-sidebar-primary font-medium`; icon `mr-2 h-4 w-4`; label hidden when collapsed):
  1. Dashboard `/` — LayoutDashboard
  2. Fleet Map `/fleet-map` — MapPin
  3. Buses `/buses` — Bus
  4. Routes `/routes` — MapPin
  5. Students `/students` — Users
  6. Run History `/runs` — Clock
  7. Schools `/schools` — School
  8. Parent Assignments `/parent-assignments` — UserCheck
  9. Parents `/parents` — UserCog
  10. Drivers `/drivers` — UserPlus
  11. Alerts `/alerts` — Bell, plus **unread badge** when count > 0: `Badge.ml-auto.h-5.min-w-5.px-1.5.text-[10px].bg-destructive.text-destructive-foreground` showing the count.
- **Unread alerts query**: queryKey `["unread-alerts"]` — `we.from("incidents").select("id", { count: "exact", head: true }).eq("acknowledged", false)`; returns 0 on error. Realtime channel `"incidents-sidebar"` (postgres_changes `*` on public.incidents) invalidates it; removed on unmount.
- **Footer** (`p-4 space-y-2`): when expanded, school card `div.rounded-lg.bg-sidebar-accent/50.p-3`: label "School" (`text-[10px] uppercase tracking-wider text-sidebar-foreground/50`), hardcoded "Greenfield Academy" (`text-sm font-medium truncate`), "Beta Programme" (`text-[10px] text-sidebar-foreground/40`). Below: Sign Out button (`flex items-center gap-2 w-full rounded-md px-3 py-2 text-sm text-sidebar-foreground/70 hover:bg-sidebar-accent/30`) with LogOut icon `h-4 w-4`, label hidden when collapsed → `await signOut(); navigate("/auth")`.

### `ProtectedRoute` (`lr`, 18501)
Props `{ children, allowedRoles }`. For admin-only routes (`allowedRoles.length === 1 && allowedRoles[0] === "admin"`): while auth `loading` → full-screen centered skeleton stack (`div.space-y-4.w-64` with Skeletons h-8 w-full / h-4 w-3/4 / h-4 w-1/2); no user → `<Navigate to="/auth" replace>`; user with a role NOT in allowedRoles → redirect by role (admin→"/", driver→"/driver", else "/parent"); user with role still null renders children. NOTE: for any other allowedRoles value it renders children unconditionally (gate only enforced for the admin case).

## Shared primitives / hooks
- `we` = Supabase client; `wr` = useQuery; `Dt` = useMutation; `Et` = useQueryClient; `Qt` = useToast; QueryClient created with default options (line 57820).
- `Bu` StatCard and `Ax` status badge (defined at 24392/24432, used by dashboard; a non-pulsing twin `A3` exists at 31148 for the Buses table).
- `qt` Skeleton (18491), `s2` Progress (24375), `ot/yn/wn/ct` = Card/CardHeader/CardTitle/CardContent, `Ot` = Badge, `tt` = Input, `De` = Button.
- Data hooks cluster at 24110-24260: `dn` useBuses, `da` useRoutes (routes + ordered route_stops), `Ji` useStudents, `oi` useRuns, `Ip` useSchools, `Xl` useStudentRoutes, `PD` useParentStudents, `Lp` useMyStudents, `RD` useIncidentsTodayCount, `jD` useDashboardStats.
- Icon map (lucide): `$r`=Bus, `ea`=TriangleAlert, `Bi`=Users, `Mi`=Clock, `_n`=MapPin, `Dl`=Bell, `HA`=LayoutDashboard, `XA`=Shield, `Ml`=LogOut, `G1`=School, `X0`=UserCheck, `eN`=UserCog, `Z1`=UserPlus, `K1`=Search.
- Leaflet exposed as `pn` (≈ `L`) from bundled Leaflet 1.9.4 (24656-29942); fleet-map marker icon factory `$D` (29951), status hex colors `Nx` (29944), popup status labels `zD` (29972).

## Agent notes

Scope covered: UD (admin dashboard, lines 24450-24655 — the rest of the 24450-29979 range is the bundled Leaflet 1.9.4 library source) and HD (/fleet-map, lines 29979-30052), plus their supporting hooks (lines 24038-24448), marker icon factory (29943-29977), shared admin layout Fn (24038) and sidebar pF (22811), and route registration (57833-57845). Map library is Leaflet 1.9.4 with raw OpenStreetMap tiles (no Mapbox/Google, no API key). Noteworthy quirks to preserve or consciously fix when rebuilding: (1) /fleet-map has NO realtime subscription and no refetch interval — "Live bus positions" updates only via React Query window-focus refetch or cache invalidation from other mounted pages; (2) marker rebuild + fitBounds re-runs on every render because the filtered bus array is a new reference each render; (3) dashboard "today" uses UTC date (toISOString) for run filtering but LOCAL midnight for the incidents count; (4) header bell button has no onClick and no badge (decorative); (5) sidebar school card is hardcoded "Greenfield Academy / Beta Programme"; (6) studentsWaiting is hardcoded 0 and absentStudents is computed but never displayed.