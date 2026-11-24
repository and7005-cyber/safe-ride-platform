// The driver's PIN tab (hardening U10; DL3, DL4, DL5): the code step, the
// "Is this your school?" step on the typed and the link paths, the PIN form
// under the school's name, Change school, and the refusals by code — the
// lock line counted down from Retry-After included.
import "@testing-library/jest-dom/vitest";
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter, useLocation } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const mocks = vi.hoisted(() => ({
  get: vi.fn(),
  post: vi.fn(),
  signIn: vi.fn(),
  toast: vi.fn(),
}));

vi.mock("@/lib/publicConfig", () => ({ useMailEnabled: () => true }));
vi.mock("@/lib/auth", () => ({ useAuth: () => ({ signIn: mocks.signIn }) }));
vi.mock("@/components/ui/use-toast", () => ({ useToast: () => ({ toast: mocks.toast }) }));
vi.mock("@/lib/apiClient", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/apiClient")>();
  return { ...actual, api: { ...actual.api, get: mocks.get, post: mocks.post } };
});

import { AuthPage } from "@/features/auth/AuthPage";
import {
  CHANGE_SCHOOL_CONFIRM_TEXT,
  CONFIRM_SCHOOL_NO,
  CONFIRM_SCHOOL_QUESTION,
  CONFIRM_SCHOOL_YES,
  DRIVER_CODE_IS_SCHOOL_CODE,
  DRIVER_CODE_NOT_FOUND,
  DRIVER_CODE_OFFLINE,
  DRIVER_CODE_PROMPT,
  DRIVER_CODE_THROTTLED,
  INVALID_PIN_TITLE,
  pinLockedText,
} from "@/features/auth/authCopy";
import { ApiError } from "@/lib/apiClient";
import { readDriverSchool, storeDriverSchool } from "@/lib/driverSchool";

const SCHOOL = { schoolId: "5cae0000-0000-0000-0000-000000000001", name: "Greenfield Academy" };

let currentSearch = "";
function LocationProbe() {
  currentSearch = useLocation().search;
  return null;
}

function renderAuth(path = "/auth") {
  return render(
    <MemoryRouter initialEntries={[path]}>
      <AuthPage />
      <LocationProbe />
    </MemoryRouter>,
  );
}

beforeEach(() => {
  localStorage.clear();
  mocks.get.mockReset();
  mocks.post.mockReset();
  mocks.signIn.mockReset();
  mocks.toast.mockReset();
});

afterEach(() => {
  cleanup();
  vi.useRealTimers();
});

