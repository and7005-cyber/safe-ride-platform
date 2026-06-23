import { useEffect, useRef, useState } from "react";
import { AdvancedMarker, Map, useMap } from "@vis.gl/react-google-maps";
import { MAP_ID, NAIROBI } from "@/lib/googleMaps";
import { AddressAutocomplete } from "@/features/admin/components/AddressAutocomplete";

const round = (n: number) => Number(n.toFixed(6));

// Recenter the map when coordinates change (e.g. after a search/geocode).
function Recenter({ lat, lng }: { lat: number | null; lng: number | null }) {
  const map = useMap();
  useEffect(() => {
    if (map && lat != null && lng != null) map.panTo({ lat, lng });
  }, [map, lat, lng]);
  return null;
}

export function MapPicker({
  lat,
  lng,
  onPick,
}: {
  lat: number | null;
  lng: number | null;
  onPick: (lat: number, lng: number) => void;
}) {
  const [search, setSearch] = useState("");
  // AdvancedMarker drag-end also fires a map click (vis.gl quirk) — suppress it.
  const justDragged = useRef(false);
  const center = lat != null && lng != null ? { lat, lng } : NAIROBI;

  return (
    <div className="space-y-2">
      <AddressAutocomplete
        value={search}
        placeholder="Search for an address…"
        testId="picker-search"
        onChange={setSearch}
        onResolve={(address, la, ln) => {
          setSearch(address);
          onPick(round(la), round(ln));
        }}
      />
      <div className="h-56 w-full overflow-hidden rounded-md border" data-testid="map-picker">
        <Map
          mapId={MAP_ID}
          defaultCenter={center}
          defaultZoom={13}
          gestureHandling="greedy"
          className="h-full w-full"
          onClick={(e) => {
            if (justDragged.current) return;
            const ll = e.detail.latLng;
            if (ll) onPick(round(ll.lat), round(ll.lng));
          }}
        >
          <Recenter lat={lat} lng={lng} />
          {lat != null && lng != null && (
            <AdvancedMarker
              position={{ lat, lng }}
              draggable
              onDragStart={() => {
                justDragged.current = true;
              }}
              onDragEnd={(e: google.maps.MapMouseEvent) => {
                const p = e.latLng;
                if (p) onPick(round(p.lat()), round(p.lng()));
                setTimeout(() => {
                  justDragged.current = false;
                }, 0);
              }}
            />
          )}
        </Map>
      </div>
    </div>
  );
}
