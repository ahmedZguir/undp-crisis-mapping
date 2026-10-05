// AdminsPage: invite, disable, self-row guard. The API module is mocked so the
// page never touches `fetch`. The self-row check is defence in depth on top of
// the server-side `cannot_modify_self` guard.

import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { Coordinator } from "../../types/admin";

vi.mock("../../api/admin", () => {
  return {
    CoordinatorEmailExistsError: class CoordinatorEmailExistsError extends Error {
      constructor() {
        super("email_exists");
        this.name = "CoordinatorEmailExistsError";
      }
    },
    listCoordinators: vi.fn(),
    createCoordinator: vi.fn(),
    rotateCoordinatorPassword: vi.fn(),
    disableCoordinator: vi.fn(),
    enableCoordinator: vi.fn(),
  };
});

// Stub the timezone hook so the test does not depend on the browser's locale
// or on localStorage initialisation.
vi.mock("../../hooks/useAdminTimezone", () => ({
  useAdminTimezone: () => "UTC",
}));

import {
  CoordinatorEmailExistsError,
  createCoordinator,
  disableCoordinator,
  enableCoordinator,
  listCoordinators,
  rotateCoordinatorPassword,
} from "../../api/admin";
import { AdminsPage } from "./AdminsPage";

const listMock = vi.mocked(listCoordinators);
const createMock = vi.mocked(createCoordinator);
const disableMock = vi.mocked(disableCoordinator);
const enableMock = vi.mocked(enableCoordinator);
const rotateMock = vi.mocked(rotateCoordinatorPassword);

const SELF: Coordinator = {
  id: "00000000-0000-4000-8000-000000000001",
  email: "me@example.com",
  role: "admin",
  created_at: "2024-01-02T03:04:05+00:00",
  banned_until: null,
  is_disabled: false,
  is_self: true,
};

// A coordinator-tier peer, the actionable tier: admin peers are immutable here.
const PEER: Coordinator = {
  id: "00000000-0000-4000-8000-000000000002",
  email: "peer@example.com",
  role: "coordinator",
  created_at: "2024-02-03T04:05:06+00:00",
  banned_until: null,
  is_disabled: false,
  is_self: false,
};

// Another admin (not self): managed out of band, so the row exposes no actions.
const ADMIN_PEER: Coordinator = {
  ...PEER,
  id: "00000000-0000-4000-8000-000000000004",
  email: "boss@example.com",
  role: "admin",
};

const DISABLED_PEER: Coordinator = {
  ...PEER,
  id: "00000000-0000-4000-8000-000000000003",
  email: "old@example.com",
  banned_until: "2099-01-01T00:00:00+00:00",
  is_disabled: true,
};

// The invite section is collapsed by default; expand it before interacting
// with its fields. The toggle's accessible name is the section heading, which
// the anchored /^invite$/i submit-button matcher never collides with.
async function openInvite(user: ReturnType<typeof userEvent.setup>) {
  await user.click(screen.getByRole("button", { name: /invite a new account/i }));
}

beforeEach(() => {
  listMock.mockReset();
  createMock.mockReset();
  disableMock.mockReset();
  enableMock.mockReset();
  rotateMock.mockReset();
});

afterEach(() => {
  vi.clearAllMocks();
});

