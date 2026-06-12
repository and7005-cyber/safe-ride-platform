# Admin Students, Runs, Schools, Parent Assignments

_Reverse-engineered from the live www.saferidekenya.com bundle (app.pretty.js in safe-ride/.local/live-reference/). Source of truth for alignment._

## `/students` — Students (Admin)

# Students Page (`rz`, lines 52894-53121)

Wrapped in AdminLayout (`Fn`) with `title="Students"`, `subtitle="Manage student profiles and assignments"`.

## Layout (top to bottom, inside `space-y-4`)
1. **Toolbar row** — `flex flex-col sm:flex-row justify-between gap-3`:
   - **SearchFilterBar** (`xc`): search input placeholder `"Search students, parents, grades..."` + 2 select filters:
     - Status filter, placeholder `"All statuses"`, options: `on-bus` "On Bus", `at-school` "At School", `absent` "Absent", `dropped-off` "Dropped Off" (plus implicit "all" item rendered with the placeholder label).
     - Bus filter, placeholder `"All buses"`, options dynamically built from buses query (`value=bus.id`, `label=bus.name`).
     - When search non-empty or any filter ≠ "all", a ghost "Clear" button (X icon) resets search to "" and all filters to "all".
   - **Buttons** (`flex gap-2 shrink-0`):
     - `Bulk Upload` — size sm, variant outline, gap-1.5, Upload icon h-3.5 w-3.5. Opens BulkUploadStudentsDialog.
     - `Add Student` — size sm (default/primary), Plus icon h-3.5 w-3.5. Sets editing student to null and opens StudentFormDialog.
2. **Count line**: `text-xs text-muted-foreground` — `"{filtered.length} of {students.length} students"`.
3. **Card** (`ot`/`ct` Card+CardContent, `className="p-0 overflow-x-auto"`) containing:
   - **Loading state**: `p-4 space-y-3` with 4 Skeletons `h-10 w-full`.
   - **Empty state**: `p-8 text-center text-sm text-muted-foreground` → `"No students match your filters"`.
   - **Table** columns: Student | Grade | Routes (`hidden md:table-cell`) | Home Address (`hidden lg:table-cell`) | Pickup (`hidden xl:table-cell`) | Parent (`hidden md:table-cell`) | Status | actions col (`w-10`, no header text).

## Table rows (per student, `hover:bg-muted/50`)
- **Student**: avatar circle `h-8 w-8 rounded-full bg-primary/10 ... text-xs font-semibold text-primary` showing initials (`name.split(" ").map(w=>w[0]).join("")`) + name in `font-medium text-sm`.
- **Grade**: `text-sm text-muted-foreground`.
- **Routes**: computed by joining `student_routes` (all rows from `useStudentRoutes()` hook `Xl`) filtered by `student_id === student.id` against routes list; renders `"{route.name} (AM)"` or `"(PM)"` (`route.type==="morning"?"AM":"PM"`) joined with `" · "`; em-dash `—` if none.
- **Home Address**: `student.home_address || student.boarding_stop_name || "—"`.
- **Pickup**: `student.pickup_time || "—"`.
- **Parent**: two lines — parent_name (`text-sm`) and parent_phone (`text-[11px] text-muted-foreground flex items-center gap-1` with Phone icon h-2.5 w-2.5).
- **Status**: StudentStatusBadge (`tz`) — outline Badge `text-[10px]` with per-status classes: `on-bus`=`bg-success/10 text-success border-success/20` label "On Bus"; `at-school`=`bg-primary/10 text-primary border-primary/20` "At School"; `absent`=`bg-destructive/10 text-destructive border-destructive/20` "Absent"; `dropped-off`=`bg-muted text-muted-foreground border-border` "Dropped Off". Unknown status falls back to raw value with no color class.
- **Actions**: DropdownMenu (`modal:false`) triggered by ghost icon Button h-8 w-8 with Ellipsis icon h-4 w-4; menu `align="end"` with: `Edit` (Pencil icon h-3.5 w-3.5 mr-2 → opens StudentFormDialog with this student) and `Delete` (Trash2 icon, item class `text-destructive` → opens DeleteStudentDialog).

## Client-side filtering (useMemo)
Case-insensitive search matches `name`, `parent_name`, or `grade` (includes). Status filter exact match on `student.status`; bus filter exact match on `student.bus_id`.

