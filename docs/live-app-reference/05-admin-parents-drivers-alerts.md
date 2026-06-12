# Admin Parents, Drivers, Alerts, 404

_Reverse-engineered from the live www.saferidekenya.com bundle (app.pretty.js in safe-ride/.local/live-reference/). Source of truth for alignment._

## `/parents` — Admin – Parents

# Parents page (component `gz`, lines 54290–54596; edit dialog `xz` 54598–54687; query hook `mz` 54272–54288)

Wrapped in the shared **AdminLayout** (`Fn`) with `title="Parents"`, `subtitle="Manage parent accounts and credentials"` (sidebar + header with bell icon and avatar/sign-out dropdown).

## Data
- `useQuery({ queryKey: ["admin-parents"] })` → `supabase.functions.invoke("admin-manage-parents", { body: { action: "list" } })`. Throws on error.
- Returned parent objects used: `id`, `full_name` (nullable), `email`, `phone` (nullable), `status` (`"registered"` or anything else = pending), `students` (string[] of student names), `created_at` (nullable).
- No realtime subscription on this page.

## Layout (top to bottom, inside `space-y-4`)
1. **Toolbar row** (`flex flex-col sm:flex-row justify-between gap-3`): shared FilterToolbar (`xc`) with `searchPlaceholder="Search parents by name or email..."` and `filters: []` (search only, no select filters). **There is NO "Add Parent" button** — parents are created via the Students flow (see empty state copy).
2. **Count line**: `<p class="text-xs text-muted-foreground">{n} parent{s}</p>` — pluralizes (`"1 parent"` / `"3 parents"`), counts FILTERED rows.
3. **Card → CardContent** (`p-0 overflow-x-auto`) containing the table.

## Client-side filtering
`useMemo`: lowercase search term must be a substring of `full_name ?? ""` (lowercased) OR `email` (lowercased). Empty search → all rows.

## Loading state
4 Skeleton rows (`h-10 w-full`) inside a `p-4 space-y-3` div.

## Empty states (`p-8 text-center text-sm text-muted-foreground`)
- Raw data length === 0: `"No parents yet. Add students with parent details to see them here."`
- Filtered length === 0 (but data exists): `"No parents match your search."`

## Table columns
| Column | Visibility | Content |
|---|---|---|
| Name | always | Avatar circle `h-8 w-8 rounded-full bg-accent/60 ... text-accent-foreground` with initials = first letter of each word of `full_name` (fallback `"?"`), joined, sliced to 2 chars; then `full_name` in `font-medium text-sm`, fallback `"—"` |
| Status | always | Badge: `status === "registered"` → variant `default`, text `"Registered"`; else variant `outline`, text `"Pending"`. Both `text-[10px]` |
| Email | always | Mail icon (`h-3 w-3`) + email, `flex items-center gap-1 text-muted-foreground`, cell `text-sm` |
| Students | always | If `students.length > 0`: `flex flex-wrap gap-1` of Badge variant `secondary` `text-[10px] font-normal`, one per student name (keyed by index). Else `<span class="text-xs text-muted-foreground">No students</span>` |
| Phone | `hidden md:table-cell`, `text-sm text-muted-foreground` | If phone: Phone icon (`h-3 w-3`) + phone in `flex items-center gap-1`; else `"—"` |
| Joined | `hidden lg:table-cell`, `text-sm text-muted-foreground` | `new Date(created_at).toLocaleDateString()` or `"—"` |
| (actions) | header `w-10` | See below |

Rows: `hover:bg-muted/50`, keyed by `id`.

## Row actions
- Only when `status === "registered"`: DropdownMenu (`modal: false`) triggered by ghost icon Button (`h-8 w-8`) with Ellipsis icon (`h-4 w-4`); content `align="end"` with 3 items:
  - **Edit** (Pencil icon `h-3.5 w-3.5 mr-2`) → opens Edit Parent dialog with that row
  - **Reset Password** (KeyRound icon) → opens reset-password confirm dialog
  - **Delete** (Trash2 icon, item class `text-destructive`) → opens delete confirm dialog
- When NOT registered: plain text `<span class="text-xs text-muted-foreground">Awaiting signup</span>` (no actions).

