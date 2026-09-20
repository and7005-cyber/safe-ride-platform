// GPS plan U8 — the freshness formatter every map surface shares (R9, R27).
// The server decides `stale`; this module only puts words to the position, so
// the cases pin the wording, the clamping and the accuracy-circle cap.
import { describe, expect, it } from "vitest";
import {
  ACCURACY_CIRCLE_CAP_M,
  accuracyRadius,
  ageSeconds,
  formatAge,
  freshnessLabel,
  sourceLabel,
} from "@/lib/positionFreshness";

const NOW = Date.parse("2026-09-20T07:30:00.000Z");
const at = (secondsAgo: number) => new Date(NOW - secondsAgo * 1000).toISOString();

describe("ageSeconds", () => {
  it("counts whole seconds since the position time", () => {
    expect(ageSeconds(at(42.9), NOW)).toBe(42);
    expect(ageSeconds(at(0), NOW)).toBe(0);
  });

  it("never goes negative when the client clock trails the server", () => {
    expect(ageSeconds(at(-3), NOW)).toBe(0);
  });

  it("is null for an unknown or unparseable time (a checkpoint of unknown age)", () => {
    expect(ageSeconds(null, NOW)).toBeNull();
    expect(ageSeconds(undefined, NOW)).toBeNull();
    expect(ageSeconds("not a time", NOW)).toBeNull();
  });
});

describe("formatAge", () => {
  it("reads just now under ten seconds, then seconds, minutes, hours, days", () => {
    expect(formatAge(0)).toBe("just now");
    expect(formatAge(9)).toBe("just now");
    expect(formatAge(10)).toBe("10 s ago");
    expect(formatAge(59)).toBe("59 s ago");
    expect(formatAge(60)).toBe("1 min ago");
    expect(formatAge(4 * 60 + 30)).toBe("4 min ago");
    expect(formatAge(59 * 60 + 59)).toBe("59 min ago");
    expect(formatAge(3600)).toBe("1 h ago");
    expect(formatAge(23 * 3600 + 59 * 60)).toBe("23 h ago");
    expect(formatAge(24 * 3600)).toBe("1 d ago");
  });

  it("floors fractional and negative input", () => {
    expect(formatAge(75.9)).toBe("1 min ago");
    expect(formatAge(-5)).toBe("just now");
  });
});

describe("freshnessLabel", () => {
  it("says updated while fresh and last seen once the server says stale (AE13 wording)", () => {
    expect(freshnessLabel({ position_at: at(12), stale: false }, NOW)).toBe("updated 12 s ago");
    expect(freshnessLabel({ position_at: at(4 * 60), stale: true }, NOW)).toBe("last seen 4 min ago");
  });

  it("uses the tap time, not the poll time: a two-second-old fix reads just now", () => {
    expect(freshnessLabel({ position_at: at(2), stale: false }, NOW)).toBe("updated just now");
  });

  it("trusts the server's verdict, not its own clock, for the stale wording", () => {
    // 120 s old but the server (a longer per-school threshold) says fresh.
    expect(freshnessLabel({ position_at: at(120), stale: false }, NOW)).toBe("updated 2 min ago");
    // 30 s old but flagged stale (a shorter threshold): still "last seen".
    expect(freshnessLabel({ position_at: at(30), stale: true }, NOW)).toBe("last seen 30 s ago");
  });

  it("makes no claim for a missing position or an unknown time", () => {
    expect(freshnessLabel(null, NOW)).toBeNull();
    expect(freshnessLabel(undefined, NOW)).toBeNull();
    expect(freshnessLabel({ position_at: null, stale: false }, NOW)).toBeNull();
  });
});

describe("accuracyRadius", () => {
  it("passes a usable accuracy through and caps it at 300 m", () => {
    expect(accuracyRadius(12)).toBe(12);
    expect(accuracyRadius(299.5)).toBe(299.5);
    expect(accuracyRadius(300)).toBe(300);
    expect(accuracyRadius(1500)).toBe(ACCURACY_CIRCLE_CAP_M);
    expect(ACCURACY_CIRCLE_CAP_M).toBe(300);
  });

  it("draws nothing for an absent, zero or non-finite accuracy", () => {
    expect(accuracyRadius(null)).toBeNull();
    expect(accuracyRadius(undefined)).toBeNull();
    expect(accuracyRadius(0)).toBeNull();
    expect(accuracyRadius(-4)).toBeNull();
    expect(accuracyRadius(Number.NaN)).toBeNull();
    expect(accuracyRadius(Number.POSITIVE_INFINITY)).toBeNull();
  });
});

describe("sourceLabel", () => {
  it("names every source the server can stamp, and the legacy null shape", () => {
    expect(sourceLabel("checkpoint")).toBe("Planned stop");
    expect(sourceLabel("action")).toBe("Phone GPS (tap)");
    expect(sourceLabel("ping")).toBe("Phone GPS (live)");
    expect(sourceLabel(null)).toBe("Checkpoint (older app)");
    expect(sourceLabel(undefined)).toBe("Checkpoint (older app)");
  });
});
