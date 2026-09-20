import { useState } from "react";
import { LocateOff } from "lucide-react";
import { Button } from "@/components/ui/button";
import { useLocationStatus } from "@/lib/geo/fixCapture";

// The persistent, non-blocking location indicator (GPS plan U6: R4; F5).
//
// Shown after a real PERMISSION_DENIED error — never off the Permissions API
// — or after several fixes at 1 km or worse, which is what an "approximate
// location" grant looks like from inside the page. It names what the office
// loses and how to put it right on this platform, and it goes the moment a
// good fix arrives. It never blocks: every tap keeps working underneath it,
// and it pushes the page down rather than covering anything.

export type Platform = "android" | "ios" | "other";

export function detectPlatform(userAgent: string): Platform {
  if (/android/i.test(userAgent)) return "android";
  if (/iphone|ipad|ipod/i.test(userAgent)) return "ios";
  // iPadOS Safari reports itself as a Mac; touch points tell it apart.
  if (/macintosh/i.test(userAgent) && typeof navigator !== "undefined" && navigator.maxTouchPoints > 1) {
    return "ios";
  }
  return "other";
}

/** Installed to the home screen: iOS then files the permission under the
 * app's own name, not Safari's. */
export function isStandalone(): boolean {
  if (typeof window === "undefined") return false;
  try {
    return (
      window.matchMedia?.("(display-mode: standalone)")?.matches === true
      || (navigator as any).standalone === true
    );
  } catch {
    return false;
  }
}

export function unblockSteps(kind: "denied" | "approximate", platform: Platform, standalone: boolean): string[] {
  if (platform === "android") {
    const steps = [
      "Tap the lock icon (or the tune icon) next to the address bar, open Permissions, and set Location to Allow.",
    ];
    if (kind === "approximate") {
      steps.push(
        "Then in Android Settings, open Apps, Chrome, Permissions, Location, and turn on \"Use precise location\".",
      );
    }
    steps.push("Come back to this page; the next stop you arrive at picks it up.");
    return steps;
  }
  if (platform === "ios") {
    const entry = standalone ? "the SafeRide app" : "Safari Websites";
    return [
      `Open Settings, Privacy & Security, Location Services, ${entry}, and choose "While Using the App".`,
      ...(kind === "approximate" ? ["On the same screen, turn on Precise Location."] : []),
      ...(standalone
        ? []
        : ["In Safari, tap \"aA\" in the address bar, open Website Settings, and set Location to Allow."]),
      "Come back to this page; the next stop you arrive at picks it up.",
    ];
  }
  return [
    "In your browser's site settings, allow Location for this site, then reload the page.",
  ];
}

export function LocationStatusBanner() {
  const status = useLocationStatus();
  const [showSteps, setShowSteps] = useState(false);
  if (!status.denied && !status.approximate) return null;

  const kind: "denied" | "approximate" = status.denied ? "denied" : "approximate";
  const platform = detectPlatform(typeof navigator !== "undefined" ? navigator.userAgent : "");
  const steps = unblockSteps(kind, platform, isStandalone());
  const title = kind === "denied" ? "Location off" : "Turn on precise location";
  const body =
    kind === "denied"
      ? "The office sees checkpoint positions only — each stop you arrive at, not where the bus is. Your taps still work."
      : "Your phone is sharing an approximate location, about a kilometre wide, which cannot confirm you are at a stop. Your taps still work.";

  return (
    <section
      role="status"
      aria-live="polite"
      data-testid="location-banner"
      data-kind={kind}
      className="mb-4 rounded-lg border border-amber-500/60 bg-amber-50 p-3 text-sm shadow-sm dark:bg-amber-950/30"
    >
      <div className="flex items-start gap-2">
        <LocateOff className="mt-0.5 h-5 w-5 shrink-0 text-amber-600" />
        <div className="min-w-0 flex-1">
          <p className="font-semibold leading-tight">{title}</p>
          <p className="mt-1 text-muted-foreground">{body}</p>
          <Button
            size="sm"
            variant="link"
            className="h-auto px-0 py-1"
            data-testid="location-banner-steps"
            aria-expanded={showSteps}
            onClick={() => setShowSteps((v) => !v)}
          >
            {showSteps ? "Hide the steps" : "How to turn it on"}
          </Button>
          {showSteps && (
            <ol className="list-decimal space-y-1 pl-5 text-muted-foreground">
              {steps.map((step) => (
                <li key={step}>{step}</li>
              ))}
            </ol>
          )}
        </div>
      </div>
    </section>
  );
}
