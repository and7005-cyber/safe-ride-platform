# Live app design system (tokens, fonts, tailwind config)

# Safe Ride Kenya — Live App Design System (extracted from compiled bundle)

Source: `/Users/andreanatali/Documents/GitHub/Safe Ride 1/safe-ride/.local/live-reference/index.pretty.css` (`:root` block at lines 362–397) and `app.pretty.js`.

## 1. HTML shell
- `<title>Safe Ride Kenya</title>`
- `<meta name="description" content="Easy track your loved ones">`
- Fonts are NOT loaded via `<link>` in HTML — they come from a CSS `@import` at line 1 of the compiled CSS (see §3).

## 2. Token table (`:root`) — ALL HSL triplets, shadcn `hsl(var(--x))` convention

| Token | Value | Resolved color |
|---|---|---|
| `--background` | `150 10% 97%` | near-white mint `#F7F9F8` |
| `--foreground` | `160 30% 10%` | very dark green `#122119` |
| `--card` | `0 0% 100%` | white |
| `--card-foreground` | `160 30% 10%` | dark green |
| `--popover` | `0 0% 100%` | white |
| `--popover-foreground` | `160 30% 10%` | dark green |
| `--primary` | `152 55% 28%` | forest green `#206F4A` |
| `--primary-foreground` | `0 0% 100%` | white |
| `--secondary` | `150 15% 92%` | pale green-grey |
| `--secondary-foreground` | `160 30% 15%` | dark green |
| `--muted` | `150 10% 94%` | light grey-green |
| `--muted-foreground` | `160 10% 45%` | medium grey-green |
| `--accent` | `38 90% 55%` | amber/gold `#F0A123` |
| `--accent-foreground` | `38 90% 15%` | dark brown |
| `--destructive` | `0 72% 51%` | red `#DC2828` |
| `--destructive-foreground` | `0 0% 100%` | white |
| `--success` | `152 55% 28%` | same forest green as primary |
| `--success-foreground` | `0 0% 100%` | white |
| `--warning` | `38 90% 55%` | same amber as accent |
| `--warning-foreground` | `38 90% 15%` | dark brown |
| `--border` | `150 12% 88%` | light green-grey |
| `--input` | `150 12% 88%` | same as border |
| `--ring` | `152 55% 28%` | primary green |
| `--radius` | `0.75rem` | — |

Sidebar tokens (dark-green sidebar even in light mode):

| Token | Value |
|---|---|
| `--sidebar-background` | `152 40% 14%` (deep forest) |
| `--sidebar-foreground` | `150 15% 85%` |
| `--sidebar-primary` | `38 90% 55%` (amber) |
| `--sidebar-primary-foreground` | `0 0% 100%` |
| `--sidebar-accent` | `152 35% 20%` |
| `--sidebar-accent-foreground` | `150 15% 90%` |
| `--sidebar-border` | `152 30% 22%` |
| `--sidebar-ring` | `38 90% 55%` |

Font tokens: `--font-heading: "Space Grotesk", sans-serif;` and `--font-body: "DM Sans", sans-serif;`

**IMPORTANT — things that do NOT exist in the live bundle:**
- NO `.dark { ... }` token block. Dark mode class variant IS compiled in (`darkMode: ["class"]` — one utility `.dark\:border-destructive:is(.dark *)` exists at line 3402), but no dark token overrides were ever defined. Light theme only.
- NO `--chart-1`…`--chart-5` tokens (no recharts theming).
- NO custom `.container` config (class never emitted).

## 3. Fonts
CSS line 1: `@import "https://fonts.googleapis.com/css2?family=DM+Sans:ital,opsz,wght@0,9..40,400;0,9..40,500;0,9..40,600;0,9..40,700;1,9..40,400&family=Space+Grotesk:wght@500;600;700&display=swap";`
- Body: DM Sans 400/500/600/700 + italic 400 (variable opsz 9..40)
- Headings: Space Grotesk 500/600/700
- Base layer: `body { background: hsl(var(--background)); font-family: DM Sans, sans-serif; color: hsl(var(--foreground)); antialiased; }`, `h1–h6 { font-family: var(--font-heading) }`, and `* { border-color: hsl(var(--border)) }`
- Utility `.font-heading` → `Space Grotesk, sans-serif` exists (so `fontFamily.heading` is in the tailwind config). No `.font-sans` utility emitted; `.font-mono` is the default tailwind stack.

