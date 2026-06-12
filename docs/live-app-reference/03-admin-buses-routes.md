# Admin Buses and Routes pages

_Reverse-engineered from the live www.saferidekenya.com bundle (app.pretty.js in safe-ride/.local/live-reference/). Source of truth for alignment._

## `/buses` — Buses (Fleet Management)

# Buses Page (`N3`, lines 31164-31327)

Wrapped in the shared admin layout `Fn` with `title: "Buses"`, `subtitle: "Manage your fleet"`. Content root: `div.space-y-4`.

## Page state
- `formOpen` (bool) — add/edit dialog
- `editingBus` (bus | null) — null = add mode
- `deletingBus` (bus | null) — delete confirm dialog
- `search` (string), `statusFilter` (string, default `"all"`)
- `filtered` = `useMemo`: client-side filter of all buses. Search is case-insensitive substring match against `name`, `plate_number`, OR `driver_name`. Status filter: `statusFilter === "all" || bus.status === statusFilter`. Returns `[]` while data undefined.

## Toolbar row
`div.flex.flex-col.sm:flex-row.justify-between.gap-3` containing:
1. **SearchFilterBar** (`xc`, shared — see sharedComponents) with `searchPlaceholder: "Search buses, plates, drivers..."` and one filter: placeholder `"All statuses"`, options `[{active, "Active"}, {delayed, "Delayed"}, {idle, "Idle"}, {offline, "Offline"}]`.
2. **Add Bus button**: `Button size="sm" className="gap-1.5 shrink-0"`, Plus icon (`h-3.5 w-3.5`), text `" Add Bus"`. onClick: set editingBus=null, open form dialog.

## Count line
`<p className="text-xs text-muted-foreground">{filtered.length} of {(buses?.length ?? 0)} buses</p>`

## Table card
`Card > CardContent className="p-0 overflow-x-auto"`.
- **Loading state**: `div.p-4.space-y-3` with 4 `Skeleton className="h-10 w-full"`.
- **Empty state** (no rows after filtering): `div.p-8.text-center.text-sm.text-muted-foreground` → text `"No buses match your filters"`.
- **Table** (shadcn Table/TableHeader/TableBody/TableRow/TableHead/TableCell). Header columns in order:
  1. `Bus Name`
  2. `Plate Number`
  3. `Driver`
  4. `Phone` — `className="hidden md:table-cell"` (desktop only)
  5. `Capacity` — `className="hidden md:table-cell"` (desktop only)
  6. `Status`
  7. (actions, no label) — `className="w-10"`
- **Rows** (`TableRow className="hover:bg-muted/50"`, keyed by bus.id):
  1. Bus Name cell: `div.flex.items-center.gap-2` → icon chip `div.h-8.w-8.rounded-lg.bg-primary/10.flex.items-center.justify-center.shrink-0` containing lucide **Bus** icon `h-4 w-4 text-primary`; then `<span className="font-medium text-sm">{name}</span>`
  2. Plate: `className="text-sm text-muted-foreground font-mono"`
  3. Driver: `className="text-sm"` → `driver_name`
  4. Phone: `hidden md:table-cell text-sm text-muted-foreground` → `driver_phone`
  5. Capacity: `hidden md:table-cell text-sm text-muted-foreground` → `"{capacity} seats"`
  6. Status: `BusStatusBadge` (`A3`, see below)
  7. Actions: DropdownMenu; trigger = ghost icon Button `h-8 w-8` with lucide **Ellipsis** (horizontal …) `h-4 w-4`; content `align="end"` with items: `Edit` (Pencil icon `h-3.5 w-3.5 mr-2`; opens form dialog with bus) and `Delete` (`className="text-destructive"`, Trash2 icon `h-3.5 w-3.5 mr-2`; sets deletingBus).

## Status badge (`A3`)
`Badge variant="outline" className={"text-[10px] " + map[status]}` with label = status with first letter uppercased (Active/Delayed/Offline/Idle). Color map (falls back to idle style for unknown):
- `active`: `bg-success/10 text-success border-success/20`
- `delayed`: `bg-warning/10 text-warning border-warning/20`
- `offline`: `bg-destructive/10 text-destructive border-destructive/20`
- `idle`: `bg-muted text-muted-foreground border-border`

