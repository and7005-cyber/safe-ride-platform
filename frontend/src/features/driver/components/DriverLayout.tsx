import type { ReactNode } from "react";
import { Bus, Home, MapPin, TriangleAlert } from "lucide-react";
import { RoleMobileLayout, type NavItem } from "@/app/layouts/RoleMobileLayout";
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
export function DriverLayout({ title, children }: { title: string; children: ReactNode }) {
  return (
    <RoleMobileLayout nav={DRIVER_NAV} variant="primary" title={title}>
      <NudgeQueue />
      {children}
    </RoleMobileLayout>
  );
}