describe("a phone that is not set up (DL3, DL5)", () => {
  it("opens on the email tab and asks the PIN tab for the driver code", () => {
    renderAuth();
    expect(screen.getByRole("tab", { name: /Email/ })).toHaveAttribute("aria-selected", "true");
    fireEvent.mouseDown(screen.getByRole("tab", { name: /Driver PIN/ }));
    expect(screen.getByText(DRIVER_CODE_PROMPT)).toBeInTheDocument();
    const input = screen.getByLabelText("Driver code");
    // Phone keyboards: capitals, no autocorrect or suggestions.
    expect(input).toHaveAttribute("autocapitalize", "characters");
    expect(input).toHaveAttribute("autocorrect", "off");
    expect(input).toHaveAttribute("spellcheck", "false");
    expect(input).toHaveAttribute("autocomplete", "off");
    expect(screen.queryByLabelText("Driver PIN", { selector: "input" })).toBeNull();
  });

  it("looks a typed code up, asks 'Is this your school?' and stores the answer", async () => {
    mocks.get.mockResolvedValue(SCHOOL);
    renderAuth();
    fireEvent.mouseDown(screen.getByRole("tab", { name: /Driver PIN/ }));
    fireEvent.change(screen.getByLabelText("Driver code"), { target: { value: "gfaO-7kIq" } });
    fireEvent.click(screen.getByRole("button", { name: "Continue" }));
    expect(await screen.findByText(CONFIRM_SCHOOL_QUESTION)).toBeInTheDocument();
    expect(mocks.get).toHaveBeenCalledWith("/api/auth/schools/GFA07K1Q");
    expect(screen.getByTestId("confirm-school-name")).toHaveTextContent(SCHOOL.name);
    expect(readDriverSchool()).toBeNull(); // nothing stored before the driver says yes

    fireEvent.click(screen.getByRole("button", { name: CONFIRM_SCHOOL_YES }));
    expect(readDriverSchool()).toEqual(SCHOOL);
    expect(screen.getByTestId("driver-school-name")).toHaveTextContent(SCHOOL.name);
    expect(screen.getByLabelText("Driver PIN", { selector: "input" })).toBeInTheDocument();
  });

  it("'No, enter a different code' goes back to an empty code step", async () => {
    mocks.get.mockResolvedValue(SCHOOL);
    renderAuth();
    fireEvent.mouseDown(screen.getByRole("tab", { name: /Driver PIN/ }));
    fireEvent.change(screen.getByLabelText("Driver code"), { target: { value: "GFA0-7K1Q" } });
    fireEvent.click(screen.getByRole("button", { name: "Continue" }));
    fireEvent.click(await screen.findByRole("button", { name: CONFIRM_SCHOOL_NO }));
    expect(screen.getByLabelText("Driver code")).toHaveValue("");
    expect(readDriverSchool()).toBeNull();
  });

  it.each([
    [new ApiError("No school has that driver code", 404), DRIVER_CODE_NOT_FOUND],
    [new ApiError("Too many tries.", 429), DRIVER_CODE_THROTTLED],
    [new TypeError("Failed to fetch"), DRIVER_CODE_OFFLINE],
  ])("shows its own words when the lookup fails (%s)", async (error, text) => {
    mocks.get.mockRejectedValue(error);
    renderAuth();
    fireEvent.mouseDown(screen.getByRole("tab", { name: /Driver PIN/ }));
    fireEvent.change(screen.getByLabelText("Driver code"), { target: { value: "ZZZZ-ZZZZ" } });
    fireEvent.click(screen.getByRole("button", { name: "Continue" }));
    expect(await screen.findByRole("alert")).toHaveTextContent(text);
  });

  it("tells a driver who typed the school code that it is a different code", () => {
    renderAuth();
    fireEvent.mouseDown(screen.getByRole("tab", { name: /Driver PIN/ }));
    fireEvent.change(screen.getByLabelText("Driver code"), { target: { value: "GFA-001" } });
    fireEvent.click(screen.getByRole("button", { name: "Continue" }));
    expect(screen.getByRole("alert")).toHaveTextContent(DRIVER_CODE_IS_SCHOOL_CODE);
    expect(mocks.get).not.toHaveBeenCalled();
  });
});

describe("the sign-in link (DL3)", () => {
  it("opens the PIN tab, confirms the school and strips ?school= once stored", async () => {
    mocks.get.mockResolvedValue(SCHOOL);
    renderAuth("/auth?school=gfa0-7k1q");
    expect(screen.getByRole("tab", { name: /Driver PIN/ })).toHaveAttribute("aria-selected", "true");
    expect(await screen.findByText(CONFIRM_SCHOOL_QUESTION)).toBeInTheDocument();
    expect(currentSearch).toContain("school=");
    fireEvent.click(screen.getByRole("button", { name: CONFIRM_SCHOOL_YES }));
    expect(readDriverSchool()).toEqual(SCHOOL);
    await waitFor(() => expect(currentSearch).not.toContain("school="));
  });

  it("asks again when the link names another school than the stored one", async () => {
    storeDriverSchool({ schoolId: "other", name: "Other School" });
    mocks.get.mockResolvedValue(SCHOOL);
    renderAuth("/auth?school=GFA07K1Q");
    expect(await screen.findByTestId("confirm-school-name")).toHaveTextContent(SCHOOL.name);
  });

  it("keeps a set-up phone's school when an old link no longer resolves", async () => {
    storeDriverSchool(SCHOOL);
    mocks.get.mockRejectedValue(new ApiError("No school has that driver code", 404));
    renderAuth("/auth?school=OLDC0DE1");
    await waitFor(() => expect(currentSearch).not.toContain("school="));
    expect(readDriverSchool()).toEqual(SCHOOL);
    expect(screen.getByTestId("driver-school-name")).toHaveTextContent(SCHOOL.name);
  });

  it("asks nothing when the link names the school the phone is set up for", async () => {
    storeDriverSchool(SCHOOL);
    mocks.get.mockResolvedValue(SCHOOL);
    renderAuth("/auth?school=GFA07K1Q");
    await waitFor(() => expect(currentSearch).not.toContain("school="));
    expect(screen.queryByText(CONFIRM_SCHOOL_QUESTION)).toBeNull();
    expect(screen.getByTestId("driver-school-name")).toHaveTextContent(SCHOOL.name);
  });
});