## StudentFormDialog (`K3`, lines 31976-32393) — Add/Edit Student
Rendered only when open (`{i && <K3 .../>}`); Dialog content `sm:max-w-lg max-h-[90vh] overflow-y-auto`; title `font-heading`: "Edit Student" / "Add Student".
Form `space-y-4` fields:
- Row 1 (grid-cols-2 gap-3): **Student Name** (id sName, required, maxLength 100), **Grade** (id grade, required, maxLength 20).
- Row 2 (grid-cols-2): **Parent Name** (required, maxLength 100), **Parent Phone** (required, maxLength 20).
- Row 3 (grid-cols-2): **Alt. Phone (optional)** (maxLength 20), **Parent Email** (type email, placeholder `"Used for auto-linking"`, maxLength 100).
- Row 4 (grid-cols-2): **Morning Route** select and **Afternoon Route** select. Each shows `"None"` sentinel item (value `__none__`, mapped to empty string in state) plus routes filtered by `type==="morning"` / `type==="afternoon"`; option label is `route.name` + ` (busName)` when the route's `bus_id` resolves in buses list. Initial values derived in a useEffect from existing `student_routes` rows for this student (first row whose route_id is in morning list / afternoon list).
- **Home Address** input (placeholder `"e.g. 123 Forest Rd, Karen"`, maxLength 200).
- **Pickup Location** label with hint `"(click map)"` in `text-muted-foreground font-normal`: a Leaflet MapContainer `w-full h-48 rounded-lg border border-border overflow-hidden`, center = parsed (home_lat,home_lng) when both valid else Nairobi default `[-1.2921, 36.8219]`, zoom 13, zoomControl on, OSM tile layer (`https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png`, attribution OpenStreetMap link). Clicking the map sets home_lat/home_lng to `toFixed(6)` strings; when coords valid a Marker is shown (custom divIcon `V3`) and a helper recenters map view to coords. Map only mounted while dialog open.
- Row (grid-cols-3): **Latitude** (number, step any), **Longitude** (number, step any), **Pickup Time** (text, placeholder `"07:15"`).
- **Status** select: at-school "At School", on-bus "On Bus", absent "Absent", dropped-off "Dropped Off". Default for new student: `"at-school"`.
- Footer: Cancel (outline, type button) and submit Button labeled "Saving..." while pending else "Update"/"Create", disabled while create or update mutation pending.

Submit handler: builds payload `{name, grade, parent_name, parent_phone, parent_phone2: x||null, parent_email: x||null, home_address: x||null, home_lat: parseFloat||null, home_lng: parseFloat||null, pickup_time: x||null, status}`. Edit → `useUpdateStudent` (`p3`: `we.from("students").update(rest).eq("id",id).select().single()`), toast "Student updated". Create → `useCreateStudent` (`f3`: `we.from("students").insert(payload).select().single()`), toast "Student created". Then ALWAYS calls `useSetStudentRoutes` (`g3`) with `{studentId, routeIds: [morning_route_id, afternoon_route_id].filter(Boolean)}` — this mutation diffs existing `student_routes` rows vs desired: deletes rows whose route_id is not desired (`.delete().in("id", removedIds)`), inserts missing `{student_id, route_id}` rows; invalidates `student-routes`, `routes`, `students`. Errors toast `{title:"Error", description: err.message, variant:"destructive"}`. Closes dialog on success.

## DeleteStudentDialog (`Z3`, lines 32395-32437)
AlertDialog. Title: `Delete "{studentName}"?` Description: "This will permanently remove this student record. This action cannot be undone." Actions: Cancel + destructive action button (`bg-destructive text-destructive-foreground hover:bg-destructive/90`) labeled "Deleting..."/"Delete". Confirm runs `useDeleteStudent` (`m3`: `we.from("students").delete().eq("id", id)`), toast "Student deleted", closes; error toast destructive.

## BulkUploadStudentsDialog (`ez`, lines 52592-52870)
Dialog `sm:max-w-2xl max-h-[85vh] overflow-y-auto`. Title `font-heading flex items-center gap-2` with FileSpreadsheet icon `h-5 w-5 text-primary`: "Bulk Upload Students".

Constants: REQUIRED_COLUMNS = `["name","grade","parent_name","parent_phone"]`; ALL_COLUMNS = `["name","grade","parent_name","parent_phone","parent_phone2","parent_email","home_address","home_lat","home_lng","pickup_time","route_name"]`. CSV template literal (downloaded as `students_template.csv` via Blob + temp anchor):
```
name,grade,parent_name,parent_phone,parent_phone2,parent_email,home_address,home_lat,home_lng,pickup_time,route_name
John Doe,Grade 3,Jane Doe,+254712345678,+254712345679,jane@example.com,123 Forest Rd Karen,-1.3197,36.7076,07:15,Karen Morning
Mary Smith,Grade 5,Tom Smith,+254798765432,,tom@example.com,45 Ngong Rd,-1.2966,36.7873,07:20,Karen Morning
```