## Edit Parent dialog (`xz`)
Dialog (`sm:max-w-md`), title `"Edit Parent"` (class `font-heading`). State initialized once from the parent prop (`full_name ?? ""`, `email`, `phone ?? ""`). Form `space-y-4`:
- **Full Name** (`id=pName`, Input, `maxLength 100`, not required)
- **Email** (`id=pEmail`, `type=email`, `required`, `maxLength 255`)
- **Phone** (`id=pPhone`, `maxLength 20`, not required)
Footer: outline Button `"Cancel"` (closes), submit Button `"Update"` / `"Saving..."` while pending (disabled when pending).
Submit calls update mutation with `{ id, full_name, email, phone }` → edge fn `admin-manage-parents` with `action: "update"`. On success: invalidate `["admin-parents"]`, toast `"Parent updated"`, close dialog. On error: destructive toast `title: "Error"`, `description: err.message`.
Dialog open is controlled: rendered only while a parent is selected; `onOpenChange(false)` clears selection.

## Delete confirm (AlertDialog)
Title: `Delete "{full_name || email}"?` Description: `"This will permanently delete this parent account and remove all student assignments. This action cannot be undone."` Buttons: Cancel; Action button class `bg-destructive text-destructive-foreground hover:bg-destructive/90`, label `"Delete"` / `"Deleting..."` while pending. Confirm → edge fn `action: "delete"` with `{ id }`. Success: invalidate `["admin-parents"]` AND `["parent-students"]`, toast `"Parent deleted"`, close. Error: destructive `"Error"` toast with message.

## Reset password confirm (AlertDialog)
Title: `Reset password for "{full_name || email}"?` Description: `"A password reset email will be sent to <strong>{email}</strong>. The parent will need to click the link to set a new password."` Buttons: Cancel; Action label `"Send Reset Email"` / `"Sending..."`. Confirm → edge fn `action: "reset-password"` with `{ email }` (no query invalidation). Success toast: title `"Password reset email sent"`, description `` `Sent to ${email}` ``. Error: destructive toast.

### Data sources

Edge function `admin-manage-parents` via `supabase.functions.invoke` with body `{ action }`: `list` (query key ["admin-parents"]), `update` ({ id, full_name, email, phone }), `delete` ({ id }), `reset-password` ({ email }). Invalidations: update → ["admin-parents"]; delete → ["admin-parents"], ["parent-students"]. No direct table reads, no realtime.

### Styling notes

shadcn/ui: Card/CardContent, Table family, Badge, Button, DropdownMenu, AlertDialog, Dialog, Label, Input, Skeleton, toast (useToast). Avatar initials chip `h-8 w-8 rounded-full bg-accent/60 text-accent-foreground text-xs font-semibold`. Status badge text-[10px]. Table card `p-0 overflow-x-auto`; responsive cols via `hidden md:table-cell` / `hidden lg:table-cell`. Icons: lucide Mail, Phone, Ellipsis, Pencil, KeyRound, Trash2.

## `/drivers` — Admin – Drivers

# Drivers page (component `_z`, lines 54707–55069; add/edit dialog `yz` 55071–55228; query hook `vz` 54689–54705)

Wrapped in AdminLayout (`Fn`) with `title="Drivers"`, `subtitle="Manage driver accounts and assignments"`.

## Data
- `useQuery({ queryKey: ["admin-drivers"] })` → `supabase.functions.invoke("admin-manage-drivers", { body: { action: "list" } })`.
- Driver objects used: `id`, `full_name` (nullable), `email`, `phone` (nullable), `has_pin` (boolean), `assigned_bus` (string|null — bus name/label), `created_at` (nullable).
- No realtime subscription.

## Layout (`space-y-4`)
1. **Toolbar row** (`flex flex-col sm:flex-row justify-between gap-3`): FilterToolbar (`xc`) with `searchPlaceholder="Search drivers by name or email..."`, `filters: []`; plus an **"Add Driver"** Button (`size="sm"`, `gap-1.5 shrink-0`, Plus icon `h-3.5 w-3.5`) which clears the editing driver and opens the dialog in create mode.
2. **Count line**: `text-xs text-muted-foreground`, `{n} driver{s}` (pluralized, filtered count).
3. Card → CardContent (`p-0 overflow-x-auto`) with table.

## Filtering
Same as parents: case-insensitive substring match on `full_name ?? ""` or `email`.

