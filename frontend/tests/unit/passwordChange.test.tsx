// Hardening U9 (DL6, DL12, DL13): the office reset and Confirm and reset on
// the Parents page, the voluntary Change password dialog, and where each role
// reaches it — never from a session created by PIN. The live flows run in
// tests/e2e/parent-office-reset.spec.ts; here, the states and words.
// The matchers are installed by the vitest setup file; this import brings
// their types into the typecheck (tsc includes tests/).
import "@testing-library/jest-dom/vitest";
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { AuthUser } from "@/lib/auth";

const mocks = vi.hoisted(() => ({
  user: null as AuthUser | null,
  post: vi.fn(),
  toast: vi.fn(),
  refresh: vi.fn(),
  signOut: vi.fn(),
  parents: [] as any[],
  mailEnabled: true as boolean | null,
}));

vi.mock("@/lib/auth", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/auth")>();
  return {
    ...actual,
    useAuth: () => ({
      user: mocks.user,
      role: mocks.user?.role ?? null,
      loading: false,
      signIn: vi.fn(),
      signOut: mocks.signOut,
      refresh: mocks.refresh,
    }),
    useIsDirector: () => true,
  };
});
vi.mock("@/components/ui/use-toast", () => ({ useToast: () => ({ toast: mocks.toast }) }));
vi.mock("@/lib/publicConfig", () => ({ useMailEnabled: () => mocks.mailEnabled }));
vi.mock("@/lib/queries", () => ({
  useParents: () => ({ data: mocks.parents }),
  useSchoolKey: () => (key: string) => ["school", "s-1", key],
}));
vi.mock("@/lib/apiClient", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/apiClient")>();
  return { ...actual, api: { ...actual.api, post: mocks.post } };
});

import { ConfirmProvider } from "@/components/ui/confirm-dialog";
import { TooltipProvider } from "@/components/ui/tooltip";
import { RoleMobileLayout } from "@/app/layouts/RoleMobileLayout";
import { ParentsPage, temporaryPasswordState } from "@/features/admin/ParentsPage";
import {
  CHANGE_PASSWORD_DIALOG_TEXT,
  CONFIRM_SLOT_WARNING,
  NOTHING_TO_CONFIRM_TEXT,
  NOTHING_TO_CONFIRM_TITLE,
  NOT_RESETTABLE_HINT,
  PASSWORD_CHANGED_TOAST,
  TEMPORARY_PASSWORD_EXPIRED_CHIP,
  groupInFours,
  resetConfirmText,
} from "@/features/auth/authCopy";
import { ChangePasswordDialog, ChangePasswordPage } from "@/features/auth/ChangePasswordPage";
import { ProviderLayout } from "@/features/provider/ProviderLayout";
import { ApiError } from "@/lib/apiClient";
import { canChangeOwnPassword } from "@/lib/auth";

function user(overrides: Partial<AuthUser> = {}): AuthUser {
  return {
    id: "u-1",
    email: "someone@saferide.test",
    fullName: "Some One",
    role: null,
    memberships: [],
    pendingOffers: [],
    activeSchoolId: null,
    provider: null,
    supportSession: null,
    mustChangePassword: false,
    emailVerified: true,
    signedInWithPin: false,
    hydrated: true,
    ...overrides,
  };
}

function withProviders(node: React.ReactNode) {
  const client = new QueryClient();
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter>
        <TooltipProvider>
          <ConfirmProvider>{node}</ConfirmProvider>
        </TooltipProvider>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  mocks.user = user();
  mocks.post.mockReset();
  mocks.toast.mockReset();
  mocks.refresh.mockReset();
  mocks.parents = [];
  mocks.mailEnabled = true;
});

afterEach(() => cleanup());

describe("helpers", () => {
  it("groups a relayed code in fours for reading out", () => {
    expect(groupInFours("CMEJ4QPBJTNY10C5")).toBe("CMEJ 4QPB JTNY 10C5");
    expect(groupInFours("ABC")).toBe("ABC");
  });

  it("offers Change password to every session except one created by PIN", () => {
    expect(canChangeOwnPassword(null)).toBe(false);
    expect(canChangeOwnPassword(user())).toBe(true);
    expect(canChangeOwnPassword(user({ role: "driver", signedInWithPin: true }))).toBe(false);
  });

  it("derives the row chip from the temporary-password state", () => {
    const now = Date.parse("2026-11-23T10:00:00Z");
    expect(temporaryPasswordState({ mustChangePassword: false }, now)).toBeNull();
    expect(
      temporaryPasswordState(
        { mustChangePassword: true, temporaryPasswordExpiresAt: "2026-11-23T09:00:00Z" },
        now,
      ),
    ).toEqual({ expired: true, label: TEMPORARY_PASSWORD_EXPIRED_CHIP });
    const live = temporaryPasswordState(
      { mustChangePassword: true, temporaryPasswordExpiresAt: "2026-11-26T10:00:00Z" },
      now,
    );
    expect(live?.expired).toBe(false);
    expect(live?.label).toMatch(/^Temporary password · expires 26 Nov, /);
  });
});

