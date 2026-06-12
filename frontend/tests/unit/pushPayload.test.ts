// Behavioral coverage for the service worker's push payload parser
// (public/push-payload.js): both delivery shapes the backend produces must
// normalize to the same {title, body, url, type}.
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { describe, expect, it } from "vitest";

type Parsed = { title: string; body: string; url: string; type: string };

function loadParser(): (raw: unknown) => Parsed {
  const source = readFileSync(
    resolve(__dirname, "../../public/push-payload.js"),
    "utf8",
  );
  const sandbox: { parsePushPayload?: (raw: unknown) => Parsed } = {};
  new Function("self", source)(sandbox);
  if (!sandbox.parsePushPayload) throw new Error("parsePushPayload not exported onto self");
  return sandbox.parsePushPayload;
}

const parse = loadParser();

describe("parsePushPayload", () => {
  it("parses the raw web-push shape the backend sends via pywebpush", () => {
    // Mirrors backend _send_web_push: {"title", "body", "url", "type"}
    expect(
      parse({ title: "Boarded the bus", body: "Leila has boarded Kifaru Bus.", url: "/parent/alerts", type: "student-boarded" }),
    ).toEqual({
      title: "Boarded the bus",
      body: "Leila has boarded Kifaru Bus.",
      url: "/parent/alerts",
      type: "student-boarded",
    });
  });

  it("parses the FCM webpush shape (notification + data)", () => {
    // Mirrors backend _send_fcm: WebpushConfig(notification=..., data=...)
    expect(
      parse({
        notification: { title: "Bus approaching", body: "Kifaru Bus is approaching Leila's stop.", icon: "/icons/icon-192.png" },
        data: { url: "/parent/alerts", type: "bus-approaching" },
        fcmMessageId: "m1",
      }),
    ).toEqual({
      title: "Bus approaching",
      body: "Kifaru Bus is approaching Leila's stop.",
      url: "/parent/alerts",
      type: "bus-approaching",
    });
  });

  it("prefers FCM notification fields over top-level ones", () => {
    const parsed = parse({
      title: "wrong",
      notification: { title: "right", body: "body" },
      data: { url: "/parent/alerts" },
    });
    expect(parsed.title).toBe("right");
  });

  it("falls back to fcmOptions.link for the click-through url", () => {
    expect(parse({ notification: { title: "t" }, fcmOptions: { link: "/somewhere" } }).url).toBe(
      "/somewhere",
    );
  });

  it("survives null, junk, and empty payloads with safe defaults", () => {
    for (const junk of [null, undefined, 42, "text", {}]) {
      const parsed = parse(junk);
      expect(parsed.title).toBe("SafeRide");
      expect(parsed.url).toBe("/parent/alerts");
      expect(parsed.type).toBe("custom");
    }
  });
});
