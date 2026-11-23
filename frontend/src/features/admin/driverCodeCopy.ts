// The Drivers page's driver code card (hardening U10, DL5, DL7, DL8). One
// module so the office guide can be held to these words:
// tests/unit/manualVocabulary.test.ts fails when the guide stops quoting one.

export const DRIVER_CODE_CARD_TITLE = "Driver code";
export const DRIVER_CODE_CARD_LEAD =
  "Drivers type this code once on each phone, then sign in with their PIN. It is not the school code.";
export const COPY_DRIVER_CODE_LABEL = "Copy code";
export const COPY_DRIVER_MESSAGE_LABEL = "Copy message for drivers";
export const SHARE_BY_LINK_TITLE = "Share by link or QR";
export const COPY_LINK_LABEL = "Copy link";
export const QR_ACCESSIBLE_NAME = "QR code for the driver sign-in link";
export const REGENERATE_LABEL = "Regenerate";
export const REGENERATE_CONFIRM_TITLE = "Replace the driver code?";
export const REGENERATE_CONFIRM_TEXT =
  "Links and QR codes already sent stop working. Phones already set up keep working.";
export const REGENERATE_CONFIRM_LABEL = "Replace code";
export const REGENERATE_CANCEL_LABEL = "Keep the current code";
export const REGENERATED_TOAST = "Driver code replaced";

/** The ready-to-send message (DL7): the code first, then the link, then the
 * installed-app instruction iOS needs (DL5). */
export function driverMessage(schoolName: string, code: string, link: string): string {
  return [
    `SafeRide driver sign-in for ${schoolName || "your school"}.`,
    `Open the SafeRide app. When it asks for the driver code, type ${code}. If it shows another school's name, tap Change school.`,
    `Or open this link on your phone: ${link}`,
    "Then sign in with your PIN.",
  ].join("\n");
}

/** The PIN reveal dialog's line about the code (DL7): a driver added after
 * go-live needs it on a new phone. */
export function pinRevealCodeLine(code: string): string {
  return `On a new phone they first enter the driver code ${code}.`;
}