Sections:
1. **Template box**: `rounded-lg border border-dashed border-border p-4 bg-muted/30`; left text "Download template" (`text-sm font-medium`) + `"CSV with columns: " + ALL_COLUMNS.join(", ")` (`text-xs text-muted-foreground`); right: outline sm Button "Template" with Download icon h-3.5 w-3.5.
2. **File picker**: hidden `<input type=file accept=".csv,.xlsx,.xls">` + full-width outline Button `h-20 border-dashed gap-2` with Upload icon h-5 w-5; center text shows selected filename or "Click to select CSV or Excel file" plus "Supports .csv, .xlsx, .xls".
3. **Parsing** (on file change): reads arrayBuffer, parses with SheetJS (`XLSX.read(type:"array")`), first sheet, `sheet_to_json(defval:"")`. Errors collected: empty file → `"File is empty or has no data rows"`; header keys normalized via `trim().toLowerCase().replace(/\s+/g,"_")`; missing required columns → `"Missing required columns: {list}. Required: name, grade, parent_name, parent_phone"`. Per-row validation (row index reported as spreadsheet row `i+2`): "Row N: Missing student name" / "Missing grade" / "Missing parent name" / "Missing parent phone". Every row mapped (values String().trim()) to all 11 columns with "" defaults. Parse exception → `"Failed to parse file: {message}"`.
4. **Errors box** (if any issues): `rounded-lg border border-destructive/30 bg-destructive/5 p-3 space-y-1`, header with CircleAlert icon `"{n} issue(s) found"`, bulleted (`• `) list `text-xs text-destructive/80 max-h-24 overflow-y-auto`.
5. **Preview** (if rows parsed): header row "Preview ({n} rows)" + secondary Badge "{validCount} valid" (valid = has name, grade, parent_name, parent_phone). Table in `border rounded-lg overflow-auto max-h-48` with `text-xs` columns Name | Grade | Parent | Phone | Email, showing first 10 rows (missing values render `—`; invalid rows get `bg-destructive/5`); footer text "...and {n-10} more rows" when >10.
6. **Upload**: checks `we.auth.getSession()` and throws "Not authenticated" if no session; invokes edge function `we.functions.invoke("bulk-upload-students", { body: { students: parsedRows } })` (sends ALL parsed rows, not just valid ones). Response shape `{inserted: number, parentAssignments: number, errors: string[]}`. If `inserted > 0`: toast `title: "{inserted} students imported"` with description `"{parentAssignments} auto-assigned to parents"` when >0, and calls onComplete → parent invalidates `["students"]` query. Failure toast: "Upload failed" + message, destructive.
7. **Result box** (after upload): `border-success/30 bg-success/5` with CircleCheck icon "Upload complete", line "{inserted} students imported" (+ ", {n} auto-assigned to parents"), and bulleted server `errors` list in destructive text if non-empty.
8. **Footer**: outline button labeled "Close" after a result exists else "Cancel" (resets state and closes). Upload button hidden after result; otherwise label `"Upload {validCount} Students"` / "Uploading..." with spinning LoaderCircle (else Upload icon), disabled when validCount===0 or uploading. Closing the dialog resets file input, rows, errors, result, filename.

### Data sources

students: `we.from("students").select("*").order("name")` [queryKey students]; buses: `we.from("buses").select("*").order("name")` [buses] (for bus filter + route option bus names); routes: `we.from("routes").select("*, route_stops(*)").order("name")` with route_stops sorted by stop_order [routes]; student_routes: `we.from("student_routes").select("id, student_id, route_id")` optionally `.eq("student_id", id)` [student-routes, id??"all"]. Mutations: students insert/update/delete (invalidate students); student_routes diff-sync (select→delete .in→insert; invalidates student-routes, routes, students). Edge function: `bulk-upload-students` (POST {students: row[]}) returns {inserted, parentAssignments, errors[]}; requires auth session. No realtime subscriptions on this page.

### Styling notes

shadcn/ui (Card, Table, Dialog, AlertDialog, DropdownMenu, Select, Badge, Button, Input, Label, Skeleton) + Tailwind with semantic tokens (primary/success/warning/destructive/muted). Headings use `font-heading`. Badges are `variant=outline text-[10px]` with `/10` bg + `/20` border tints. Table card is `p-0 overflow-x-auto`; responsive column hiding at md/lg/xl. Avatar = initials in `bg-primary/10 text-primary` circle. Leaflet map in form dialog (react-leaflet MapContainer/TileLayer/Marker/useMapEvents/useMap), OSM tiles, Nairobi fallback center. SheetJS (xlsx) bundled for CSV/Excel parsing.

## `/runs` — Run History (Admin)

# Runs Page (`iz`, lines 53425-53606)

AdminLayout with `title="Run History"`, `subtitle="Track all bus runs and incidents"`.

