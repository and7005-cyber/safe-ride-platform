import { useState } from "react";
import { ArrowDown, ArrowUp, Bus, Clock, GripVertical, Pin, PinOff } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader } from "@/components/ui/card";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import {
  PATTERN_LABEL,
  PLAN_LEGS,
  fmtDriving,
  fmtRide,
  legLabel,
  movedOrder,
} from "@/lib/planReview";

// U9 — one bus's AM/PM roster panels: computed stop times, per-child ride
// times, a children/capacity bar per leg, and the review edit grammar —
// ArrowUp/Down + HTML5 drag rows (the FleetMapPage/RoutesPage interaction
// precedent), a move-to-bus action, pin toggle, and a draft-scoped pattern
// select per child. All mutations live in the page; this panel only builds
// the payload-shaped callbacks.

export interface ReviewStudent {
  id: string;
  name: string;
}

export interface ReviewStop {
  key: string;
  name: string | null;
  lat: number | null;
  lng: number | null;
  students: ReviewStudent[];
  ride_seconds: number;
  scheduled_time: string;
}

export interface ReviewLeg {
  stops: ReviewStop[];
  driving_seconds: number;
  children: number;
}

export interface ReviewBus {
  bus_id: string;
  bus_name: string | null;
  capacity: number | null;
  capacity_use: Record<string, { children: number; capacity: number | null }>;
  legs: Record<string, ReviewLeg>;
}

function CapacityBar({ count, capacity }: { count: number; capacity: number | null }) {
  const cap = capacity ?? 0;
  const over = cap > 0 && count > cap;
  const pct = cap > 0 ? Math.min(100, Math.round((count / cap) * 100)) : 0;
  return (
    <div className="flex items-center gap-2" data-testid="capacity-bar">
      <div className="h-2 w-24 overflow-hidden rounded-full bg-muted">
        <div
          className={`h-full rounded-full ${over ? "bg-destructive" : "bg-primary"}`}
          style={{ width: `${pct}%` }}
        />
      </div>
      <span className={`text-xs ${over ? "font-medium text-destructive" : "text-muted-foreground"}`}>
        {count}/{cap || "—"} children
      </span>
    </div>
  );
}