## Add/Edit Bus dialog (`k3`, lines 30800-31052)
Dialog `DialogContent className="sm:max-w-md"`. Title (`className="font-heading"`): `"Edit Bus"` if editing else `"Add Bus"`. Form `className="space-y-4"`, fields all in `div.space-y-1.5` groups:
1. Row `grid grid-cols-2 gap-3`:
   - **Bus Name** (Label htmlFor="name"): Input, `required`, `maxLength: 100`
   - **Plate Number** (htmlFor="plate"): Input, `required`, `maxLength: 20`
2. **Assign Driver** Select: value = `driver_id || "none"`; trigger placeholder `"Select a driver..."`; options: first item value `"none"` label `"— No driver —"`, then drivers from the `admin-manage-drivers` edge function list (label `full_name || "Unnamed driver"`, value = driver id). On change: choosing "none" clears driver_id/driver_name/driver_phone; choosing a driver sets driver_id and auto-fills driver_name=profile.full_name, driver_phone=profile.phone.
3. Row `grid grid-cols-2 gap-3`:
   - **Driver Name** (htmlFor="driver"): Input, `required`, `maxLength: 100`, `readOnly` when a driver is selected (`!!driver_id`) with `className="bg-muted"`; when readOnly, helper text below: `<p className="text-[11px] text-muted-foreground">Synced from the selected driver's profile.</p>`
   - **Driver Phone** (htmlFor="phone"): Input, `required`, `maxLength: 20`, same readOnly/bg-muted behavior when driver selected.
4. Row `grid grid-cols-2 gap-3`:
   - **Capacity** (htmlFor="capacity"): Input `type="number" min={1} max={100}`, `required`; onChange `parseInt(value) || 45` (falls back to 45 on NaN); default 45 for new bus.
   - **Status** Select (no placeholder, empty SelectValue): options `idle→"Idle"`, `active→"Active"`, `delayed→"Delayed"`, `offline→"Offline"`; default `"idle"` for new bus.
5. DialogFooter: `Cancel` (Button type=button variant=outline, closes dialog) and submit Button disabled while create/update pending, label: pending → `"Saving..."`, else editing → `"Update"` else `"Create"`.

Form state is re-synced via `useEffect` on `[bus, driversList]`: if bus has driver_id and a matching driver profile is found, driver_name/driver_phone prefer the profile values (`full_name`/`phone`) over the stored bus columns.

**Submit**: payload `{name, plate_number, driver_name, driver_phone, capacity, status, driver_id: driver_id || null}`. Edit → update mutation with `{id, ...payload}`, toast `"Bus updated"`; Add → insert mutation, toast `"Bus created"`. On error: toast `{title: "Error", description: err.message, variant: "destructive"}`. Closes dialog on success.

## Delete Bus dialog (`C3`, lines 31054-31096)
AlertDialog. Title: `Delete "{busName}"?`. Description: `"This will permanently remove this bus. This action cannot be undone."`. Footer: `Cancel` (AlertDialogCancel) + `Delete` action with `className="bg-destructive text-destructive-foreground hover:bg-destructive/90"`, label `"Deleting..."` while pending else `"Delete"`. On confirm: delete mutation by busId, toast `"Bus deleted"`, close; error → destructive Error toast. Dialog is conditionally rendered only when `deletingBus` is set; `onOpenChange: open => !open && setDeletingBus(null)`.

### Data sources