## Layout
1. **Toolbar** (`flex flex-col sm:flex-row justify-between gap-3`):
   - SearchFilterBar: placeholder `"Search buses, routes, dates..."`; filters:
     - Status: placeholder "All statuses", options `in-progress` "In Progress", `completed` "Completed", `delayed` "Delayed".
     - Type: placeholder "All types", options `morning` "🌅 Morning", `afternoon` "🌇 Afternoon".
   - `Add Run` primary Button size sm `gap-1.5 shrink-0` with Plus icon h-3.5 w-3.5 → opens RunFormDialog with run=null.
2. **Count line**: `"{filtered} of {total} runs"` (`text-xs text-muted-foreground`).
3. **Card** `p-0 overflow-x-auto`:
   - Loading: 4 Skeletons h-10.
   - Empty: "No runs match your filters" (`p-8 text-center text-sm text-muted-foreground`).
   - Table columns: Bus | Route | Type (`hidden md:table-cell`) | Date | Time (`hidden md:table-cell`) | Stops (`hidden lg:table-cell`) | Students (`hidden lg:table-cell`) | Status | actions (`w-10`).

## Rows (hover:bg-muted/50)
- **Bus**: `run.buses?.name` (joined) — `font-medium text-sm`.
- **Route**: `run.routes?.name` — `text-sm text-muted-foreground`.
- **Type**: outline Badge `text-[10px]`: `"🌅 AM"` for morning else `"🌇 PM"`.
- **Date**: raw `run.date` string, `text-sm`.
- **Time**: `start_time` + (end_time ? ` → ${end_time}` : "").
- **Stops**: `{stops_completed}/{total_stops}`.
- **Students**: `{students_boarded}/{total_students}`.
- **Status**: RunStatusBadge (`az`) — outline Badge `text-[10px] gap-1` with icon h-2.5 w-2.5: `in-progress` → `bg-success/10 text-success border-success/20`, Clock icon, "In Progress"; `completed` → `bg-muted text-muted-foreground border-border`, CircleCheckBig icon, "Completed"; `delayed` → `bg-warning/10 text-warning border-warning/20`, TriangleAlert icon, "Delayed". Unknown statuses fall back to the completed style.
- **Actions**: Ellipsis dropdown (align end): Edit (Pencil; opens RunFormDialog with run) and Delete (Trash2, `text-destructive`; opens DeleteRunDialog).

## Filtering (useMemo)
Search lowercase matches `run.buses?.name`, `run.routes?.name`, or `run.date.includes(q)`. Status exact match; type exact match.

## RunFormDialog (`nz`, lines 53123-53350) — Add/Edit Run
Note: this dialog is ALWAYS mounted (`<nz open={r} .../>`) so form state initializes from the `run` prop captured at mount via useState initializers only (no useEffect resync — editing relies on remount/prop at open time; replicate exactly). Dialog `sm:max-w-lg`, title "Edit Run"/"Add Run" (`font-heading`).

Default state for new run: `bus_id:""`, `route_id:""`, `type:"morning"`, `date: new Date().toISOString().split("T")[0]` (today), `start_time:""`, `end_time:""`, `status:"in-progress"`, `total_stops:0`, `stops_completed:0`, `total_students:0`, `students_boarded:0`, `incidents:0`. (Counter fields are kept in state and submitted but have NO visible inputs — they pass through unchanged on edit, zeros on create.)

Fields:
- Row 1 (grid-cols-2 gap-3): **Bus** select (placeholder "Select bus", options all buses by name) and **Route** select (placeholder "Select route", options ALL routes by name — not filtered by type).
- Row 2 (grid-cols-3): **Type** select (morning "Morning" / afternoon "Afternoon"); **Date** input `type=date` required; **Status** select (in-progress "In Progress", completed "Completed", delayed "Delayed").
- Row 3 (grid-cols-2): **Start Time** text input placeholder "06:00" maxLength 10; **End Time** text input placeholder "07:30" maxLength 10 (free-text, not type=time).
- Footer: Cancel (outline) + submit ("Saving..." while pending; "Update"/"Create").

Submit: payload = state with `route_id: route_id || null`, `start_time: start_time || null`, `end_time: end_time || null` (bus_id NOT nulled). Edit → `useUpdateRun` (`v3`: `we.from("runs").update(rest).eq("id",id).select().single()`), toast "Run updated". Create → `useCreateRun` (`x3`: `we.from("runs").insert(payload).select().single()`), toast "Run created". Error toast destructive with message. Both invalidate `["runs"]`. Closes on success.

