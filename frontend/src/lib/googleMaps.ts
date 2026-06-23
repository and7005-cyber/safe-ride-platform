// Central Google Maps config. Replaces the old Leaflet/OSM setup.

export const GOOGLE_MAPS_API_KEY =
  (import.meta.env.VITE_GOOGLE_MAPS_API_KEY as string | undefined) ?? "";

// A Map ID is mandatory for AdvancedMarker. The Google-provided demo ID renders
// a default-styled vector map and works for development; swap for a Cloud-styled
// Map ID (Google Cloud → Maps Platform → Map management) in production.
export const MAP_ID = "4504f8b37365c3d0";

export const NAIROBI = { lat: -1.286389, lng: 36.817223 } as const;

// Libraries the embedded maps need. `geometry` decodes route polylines, `marker`
// powers AdvancedMarker. (Places autocomplete is proxied server-side.)
export const MAP_LIBRARIES: Array<"geometry" | "marker"> = ["geometry", "marker"];

export const hasMapsKey = Boolean(GOOGLE_MAPS_API_KEY);