- `useBuses` (`dn`, line 24110): queryKey `["buses"]` → `we.from("buses").select("*").order("name")`. Bus columns used: id, name, plate_number, driver_id, driver_name, driver_phone, capacity, status.
- `useAdminDriversList` (`T3`, line 30782): queryKey `["admin-drivers-list"]` → `we.functions.invoke("admin-manage-drivers", { body: { action: "list" } })`; returns driver profiles with `id`, `full_name`, `phone`. Used to populate the Assign Driver select in the bus form.
- Mutations (all invalidate `["buses"]` on success):
  - create (`o3`): `we.from("buses").insert(payload).select().single()`
  - update (`c3`): `we.from("buses").update(rest).eq("id", id).select().single()`
  - delete (`l3`): `we.from("buses").delete().eq("id", id)`
- No realtime subscriptions on this page; filtering/search is fully client-side.

### Styling notes

shadcn/ui components throughout (Card, Table, Badge variant outline, Dialog sm:max-w-md, AlertDialog, DropdownMenu, Select, Input, Label, Button, Skeleton). Lucide icons: Bus ($r), Plus (Zi), Ellipsis horizontal (uc) for row menu, Pencil (Ki), Trash2 (oa), Search (K1), X (zd). Status badges use semantic tokens success/warning/destructive/muted at /10 background + /20 border, text-[10px]. Table card uses `p-0 overflow-x-auto` so the table scrolls horizontally on small screens; Phone and Capacity columns hidden below `md`. Dialog titles use `font-heading`. Page content spacing `space-y-4`.

## `/routes` — Routes (Route & Stops Management)

# Routes Page (`j3`, lines 31586-31727)

Wrapped in admin layout `Fn` with `title: "Routes"`, `subtitle: "Manage bus routes and stops"`. Content root: `div.space-y-4`.

## Page state
- `formOpen` (bool), `editingRoute` (route|null), `deletingRoute` (route|null). No search/filter bar on this page.

## Header row
`div.flex.justify-between.items-center`:
- Left: `<p className="text-sm text-muted-foreground">{routes?.length ?? 0} routes configured</p>`
- Right: **Add Route** button: `Button size="sm" className="gap-1.5"`, Plus icon `h-3.5 w-3.5`, text `" Add Route"`. Opens form dialog with route=null.

## Loading state
`div.grid.md:grid-cols-2.gap-4` with 4 `Skeleton className="h-40 rounded-xl"`.

## Route cards grid
`div.grid.md:grid-cols-2.gap-4`; one Card per route (`className="hover:shadow-md transition-shadow"`, `CardContent className="p-5"`, keyed by route.id). For each route the page looks up `bus = buses?.find(b => b.id === route.bus_id)` and `school = schools?.find(s => s.id === route.school_id)`.

NOTE: there is NO dedicated empty state for zero routes — the grid simply renders nothing and the header reads "0 routes configured".

### Card header (`div.flex.items-start.justify-between.mb-3`)
- Left block:
  - `<h3 className="font-heading font-semibold text-sm">{route.name}</h3>`
  - `<p className="text-xs text-muted-foreground mt-0.5">{bus?.name} · {bus?.plate_number}{school ? ` · ${school.name}` : ""}</p>` (literal children array `[bus?.name, " · ", bus?.plate_number, school ? " · school.name" : ""]` — when no bus assigned it renders just the " · " separator with blanks).
- Right block `div.flex.items-center.gap-1`:
  - **Type badge**: `Badge variant="outline" className={"text-[10px] " + (type==="morning" ? "bg-primary/10 text-primary border-primary/20" : "bg-accent/10 text-accent-foreground border-accent/20")}`; label `"🌅 Morning"` or `"🌇 Afternoon"`.
  - **DropdownMenu**: trigger ghost icon Button `h-7 w-7` with lucide **EllipsisVertical** `h-3.5 w-3.5`; content `align="end"`: `Edit Route` (Pencil `h-3.5 w-3.5 mr-2`) and `Delete Route` (`className="text-destructive"`, Trash2 `h-3.5 w-3.5 mr-2`).

