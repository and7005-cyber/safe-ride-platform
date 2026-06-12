import "leaflet/dist/leaflet.css";
import { MapContainer, Marker, Popup, TileLayer } from "react-leaflet";
import { Card } from "@/components/ui/card";
import { NAIROBI } from "@/lib/leafletSetup";
import { useBuses } from "@/lib/queries";

export function FleetMapPage() {
  // No refetchInterval: the live fleet map has no realtime channel either.
  const { data: buses = [] } = useBuses();
  const located = buses.filter((b: any) => b.current_lat != null && b.current_lng != null);

  return (
    <div className="space-y-6">
      <div>
        <h1 className="font-heading text-2xl font-bold">Fleet Map</h1>
        <p className="text-sm text-muted-foreground">Live bus positions</p>
      </div>

      <Card className="overflow-hidden">
        <div className="h-[70vh] w-full">
          <MapContainer center={NAIROBI} zoom={12} className="h-full w-full" scrollWheelZoom>
            <TileLayer
              attribution='&copy; OpenStreetMap contributors'
              url="https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png"
            />
            {located.map((bus: any) => (
              <Marker key={bus.id} position={[bus.current_lat, bus.current_lng]}>
                <Popup>
                  <div className="space-y-1 text-sm">
                    <p className="font-semibold">{bus.name}</p>
                    <p>Plate: {bus.plate_number ?? "—"}</p>
                    <p>Driver: {bus.driver_name ?? "—"}</p>
                    <p>Phone: {bus.driver_phone ?? "—"}</p>
                    <p>Capacity: {bus.capacity}</p>
                    <p>Status: {bus.status}</p>
                  </div>
                </Popup>
              </Marker>
            ))}
          </MapContainer>
        </div>
      </Card>
    </div>
  );
}