export function PlanBusRoster({
  bus,
  color,
  busy,
  pinned,
  patternOf,
  onReorder,
  onMove,
  onPinToggle,
  onPatternChange,
}: {
  bus: ReviewBus;
  color: string;
  /** A recompute round-trip is in flight: every control disables (the
   * RoutesPage busyRouteId pattern) so a double-fired edit can never send a
   * stale full-order payload. */
  busy: boolean;
  /** Whether a student carries any pin in the draft document. */
  pinned: (studentId: string) => boolean;
  /** The student's effective ridership pattern (draft edit, else basis). */
  patternOf: (studentId: string) => string;
  onReorder: (busId: string, leg: string, order: string[]) => void;
  onMove: (student: ReviewStudent, fromBusId: string, leg: string) => void;
  onPinToggle: (student: ReviewStudent, isPinned: boolean) => void;
  onPatternChange: (studentId: string, pattern: string) => void;
}) {
  // Drag-to-reorder, scoped per leg (a cross-leg drop makes no sense).
  const [drag, setDrag] = useState<{ leg: string; index: number } | null>(null);

  const reorderTo = (leg: string, keys: string[], from: number, to: number) => {
    const order = movedOrder(keys, from, to);
    if (order) onReorder(bus.bus_id, leg, order);
  };

  return (
    <Card data-testid={`plan-bus-${bus.bus_id}`}>
      <CardHeader className="flex-row items-center gap-2 space-y-0 pb-2">
        <span
          className="flex h-6 w-6 shrink-0 items-center justify-center rounded-full text-white"
          style={{ background: color }}
        >
          <Bus className="h-3.5 w-3.5" />
        </span>
        <p className="font-heading text-lg font-semibold">{bus.bus_name ?? "Bus"}</p>
      </CardHeader>
      <CardContent className="space-y-4">
        {PLAN_LEGS.map((leg) => {
          const legDoc = bus.legs?.[leg] ?? { stops: [], driving_seconds: 0, children: 0 };
          const use = bus.capacity_use?.[leg] ?? { children: legDoc.children, capacity: bus.capacity };
          const keys = legDoc.stops.map((s) => s.key);
          return (
            <div key={leg} className="space-y-1.5" data-testid={`plan-leg-${bus.bus_id}-${leg}`}>
              <div className="flex flex-wrap items-center gap-2">
                <Badge variant={leg === "morning" ? "secondary" : "warning"}>{legLabel(leg)}</Badge>
                <CapacityBar count={use.children} capacity={use.capacity ?? bus.capacity} />
                <span className="ml-auto text-xs text-muted-foreground">
                  Driving {fmtDriving(legDoc.driving_seconds)}
                </span>
              </div>
              {legDoc.stops.length === 0 ? (
                <p className="text-sm text-muted-foreground">No stops on this leg.</p>
              ) : (
                <ol className="space-y-1">
                  {legDoc.stops.map((stop, i) => (
                    <li
                      key={stop.key}
                      data-testid="plan-stop-row"
                      className={`rounded border px-2 py-1.5 text-sm ${
                        drag?.leg === leg && drag.index === i ? "bg-accent" : ""
                      }`}
                      draggable={!busy}
                      onDragStart={() => setDrag({ leg, index: i })}
                      onDragOver={(e) => e.preventDefault()}
                      onDrop={() => {
                        if (drag && drag.leg === leg) reorderTo(leg, keys, drag.index, i);
                        setDrag(null);
                      }}
                    >
                      <div className="flex items-center gap-2">
                        <GripVertical className="h-3.5 w-3.5 shrink-0 cursor-grab text-muted-foreground" />
                        <span className="font-medium">{i + 1}.</span>
                        <span className="truncate" title={stop.name ?? undefined}>
                          {stop.name}
                        </span>
                        <span className="ml-auto flex items-center gap-1 text-xs text-muted-foreground">
                          <Clock className="h-3 w-3" /> {stop.scheduled_time}
                        </span>
                        <span className="flex flex-col">
                          <button
                            type="button"
                            aria-label="Move up"
                            disabled={busy || movedOrder(keys, i, i - 1) === null}
                            onClick={() => reorderTo(leg, keys, i, i - 1)}
                            className="text-muted-foreground hover:text-foreground disabled:opacity-30"
                          >
                            <ArrowUp className="h-3 w-3" />
                          </button>
                          <button
                            type="button"
                            aria-label="Move down"
                            disabled={busy || movedOrder(keys, i, i + 1) === null}
                            onClick={() => reorderTo(leg, keys, i, i + 1)}
                            className="text-muted-foreground hover:text-foreground disabled:opacity-30"
                          >
                            <ArrowDown className="h-3 w-3" />
                          </button>
                        </span>
                      </div>
                      <ul className="mt-1 space-y-1 pl-6">
                        {stop.students.map((s) => {
                          const isPinned = pinned(s.id);
                          return (
                            <li key={s.id} className="flex flex-wrap items-center gap-1.5">
                              <span className="text-sm">{s.name}</span>
                              <span className="text-xs text-muted-foreground">
                                ride {fmtRide(stop.ride_seconds)}
                              </span>
                              {isPinned && (
                                <Badge variant="outline" className="text-[10px]">
                                  Pinned
                                </Badge>
                              )}
                              <span className="ml-auto flex items-center gap-1">
                                <Select
                                  value={patternOf(s.id)}
                                  onValueChange={(v) => onPatternChange(s.id, v)}
                                  disabled={busy}
                                >
                                  <SelectTrigger
                                    className="h-7 w-36 text-xs"
                                    aria-label={`Pattern for ${s.name}`}
                                  >
                                    <SelectValue />
                                  </SelectTrigger>
                                  <SelectContent>
                                    {Object.entries(PATTERN_LABEL).map(([value, label]) => (
                                      <SelectItem key={value} value={value}>
                                        {label}
                                      </SelectItem>
                                    ))}
                                  </SelectContent>
                                </Select>
                                <Button
                                  variant="ghost"
                                  size="icon"
                                  className="h-7 w-7"
                                  title={isPinned ? `Unpin ${s.name}` : `Pin ${s.name} to this bus`}
                                  aria-label={isPinned ? `Unpin ${s.name}` : `Pin ${s.name}`}
                                  disabled={busy}
                                  onClick={() => onPinToggle(s, isPinned)}
                                >
                                  {isPinned ? (
                                    <PinOff className="h-3.5 w-3.5" />
                                  ) : (
                                    <Pin className="h-3.5 w-3.5" />
                                  )}
                                </Button>
                                <Button
                                  variant="outline"
                                  size="sm"
                                  className="h-7 px-2 text-xs"
                                  aria-label={`Move ${s.name}`}
                                  disabled={busy}
                                  onClick={() => onMove(s, bus.bus_id, leg)}
                                >
                                  Move
                                </Button>
                              </span>
                            </li>
                          );
                        })}
                      </ul>
                    </li>
                  ))}
                </ol>
              )}
            </div>
          );
        })}
      </CardContent>
    </Card>
  );
}
