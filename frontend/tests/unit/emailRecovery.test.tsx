// Recovery and verification screens (hardening Release 1b: U7, U8; DL1, DL2,
// DL9, DL11). The mail-off copy is covered here rather than in e2e: the local
// stack always runs the simulated transport, so its public mailEnabled flag
// is true there by design.
// The matchers are installed by the vitest setup file; this import brings
// their types into the typecheck (tsc includes tests/).
import "@testing-library/jest-dom/vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const mocks = vi.hoisted(() => ({
  mailEnabled: true as boolean | null,
  post: vi.fn(),
  signIn: vi.fn(),
}));

vi.mock("@/lib/publicConfig", () => ({ useMailEnabled: () => mocks.mailEnabled }));
vi.mock("@/lib/auth", () => ({ useAuth: () => ({ signIn: mocks.signIn }) }));
vi.mock("@/lib/apiClient", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/apiClient")>();
  return { ...actual, api: { ...actual.api, post: mocks.post } };
});

import { AuthPage } from "@/features/auth/AuthPage";
import {
  MAIL_OFF_FORGOT,
  MAIL_OFF_SIGNUP,
  MAIL_OFF_VERIFY,
  RESEND_COOLDOWN,
  RESEND_SENT,
  SIGNUP_DONE_TITLE,
  VERIFY_CARD_WRONG_ADDRESS,
  forgotSentText,
  signupDoneText,
  verifyCardText,
} from "@/features/auth/authCopy";
import { VerifyEmailCard } from "@/features/parent/VerifyEmailCard";
import { api } from "@/lib/apiClient";
import { reattachPushSubscription } from "@/lib/push";

function renderAuth(path = "/auth") {
  return render(
    <MemoryRouter initialEntries={[path]}>
      <AuthPage />
    </MemoryRouter>,
  );
}

beforeEach(() => {
  mocks.mailEnabled = true;
  mocks.post.mockReset();
  mocks.post.mockResolvedValue({ ok: true });
  mocks.signIn.mockReset();
});

afterEach(() => cleanup());

