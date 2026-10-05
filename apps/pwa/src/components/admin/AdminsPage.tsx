// Staff accounts: list, invite, rotate password, enable/disable, over the
// `/admin/coordinators` routes. UI guards are defence in depth on top of the server:
//   * viewer-role: only admins manage accounts; a coordinator gets a read-only
//     view. The tier is read off the viewer's own `is_self` row.
//   * self-row: mirrors the 400 `cannot_modify_self` guard.
//   * admin-row: admin accounts are immutable via the API (403
//     `admin_immutable`); their lifecycle is handled out of band.
// Citizens are not users (they have client_ids).

import { type FormEvent, useCallback, useEffect, useState } from "react";
import {
  CoordinatorEmailExistsError,
  createCoordinator,
  disableCoordinator,
  enableCoordinator,
  listCoordinators,
  rotateCoordinatorPassword,
} from "../../api/admin";
import { useAdminTimezone } from "../../hooks/useAdminTimezone";
import { formatDisplay, shortZoneLabel } from "../../lib/adminTimezone";
import type { Coordinator, CoordinatorRole } from "../../types/admin";

type LoadState = "loading" | "ready" | "error";

interface ConfirmDisableState {
  coordinator: Coordinator;
}

interface RotatePasswordState {
  coordinator: Coordinator;
}