### Mini route map (`Mp`, lines 31532-31584)
Rendered with `stops={route.route_stops}` and `className="mb-3"`. Returns `null` when `stops.length === 0` (no map shown). Otherwise a `div` with `className="w-full rounded-lg border border-border overflow-hidden mb-3"` and inline `style={{height:160}}` hosting a raw Leaflet map (imperatively created in useEffect, recreated whenever `stops` changes):
- Map options: `zoomControl:false, attributionControl:false, dragging:false, scrollWheelZoom:false, doubleClickZoom:false, touchZoom:false` (fully static/non-interactive).
- Tile layer: `https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png`.
- One divIcon marker per stop at `[stop.lat, stop.lng]`, `interactive:false`:
  - Regular stop: 18px circle, background `hsl(152,55%,45%)`, 2px white border, `box-shadow:0 1px 4px rgba(0,0,0,0.3)`, white bold 8px text showing `stop_order`.
  - LAST stop in the array (school gate / terminus): 22px circle, background `hsl(152,55%,28%)`, white "●" glyph at 10px, iconAnchor centered.
- If >1 stop: dashed polyline through all stop coords: `color:"hsl(152,55%,28%)", weight:3, opacity:0.6, dashArray:"6 4"`.
- `fitBounds(allStops, {padding:[20,20], maxZoom:15})`.

### Stops list (below map)
If `route.route_stops.length > 0`: `div.space-y-1.5`, one row per stop (already sorted ascending by `stop_order` in the query hook), keyed by stop.id, each `div.flex.items-center.gap-2.text-xs`:
1. Marker bubble (`div.flex.items-center.justify-center.shrink-0`):
   - Last stop: `div.h-5.w-5.rounded-full.bg-primary.flex.items-center.justify-center` containing lucide **MapPin** `h-3 w-3 text-primary-foreground`.
   - Other stops: `div.h-5.w-5.rounded-full.border-2.border-primary/30.flex.items-center.justify-center.text-[9px].font-bold.text-primary` containing `stop_order`.
2. `<span className="text-muted-foreground">{stop.scheduled_time}</span>`
3. lucide **ArrowRight** `h-2.5 w-2.5 text-muted-foreground/50`
4. `<span className="font-medium flex-1 truncate">` → if `stop.is_school_gate` show `stop.name`; otherwise show the matched student's `home_address` (lookup `students?.find(s => s.id === stop.student_id)?.home_address`) with fallback string `"Address not set"`.

### Per-card empty state (the known "match routes empty state" item)
When `route.route_stops.length === 0`, render EXACTLY:
`<p className="text-xs text-muted-foreground italic py-3">No students assigned to this route yet. Add students with home addresses to see pickups here.</p>`
(plain left-aligned italic paragraph inside the card, no icon, no centering — the map is also absent since `Mp` returns null with 0 stops).

## Add/Edit Route dialog (`P3`, lines 31329-31486)
Dialog `DialogContent className="sm:max-w-md"`. Title `font-heading`: `"Edit Route"` / `"Add Route"`. Form `space-y-4`:
1. **Route Name** (Label htmlFor="routeName"): Input `required`, `maxLength: 100`.
2. Row `grid grid-cols-2 gap-3`:
   - **Type** Select: options `morning → "🌅 Morning"`, `afternoon → "🌇 Afternoon"`; default `"morning"` for new route. No placeholder (empty SelectValue).
   - **School** Select: placeholder `"Select school"`; options = all schools (label `school.name`, value id). Not required at HTML level.
3. **Assigned Bus** Select: placeholder `"Select bus"`; options = all buses with label `{bus.name} ({bus.plate_number})`, value id. Not required.
4. DialogFooter: `Cancel` (outline, closes) + submit (disabled while pending; `"Saving..."` / `"Update"` / `"Create"`).

Form state initialized once from `route` prop: `{name: route?.name ?? "", type: route?.type ?? "morning", bus_id: route?.bus_id ?? "", school_id: route?.school_id ?? ""}` (no useEffect resync — the dialog component is remounted per open in practice).

**Submit**: payload = form values with `bus_id: bus_id || null` and `school_id: school_id || null` (empty selects stored as NULL). Edit → update with `{id, ...payload}`, toast `"Route updated"`; Add → insert, toast `"Route created"`. Error → destructive `"Error"` toast with message. Closes on success.