describe("forgot password (DL1, DL11)", () => {
  it("with mail off says recovery is not switched on and offers no form", () => {
    mocks.mailEnabled = false;
    renderAuth("/auth?forgot=1");
    expect(screen.getByText(MAIL_OFF_FORGOT)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Send Reset Link" })).toBeNull();
    expect(mocks.post).not.toHaveBeenCalled();
  });

  it("with mail on ends on a persistent in-card state naming the address", async () => {
    renderAuth("/auth?forgot=1");
    fireEvent.change(screen.getByLabelText("Email"), { target: { value: "amina@saferide.test" } });
    fireEvent.click(screen.getByRole("button", { name: "Send Reset Link" }));
    const notice = await screen.findByTestId("forgot-sent");
    expect(notice).toHaveAttribute("role", "status");
    expect(notice).toHaveTextContent(forgotSentText("amina@saferide.test"));
    expect(notice).toHaveTextContent("It works once and lasts 30 minutes.");
    expect(mocks.post).toHaveBeenCalledWith("/api/auth/forgot-password", {
      email: "amina@saferide.test",
    });
  });
});

describe("sign-up (DL1, DL2)", () => {
  async function signUp() {
    renderAuth();
    fireEvent.click(screen.getByRole("button", { name: "Sign up" }));
    fireEvent.change(screen.getByLabelText("Full name"), { target: { value: "Amina A" } });
    fireEvent.change(screen.getByLabelText("Email"), { target: { value: "amina@saferide.test" } });
    fireEvent.change(screen.getByLabelText("Password"), { target: { value: "Passw0rd!" } });
    fireEvent.click(screen.getByRole("button", { name: "Create Account" }));
    return screen.findByTestId("signup-done");
  }

  it("issues no sign-in and ends on 'Check your email' with a prefilled Sign in", async () => {
    const done = await signUp();
    expect(done).toHaveTextContent(SIGNUP_DONE_TITLE);
    expect(done).toHaveTextContent(signupDoneText("amina@saferide.test"));
    // No automatic login after sign-up, in either branch.
    expect(mocks.post).toHaveBeenCalledTimes(1);
    expect(mocks.post.mock.calls[0][0]).toBe("/api/auth/signup");
    expect(mocks.signIn).not.toHaveBeenCalled();

    fireEvent.click(screen.getByRole("button", { name: "Sign in" }));
    await waitFor(() => expect(screen.getByLabelText("Email")).toHaveValue("amina@saferide.test"));
    expect(screen.getByLabelText("Password")).toHaveValue("");
  });

  it("with mail off points to the school office instead of an email", async () => {
    mocks.mailEnabled = false;
    const done = await signUp();
    expect(done).toHaveTextContent(MAIL_OFF_SIGNUP);
    expect(done).not.toHaveTextContent(SIGNUP_DONE_TITLE);
  });
});

describe("the Home verify card (DL1, DL9)", () => {
  const noop = () => {};

  it("names the address and offers Resend while mail is on", () => {
    render(
      <VerifyEmailCard
        email="amina@saferide.test" mailEnabled resendState="idle" onResend={noop} onSignOut={noop}
      />,
    );
    expect(screen.getByText(verifyCardText("amina@saferide.test"))).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Resend link" })).toBeInTheDocument();
    expect(screen.getByText(VERIFY_CARD_WRONG_ADDRESS)).toBeInTheDocument();
  });

  it("drops Resend and asks the office to confirm when mail is off", () => {
    render(
      <VerifyEmailCard
        email="amina@saferide.test" mailEnabled={false} resendState="idle" onResend={noop} onSignOut={noop}
      />,
    );
    expect(screen.getByText(MAIL_OFF_VERIFY)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Resend link" })).toBeNull();
    expect(screen.queryByText(/We sent a link/)).toBeNull();
  });

  it("shows the sent and cooldown states", () => {
    const { rerender } = render(
      <VerifyEmailCard email="a@saferide.test" mailEnabled resendState="sent" onResend={noop} onSignOut={noop} />,
    );
    expect(screen.getByText(RESEND_SENT)).toBeInTheDocument();
    rerender(
      <VerifyEmailCard email="a@saferide.test" mailEnabled resendState="cooldown" onResend={noop} onSignOut={noop} />,
    );
    expect(screen.getByText(RESEND_COOLDOWN)).toBeInTheDocument();
  });
});

describe("push re-attach on sign-in (hardening U7)", () => {
  const subscription = {
    endpoint: "https://push.saferide.test/abc",
    toJSON: () => ({ keys: { p256dh: "p", auth: "a" } }),
  };

  function installBrowser(permission: NotificationPermission, sub: unknown) {
    Object.defineProperty(globalThis, "Notification", {
      configurable: true,
      value: { permission },
    });
    Object.defineProperty(navigator, "serviceWorker", {
      configurable: true,
      value: {
        getRegistration: vi.fn().mockResolvedValue({
          pushManager: { getSubscription: vi.fn().mockResolvedValue(sub) },
        }),
      },
    });
  }

  afterEach(() => {
    localStorage.clear();
    Reflect.deleteProperty(navigator, "serviceWorker");
  });

  it("re-posts the browser's existing subscription", async () => {
    installBrowser("granted", subscription);
    await expect(reattachPushSubscription()).resolves.toBe(true);
    expect(api.post).toHaveBeenCalledWith(
      "/api/push/subscribe",
      expect.objectContaining({ endpoint: subscription.endpoint, p256dh: "p", auth: "a" }),
    );
  });

  it("never prompts or subscribes: no permission or no subscription means nothing", async () => {
    installBrowser("default", subscription);
    await expect(reattachPushSubscription()).resolves.toBe(false);
    installBrowser("granted", null);
    await expect(reattachPushSubscription()).resolves.toBe(false);
    expect(api.post).not.toHaveBeenCalled();
  });
});