## DeleteRunDialog (`sz`, lines 53352-53393)
AlertDialog. Title "Delete this run?"; description "This will permanently remove this run record. This action cannot be undone." Cancel + destructive "Delete"/"Deleting..." button. Confirm → `useDeleteRun` (`_3`: `we.from("runs").delete().eq("id", id)`, invalidates runs), toast "Run deleted"; error toast destructive. Rendered only when a run is pending deletion (`{i && <sz .../>}`).

### Data sources

runs list: `we.from("runs").select("*, buses(name, plate_number), routes(name)").order("date", { ascending: false })` [queryKey runs]. Buses (`buses` table, order name) and routes (`routes` with route_stops, order name) feed the form selects. Mutations: runs insert / update / delete, each invalidating ["runs"]. No realtime subscriptions, no edge functions.

### Styling notes

Same shadcn/Tailwind system as Students. Emoji used in type filter options and badges (🌅 / 🌇). Run status badges add a tiny leading lucide icon (Clock / CircleCheckBig / TriangleAlert) at h-2.5 w-2.5 with gap-1. Responsive hiding: Type+Time at <md, Stops+Students at <lg.

## `/schools` — Schools (Admin)

# Schools Page (`hz`, lines 53910-54040)

AdminLayout `title="Schools"`, `subtitle="Manage your schools"`.

## Layout
1. **Toolbar**: SearchFilterBar with ONLY search (no filters), placeholder `"Search schools, addresses, phones..."` + primary `Add School` Button (sm, Plus icon, `gap-1.5 shrink-0`) → opens SchoolFormDialog with school=null.
2. **Count line**: `"{filtered} of {total} schools"`.
3. **Card** `p-0 overflow-x-auto`:
   - Loading: 3 Skeletons h-10.
   - Empty: "No schools match your search".
   - Table columns: School Name | Address | Phone (`hidden md:table-cell`) | actions (`w-10`).

## Rows (hover:bg-muted/50)
- **School Name**: `h-8 w-8 rounded-lg bg-primary/10` square containing lucide School icon `h-4 w-4 text-primary`, plus name `font-medium text-sm`.
- **Address**: `school.address || "—"` (`text-sm text-muted-foreground`).
- **Phone**: `school.phone || "—"`.
- **Actions**: Ellipsis dropdown (align end): Edit (Pencil) / Delete (Trash2, text-destructive → DeleteSchoolDialog).

## Search
Case-insensitive match on `name`, `address?`, or `phone?` (optional-chained — address/phone may be null).

## SchoolFormDialog (`uz`, lines 53624-53864) — Add/Edit School
Always mounted; has a useEffect that resyncs form state whenever `school` or `open` changes (unlike RunFormDialog). Dialog `sm:max-w-lg max-h-[90vh] overflow-y-auto`; title "Edit School"/"Add School" (`font-heading`).

State: `{name, address, phone, lat (string), lng (string)}` from school or "".

Fields in order:
1. **School Name** (id name, required, maxLength 200).
2. **Address** — label shows red asterisk (`<span class="text-destructive">*</span>`); required, maxLength 300, placeholder `"e.g. Forest Road, Nairobi"`.
3. **School Gates Location** — label with hint `"(click map to set)"` (`text-muted-foreground font-normal`). Leaflet MapContainer `w-full h-56 rounded-lg border border-border overflow-hidden` (mounted only while open), center = parsed lat/lng when both valid else Nairobi `[-1.2921, 36.8219]`, zoom 13, zoomControl, OSM tiles (attribution "© OpenStreetMap"). Click handler (`cz` useMapEvents) sets lat/lng to `toFixed(6)`. When valid coords: Marker with custom school divIcon (`oz`): 30x30 white-bordered circle, background `hsl(152,55%,28%)` (brand green), box-shadow, containing 🏫 emoji, iconSize [30,30], iconAnchor [15,30]; plus a RecenterOnCoords helper (`lz`) that calls `map.setView([lat,lng])` when lat/lng change.
4. Row (grid-cols-2 gap-3): **Latitude** (id lat, type number step any, required, placeholder "-1.2921") and **Longitude** (id lng, type number step any, required, placeholder "36.8219") — manually editable, kept in sync with map clicks.
5. **Phone** (id phone, maxLength 20, optional).
6. Footer: Cancel (outline) + submit ("Saving..."/"Update"/"Create", disabled while pending).

Submit validation (before mutation):
- `!address.trim()` → destructive toast `title:"Address required"`, description "Please enter the school's address."; abort.
- lat or lng NaN → destructive toast `title:"Location required"`, description "Click the map to set the school gates location."; abort.
Payload `{name, address, phone, lat: float, lng: float}`. Edit → `useUpdateSchool` (`w3`: update .eq id .select .single), toast "School updated". Create → `useCreateSchool` (`y3`: insert .select .single), toast "School created". Error → destructive "Error" toast with message. Close on success. Both invalidate ["schools"].