describe("Change password (DL13)", () => {
  it("voluntary: a dialog with the stay-signed-in line, Cancel and no Sign out", async () => {
    const onOpenChange = vi.fn();
    mocks.post.mockResolvedValue({ ok: true });
    withProviders(<ChangePasswordDialog open onOpenChange={onOpenChange} />);
    const dialog = screen.getByTestId("change-password-dialog");
    expect(dialog).toHaveTextContent(CHANGE_PASSWORD_DIALOG_TEXT);
    expect(within(dialog).getByRole("button", { name: "Cancel" })).toBeInTheDocument();
    expect(within(dialog).queryByRole("button", { name: "Sign out" })).toBeNull();
    expect(dialog).not.toHaveTextContent("Your temporary password must be changed");

    fireEvent.change(screen.getByLabelText("Current password"), { target: { value: "Old123!" } });
    fireEvent.change(screen.getByLabelText("New password"), { target: { value: "New1234!" } });
    fireEvent.change(screen.getByLabelText("Confirm new password"), { target: { value: "New1234!" } });
    fireEvent.click(within(dialog).getByRole("button", { name: "Change password" }));
    await waitFor(() => expect(onOpenChange).toHaveBeenCalledWith(false));
    expect(mocks.post).toHaveBeenCalledWith("/api/auth/change-password", {
      currentPassword: "Old123!",
      newPassword: "New1234!",
    });
    expect(mocks.toast).toHaveBeenCalledWith({ title: PASSWORD_CHANGED_TOAST });
    // The voluntary change does not wait on /me: nothing was holding the page.
    expect(mocks.refresh).not.toHaveBeenCalled();
  });

  it("forced: keeps its own screen, words and Sign out link", () => {
    withProviders(<ChangePasswordPage />);
    expect(
      screen.getByText(/Your temporary password must be changed before you can continue\./),
    ).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Sign out" })).toBeInTheDocument();
    expect(screen.queryByText(CHANGE_PASSWORD_DIALOG_TEXT)).toBeNull();
  });
});

describe("where Change password is reached", () => {
  const NAV = [] as never[];

  it("the driver header offers it to a password session only", () => {
    mocks.user = user({ role: "driver" });
    const { unmount } = withProviders(
      <RoleMobileLayout nav={NAV} variant="primary" title="Home">x</RoleMobileLayout>,
    );
    expect(screen.getByRole("button", { name: "Change password" })).toBeInTheDocument();
    unmount();

    mocks.user = user({ role: "driver", signedInWithPin: true });
    withProviders(<RoleMobileLayout nav={NAV} variant="primary" title="Home">x</RoleMobileLayout>);
    expect(screen.queryByRole("button", { name: "Change password" })).toBeNull();
  });

  it("parents use their Profile page, not the header", () => {
    mocks.user = user({ role: "parent" });
    withProviders(<RoleMobileLayout nav={NAV} variant="accent" title="Home">x</RoleMobileLayout>);
    expect(screen.queryByRole("button", { name: "Change password" })).toBeNull();
  });

  it("the provider console's account menu carries it", async () => {
    mocks.user = user({
      provider: { isProvider: true, totpEnrolled: true, totpVerifiedAt: null },
    });
    withProviders(<ProviderLayout>x</ProviderLayout>);
    const trigger = screen.getByRole("button", { name: "Account menu" });
    fireEvent.keyDown(trigger, { key: "Enter" });
    fireEvent.click(await screen.findByTestId("menu-change-password"));
    expect(await screen.findByTestId("change-password-dialog")).toHaveTextContent(
      CHANGE_PASSWORD_DIALOG_TEXT,
    );
  });
});