**Important**: there is NO stops editing UI anywhere in the admin Routes page. `route_stops` are read-only here — they are derived from students assigned to routes (student form assigns morning/afternoon route ids via `student_routes`; stop generation happens elsewhere/backend). The route form only edits name/type/school/bus.

## Delete Route dialog (`R3`, lines 31488-31530)
AlertDialog. Title: `Delete "{routeName}"?`. Description: `"This will permanently remove this route and all its stops. This action cannot be undone."`. Footer: `Cancel` + `Delete` action (`bg-destructive text-destructive-foreground hover:bg-destructive/90`, `"Deleting..."` while pending). On confirm: delete mutation by routeId, toast `"Route deleted"`; error → destructive toast. Rendered only when `deletingRoute` set; `onOpenChange: open => !open && setDeletingRoute(null)`.

### Data sources

- `useRoutes` (`da`, line 24124): queryKey `["routes"]` → `we.from("routes").select("*, route_stops(*)").order("name")`, then sorts each route's `route_stops` ascending by `stop_order`. Route columns used: id, name, type ('morning'|'afternoon'), bus_id, school_id. route_stops columns used: id, stop_order, scheduled_time, is_school_gate, name, student_id, lat, lng.
- `useBuses` (`dn`): `["buses"]` → `we.from("buses").select("*").order("name")` — for bus name/plate in card subtitle and the Assigned Bus select.
- `useSchools` (`Ip`, line 24171): `["schools"]` → `we.from("schools").select("*").order("name")` — for school name in card subtitle and the School select.
- `useStudents` (`Ji`, line 24141): `["students"]` → `we.from("students").select("*").order("name")` — used only to resolve a non-school-gate stop's label to the student's `home_address`.
- Mutations (all invalidate `["routes"]`):
  - create (`u3`): `we.from("routes").insert(payload).select().single()`
  - update (`d3`): `we.from("routes").update(rest).eq("id", id).select().single()`
  - delete (`h3`): `we.from("routes").delete().eq("id", id)` (DB cascade removes stops per the dialog copy)
- No realtime subscriptions. Map tiles from openstreetmap.org via Leaflet (no API key).

### Styling notes

Two-column responsive card grid (`grid md:grid-cols-2 gap-4`), single column on mobile. Brand green used directly in Leaflet HTML markers as `hsl(152,55%,28%)` (primary) and `hsl(152,55%,45%)` (lighter); everything else uses theme tokens (primary, accent, muted, destructive, border). Morning badge = primary tint, Afternoon badge = accent tint, both `text-[10px]` outline badges with emoji prefixes. Card menu trigger is smaller than buses page (h-7 w-7 with EllipsisVertical) vs buses table (h-8 w-8 with horizontal Ellipsis). Mini-map fixed 160px height, `rounded-lg border border-border overflow-hidden`. Stop rows are `text-xs` with timeline-style numbered circles; terminus uses filled `bg-primary` circle with MapPin icon. Headings use `font-heading`.

## Shared components/hooks in this section

## Shared components/hooks discovered in this section (absolute file: /Users/andreanatali/Documents/GitHub/Safe Ride 1/safe-ride/.local/live-reference/app.pretty.js)

- **`Fn` — AdminLayout** (~line 24100 region): shell with sidebar + header taking `title`/`subtitle` props; page content rendered in `<main className=\"flex-1 p-4 md:p-6 overflow-auto\">`. Used by both /buses and /routes.