## DeleteSchoolDialog (`dz`, lines 53866-53908)
AlertDialog. Title `Delete "{schoolName}"?`; description "This will permanently remove this school. This action cannot be undone." Cancel + destructive action ("Deleting..."/"Delete"). Confirm → `useDeleteSchool` (`b3`: `we.from("schools").delete().eq("id", id)`, invalidates schools), toast "School deleted". Rendered only while a school is pending deletion.

### Data sources

schools: `we.from("schools").select("*").order("name")` [queryKey schools]. Mutations: schools insert / update / delete, each invalidating ["schools"]. No realtime, no edge functions.

### Styling notes

Same shadcn/Tailwind admin system. Distinctive bits: school rows use a rounded-lg (square-ish) icon tile instead of a circle avatar; Leaflet map picker h-56 with custom 🏫 divIcon marker in brand green hsl(152,55%,28%) with 3px white border and drop shadow; required-field asterisk on Address; lat/lng paired number inputs with Nairobi placeholder coords.

## `/parent-assignments` — Parent Assignments (Admin)

# Parent Assignments Page (`pz`, lines 54060-54270)

AdminLayout `title="Parent Assignments"`, `subtitle="Assign students to parent accounts"`. Outer wrapper `space-y-6`. No search bar, no table-level CRUD — this page links students to parent auth accounts.

## Section 1 — Stat cards (`grid grid-cols-2 sm:grid-cols-3 gap-4`, only 2 cards rendered)
1. Card (`p-4 flex items-center gap-3`): Users icon `h-8 w-8 text-primary`; value `parents?.length ?? 0` in `text-2xl font-bold font-heading`; caption "Parent Accounts" (`text-xs text-muted-foreground`).
2. Card: UserCheck icon `h-8 w-8 text-success`; value `parentStudents?.length ?? 0` (total assignment rows across ALL parents); caption "Total Assignments".

## Section 2 — "Manage Assignments" Card
CardHeader title `text-base font-heading` "Manage Assignments". CardContent `space-y-4`:

### Assignment form row (`grid grid-cols-1 sm:grid-cols-3 gap-3 items-end`)
1. **Select Parent** (plain `<label class="text-sm font-medium">`): Select with placeholder "Choose a parent...". Options: parents with `status === "registered"` listed first as selectable items labeled `full_name || "Unnamed"`. If ANY parent has `status === "pending"`, a non-item section header div is appended: `px-2 py-1.5 text-xs font-medium text-muted-foreground border-t mt-1 pt-1` reading "Pending Signup", followed by DISABLED select items labeled `"{full_name || 'Unnamed'} (pending)"`. Changing parent resets the student selection to "".
2. **Assign Student**: Select disabled until a parent is chosen; placeholder `"Choose a student..."` when parent selected else `"Select parent first"`. Options = all students NOT already assigned to the selected parent (computed via a Set of assigned student_ids for that parent), labeled `"{name} ({grade})"`.
3. **Assign** Button (`gap-1.5`, Plus icon h-4 w-4), label "Assigning..." while mutation pending else "Assign"; disabled when no parent, no student, or pending. On click → `useAssignParentStudent` (`E3`: `we.from("parent_students").insert({parent_id, student_id}).select().single()`, invalidates ["parent-students"]), success toast "Student assigned to parent", then clears the student selection (parent stays selected); error → destructive "Error" toast with message.

### Assigned-students panel (rendered only when a parent is selected, `mt-4`)
- Heading `text-sm font-medium mb-2`: "Assigned Students" + secondary Badge `ml-2` with count of this parent's assignment rows.
- Loading (parents query OR parent_students query loading): 2 Skeletons h-10 in space-y-2.
- Empty: `"No students assigned to this parent yet"` (`text-sm text-muted-foreground py-4 text-center`).
- Table columns: Student | Grade | Route Stop (`hidden sm:table-cell`) | unassign col (`w-10`). Rows iterate this parent's `parent_students` rows, resolving each `student_id` against the students query; rows with unresolvable students render nothing (null). Cells: student.name (`font-medium text-sm`), student.grade (`text-sm text-muted-foreground`), `student.boarding_stop_name || "—"`, and a ghost icon Button `h-8 w-8 text-destructive hover:text-destructive` with Trash2 icon h-4 w-4 (disabled while unassign pending) → `useUnassignParentStudent` (`S3`: `we.from("parent_students").delete().eq("id", assignmentRowId)`, invalidates ["parent-students"]), success toast "Student unassigned"; error destructive toast. Note: unassign deletes by the parent_students row id, with NO confirmation dialog.