export function AdminsPage() {
  const [coordinators, setCoordinators] = useState<Coordinator[]>([]);
  const [load, setLoad] = useState<LoadState>("loading");
  const [loadError, setLoadError] = useState<string | null>(null);

  // Kept here so the success path can clear and re-fetch in one place.
  const [inviteEmail, setInviteEmail] = useState("");
  const [inviteRole, setInviteRole] = useState<CoordinatorRole>("coordinator");
  const [invitePassword, setInvitePassword] = useState("");
  const [inviteConfirm, setInviteConfirm] = useState("");
  const [inviteSubmitting, setInviteSubmitting] = useState(false);
  const [inviteError, setInviteError] = useState<string | null>(null);

  // At most one modal open at a time.
  const [confirmDisable, setConfirmDisable] = useState<ConfirmDisableState | null>(null);
  const [rotateModal, setRotateModal] = useState<RotatePasswordState | null>(null);

  const refreshList = useCallback(async () => {
    setLoad((prev) => (prev === "ready" ? "ready" : "loading"));
    setLoadError(null);
    try {
      const rows = await listCoordinators();
      // Self first, then by email, so the order is stable across reloads.
      rows.sort((a, b) => {
        if (a.is_self !== b.is_self) return a.is_self ? -1 : 1;
        return a.email.localeCompare(b.email);
      });
      setCoordinators(rows);
      setLoad("ready");
    } catch (err) {
      setLoadError(err instanceof Error ? err.message : "Failed to load admins.");
      setLoad("error");
    }
  }, []);

  useEffect(() => {
    void refreshList();
  }, [refreshList]);

  const viewerIsAdmin = coordinators.some((c) => c.is_self && c.role === "admin");

  async function handleInvite(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (inviteSubmitting) return;
    setInviteError(null);
    if (invitePassword.length < 8) {
      setInviteError("Password must be at least 8 characters.");
      return;
    }
    if (invitePassword !== inviteConfirm) {
      setInviteError("Passwords do not match.");
      return;
    }
    setInviteSubmitting(true);
    try {
      await createCoordinator(inviteEmail.trim(), invitePassword, inviteRole);
      setInviteEmail("");
      setInviteRole("coordinator");
      setInvitePassword("");
      setInviteConfirm("");
      await refreshList();
    } catch (err) {
      if (err instanceof CoordinatorEmailExistsError) {
        setInviteError("An account with that email already exists.");
      } else {
        setInviteError(err instanceof Error ? err.message : "Failed to invite.");
      }
    } finally {
      setInviteSubmitting(false);
    }
  }

  async function handleEnable(coordinator: Coordinator) {
    try {
      await enableCoordinator(coordinator.id);
      await refreshList();
    } catch (err) {
      // Rows have no slot for per-action errors. Refresh in case it succeeded anyway.
      setLoadError(err instanceof Error ? err.message : "Failed to enable admin.");
      await refreshList();
    }
  }

  return (
    <div
      style={{
        flex: 1,
        display: "flex",
        flexDirection: "column",
        minHeight: 0,
        background: "var(--c-bg)",
      }}
    >
      <div className="page-hero is-compact">
        <div className="page-hero-inner">
          <div className="hero-title" style={{ flex: 1 }}>
            <h1>Manage Staff Accounts</h1>
          </div>
        </div>
      </div>

      <div style={{ flex: 1, overflowY: "auto", padding: "22px 28px 40px" }}>
        {/* Cap the column width on ultrawide monitors. */}
        <div
          style={{
            maxWidth: 1080,
            margin: "0 auto",
            display: "flex",
            flexDirection: "column",
            gap: 18,
          }}
        >
          {loadError && (
            <div
              role="alert"
              style={{
                background: "var(--c-danger-bg)",
                color: "var(--c-danger)",
                padding: "10px 14px",
                borderRadius: 8,
                fontSize: 13,
                border: "1px solid var(--c-danger)",
              }}
            >
              {loadError}
            </div>
          )}

          {viewerIsAdmin && (
            <InviteForm
              email={inviteEmail}
              role={inviteRole}
              password={invitePassword}
              confirm={inviteConfirm}
              submitting={inviteSubmitting}
              error={inviteError}
              onEmailChange={setInviteEmail}
              onRoleChange={setInviteRole}
              onPasswordChange={setInvitePassword}
              onConfirmChange={setInviteConfirm}
              onSubmit={handleInvite}
            />
          )}

          <section className="section-block fade-in">
            <div className="section-head" style={{ marginBottom: 0, paddingBottom: 14 }}>
              <span className="section-num" aria-hidden="true">
                ▤
              </span>
              <div>
                <div className="section-title">Active roster</div>
                <div className="section-sub">
                  Everyone with console access. Admins are managed out-of-band; coordinators can be
                  rotated or disabled here.
                </div>
              </div>
            </div>
            <AdminsTable
              coordinators={coordinators}
              state={load}
              canManage={viewerIsAdmin}
              onRotate={(c) => setRotateModal({ coordinator: c })}
              onDisable={(c) => setConfirmDisable({ coordinator: c })}
              onEnable={handleEnable}
            />
          </section>
        </div>
      </div>

      {confirmDisable && (
        <ConfirmDisableModal
          coordinator={confirmDisable.coordinator}
          onClose={() => setConfirmDisable(null)}
          onConfirmed={async () => {
            setConfirmDisable(null);
            await refreshList();
          }}
        />
      )}

      {rotateModal && (
        <RotatePasswordModal
          coordinator={rotateModal.coordinator}
          onClose={() => setRotateModal(null)}
          onDone={async () => {
            setRotateModal(null);
            await refreshList();
          }}
        />
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Invite form
// ---------------------------------------------------------------------------

interface InviteFormProps {
  email: string;
  role: CoordinatorRole;
  password: string;
  confirm: string;
  submitting: boolean;
  error: string | null;
  onEmailChange: (v: string) => void;
  onRoleChange: (v: CoordinatorRole) => void;
  onPasswordChange: (v: string) => void;
  onConfirmChange: (v: string) => void;
  onSubmit: (event: FormEvent<HTMLFormElement>) => void;
}

// Chevron over the `appearance: none` role <select>; ignores pointer events so
// clicks reach the select.
function SelectChevron() {
  return (
    <span
      aria-hidden="true"
      style={{
        position: "absolute",
        right: 12,
        top: "50%",
        transform: "translateY(-50%)",
        display: "flex",
        pointerEvents: "none",
        color: "var(--c-ink-3)",
      }}
    >
      <svg
        width="12"
        height="12"
        viewBox="0 0 24 24"
        fill="none"
        stroke="currentColor"
        aria-hidden="true"
      >
        <path d="M6 9l6 6 6-6" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round" />
      </svg>
    </span>
  );
}

function InviteForm(props: InviteFormProps) {
  // Collapsed by default: the roster is the primary content, inviting is occasional.
  const [open, setOpen] = useState(false);

  return (
    <section className="section-block is-accent ap-invite">
      {/* The shared `.field > label` rule misses these <span> labels, so they
          are restyled locally. */}
      <style>{INVITE_CSS}</style>
      <button
        type="button"
        className="ap-invite-toggle"
        aria-expanded={open}
        onClick={() => setOpen((o) => !o)}
      >
        <span className="section-num" aria-hidden="true">
          +
        </span>
        <div style={{ flex: 1, minWidth: 0 }}>
          <div className="section-title">Invite a new account</div>
          <div className="section-sub">
            Set an email and a temporary password. They can sign in immediately. Pick the tier
            carefully; admins can manage other accounts.
          </div>
        </div>
        <span className="ap-invite-caret" aria-hidden="true" data-open={open}>
          <svg
            width="14"
            height="14"
            viewBox="0 0 24 24"
            fill="none"
            stroke="currentColor"
            aria-hidden="true"
          >
            <path d="M6 9l6 6 6-6" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round" />
          </svg>
        </span>
      </button>

      {open && (
        <form
          onSubmit={props.onSubmit}
          style={{
            display: "flex",
            flexDirection: "column",
            gap: 16,
            marginTop: 18,
            paddingTop: 18,
            borderTop: "1px solid var(--c-line)",
          }}
        >
          <div className="ap-grid">
            <label className="field ap-field">
              <span>Email</span>
              <input
                type="email"
                required
                autoComplete="off"
                placeholder="name@org.example"
                value={props.email}
                onChange={(e) => props.onEmailChange(e.target.value)}
                disabled={props.submitting}
              />
            </label>
            <label className="field ap-field">
              <span>Role</span>
              {/* The native arrow renders inconsistently under the admin
                shell's zoom, so it is replaced by our own chevron. */}
              <div style={{ position: "relative" }}>
                <select
                  value={props.role}
                  onChange={(e) => props.onRoleChange(e.target.value as CoordinatorRole)}
                  disabled={props.submitting}
                  style={{ appearance: "none", paddingRight: 34, width: "100%" }}
                >
                  <option value="coordinator">Coordinator</option>
                  <option value="admin">Admin</option>
                </select>
                <SelectChevron />
              </div>
            </label>
            <label className="field ap-field">
              <span>Password</span>
              <input
                type="password"
                required
                autoComplete="new-password"
                minLength={8}
                placeholder="At least 8 characters"
                value={props.password}
                onChange={(e) => props.onPasswordChange(e.target.value)}
                disabled={props.submitting}
              />
            </label>
            <label className="field ap-field">
              <span>Confirm password</span>
              <input
                type="password"
                required
                autoComplete="new-password"
                minLength={8}
                placeholder="Re-enter password"
                value={props.confirm}
                onChange={(e) => props.onConfirmChange(e.target.value)}
                disabled={props.submitting}
              />
            </label>
          </div>

          {props.role === "admin" && (
            <div className="ap-admin-note">
              <span aria-hidden="true" className="ap-admin-note-glyph">
                ⚑
              </span>
              <span>
                <strong>Admin tier.</strong> They can manage coordinators and create other admins.
              </span>
            </div>
          )}
          {props.error && (
            <div role="alert" className="ap-error">
              {props.error}
            </div>
          )}

          <div className="ap-footer">
            <span className="ap-footer-note">
              Minimum 8 characters. The account is active at once.
            </span>
            <button
              type="submit"
              className="btn"
              style={{ minWidth: 132 }}
              disabled={props.submitting || !props.email || !props.password || !props.confirm}
            >
              {props.submitting ? (
                <>
                  <span className="spin" aria-hidden="true" /> Inviting…
                </>
              ) : (
                "Invite"
              )}
            </button>
          </div>
        </form>
      )}
    </section>
  );
}

// Page-local CSS for InviteForm (the shared `.field > label` rule misses <span> labels).
const INVITE_CSS = `
.admin-root .ap-invite .ap-invite-toggle {
  display: flex;
  align-items: flex-start;
  gap: 12px;
  width: 100%;
  background: none;
  border: none;
  padding: 0;
  margin: 0;
  cursor: pointer;
  text-align: left;
  font: inherit;
  color: inherit;
}
.admin-root .ap-invite .ap-invite-toggle:hover .section-title {
  color: var(--c-blue-700);
}
.admin-root .ap-invite .ap-invite-caret {
  align-self: center;
  flex-shrink: 0;
  display: flex;
  color: var(--c-ink-3);
  transition: transform 160ms ease;
}
.admin-root .ap-invite .ap-invite-caret[data-open="true"] {
  transform: rotate(180deg);
}
.admin-root .ap-invite .ap-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 16px 20px;
}
.admin-root .ap-invite .ap-field > span {
  font-size: 12px;
  font-weight: 600;
  letter-spacing: 0.02em;
  color: var(--c-ink-3);
  line-height: 1.3;
}
.admin-root .ap-invite .ap-field input,
.admin-root .ap-invite .ap-field select {
  height: 42px;
  padding: 0 13px;
  font-size: 14px;
  background: var(--c-card-2);
  border-radius: 9px;
  transition: border-color 120ms ease, box-shadow 120ms ease, background 120ms ease;
}
.admin-root .ap-invite .ap-field input::placeholder {
  color: var(--c-ink-4);
}
.admin-root .ap-invite .ap-field input:focus,
.admin-root .ap-invite .ap-field select:focus {
  background: var(--c-card);
}
.admin-root .ap-invite .ap-admin-note {
  display: flex;
  gap: 10px;
  align-items: center;
  font-size: 12.5px;
  line-height: 1.5;
  color: var(--c-ink-2);
  background: var(--c-blue-50);
  border: 1px solid var(--c-blue-200);
  border-radius: 9px;
  padding: 10px 13px;
}
.admin-root .ap-invite .ap-admin-note strong { color: var(--c-ink); font-weight: 600; }
.admin-root .ap-invite .ap-admin-note-glyph {
  color: var(--c-blue-700);
  font-weight: 700;
  font-size: 14px;
  flex-shrink: 0;
}
.admin-root .ap-invite .ap-error {
  background: var(--c-danger-bg);
  color: var(--c-danger);
  border: 1px solid var(--c-danger);
  padding: 9px 13px;
  border-radius: 8px;
  font-size: 13px;
}
.admin-root .ap-invite .ap-footer {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
  padding-top: 16px;
  border-top: 1px solid var(--c-line);
}
.admin-root .ap-invite .ap-footer-note {
  font-size: 12px;
  color: var(--c-ink-4);
}
@media (max-width: 640px) {
  .admin-root .ap-invite .ap-grid { grid-template-columns: minmax(0, 1fr); }
  .admin-root .ap-invite .ap-footer { flex-direction: column; align-items: stretch; }
}
`;

// ---------------------------------------------------------------------------
// Admins table
// ---------------------------------------------------------------------------

interface AdminsTableProps {
  coordinators: Coordinator[];
  state: LoadState;
  canManage: boolean;
  onRotate: (c: Coordinator) => void;
  onDisable: (c: Coordinator) => void;
  onEnable: (c: Coordinator) => void;
}

function AdminsTable(props: AdminsTableProps) {
  const timezone = useAdminTimezone();
  const zoneLabel = shortZoneLabel(timezone);

  if (props.state === "loading") {
    return <AdminsSkeleton />;
  }
  if (props.state === "error") {
    return (
      <EmptyState
        title="Could not load admins"
        hint="Refresh the page or check the API logs."
        tone="danger"
      />
    );
  }
  if (props.coordinators.length === 0) {
    return (
      <EmptyState
        title="No admins yet"
        hint="Invite a coordinator using the form above to grant console access."
      />
    );
  }

  return (
    // Break the table out to the section-block's inner edges.
    <div
      style={{
        margin: "4px -18px -18px",
        borderTop: "1px solid var(--c-line)",
      }}
    >
      <table
        style={{
          width: "100%",
          borderCollapse: "collapse",
          fontSize: 14,
          tableLayout: "fixed",
        }}
      >
        <colgroup>
          <col style={{ width: "auto" }} />
          <col style={{ width: 140 }} />
          <col style={{ width: 200 }} />
          <col style={{ width: 120 }} />
          <col style={{ width: 250 }} />
        </colgroup>
        <thead>
          <tr style={{ textAlign: "left" }}>
            <Th first>Account</Th>
            <Th>Role</Th>
            <Th>Created ({zoneLabel})</Th>
            <Th>Status</Th>
            <Th align="right">Actions</Th>
          </tr>
        </thead>
        <tbody>
          {props.coordinators.map((c) => (
            <AdminRow
              key={c.id}
              coordinator={c}
              timezone={timezone}
              canManage={props.canManage}
              onRotate={() => props.onRotate(c)}
              onDisable={() => props.onDisable(c)}
              onEnable={() => props.onEnable(c)}
            />
          ))}
        </tbody>
      </table>
    </div>
  );
}

function EmptyState({
  title,
  hint,
  tone = "neutral",
}: {
  title: string;
  hint?: string;
  tone?: "neutral" | "danger";
}) {
  const iconBg = tone === "danger" ? "var(--c-danger-bg)" : "var(--c-blue-100)";
  const iconColor = tone === "danger" ? "var(--c-danger)" : "var(--c-blue-700)";
  return (
    <div
      style={{
        padding: "44px 24px",
        textAlign: "center",
        display: "flex",
        flexDirection: "column",
        alignItems: "center",
        gap: 10,
      }}
    >
      <span
        aria-hidden="true"
        style={{
          width: 40,
          height: 40,
          borderRadius: 999,
          background: iconBg,
          color: iconColor,
          display: "grid",
          placeItems: "center",
          fontSize: 18,
          fontWeight: 700,
        }}
      >
        {tone === "danger" ? "!" : "+"}
      </span>
      <div style={{ fontSize: 14, fontWeight: 700, color: "var(--c-ink)" }}>{title}</div>
      {hint && (
        <div style={{ fontSize: 13, color: "var(--c-ink-3)", maxWidth: 320, lineHeight: 1.5 }}>
          {hint}
        </div>
      )}
    </div>
  );
}

function AdminsSkeleton() {
  return (
    <div style={{ padding: "12px 16px", display: "flex", flexDirection: "column", gap: 12 }}>
      {[0, 1, 2].map((i) => (
        <div
          key={i}
          style={{
            display: "grid",
            gridTemplateColumns: "minmax(0, 1fr) 90px 160px 90px 200px",
            gap: 12,
            alignItems: "center",
            padding: "8px 0",
          }}
        >
          <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
            <div className="skeleton" style={{ width: 30, height: 30, borderRadius: 999 }} />
            <div className="skeleton" style={{ height: 14, width: "60%" }} />
          </div>
          <div className="skeleton" style={{ height: 18, width: 72, borderRadius: 999 }} />
          <div className="skeleton" style={{ height: 14, width: 130 }} />
          <div className="skeleton" style={{ height: 18, width: 70, borderRadius: 999 }} />
          <div className="skeleton" style={{ height: 28, width: 180, borderRadius: 8 }} />
        </div>
      ))}
    </div>
  );
}

function Th({
  children,
  align = "left",
  first = false,
}: {
  children: React.ReactNode;
  align?: "left" | "right";
  first?: boolean;
}) {
  return (
    <th
      style={{
        padding: first
          ? "11px 16px 11px 18px"
          : align === "right"
            ? "11px 18px 11px 16px"
            : "11px 16px",
        fontSize: 10.5,
        fontWeight: 700,
        textTransform: "uppercase",
        letterSpacing: "0.09em",
        color: "var(--c-ink-4)",
        background: "var(--c-card-2)",
        borderBottom: "1px solid var(--c-line)",
        textAlign: align,
        fontFamily: "var(--font-mono)",
      }}
    >
      {children}
    </th>
  );
}

// Monogram from the email's first letter; blue for admins, slate for coordinators.
function Monogram({ email, role }: { email: string; role: string }) {
  const initial = (email.trim()[0] ?? "?").toUpperCase();
  const isAdmin = role === "admin";
  return (
    <span
      aria-hidden="true"
      style={{
        flexShrink: 0,
        width: 30,
        height: 30,
        borderRadius: 999,
        display: "grid",
        placeItems: "center",
        fontSize: 13,
        fontWeight: 700,
        background: isAdmin ? "var(--c-blue-100)" : "var(--c-line-2)",
        color: isAdmin ? "var(--c-blue-700)" : "var(--c-ink-3)",
        border: `1px solid ${isAdmin ? "var(--c-blue-200)" : "var(--c-line)"}`,
      }}
    >
      {initial}
    </span>
  );
}

function ActionNote({ children }: { children: React.ReactNode }) {
  return (
    <span
      style={{
        display: "inline-flex",
        alignItems: "center",
        gap: 6,
        fontSize: 12,
        color: "var(--c-ink-4)",
      }}
    >
      <span
        aria-hidden="true"
        style={{ width: 4, height: 4, borderRadius: 999, background: "var(--c-ink-4)" }}
      />
      {children}
    </span>
  );
}

interface AdminRowProps {
  coordinator: Coordinator;
  timezone: string;
  canManage: boolean;
  onRotate: () => void;
  onDisable: () => void;
  onEnable: () => void;
}

function AdminRow({
  coordinator,
  timezone,
  canManage,
  onRotate,
  onDisable,
  onEnable,
}: AdminRowProps) {
  const c = coordinator;
  const created = formatDisplay(c.created_at, timezone);
  const muted = c.is_disabled;

  return (
    <tr
      className={c.is_self ? undefined : "admin-row"}
      style={{
        borderBottom: "1px solid var(--c-line-2)",
        opacity: muted ? 0.55 : 1,
        background: c.is_self ? "var(--c-blue-50)" : undefined,
        // A 3px accent rail marks the viewer's own row without flooding it.
        boxShadow: c.is_self ? "inset 3px 0 0 var(--c-blue-700)" : undefined,
      }}
    >
      <Td first>
        <div style={{ display: "flex", alignItems: "center", gap: 11, minWidth: 0 }}>
          <Monogram email={c.email} role={c.role} />
          <span
            style={{
              fontWeight: 500,
              color: "var(--c-ink)",
              overflow: "hidden",
              textOverflow: "ellipsis",
              whiteSpace: "nowrap",
            }}
          >
            {c.email}
          </span>
          {c.is_self && (
            <span
              className="chip blue"
              style={{ padding: "1px 8px", fontSize: 10.5, fontWeight: 600, flexShrink: 0 }}
            >
              you
            </span>
          )}
        </div>
      </Td>
      <Td>
        {c.role === "admin" ? (
          <span className="chip blue" style={{ padding: "2px 9px", fontSize: 11, fontWeight: 600 }}>
            Admin
          </span>
        ) : c.role ? (
          <span className="chip" style={{ padding: "2px 9px", fontSize: 11, fontWeight: 600 }}>
            Coordinator
          </span>
        ) : (
          <span style={{ color: "var(--c-ink-4)" }}>—</span>
        )}
      </Td>
      <Td>
        <span className="mono" style={{ fontSize: 12.5, color: "var(--c-ink-2)" }}>
          {created}
        </span>
      </Td>
      <Td>
        {c.is_disabled ? (
          <span className="chip danger" style={{ padding: "2px 9px", fontSize: 11 }}>
            <span
              aria-hidden="true"
              style={{ width: 5, height: 5, borderRadius: 999, background: "currentColor" }}
            />
            Disabled
          </span>
        ) : (
          <span className="chip safe" style={{ padding: "2px 9px", fontSize: 11 }}>
            <span
              aria-hidden="true"
              style={{ width: 5, height: 5, borderRadius: 999, background: "currentColor" }}
            />
            Active
          </span>
        )}
      </Td>
      <Td align="right">
        {c.is_self ? (
          <ActionNote>self-actions disabled</ActionNote>
        ) : !canManage ? (
          <ActionNote>view only</ActionNote>
        ) : c.role === "admin" ? (
          <ActionNote>managed out-of-band</ActionNote>
        ) : (
          <div style={{ display: "inline-flex", gap: 8 }}>
            <button
              type="button"
              className="btn secondary"
              style={{ padding: "6px 12px", fontSize: 13 }}
              onClick={onRotate}
            >
              Rotate password
            </button>
            {c.is_disabled ? (
              <button
                type="button"
                className="btn"
                style={{ padding: "6px 12px", fontSize: 13 }}
                onClick={onEnable}
              >
                Enable
              </button>
            ) : (
              <button
                type="button"
                className="btn danger"
                style={{ padding: "6px 12px", fontSize: 13 }}
                onClick={onDisable}
              >
                Disable
              </button>
            )}
          </div>
        )}
      </Td>
    </tr>
  );
}

function Td({
  children,
  align = "left",
  first = false,
}: {
  children: React.ReactNode;
  align?: "left" | "right";
  first?: boolean;
}) {
  return (
    <td
      style={{
        padding: first
          ? "13px 16px 13px 18px"
          : align === "right"
            ? "13px 18px 13px 16px"
            : "13px 16px",
        textAlign: align,
        verticalAlign: "middle",
      }}
    >
      {children}
    </td>
  );
}

// ---------------------------------------------------------------------------
// Modals
// ---------------------------------------------------------------------------

interface ModalShellProps {
  title: string;
  onClose: () => void;
  children: React.ReactNode;
}

function ModalShell({ title, onClose, children }: ModalShellProps) {
  // The backdrop is a <button> for native keyboard semantics. The body uses
  // `role="dialog"` rather than <dialog>.showModal(), which jsdom lacks.
  return (
    <div
      style={{
        position: "fixed",
        inset: 0,
        zIndex: 1000,
      }}
    >
      <button
        type="button"
        aria-label="Dismiss dialog"
        onClick={onClose}
        style={{
          position: "absolute",
          inset: 0,
          background: "rgba(10, 22, 40, 0.45)",
          border: "none",
          cursor: "pointer",
          padding: 0,
        }}
      />
      <div
        // biome-ignore lint/a11y/useSemanticElements: <dialog> requires showModal() which is
        // not available in jsdom for tests; the role attribute matches WAI-ARIA semantics.
        role="dialog"
        aria-modal="true"
        aria-label={title}
        style={{
          position: "relative",
          margin: "10vh auto 0",
          background: "var(--c-card)",
          borderRadius: 12,
          width: "100%",
          maxWidth: 460,
          padding: 24,
          boxShadow: "var(--shadow-2)",
          display: "flex",
          flexDirection: "column",
          gap: 14,
        }}
      >
        <div
          style={{
            display: "flex",
            alignItems: "center",
            justifyContent: "space-between",
            gap: 12,
          }}
        >
          <h2 style={{ margin: 0, fontSize: 16, fontWeight: 700 }}>{title}</h2>
          <button
            type="button"
            onClick={onClose}
            aria-label="Close"
            style={{
              border: "none",
              background: "transparent",
              fontSize: 18,
              color: "var(--c-ink-3)",
              cursor: "pointer",
            }}
          >
            ×
          </button>
        </div>
        {children}
      </div>
    </div>
  );
}

interface ConfirmDisableModalProps {
  coordinator: Coordinator;
  onClose: () => void;
  onConfirmed: () => Promise<void> | void;
}

function ConfirmDisableModal({ coordinator, onClose, onConfirmed }: ConfirmDisableModalProps) {
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function confirm() {
    if (submitting) return;
    setSubmitting(true);
    setError(null);
    try {
      await disableCoordinator(coordinator.id);
      await onConfirmed();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to disable.");
      setSubmitting(false);
    }
  }

  return (
    <ModalShell title="Disable admin" onClose={onClose}>
      <div style={{ fontSize: 14, color: "var(--c-ink-2)" }}>
        Disable admin <strong>{coordinator.email}</strong>? They will lose access immediately and
        any active sessions will be revoked.
      </div>
      {error && (
        <div
          role="alert"
          style={{
            background: "var(--c-danger-bg)",
            color: "var(--c-danger)",
            padding: "8px 12px",
            borderRadius: 6,
            fontSize: 13,
          }}
        >
          {error}
        </div>
      )}
      <div style={{ display: "flex", justifyContent: "flex-end", gap: 8 }}>
        <button type="button" className="btn secondary" onClick={onClose} disabled={submitting}>
          Cancel
        </button>
        <button type="button" className="btn danger" onClick={confirm} disabled={submitting}>
          {submitting ? "Disabling…" : "Disable"}
        </button>
      </div>
    </ModalShell>
  );
}

interface RotatePasswordModalProps {
  coordinator: Coordinator;
  onClose: () => void;
  onDone: () => Promise<void> | void;
}

function RotatePasswordModal({ coordinator, onClose, onDone }: RotatePasswordModalProps) {
  const [password, setPassword] = useState("");
  const [confirm, setConfirm] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (submitting) return;
    setError(null);
    if (password.length < 8) {
      setError("Password must be at least 8 characters.");
      return;
    }
    if (password !== confirm) {
      setError("Passwords do not match.");
      return;
    }
    setSubmitting(true);
    try {
      await rotateCoordinatorPassword(coordinator.id, password);
      await onDone();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to rotate password.");
      setSubmitting(false);
    }
  }

  return (
    <ModalShell title="Rotate password" onClose={onClose}>
      <form onSubmit={submit} style={{ display: "flex", flexDirection: "column", gap: 12 }}>
        <div style={{ fontSize: 14, color: "var(--c-ink-2)" }}>
          Set a new password for <strong>{coordinator.email}</strong>. This will sign the admin out
          of all their devices.
        </div>
        <label className="field">
          <span>New password</span>
          <input
            type="password"
            required
            autoComplete="new-password"
            minLength={8}
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            disabled={submitting}
          />
        </label>
        <label className="field">
          <span>Confirm password</span>
          <input
            type="password"
            required
            autoComplete="new-password"
            minLength={8}
            value={confirm}
            onChange={(e) => setConfirm(e.target.value)}
            disabled={submitting}
          />
        </label>
        {error && (
          <div
            role="alert"
            style={{
              background: "var(--c-danger-bg)",
              color: "var(--c-danger)",
              padding: "8px 12px",
              borderRadius: 6,
              fontSize: 13,
            }}
          >
            {error}
          </div>
        )}
        <div style={{ display: "flex", justifyContent: "flex-end", gap: 8 }}>
          <button type="button" className="btn secondary" onClick={onClose} disabled={submitting}>
            Cancel
          </button>
          <button type="submit" className="btn" disabled={submitting || !password || !confirm}>
            {submitting ? "Rotating…" : "Rotate password"}
          </button>
        </div>
      </form>
    </ModalShell>
  );
}