- **`xc` — SearchFilterBar** (lines 31098-31146): reusable toolbar. Props: `search`, `onSearchChange`, `searchPlaceholder` (default `\"Search...\"`), `filters` (array of `{value, onChange, placeholder, options:[{value,label}]}`). Layout: `div.flex.flex-wrap.items-center.gap-2`; search box `relative flex-1 min-w-[200px] max-w-sm` with lucide Search icon absolutely positioned (`left-2.5 top-1/2 -translate-y-1/2 h-3.5 w-3.5 text-muted-foreground`) and Input `className=\"pl-8 h-9 text-sm\"`. Each filter is a Select with trigger `className=\"h-9 w-auto min-w-[130px] text-sm\"`; first item is always `value:\"all\"` labeled with the filter's placeholder text, followed by the options. A trailing **Clear** button (`Button variant=\"ghost\" size=\"sm\" className=\"h-9 px-2 text-xs text-muted-foreground\"`, X icon `h-3 w-3 mr-1`, text `\" Clear\"`) appears only when `search` is non-empty OR any filter value !== \"all\"; clicking resets search to \"\" and every filter to \"all\". Defined right before N3 — used by /buses (and other admin list pages).\n\n- **`A3` — BusStatusBadge** (lines 31148-31162): outline Badge, `text-[10px]`, capitalized status label; color classes: active=success/10+success+success/20, delayed=warning variant, offline=destructive variant, idle=`bg-muted text-muted-foreground border-border` (also the fallback).\n\n- **`Mp` — RouteStopsMiniMap** (lines 31532-31584): static (non-interactive) Leaflet preview of a route's stops with numbered green divIcon markers, larger dark terminus dot, dashed polyline, fitBounds; OSM tile layer; 160px tall, `rounded-lg border border-border overflow-hidden`; returns null for 0 stops. Reused anywhere stops need previewing.\n\n- **Query hooks** (lines 24110-24199): `dn` useBuses `[\"buses\"]`; `da` useRoutes `[\"routes\"]` with nested `route_stops(*)` sorted by stop_order; `Ji` useStudents `[\"students\"]`; `Ip` useSchools `[\"schools\"]`; `Xl` useStudentRoutes `[\"student-routes\", studentId|\"all\"]` on `student_routes(id, student_id, route_id)`.\n\n- **Mutation hooks** (lines 30440-30542): bus create/update/delete (`o3`/`c3`/`l3`) and route create/update/delete (`u3`/`d3`/`h3`) — straightforward supabase insert/update/delete with query invalidation, no optimistic updates.\n\n- **`T3` useAdminDriversList** (line 30782): `[\"admin-drivers-list\"]` → edge function `admin-manage-drivers` with body `{action: \"list\"}`; returns profiles `{id, full_name, phone}`; feeds the bus form's Assign Driver select.\n\n- Lucide icon mapping confirmed: `Zi`=Plus, `$r`=Bus, `uc`=Ellipsis (horizontal), `BA`=EllipsisVertical, `Ki`=Pencil, `oa`=Trash2, `K1`=Search, `zd`=X, `AA`=ArrowRight, `_n`=MapPin, `G1`=School.\n\n- Toast pattern everywhere: success `toast({title: \"<Entity> created|updated|deleted\"})`; failure `toast({title: \"Error\", description: err.message, variant: \"destructive\"})`.

## Agent notes

Scope covered: /buses (N3, 31164-31327) incl. pre-N3 dialogs k3 (bus form, 30800-31052) and C3 (bus delete, 31054-31096); /routes (j3, 31586-31727) incl. P3 (route form, 31329-31486), R3 (route delete, 31488-31530), Mp (mini map, 31532-31584). Routes page code ends at line 31727; everything after (O3 onward) is react-leaflet/vendor + students page code, out of scope. Key findings: (1) The \"match routes empty state\" is the per-card stops empty state — exact markup: <p class=\"text-xs text-muted-foreground italic py-3\">No students assigned to this route yet. Add students with home addresses to see pickups here.</p>; there is NO zero-routes empty state (grid renders empty, header shows \"0 routes configured\"). (2) Stops are read-only on the routes page — no stops editor exists; route_stops derive from student-route assignments. (3) Bus form's driver select pulls from the admin-manage-drivers edge function and locks driver name/phone fields (readOnly + bg-muted + \"Synced from the selected driver's profile.\") when a registered driver is chosen. (4) Buses page filtering is fully client-side (search over name/plate/driver_name + status select). Bundle path: /Users/andreanatali/Documents/GitHub/Safe Ride 1/safe-ride/.local/live-reference/app.pretty.js.