describe("a phone that is set up (AE8, DL4)", () => {
  beforeEach(() => storeDriverSchool(SCHOOL));

  function signInWith(pin: string) {
    renderAuth();
    expect(screen.getByRole("tab", { name: /Driver PIN/ })).toHaveAttribute("aria-selected", "true");
    fireEvent.change(screen.getByLabelText("Driver PIN", { selector: "input" }), { target: { value: pin } });
    fireEvent.click(screen.getByRole("button", { name: "Sign In with PIN" }));
  }

  it("shows the school's name and sends the PIN with the school's id", async () => {
    mocks.post.mockResolvedValue({
      token: "pin_t",
      user: { id: "u", email: "d@saferide.test", role: "driver", signedInWithPin: true },
    });
    signInWith("482915");
    await waitFor(() => expect(mocks.signIn).toHaveBeenCalled());
    expect(mocks.post).toHaveBeenCalledWith("/api/auth/pin-login", {
      pin: "482915",
      schoolId: SCHOOL.schoolId,
    });
  });

  it("a wrong PIN says Invalid PIN", async () => {
    mocks.post.mockRejectedValue(new ApiError("Invalid PIN", 401, "invalid-pin"));
    signInWith("000001");
    await waitFor(() =>
      expect(mocks.toast).toHaveBeenCalledWith(expect.objectContaining({ title: INVALID_PIN_TITLE })),
    );
  });

  it("the school lock counts down from Retry-After and holds the button", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    mocks.post.mockRejectedValue(
      new ApiError("paused", 429, "school-pin-locked", null, 125),
    );
    signInWith("482915");
    const line = await screen.findByTestId("pin-locked");
    expect(line).toHaveTextContent(pinLockedText(3));
    expect(screen.getByRole("button", { name: "Sign In with PIN" })).toBeDisabled();
    await act(async () => {
      vi.advanceTimersByTime(66_000);
    });
    expect(screen.getByTestId("pin-locked")).toHaveTextContent(pinLockedText(1));
    await act(async () => {
      vi.advanceTimersByTime(60_000);
    });
    expect(screen.queryByTestId("pin-locked")).toBeNull();
    expect(screen.getByRole("button", { name: "Sign In with PIN" })).toBeEnabled();
  });

  it("the per-address limit keeps its own words", async () => {
    mocks.post.mockRejectedValue(new ApiError("Too many PIN attempts. Try again shortly.", 429));
    signInWith("482915");
    await waitFor(() =>
      expect(mocks.toast).toHaveBeenCalledWith(
        expect.objectContaining({ description: "Too many PIN attempts. Try again shortly." }),
      ),
    );
    expect(screen.queryByTestId("pin-locked")).toBeNull();
  });

  it("school-required returns to the code step", async () => {
    mocks.post.mockRejectedValue(new ApiError("not set up", 401, "school-required"));
    signInWith("482915");
    expect(await screen.findByText(DRIVER_CODE_PROMPT)).toBeInTheDocument();
    expect(readDriverSchool()).toBeNull();
  });

  it("Change school asks first, then clears the binding", () => {
    renderAuth();
    fireEvent.click(screen.getByRole("button", { name: "Change school" }));
    expect(screen.getByText(CHANGE_SCHOOL_CONFIRM_TEXT)).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Cancel" }));
    expect(readDriverSchool()).toEqual(SCHOOL);

    fireEvent.click(screen.getByRole("button", { name: "Change school" }));
    const dialog = screen.getByRole("alertdialog");
    fireEvent.click(
      Array.from(dialog.querySelectorAll("button")).find((b) => b.textContent === "Change school")!,
    );
    expect(readDriverSchool()).toBeNull();
    expect(screen.getByText(DRIVER_CODE_PROMPT)).toBeInTheDocument();
  });
});
