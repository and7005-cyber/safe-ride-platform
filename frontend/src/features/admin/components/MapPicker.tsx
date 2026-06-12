import "leaflet/dist/leaflet.css";
import { MapContainer, Marker, TileLayer, useMapEvents } from "react-leaflet";
import { NAIROBI } from "@/lib/leafletSetup";

function ClickHandler({ onPick }: { onPick: (lat: number, lng: number) => void }) {
  useMapEvents({
    click(e) {
      onPick(Number(e.latlng.lat.toFixed(6)), Number(e.latlng.lng.toFixed(6)));
    },
  });
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
  const center: [number, number] = lat != null && lng != null ? [lat, lng] : NAIROBI;
  return (
    <div className="h-56 w-full overflow-hidden rounded-md border">
      <MapContainer center={center} zoom={13} className="h-full w-full" scrollWheelZoom>
        <TileLayer url="https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png" />
        <ClickHandler onPick={onPick} />
        {lat != null && lng != null && <Marker position={[lat, lng]} />}
      </MapContainer>
    </div>
  );
}