## Parent data shape
`useAdminParents` (`fz`) returns array of parent objects with at least `{id, full_name, status}` where status ∈ "registered" | "pending". The id is used directly as `parent_id` in parent_students inserts.

### Data sources

Parents: edge function `we.functions.invoke("admin-manage-parents", { body: { action: "list" } })` [queryKey admin-parents] — returns parent accounts incl. pending (un-registered) ones. Students: `we.from("students").select("*").order("name")` [students]. Assignments: `we.from("parent_students").select("*")` (unfiltered; hook accepts an id arg used only in the queryKey) [parent-students, undefined]. Mutations: parent_students insert {parent_id, student_id} and delete by row id, both invalidating ["parent-students"]. No realtime subscriptions.

### Styling notes

Same admin shell. Stat cards use big lucide icons (Users primary, UserCheck success) beside `text-2xl font-bold font-heading` numbers. Form labels here are raw <label> elements (not the shadcn Label component). Pending parents shown as a visually separated disabled group inside the SelectContent via a styled div separator labeled "Pending Signup". Destructive ghost icon button for inline unassign with no confirm dialog.

## Shared components/hooks in this section

## Shared components & hooks used by these four pages (all in /Users/andreanatali/Documents/GitHub/Safe Ride 1/safe-ride/.local/live-reference/app.pretty.js)

### AdminLayout — `Fn` (line 24038)
Sidebar provider wrapping `min-h-screen flex w-full`: admin sidebar (`pF`) + content column. Header `h-14 flex items-center justify-between border-b bg-card px-4`: sidebar trigger, page `title` (`text-base font-heading font-semibold`) and optional `subtitle` (`text-xs text-muted-foreground`); right side has a decorative bell button (Bell icon, no handler) and an avatar dropdown — initials from `user_metadata.full_name` (first letters, max 2, uppercased) falling back to first 2 chars of email, in `h-8 w-8 rounded-full bg-primary` circle; menu shows disabled email item + \"Sign Out\" (destructive, LogOut icon) which calls `signOut()` then navigates to `/auth`. Main: `flex-1 p-4 md:p-6 overflow-auto`.

### SearchFilterBar — `xc` (line 31098)
Props `{search, onSearchChange, searchPlaceholder=\"Search...\", filters?}` where each filter is `{value, onChange, placeholder, options:[{value,label}]}`. Renders `flex flex-wrap items-center gap-2`: search Input with leading Search icon (`relative flex-1 min-w-[200px] max-w-sm`, input `pl-8 h-9 text-sm`); each filter as a Select (`h-9 w-auto min-w-[130px] text-sm`) whose FIRST item is always `value=\"all\"` labeled with the filter's placeholder; and a conditional ghost \"Clear\" button (X icon h-3 w-3 mr-1, `h-9 px-2 text-xs text-muted-foreground`) shown when search is non-empty or any filter ≠ \"all\", which resets search to \"\" and every filter to \"all\".

