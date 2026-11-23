// The words the recovery and verification screens show (hardening Release 1b:
// design-lens specifications DL1, DL2, DL9, DL10, DL11). One module so the
// parent guide can be held to them: tests/unit/manualVocabulary.test.ts reads
// these constants and fails when the guide stops quoting one.

// DL1 — mail is not switched on (the public mailEnabled flag is false). No
// screen promises an email the platform does not send.
export const MAIL_OFF_FORGOT_TITLE = "Email recovery is off";
export const MAIL_OFF_FORGOT =
  "Email recovery isn't switched on yet. Ask your school office to reset your password.";
export const MAIL_OFF_SIGNUP =
  "Email isn't switched on yet, so no link was sent. Sign in, then ask your school office to confirm your sign-up.";
export const MAIL_OFF_VERIFY = "Ask your school office to confirm your sign-up.";

// DL2 — the sign-up card's persistent end state. The same words whether the
// address was new or already had an account (the mail explains which).
export const SIGNUP_DONE_TITLE = "Check your email";
export const SIGNUP_DONE_MAIL_OFF_TITLE = "Sign-up received";
export function signupDoneText(email: string): string {
  return `We sent a message to ${email}. Sign in with the password you just chose, then open the link in that email to finish signing up.`;
}
export const SIGNUP_DONE_NOTE =
  "Already had an account with this address? The email explains how to get back in.";

// DL11 — the forgot-password card's persistent end state.
export const FORGOT_SENT_TITLE = "Check your email";
export function forgotSentText(email: string): string {
  return `If an account exists for ${email}, a reset link is on its way. It works once and lasts 30 minutes. Nothing after a few minutes? Check spam, or ask your school office to reset your password.`;
}
export const RESET_LINK_INVALID_TITLE = "Reset link invalid";
export const RESET_LINK_INVALID_TEXT =
  "Reset links work once and expire after 30 minutes. Request a new one, or ask your school office to reset your password.";

// DL9 — the Home card while the address is unverified.
export const VERIFY_CARD_TITLE = "Verify your email";
export function verifyCardText(email: string): string {
  return `Check your email to finish sign-up. We sent a link to ${email}.`;
}
export const VERIFY_CARD_WRONG_ADDRESS =
  "Wrong address? Sign out and sign up again with the email your school has.";
export const RESEND_LABEL = "Resend link";
export const RESEND_SENDING = "Sending…";
export const RESEND_SENT = "Sent. Check spam; the link lasts 24 hours";
export const RESEND_COOLDOWN = "Try again in a minute";
export const EMAIL_VERIFIED_TOAST = "Email verified";

// DL10 — /verify-email.
export const VERIFY_SIGN_IN_PROMPT = "Sign in to finish verifying your email";
export const VERIFYING = "Verifying…";
export const VERIFIED_TITLE = "Email verified";
export const VERIFIED_TEXT =
  "Email verified. You can close this page and open SafeRide — your children will appear on Home";
export const WRONG_ACCOUNT_TITLE = "This link is for a different account";
export const WRONG_ACCOUNT_TEXT =
  "Sign out, then sign in with the account the email was sent to.";
export const VERIFY_LINK_INVALID_TITLE = "Verification link expired or used";
export const VERIFY_LINK_INVALID_TEXT =
  "Verification links work once and last 24 hours. Your newest link is the one that works.";
export const SEND_NEW_LINK = "Send a new link";
export const SIGN_IN_THEN_SEND = "Sign in, then send a new link";
export const VERIFY_LINK_MISSING_TITLE = "Verification link incomplete";
export const VERIFY_LINK_MISSING_TEXT =
  "Open the link from the email again, or copy the whole link into your browser.";