describe("AdminsPage", () => {
  it("renders the coordinator rows", async () => {
    listMock.mockResolvedValueOnce([SELF, PEER]);
    render(<AdminsPage />);

    await screen.findByText("me@example.com");
    expect(screen.getByText("peer@example.com")).toBeInTheDocument();
  });

  it("hides destructive buttons on the calling admin's own row", async () => {
    listMock.mockResolvedValueOnce([SELF, PEER]);
    render(<AdminsPage />);

    const selfRow = (await screen.findByText("me@example.com")).closest("tr");
    expect(selfRow).not.toBeNull();
    const inSelfRow = within(selfRow as HTMLElement);
    // No Disable / Rotate / Enable buttons inside the self row.
    expect(inSelfRow.queryByRole("button", { name: /disable/i })).toBeNull();
    expect(inSelfRow.queryByRole("button", { name: /rotate password/i })).toBeNull();
    expect(inSelfRow.queryByRole("button", { name: /^enable$/i })).toBeNull();
    expect(inSelfRow.getByText(/self-actions disabled/i)).toBeInTheDocument();
    expect(inSelfRow.getByText(/^you$/i)).toBeInTheDocument();
  });

  it("creates a coordinator on Invite and refreshes the list", async () => {
    listMock.mockResolvedValueOnce([SELF]);
    listMock.mockResolvedValueOnce([SELF, PEER]);
    createMock.mockResolvedValueOnce(PEER);

    render(<AdminsPage />);
    await screen.findByText("me@example.com");

    const user = userEvent.setup();
    await openInvite(user);
    await user.type(screen.getByLabelText(/^email$/i), "peer@example.com");
    await user.type(screen.getByLabelText(/^password$/i), "newpass1234");
    await user.type(screen.getByLabelText(/confirm password/i), "newpass1234");
    await user.click(screen.getByRole("button", { name: /^invite$/i }));

    await waitFor(() => {
      expect(createMock).toHaveBeenCalledWith("peer@example.com", "newpass1234", "coordinator");
    });
    await screen.findByText("peer@example.com");
    expect(listMock).toHaveBeenCalledTimes(2);
  });

  it("creates an admin when the role select is set to Admin", async () => {
    listMock.mockResolvedValueOnce([SELF]);
    listMock.mockResolvedValueOnce([SELF, ADMIN_PEER]);
    createMock.mockResolvedValueOnce(ADMIN_PEER);

    render(<AdminsPage />);
    await screen.findByText("me@example.com");

    const user = userEvent.setup();
    await openInvite(user);
    await user.selectOptions(screen.getByLabelText(/^role$/i), "admin");
    await user.type(screen.getByLabelText(/^email$/i), "boss@example.com");
    await user.type(screen.getByLabelText(/^password$/i), "newpass1234");
    await user.type(screen.getByLabelText(/confirm password/i), "newpass1234");
    await user.click(screen.getByRole("button", { name: /^invite$/i }));

    await waitFor(() => {
      expect(createMock).toHaveBeenCalledWith("boss@example.com", "newpass1234", "admin");
    });
  });

  it("exposes no action buttons on an admin peer row (immutable via API)", async () => {
    listMock.mockResolvedValueOnce([SELF, ADMIN_PEER]);
    render(<AdminsPage />);

    const adminRow = (await screen.findByText("boss@example.com")).closest("tr");
    expect(adminRow).not.toBeNull();
    const inAdminRow = within(adminRow as HTMLElement);
    expect(inAdminRow.queryByRole("button", { name: /disable/i })).toBeNull();
    expect(inAdminRow.queryByRole("button", { name: /rotate password/i })).toBeNull();
    expect(inAdminRow.queryByRole("button", { name: /^enable$/i })).toBeNull();
    expect(inAdminRow.getByText(/managed out-of-band/i)).toBeInTheDocument();
  });

  it("hides the invite form and row actions from a coordinator viewer", async () => {
    // The viewer is a coordinator; only admins manage accounts, so read-only.
    const selfCoordinator: Coordinator = { ...SELF, role: "coordinator" };
    listMock.mockResolvedValueOnce([selfCoordinator, PEER]);
    render(<AdminsPage />);

    await screen.findByText("me@example.com");
    // No invite form (its fields and submit button are gone).
    expect(screen.queryByLabelText(/^email$/i)).toBeNull();
    expect(screen.queryByRole("button", { name: /^invite$/i })).toBeNull();
    // Peer row exposes no actions, just a read-only marker.
    const peerRow = (await screen.findByText("peer@example.com")).closest("tr");
    const inPeerRow = within(peerRow as HTMLElement);
    expect(inPeerRow.queryByRole("button", { name: /disable/i })).toBeNull();
    expect(inPeerRow.queryByRole("button", { name: /rotate password/i })).toBeNull();
    expect(inPeerRow.getByText(/view only/i)).toBeInTheDocument();
  });

  it("surfaces the duplicate-email error on 409", async () => {
    listMock.mockResolvedValueOnce([SELF]);
    createMock.mockRejectedValueOnce(new CoordinatorEmailExistsError());

    render(<AdminsPage />);
    await screen.findByText("me@example.com");

    const user = userEvent.setup();
    await openInvite(user);
    await user.type(screen.getByLabelText(/^email$/i), "peer@example.com");
    await user.type(screen.getByLabelText(/^password$/i), "newpass1234");
    await user.type(screen.getByLabelText(/confirm password/i), "newpass1234");
    await user.click(screen.getByRole("button", { name: /^invite$/i }));

    expect(await screen.findByText(/account with that email already exists/i)).toBeInTheDocument();
  });

  it("disables a peer after confirmation", async () => {
    listMock.mockResolvedValueOnce([SELF, PEER]);
    listMock.mockResolvedValueOnce([SELF, { ...PEER, is_disabled: true }]);
    disableMock.mockResolvedValueOnce(undefined);

    render(<AdminsPage />);
    const peerRow = (await screen.findByText("peer@example.com")).closest("tr");
    expect(peerRow).not.toBeNull();
    const user = userEvent.setup();

    await user.click(within(peerRow as HTMLElement).getByRole("button", { name: /disable/i }));

    // The confirmation modal appears.
    const modal = await screen.findByRole("dialog", { name: /disable admin/i });
    const inModal = within(modal);
    await user.click(inModal.getByRole("button", { name: /^disable$/i }));

    await waitFor(() => {
      expect(disableMock).toHaveBeenCalledWith(PEER.id);
    });
    // List re-fetched.
    expect(listMock).toHaveBeenCalledTimes(2);
  });

  it("enables a disabled peer with a single click (no modal)", async () => {
    listMock.mockResolvedValueOnce([SELF, DISABLED_PEER]);
    listMock.mockResolvedValueOnce([SELF, { ...DISABLED_PEER, is_disabled: false }]);
    enableMock.mockResolvedValueOnce(undefined);

    render(<AdminsPage />);
    const disabledRow = (await screen.findByText("old@example.com")).closest("tr");
    expect(disabledRow).not.toBeNull();
    const user = userEvent.setup();
    await user.click(within(disabledRow as HTMLElement).getByRole("button", { name: /^enable$/i }));

    await waitFor(() => {
      expect(enableMock).toHaveBeenCalledWith(DISABLED_PEER.id);
    });
    expect(screen.queryByRole("dialog")).toBeNull();
  });

  it("rotates a peer's password from the modal", async () => {
    listMock.mockResolvedValueOnce([SELF, PEER]);
    listMock.mockResolvedValueOnce([SELF, PEER]);
    rotateMock.mockResolvedValueOnce(undefined);

    render(<AdminsPage />);
    const peerRow = (await screen.findByText("peer@example.com")).closest("tr");
    expect(peerRow).not.toBeNull();
    const user = userEvent.setup();
    await user.click(
      within(peerRow as HTMLElement).getByRole("button", { name: /rotate password/i }),
    );

    const modal = await screen.findByRole("dialog", { name: /rotate password/i });
    const inModal = within(modal);
    await user.type(inModal.getByLabelText(/new password/i), "freshpw1234");
    await user.type(inModal.getByLabelText(/confirm password/i), "freshpw1234");
    await user.click(inModal.getByRole("button", { name: /rotate password/i }));

    await waitFor(() => {
      expect(rotateMock).toHaveBeenCalledWith(PEER.id, "freshpw1234");
    });
  });
});