## Loading state
4 Skeletons `h-10 w-full` in `p-4 space-y-3`.

## Empty states (`p-8 text-center text-sm text-muted-foreground`)
- No data at all: stacked (`space-y-2`) UserPlus icon `h-8 w-8 mx-auto text-muted-foreground/50` + `"No drivers yet. Add your first driver account."`
- No search matches: `"No drivers match your search."`

## Table columns
| Column | Visibility | Content |
|---|---|---|
| Name | always | Avatar circle `h-8 w-8 rounded-full bg-primary/10 ... text-primary` with 2-char initials from `full_name` (fallback `"?"`); name `font-medium text-sm`, fallback `"—"` |
| PIN | always | If `has_pin`: Badge variant `outline` `font-mono text-xs gap-1` with **Hash** icon (`h-3 w-3`) + text `"PIN set"`. Else `<span class="text-xs text-muted-foreground">No PIN</span>`. (The actual PIN value is never displayed.) |
| Email | always | Mail icon + email, `flex items-center gap-1 text-muted-foreground`, cell `text-sm` |
| Phone | `hidden md:table-cell` | Phone icon + phone or `"—"` |
| Assigned Bus | always | If `assigned_bus`: Badge variant `secondary` `text-[10px] font-normal gap-1` with Bus icon (`h-3 w-3`) + bus label. Else `<span class="text-xs text-muted-foreground">Unassigned</span>` |
| Joined | `hidden lg:table-cell` | `toLocaleDateString()` or `"—"` |
| (actions) | header `w-10` | Dropdown for EVERY row (unlike parents) |

Rows `hover:bg-muted/50`, keyed by `id`.

## Row actions dropdown (`modal:false`, ghost icon button with Ellipsis)
- **Edit** (Pencil) → sets editing driver, opens dialog in edit mode
- **Reset Password** (KeyRound) → opens reset confirm dialog
- **Delete** (Trash2, `text-destructive`) → opens delete confirm dialog

## Add/Edit Driver dialog (`yz`)
Dialog `sm:max-w-md`. Title (`font-heading`): `"Edit Driver"` when a driver prop is present, else `"Add Driver"`. On every open, form state resets to `{ full_name: driver?.full_name ?? "", email: driver?.email ?? "", password: "", phone: driver?.phone ?? "", pin: "" }` — i.e. password and PIN always start blank (PIN is write-only; leaving it blank on edit presumably keeps the existing PIN). Form `space-y-4`:
- **Full Name** (`id=dName`, required, maxLength 100)
- **Email** (`id=dEmail`, type email, required, maxLength 255)
- **Password** (`id=dPassword`, type password, required, minLength 6, maxLength 72) — **rendered ONLY in create mode** (hidden when editing)
- **Phone** (`id=dPhone`, maxLength 20, optional)
- **Login PIN (4 digits)** (`id=dPin`): Input with `placeholder="e.g. 1234"`, `maxLength 4`, `inputMode="numeric"`, `pattern="\\d{4}"`, classes `font-mono tracking-widest`; onChange strips non-digits and slices to 4 (`value.replace(/\D/g,"").slice(0,4)`). Next to it a **"Generate"** Button (`type=button`, variant outline, size sm, `shrink-0 text-xs`) that sets pin to a random 4-digit number: `String(Math.floor(1000 + Math.random()*9000))`. Helper text below (`text-[11px] text-muted-foreground`): `"Drivers can use this PIN to log in quickly instead of email/password."` The PIN row is in a `flex gap-2` container.
Footer: Cancel (outline, calls `onOpenChange(false)`); submit Button disabled while pending, label `"Saving..."` when pending, else `"Update"` (edit) / `"Create"` (add).

### Submit handling (in `_z`)
- **Edit mode**: update mutation with `{ id, full_name, email, phone, pin }` (password intentionally excluded) → edge fn `action: "update"`. Success toast `"Driver updated"`.
- **Create mode**: create mutation with the full form `{ full_name, email, password, phone, pin }` → edge fn `action: "create"`. Success toast `"Driver account created"`.
- Both: close dialog on success; destructive `"Error"` toast with `err.message` on failure. `isPending` = create.isPending || update.isPending.
- Create/update success invalidates `["admin-drivers"]` and `["driver-profiles"]`.