## 4. Custom keyframes / animations
- **`pulse-dot`** (custom): `0%,100% { opacity:1 } 50% { opacity:.4 }`; utility `.animate-pulse-dot { animation: pulse-dot 2s ease-in-out infinite }` (used for live-tracking dots)
- `accordion-down` / `accordion-up` (standard shadcn, `0.2s ease-out`, uses `--radix-accordion-content-height`)
- `enter`/`exit` keyframes + `fade-in-*`, `zoom-in-*`, `slide-in-from-*` utilities → **`tailwindcss-animate` plugin is installed**
- Standard `pulse` (2s) and `spin` (1s) also present

## 5. Custom utility/override CSS (non-Tailwind)
```css
.leaflet-pane, .leaflet-top, .leaflet-bottom, .leaflet-control { z-index: 1 !important; }
.leaflet-container { z-index: 0; }
```
(Leaflet maps must sit below shadcn overlays/dialogs.)

## 6. Other confirmed details from app.pretty.js
- shadcn sidebar component with defaults: `SIDEBAR_WIDTH = "16rem"`, `SIDEBAR_WIDTH_MOBILE = "18rem"`, `SIDEBAR_WIDTH_ICON = "3rem"` (lines 22290–22292)
- Sonner toaster present with stock shadcn `sonner.tsx` classNames (`group toast group-[.toaster]:bg-background ...`)
- Radius mapping is stock shadcn: `rounded-lg = var(--radius)`, `md = calc(var(--radius) - 2px)`, `sm = calc(var(--radius) - 4px)`

## 7. Drop-in `index.css`
```css
@import url('https://fonts.googleapis.com/css2?family=DM+Sans:ital,opsz,wght@0,9..40,400;0,9..40,500;0,9..40,600;0,9..40,700;1,9..40,400&family=Space+Grotesk:wght@500;600;700&display=swap');

@tailwind base;
@tailwind components;
@tailwind utilities;

@layer base {
  :root {
    --font-heading: "Space Grotesk", sans-serif;
    --font-body: "DM Sans", sans-serif;
    --background: 150 10% 97%;
    --foreground: 160 30% 10%;
    --card: 0 0% 100%;
    --card-foreground: 160 30% 10%;
    --popover: 0 0% 100%;
    --popover-foreground: 160 30% 10%;
    --primary: 152 55% 28%;
    --primary-foreground: 0 0% 100%;
    --secondary: 150 15% 92%;
    --secondary-foreground: 160 30% 15%;
    --muted: 150 10% 94%;
    --muted-foreground: 160 10% 45%;
    --accent: 38 90% 55%;
    --accent-foreground: 38 90% 15%;
    --destructive: 0 72% 51%;
    --destructive-foreground: 0 0% 100%;
    --success: 152 55% 28%;
    --success-foreground: 0 0% 100%;
    --warning: 38 90% 55%;
    --warning-foreground: 38 90% 15%;
    --border: 150 12% 88%;
    --input: 150 12% 88%;
    --ring: 152 55% 28%;
    --radius: 0.75rem;
    --sidebar-background: 152 40% 14%;
    --sidebar-foreground: 150 15% 85%;
    --sidebar-primary: 38 90% 55%;
    --sidebar-primary-foreground: 0 0% 100%;
    --sidebar-accent: 152 35% 20%;
    --sidebar-accent-foreground: 150 15% 90%;
    --sidebar-border: 152 30% 22%;
    --sidebar-ring: 38 90% 55%;
  }

  * { @apply border-border; }
  body { @apply bg-background text-foreground antialiased; font-family: "DM Sans", sans-serif; }
  h1, h2, h3, h4, h5, h6 { font-family: var(--font-heading); }
}

/* Leaflet maps must render below shadcn overlays */
.leaflet-pane, .leaflet-top, .leaflet-bottom, .leaflet-control { z-index: 1 !important; }
.leaflet-container { z-index: 0; }
```