### Query hooks (TanStack `useQuery` = `wr`, supabase client = `we`)
- `dn` useBuses (24110): buses select * order name, key [\"buses\"].\n- `da` useRoutes (24124): routes `select(\"*, route_stops(*)\")` order name; route_stops client-sorted by stop_order, key [\"routes\"]. Route objects have `type` (\"morning\"/\"afternoon\"), `bus_id`, `name`.\n- `Ji` useStudents (24141): students select * order name, key [\"students\"].\n- `oi` useRuns (24155): runs `select(\"*, buses(name, plate_number), routes(name)\")` order date desc, key [\"runs\"].\n- `Ip` useSchools (24171): schools select * order name, key [\"schools\"].\n- `Xl` useStudentRoutes(studentId?) (24185): student_routes `select(\"id, student_id, route_id\")`, optional `.eq(\"student_id\", id)`, key [\"student-routes\", id ?? \"all\"].\n- `PD` useParentStudents (24201): parent_students select * (arg unused in filter, only in key [\"parent-students\", arg]).\n- `fz`/`mz` useAdminParents (54042/54272 — duplicated function): edge fn `admin-manage-parents` action \"list\", key [\"admin-parents\"].

### Mutation hooks (TanStack `useMutation` = `Dt`, queryClient = `Et`)
- Students: `f3` create (30544), `p3` update (30561), `m3` delete (30581) — invalidate [\"students\"].\n- `g3` setStudentRoutes (30596): diff-syncs student_routes for a student (delete removed by id list, insert added); invalidates [\"student-routes\"], [\"routes\"], [\"students\"].\n- Runs: `x3` create (30640), `v3` update (30657), `_3` delete (30677) — invalidate [\"runs\"].\n- Schools: `y3` create (30692), `w3` update (30709), `b3` delete (30729) — invalidate [\"schools\"].\n- Parent links: `E3` assign insert parent_students (30744), `S3` unassign delete by row id (30767) — invalidate [\"parent-students\"].

### UI primitive aliases (shadcn/ui)
`ot`/`ct`/`yn`/`wn` Card/CardContent/CardHeader/CardTitle; `ha`/`fa`/`pa`/`Lr`/`He`/`We` Table/TableHeader/TableBody/TableRow/TableHead/TableCell; `ci`/`ma`/`ga`/`va`/`xa` Dialog/DialogContent/DialogHeader/DialogTitle/DialogFooter; `na`/`ks`/`Cs`/`Ns`/`Ps`/`As`/`js`/`Rs` AlertDialog set (Content/Header/Title/Description/Footer/Cancel/Action); `ai`/`ii`/`ua`/`fr` DropdownMenu/Trigger/Content/Item; `Nr`/`vr`/`Pr`/`_r`/`_t` Select/Trigger/Value/Content/Item; `De` Button; `tt` Input; `Be` Label; `Ot` Badge; `qt` Skeleton; `Qt` useToast.

### Leaflet (react-leaflet) aliases
`O2` MapContainer, `L2` TileLayer, `I2` Marker, `j2` useMapEvents, `Bp` useMap, `pn` L (leaflet). Student dialog uses marker icon `V3` + click handler `q3` + recenter `G3`; school dialog uses `oz` (green 🏫 divIcon), `cz` (click), `lz` (recenter). Default map center everywhere: Nairobi [-1.2921, 36.8219], zoom 13, OSM tile URL `https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png`.

### Lucide icon aliases (verified at lines 5365-6351)
`OA` CircleAlert, `IA` CircleCheckBig, `Gi` CircleCheck, `Mi` Clock, `MA` Download, `uc` Ellipsis, `UA` FileSpreadsheet, `WA` LoaderCircle, `Ki` Pencil, `Bl` Phone, `Zi` Plus, `G1` School, `K1` Search, `oa` Trash2, `ea` TriangleAlert, `qf` Upload, `X0` UserCheck, `Bi` Users, `zd` X.

### Common patterns across all four pages
- Pages are pure client-side: fetch full table, filter with useMemo on lowercase search + \"all\"-sentinel select filters.\n- \"{filtered} of {total} {noun}\" count line under the toolbar in `text-xs text-muted-foreground`.\n- Table inside Card `p-0 overflow-x-auto`; skeleton rows while loading (3-4 × `h-10 w-full`); centered `p-8 text-center text-sm text-muted-foreground` empty state.\n- Row actions via Ellipsis ghost icon button (h-8 w-8) DropdownMenu align=end with Edit (Pencil) / Delete (Trash2 in text-destructive).\n- Delete confirmations via AlertDialog with destructive action `bg-destructive text-destructive-foreground hover:bg-destructive/90` and pending label \"Deleting...\".\n- Toasts: success `{title}`; failures `{title:\"Error\"|specific, description: error.message, variant:\"destructive\"}`.\n- Form dialogs: `sm:max-w-lg` (+ `max-h-[90vh] overflow-y-auto` when containing maps), `font-heading` titles, footer Cancel(outline)+submit with \"Saving...\" pending state. Students page passes `modal:false` to its row DropdownMenu (others don't).

## Agent notes

All four pages share the same admin shell (AdminLayout `Fn`) and a reusable SearchFilterBar (`xc`). Data layer is TanStack Query + Supabase JS client (`we`). Minified-to-real mapping used: rz=StudentsPage, ez=BulkUploadStudentsDialog, tz=StudentStatusBadge, K3=StudentFormDialog, Z3=DeleteStudentDialog, iz=RunsPage, nz=RunFormDialog, sz=DeleteRunDialog, az=RunStatusBadge, hz=SchoolsPage, uz=SchoolFormDialog (with Leaflet map picker), dz=DeleteSchoolDialog, pz=ParentAssignmentsPage, fz/mz=useAdminParents. Icons verified against lucide definitions (Plus, Pencil, Trash2, Ellipsis, Upload, Download, FileSpreadsheet, CircleAlert, CircleCheck, LoaderCircle, Phone, Search, X, School, Users, UserCheck, Clock, CircleCheckBig, TriangleAlert). Key file: /Users/andreanatali/Documents/GitHub/Safe Ride 1/safe-ride/.local/live-reference/app.pretty.js (students page 52894-53121, bulk upload dialog 52592-52870, student form dialog 31976-32393, delete student 32395-32437, runs page 53425-53606, run form 53123-53350, run delete 53352-53393, schools page 53910-54040, school form 53624-53864, school delete 53866-53908, parent assignments 54042-54270, query hooks 24110-24212, mutation hooks 30544-30780).