## Delete confirm (AlertDialog)
Title: `Delete "{full_name || email}"?` Description: `"This will permanently delete this driver account and unassign them from any buses. This action cannot be undone."` Action button destructive-styled, `"Delete"`/`"Deleting..."`. Confirm → edge fn `action: "delete"` `{ id }`. Success: invalidate `["admin-drivers"]`, `["driver-profiles"]`, AND `["buses"]` (bus assignment changes); toast `"Driver deleted"`. Error: destructive toast.

## Reset password confirm (AlertDialog)
Title: `Reset password for "{full_name || email}"?` Description: `"A password reset email will be sent to <strong>{email}</strong>."` (note: shorter than the parents version — no trailing sentence). Action label `"Send Reset Email"`/`"Sending..."`. Confirm → edge fn `action: "reset-password"` `{ email }`. Success toast `"Password reset email sent"` / `` `Sent to ${email}` ``.

### Data sources

Edge function `admin-manage-drivers` via `supabase.functions.invoke` with body `{ action }`: `list` (query key ["admin-drivers"]; returns id, full_name, email, phone, has_pin, assigned_bus, created_at), `create` ({ full_name, email, password, phone, pin }), `update` ({ id, full_name, email, phone, pin } — no password), `delete` ({ id }), `reset-password` ({ email }). Invalidations: create/update → ["admin-drivers"], ["driver-profiles"]; delete → ["admin-drivers"], ["driver-profiles"], ["buses"]. No direct table reads, no realtime.

### Styling notes