describe("the Parents page (DL12)", () => {
  const registered = {
    id: "p-1", full_name: "Amina A", email: "amina@saferide.test", phone: null,
    status: "registered", students: ["Faith A"], shared: false, linkAccepted: true,
    mustChangePassword: false, temporaryPasswordExpiresAt: null,
  };

  it("a Shared row keeps Reset enabled while Edit is frozen; a pending link greys Reset out", () => {
    mocks.parents = [
      { ...registered, shared: true },
      { ...registered, id: "p-2", email: "pending@saferide.test", linkAccepted: false },
    ];
    withProviders(<ParentsPage />);
    expect(screen.getByRole("button", { name: "Reset password for amina@saferide.test" })).toBeEnabled();
    expect(screen.getByRole("button", { name: "Edit amina@saferide.test (shared)" })).toBeDisabled();
    expect(screen.queryByRole("button", { name: "Delete amina@saferide.test" })).toBeNull();
    expect(screen.getByRole("button", { name: "Reset password for pending@saferide.test" })).toBeDisabled();
  });

  it("confirms, resets and reveals the password once, grouped in fours", async () => {
    mocks.parents = [registered];
    mocks.post.mockResolvedValue({ ok: true, temporaryPassword: "CMEJ4QPBJTNY10C5" });
    withProviders(<ParentsPage />);
    fireEvent.click(screen.getByRole("button", { name: "Reset password for amina@saferide.test" }));
    const confirmDialog = await screen.findByRole("dialog");
    expect(confirmDialog).toHaveTextContent(resetConfirmText(true));
    fireEvent.click(within(confirmDialog).getByRole("button", { name: "Reset password" }));
    const shown = await screen.findByTestId("temp-password");
    expect(shown).toHaveTextContent("CMEJ 4QPB JTNY 10C5");
    expect(shown).toHaveAttribute("data-value", "CMEJ4QPBJTNY10C5");
    expect(mocks.post).toHaveBeenCalledWith("/api/accounts/parents/p-1/reset-password");
  });

  it("with mail off the confirmation promises no email", async () => {
    mocks.mailEnabled = false;
    mocks.parents = [registered];
    withProviders(<ParentsPage />);
    fireEvent.click(screen.getByRole("button", { name: "Reset password for amina@saferide.test" }));
    const confirmDialog = await screen.findByRole("dialog");
    expect(confirmDialog).toHaveTextContent(resetConfirmText(false));
    expect(confirmDialog).not.toHaveTextContent("emailed");
  });

  it("the neutral refusal adds the Forgot password hint", async () => {
    mocks.parents = [registered];
    mocks.post.mockRejectedValue(
      new ApiError("This account cannot be reset from the Parents page", 409, "not-resettable-here"),
    );
    withProviders(<ParentsPage />);
    fireEvent.click(screen.getByRole("button", { name: "Reset password for amina@saferide.test" }));
    fireEvent.click(within(await screen.findByRole("dialog")).getByRole("button", { name: "Reset password" }));
    await waitFor(() => expect(mocks.toast).toHaveBeenCalled());
    expect(mocks.toast.mock.calls[0][0].description).toBe(
      `This account cannot be reset from the Parents page. ${NOT_RESETTABLE_HINT}`,
    );
  });

  it("Confirm and reset warns first and answers 'nothing to confirm' neutrally", async () => {
    mocks.parents = [{
      id: null, full_name: "Slot Parent", email: "slot@saferide.test", phone: null,
      status: "pending", students: ["Kid"], shared: false, linkAccepted: false,
      mustChangePassword: false, temporaryPasswordExpiresAt: null,
    }];
    mocks.post.mockRejectedValue(
      new ApiError("Nothing to confirm yet. Ask the parent to sign up with this email.", 409, "nothing-to-confirm"),
    );
    withProviders(<ParentsPage />);
    fireEvent.click(screen.getByRole("button", { name: "Confirm and reset for slot@saferide.test" }));
    const confirmDialog = await screen.findByRole("dialog");
    expect(confirmDialog).toHaveTextContent(CONFIRM_SLOT_WARNING);
    fireEvent.click(within(confirmDialog).getByRole("button", { name: "Confirm and reset" }));
    await waitFor(() => expect(mocks.toast).toHaveBeenCalled());
    expect(mocks.post).toHaveBeenCalledWith("/api/accounts/parents/slots/confirm", {
      email: "slot@saferide.test",
    });
    expect(mocks.toast.mock.calls[0][0]).toEqual({
      title: NOTHING_TO_CONFIRM_TITLE,
      description: NOTHING_TO_CONFIRM_TEXT,
    });
  });
});
