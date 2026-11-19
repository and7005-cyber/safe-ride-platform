import { readdirSync, readFileSync } from "node:fs";
import { dirname, join, relative, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { act, cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { ConfirmProvider, useConfirm, type ConfirmOptions } from "@/components/ui/confirm-dialog";

/**
 * Every dialog tells assistive technology what it is for.
 *
 * Radix links each DialogContent to a description through aria-describedby and
 * warns in the console ("Missing `Description` or `aria-describedby={undefined}`
 * for {DialogContent}") when none is rendered — the 1a phone checks found that
 * warning on the live site, from about twenty admin and provider dialogs. Each
 * dialog now renders a DialogDescription (the visible intro paragraph where it
 * has one, a screen-reader-only sentence otherwise), and the shared confirm
 * dialog opts out explicitly when its caller passes no description.
 */

const SRC = resolve(dirname(fileURLToPath(import.meta.url)), "../../src");

function tsxFiles(dir: string): string[] {
  return readdirSync(dir, { withFileTypes: true }).flatMap((entry) => {
    const path = join(dir, entry.name);
    if (entry.isDirectory()) return tsxFiles(path);
    return entry.name.endsWith(".tsx") ? [path] : [];
  });
}

describe("dialog descriptions", () => {
  it("every DialogContent renders a DialogDescription or opts out with aria-describedby", () => {
    const undescribed: string[] = [];
    let dialogs = 0;
    for (const file of tsxFiles(SRC)) {
      // The primitive itself defines DialogContent; it is not a dialog.
      if (file.endsWith(join("components", "ui", "dialog.tsx"))) continue;
      const source = readFileSync(file, "utf8");
      for (const match of source.matchAll(/<DialogContent\b/g)) {
        dialogs += 1;
        const start = match.index ?? 0;
        const end = source.indexOf("</DialogContent>", start);
        const block = source.slice(start, end === -1 ? undefined : end);
        const openingTag = block.slice(0, block.indexOf(">") + 1);
        if (!block.includes("<DialogDescription") && !openingTag.includes("aria-describedby")) {
          const line = source.slice(0, start).split("\n").length;
          undescribed.push(`${relative(SRC, file)}:${line}`);
        }
      }
    }
    expect(dialogs).toBeGreaterThan(20);
    expect(undescribed).toEqual([]);
  });
});

// The plain (boolean) overload of confirm(): no note requested.
type PlainConfirm = ConfirmOptions & { note?: undefined };

function OpenConfirm({ options }: { options: PlainConfirm }) {
  const confirm = useConfirm();
  return (
    <button type="button" onClick={() => void confirm(options)}>
      open
    </button>
  );
}

async function openConfirm(options: PlainConfirm) {
  render(
    <ConfirmProvider>
      <OpenConfirm options={options} />
    </ConfirmProvider>,
  );
  await act(async () => {
    screen.getByRole("button", { name: "open" }).click();
  });
  return screen.getByRole("dialog", { name: options.title });
}

const MISSING_DESCRIPTION = /Missing `Description`/;

describe("confirm dialog", () => {
  afterEach(() => {
    cleanup();
    vi.restoreAllMocks();
  });

  it("without a description: no aria-describedby and no Radix warning", async () => {
    const warn = vi.spyOn(console, "warn").mockImplementation(() => {});
    const dialog = await openConfirm({ title: "End this run?" });
    expect(dialog.hasAttribute("aria-describedby")).toBe(false);
    expect(warn.mock.calls.flat().some((arg) => MISSING_DESCRIPTION.test(String(arg)))).toBe(false);
  });

  it("with a description: the dialog is described by it", async () => {
    const warn = vi.spyOn(console, "warn").mockImplementation(() => {});
    const dialog = await openConfirm({
      title: "End this run?",
      description: "Two children are still unconfirmed.",
    });
    const describedBy = dialog.getAttribute("aria-describedby");
    expect(describedBy).toBeTruthy();
    expect(document.getElementById(describedBy ?? "")?.textContent).toBe(
      "Two children are still unconfirmed.",
    );
    expect(warn.mock.calls.flat().some((arg) => MISSING_DESCRIPTION.test(String(arg)))).toBe(false);
  });
});