Same shadcn/ui kit as Parents. Driver avatar chip uses `bg-primary/10 text-primary` (vs parents' `bg-accent/60`). PIN badge: outline, `font-mono text-xs gap-1` with Hash icon. Assigned-bus badge: secondary `text-[10px] font-normal gap-1` with Bus icon. PIN input `font-mono tracking-widest`. Icons: Plus (Add Driver), UserPlus (empty state), Hash, Bus, Mail, Phone, Ellipsis, Pencil, KeyRound, Trash2.

## `/alerts` — Admin – Driver Alerts

# Driver Alerts page (component `pH`, lines 55775–55929; label map `fH` 55767–55773)

Wrapped in AdminLayout (`Fn`) with `title="Driver Alerts"` and dynamic `subtitle`: if unacknowledged count > 0 → `` `${n} unacknowledged` ``, else `"All caught up"`. Unacknowledged count = `data.filter(i => !i.acknowledged).length`.

## Data
- `useQuery({ queryKey: ["incidents"] })` → `supabase.from("incidents").select("*").order("created_at", { ascending: false })` (newest first).
- Incident fields used: `id`, `type`, `description`, `driver_name` (nullable), `bus_name` (nullable), `created_at`, `acknowledged` (bool), plus written fields `acknowledged_at`, `acknowledged_by`.
- **Realtime**: `useEffect` subscribes to channel `"incidents-admin"` on `postgres_changes` `{ event: "*", schema: "public", table: "incidents" }` → invalidates `["incidents"]`; channel removed on unmount.

## Incident type → label map (`fH`)
- `breakdown` → "Vehicle Breakdown"
- `accident` → "Road Accident"
- `student` → "Student Issue"
- `traffic` → "Heavy Traffic / Delay"
- `other` → "Other"
Fallback: raw `type` string if not in map.

## Layout
A single `div.space-y-3` list of cards. **No search bar and no filters** on this page (despite scope hint — verified none exist in code).

### Loading state
3 Skeletons `h-24 rounded-xl`.

### Empty state
Card with CardContent `py-16 text-center`: Bell icon `h-10 w-10 mx-auto text-muted-foreground/40 mb-3` + `<p class="text-sm text-muted-foreground">No alerts from drivers yet</p>`.

### Alert card (one per incident, keyed by id)
- Card className: acknowledged → `opacity-60`; unacknowledged → `border-warning/40`.
- CardContent `p-4`, inner `flex items-start gap-3`:
  1. **Icon tile**: `h-10 w-10 rounded-xl flex items-center justify-center shrink-0`, bg `bg-muted` (acked) or `bg-warning/10` (new); TriangleAlert icon `h-5 w-5`, `text-muted-foreground` (acked) or `text-warning` (new).
  2. **Body** (`flex-1 min-w-0`):
     - Title row (`flex items-center gap-2 flex-wrap`): type label `<p class="text-sm font-heading font-semibold">` + status badge — acked: Badge variant outline `text-[10px]` text `"Acknowledged"`; new: Badge variant outline with classes `text-[10px] bg-warning/15 text-warning border-warning/30` text `"New"`.
     - Description: `<p class="text-sm text-foreground/80 mt-1 whitespace-pre-wrap">{description}</p>`.
     - Meta row (`flex items-center gap-3 mt-2 text-[11px] text-muted-foreground flex-wrap`): User icon `h-3 w-3` + `driver_name ?? "Driver"`; if `bus_name`: Bus icon + bus_name; relative timestamp via date-fns `formatDistanceToNow(new Date(created_at), { addSuffix: true })` (e.g. "5 minutes ago").
  3. **Actions column** (`flex flex-col gap-1.5 shrink-0`):
     - **Ack** button — only when NOT acknowledged: Button size sm, variant outline, `gap-1.5 h-8`, CircleCheck icon `h-3.5 w-3.5` + text `" Ack"`.
     - **Delete** button — always: Button size sm, variant ghost, `gap-1.5 h-8 text-destructive hover:text-destructive`, Trash2 icon only (no text). **No confirmation dialog** — deletes immediately on click.

## Mutations (raw supabase calls, NOT react-query mutations; UI refresh relies on the realtime subscription invalidating the query)
- **Acknowledge**: fetches current user via `supabase.auth.getUser()`, then `supabase.from("incidents").update({ acknowledged: true, acknowledged_at: new Date().toISOString(), acknowledged_by: user?.id }).eq("id", id)`. Toast: on error → destructive `title: "Failed to acknowledge"`, `description: error.message`; on success → `title: "Acknowledged"`.
- **Delete**: `supabase.from("incidents").delete().eq("id", id)`. Toast ONLY on error: destructive `"Failed to delete"` + message. No success toast.

### Data sources

Direct table: `incidents` — select * ordered by created_at desc (query key ["incidents"]); update (acknowledged, acknowledged_at, acknowledged_by) by id; delete by id. Realtime channel "incidents-admin" on all postgres_changes for public.incidents → invalidate ["incidents"]. `supabase.auth.getUser()` for acknowledged_by.

### Styling notes

Warning-accented cards: unacked `border-warning/40`, icon tile `bg-warning/10` + `text-warning`, "New" badge `bg-warning/15 text-warning border-warning/30`; acked cards `opacity-60` with muted icon tile. Description preserves newlines (`whitespace-pre-wrap`). Icons: TriangleAlert (alert), Bell (empty state), User, Bus, CircleCheck (Ack), Trash2. Relative time via date-fns formatDistanceToNow with addSuffix.

## `*` — 404 – Not Found

# 404 page (component `mH`, lines 55930–55951)

Catch-all route. Standalone page — NOT wrapped in any layout.

## Behavior
- `useLocation()`; `useEffect` (dep: `pathname`) logs `console.error("404 Error: User attempted to access non-existent route:", location.pathname)`.

## UI
- Outer: `div.flex min-h-screen items-center justify-center bg-muted`.
- Inner `div.text-center` containing:
  - `<h1 class="mb-4 text-4xl font-bold">404</h1>`
  - `<p class="mb-4 text-xl text-muted-foreground">Oops! Page not found</p>`
  - Plain anchor `<a href="/" class="text-primary underline hover:text-primary/90">Return to Home</a>` (a full-page navigation, not a router Link).

No data fetching, no auth.

### Data sources

None.

### Styling notes

Default Lovable 404 template: bg-muted full-screen centering, text-4xl font-bold heading, text-xl text-muted-foreground subtitle, underlined primary link.

## Shared components/hooks in this section

## Shared components/hooks referenced by these pages (minified → inferred name)\n\n- **`Fn` (line 24038) = AdminLayout**: props `{ children, title, subtitle }`. Renders SidebarProvider (`Gw`) → admin sidebar (`pF`) + main column. Header (`h-14 flex items-center justify-between border-b bg-card px-4`): SidebarTrigger (`Zw`), `<h2 class=\"text-base font-heading font-semibold\">{title}</h2>` + optional `<p class=\"text-xs text-muted-foreground\">{subtitle}</p>`; right side: a Bell icon button (no functionality attached) and an avatar dropdown — circular `h-8 w-8 bg-primary text-primary-foreground` button showing initials derived from `user.user_metadata.full_name` (or first 2 chars of email, uppercased, fallback \"??\"), dropdown shows disabled item with user email + destructive \"Sign Out\" item (LogOut icon) that calls `signOut()` from the auth context (`la`) then navigates to `/auth`.\n\n- **`xc` (line 31098) = FilterToolbar**: props `{ search, onSearchChange, searchPlaceholder = \"Search...\", filters }`. Search Input with absolute Search icon (`pl-8 h-9 text-sm`, container `relative flex-1 min-w-[200px] max-w-sm`); each filter = shadcn Select (`h-9 w-auto min-w-[130px] text-sm`) with an \"all\" item labeled by the filter's placeholder plus its options; a ghost \"Clear\" button (X icon, `h-9 px-2 text-xs text-muted-foreground`) appears when search is non-empty or any filter !== \"all\", resetting search to \"\" and all filters to \"all\". Parents/Drivers pages pass `filters: []` (search only).\n\n- **Query/mutation plumbing**: `wr` = useQuery, `Dt` = useMutation, `Et` = useQueryClient, `Qt` = useToast (shadcn toast: `toast({ title, description?, variant?: \"destructive\" })`), `we` = supabase client.\n\n- **shadcn/ui primitives**: `ot`/`ct` Card/CardContent; `ha`/`fa`/`pa`/`Lr`/`He`/`We` Table/TableHeader/TableBody/TableRow/TableHead/TableCell; `Ot` Badge; `De` Button; `qt` Skeleton; `tt` Input; `Be` Label; `ai`/`ii`/`ua`/`fr` DropdownMenu/Trigger/Content/Item; `ci`/`ma`/`ga`/`va`/`xa` Dialog/DialogContent/DialogHeader/DialogTitle/DialogFooter; `na`/`ks`/`Cs`/`Ns`/`Ps`/`As`/`js`/`Rs` AlertDialog/Content/Header/Title/Description/Footer/Cancel/Action.\n\n- **Lucide icons (verified by factory name strings)**: `Z0`=Mail(5764), `Bl`=Phone(5850), `uc`=Ellipsis(5540), `Ki`=Pencil(5834), `K0`=KeyRound(5670), `oa`=Trash2(6056), `Zi`=Plus(5874), `Z1`=UserPlus(6243), `zA`=Hash(5618), `$r`=Bus(5277), `Dl`=Bell(5213), `ea`=TriangleAlert(6090), `Y0`=User(6307), `Gi`=CircleCheck(5409), `K1`=Search(5948), `zd`=X(6351).\n\n- **`gS` (55764) = date-fns `formatDistanceToNow`** (wraps formatDistance(date, now)); used on /alerts with `{ addSuffix: true }`.\n\n- **Query hooks defined alongside these pages**: `mz` (54272) = useAdminParents → invoke `admin-manage-parents` `{action:\"list\"}`, key `[\"admin-parents\"]`; `vz` (54689) = useAdminDrivers → invoke `admin-manage-drivers` `{action:\"list\"}`, key `[\"admin-drivers\"]`.\n\n- Other query keys touched via invalidation (owned by other pages): `[\"parent-students\"]`, `[\"driver-profiles\"]`, `[\"buses\"]`, `[\"incidents\"]`.

## Agent notes

All four components fully read from /Users/andreanatali/Documents/GitHub/Safe Ride 1/safe-ride/.local/live-reference/app.pretty.js: gz=/parents (54290–54596 + xz dialog 54598–54687 + mz hook 54272), _z=/drivers (54707–55069 + yz dialog 55071–55228 + vz hook 54689), pH=/alerts (55775–55929 + fH type map 55767), mH=404 (55930–55951). Notable findings: (1) Parents page has NO create flow — parents materialize from the Students flow; row actions only for status \"registered\", pending rows show \"Awaiting signup\". (2) Driver PIN is write-only in the UI (list returns only has_pin boolean); blank PIN on edit is still sent as \"\" in the update payload — edge function presumably treats empty as \"keep existing\". (3) Alerts page delete has NO confirmation dialog and no success toast; UI consistency after ack/delete depends entirely on the realtime invalidation of [\"incidents\"]. (4) Reset-password dialogs differ slightly in description copy between parents and drivers.