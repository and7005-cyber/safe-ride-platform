// Central Google Maps config. Replaces the old Leaflet/OSM setup.

export const GOOGLE_MAPS_API_KEY =
  (import.meta.env.VITE_GOOGLE_MAPS_API_KEY as string | undefined) ?? "";

// A Map ID is mandatory for AdvancedMarker. The Google-provided demo ID renders
// a default-styled vector map and works for development, so it is the fallback
// when VITE_GOOGLE_MAPS_MAP_ID is unset or empty. Production builds get the
// Cloud-styled Map ID (Google Cloud → Maps Platform → Map management) from
// VITE_GOOGLE_MAPS_MAP_ID, which infra/scripts/deploy-frontend.sh reads from the
// parameter store (/saferide/google-maps-map-id).
const DEMO_MAP_ID = "4504f8b37365c3d0";
export const MAP_ID =
  (import.meta.env.VITE_GOOGLE_MAPS_MAP_ID as string | undefined) || DEMO_MAP_ID;

export const NAIROBI = { lat: -1.286389, lng: 36.817223 } as const;

// Libraries the embedded maps need. `geometry` decodes route polylines, `marker`
// powers AdvancedMarker. (Places autocomplete is proxied server-side.)
export const MAP_LIBRARIES: Array<"geometry" | "marker"> = ["geometry", "marker"];

export const hasMapsKey = Boolean(GOOGLE_MAPS_API_KEY);
