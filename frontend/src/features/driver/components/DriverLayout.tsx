import type { ReactNode } from "react";
import { Bus, Home, MapPin, TriangleAlert } from "lucide-react";
import { RoleMobileLayout, type NavItem } from "@/app/layouts/RoleMobileLayout";
import { useDriverContext, useRunFixWatch } from "@/features/driver/driverHooks";
import { LocationStatusBanner } from "./LocationStatusBanner";
import { NudgeQueue } from "./NudgeQueue";

export const DRIVER_NAV: NavItem[] = [
  { to: "/driver", label: "Home", icon: Home, end: true },
  { to: "/driver/run", label: "Run", icon: MapPin },
  { to: "/driver/boarding", label: "Board", icon: Bus },
  { to: "/driver/incident", label: "Incident", icon: TriangleAlert },
];

// The driver's shell (GPS plan U3): the shared mobile layout with the driver
// nav, and the prompt card above the page content on every tab — Home, Run,
// Board and Incident — so a nudge raised on the Run page is still on screen
// when the driver is on the board. In flow, not fixed: it pushes the page
// down rather than covering anything the driver is about to tap.
//
// Since U6 it also keeps the GPS watch in step with the run (every tab polls
// the context, so the watch resumes on reload from whichever tab loads) and
// shows the location indicator under the prompt card, for the same reason.
export function DriverLayout({ title, children }: { title: string; children: ReactNode }) {
  const { data } = useDriverContext();
  useRunFixWatch(data);
  return (
    <RoleMobileLayout nav={DRIVER_NAV} variant="primary" title={title}>
      <NudgeQueue />
      <LocationStatusBanner />
      {children}
    </RoleMobileLayout>
  );
}