## 8. Drop-in `tailwind.config.ts` (extend block)
```ts
export default {
  darkMode: ["class"],
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      fontFamily: {
        heading: ['"Space Grotesk"', "sans-serif"],
        body: ['"DM Sans"', "sans-serif"],
        sans: ['"DM Sans"', "sans-serif"],
      },
      colors: {
        border: "hsl(var(--border))",
        input: "hsl(var(--input))",
        ring: "hsl(var(--ring))",
        background: "hsl(var(--background))",
        foreground: "hsl(var(--foreground))",
        primary: { DEFAULT: "hsl(var(--primary))", foreground: "hsl(var(--primary-foreground))" },
        secondary: { DEFAULT: "hsl(var(--secondary))", foreground: "hsl(var(--secondary-foreground))" },
        destructive: { DEFAULT: "hsl(var(--destructive))", foreground: "hsl(var(--destructive-foreground))" },
        success: { DEFAULT: "hsl(var(--success))", foreground: "hsl(var(--success-foreground))" },
        warning: { DEFAULT: "hsl(var(--warning))", foreground: "hsl(var(--warning-foreground))" },
        muted: { DEFAULT: "hsl(var(--muted))", foreground: "hsl(var(--muted-foreground))" },
        accent: { DEFAULT: "hsl(var(--accent))", foreground: "hsl(var(--accent-foreground))" },
        popover: { DEFAULT: "hsl(var(--popover))", foreground: "hsl(var(--popover-foreground))" },
        card: { DEFAULT: "hsl(var(--card))", foreground: "hsl(var(--card-foreground))" },
        sidebar: {
          DEFAULT: "hsl(var(--sidebar-background))",
          foreground: "hsl(var(--sidebar-foreground))",
          primary: "hsl(var(--sidebar-primary))",
          "primary-foreground": "hsl(var(--sidebar-primary-foreground))",
          accent: "hsl(var(--sidebar-accent))",
          "accent-foreground": "hsl(var(--sidebar-accent-foreground))",
          border: "hsl(var(--sidebar-border))",
          ring: "hsl(var(--sidebar-ring))",
        },
      },
      borderRadius: {
        lg: "var(--radius)",
        md: "calc(var(--radius) - 2px)",
        sm: "calc(var(--radius) - 4px)",
      },
      keyframes: {
        "accordion-down": { from: { height: "0" }, to: { height: "var(--radix-accordion-content-height)" } },
        "accordion-up": { from: { height: "var(--radix-accordion-content-height)" }, to: { height: "0" } },
        "pulse-dot": { "0%, 100%": { opacity: "1" }, "50%": { opacity: "0.4" } },
      },
      animation: {
        "accordion-down": "accordion-down 0.2s ease-out",
        "accordion-up": "accordion-up 0.2s ease-out",
        "pulse-dot": "pulse-dot 2s ease-in-out infinite",
      },
    },
  },
  plugins: [require("tailwindcss-animate")],
} satisfies Config;
```

## Notes

Verification details: :root block is at index.pretty.css lines 362–397; base-layer rules at 399–418; pulse-dot keyframe at 1197–1211; tailwindcss-animate enter/exit at 2386–2398; accordion keyframes at 2959–2985; leaflet overrides at 2446–2455; .font-heading utility at 1966–1968; the single dark-variant utility (.dark\:border-destructive) at 3402, proving darkMode:["class"] but confirming no .dark token block exists anywhere in the 3877-line file. No --chart-* tokens exist (recharts uninstyled via CSS vars). Opacity variants observed in the bundle imply heavy use of primary/5..20, accent/10..30, success/5..15, warning/10..40, sidebar-accent/30..50 — all work automatically with the hsl(var(--x)) color format above. Sidebar width constants (16rem/18rem/3rem) and the sonner toaster classNames in app.pretty.js (lines ~22290 and ~3741) are stock shadcn defaults, so generating sidebar.tsx and sonner.tsx via shadcn CLI reproduces them exactly. The Google Fonts @import must stay as the first line of index.css (it is line 1 of the live compiled CSS; index.html has no font <link>).
