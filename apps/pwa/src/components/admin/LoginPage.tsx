// Coordinator login. On success the access token lives in `api/auth.ts` module
// memory and the API sets the refresh cookie.

import { type FormEvent, type SVGProps, useState } from "react";
import "../../styles/admin.css";
import { LoginError, login } from "../../api/auth";
import { Icon } from "./icons";

interface Props {
  onAuthed: () => void;
}

export function LoginPage({ onAuthed }: Props) {
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [showPassword, setShowPassword] = useState(false);

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (submitting) return;
    setSubmitting(true);
    setError(null);
    try {
      await login(email, password);
      onAuthed();
    } catch (err) {
      if (err instanceof LoginError && err.status === 401) {
        setError("Invalid email or password.");
      } else if (err instanceof LoginError && err.status === 403) {
        setError("This account is not a coordinator.");
      } else {
        setError(err instanceof Error ? err.message : "Login failed.");
      }
    } finally {
      setSubmitting(false);
    }
  }

  const canSubmit = !submitting && email.length > 0 && password.length > 0;

  return (
    <div className="admin-root login-screen">
      {/* Brand canvas. */}
      <section className="login-hero" aria-hidden="true">
        <ContourRings />

        <div className="login-brand login-rise" style={{ animationDelay: "60ms" }}>
          <img
            className="login-brand-logo"
            src="/favicon-admin.svg"
            alt="RASID Admin"
            width={64}
            height={64}
          />
          <span className="login-brand-text">
            <span className="login-brand-tag">Admin Portal</span>
          </span>
        </div>

        <div className="login-hero-mid">
          <h2 className="login-hero-title login-rise" style={{ animationDelay: "140ms" }}>
            Control crisis events, <em>analyze with AI.</em>
          </h2>

          <ul className="login-caps">
            <li className="login-cap login-rise" style={{ animationDelay: "240ms" }}>
              <span className="login-cap-icon">
                <Icon.pin width="16" height="16" />
              </span>
              Live damage map
            </li>
            <li className="login-cap login-rise" style={{ animationDelay: "300ms" }}>
              <span className="login-cap-icon">
                <Icon.spark width="15" height="15" />
              </span>
              AI clustering &amp; translation
            </li>
            <li className="login-cap login-rise" style={{ animationDelay: "360ms" }}>
              <span className="login-cap-icon">
                <ReportIcon width="16" height="16" />
              </span>
              Event analysis &amp; reports
            </li>
          </ul>
        </div>
      </section>

      {/* Sign-in column. */}
      <div className="login-form-col">
        <form onSubmit={handleSubmit} aria-label="Coordinator sign-in" className="login-form">
          <p className="login-form-eyebrow login-rise" style={{ animationDelay: "120ms" }}>
            Secure access
          </p>
          <h1 className="login-rise" style={{ animationDelay: "160ms" }}>
            Sign in
          </h1>
          <div className="login-rule login-rise" style={{ animationDelay: "240ms" }} />

          <label
            htmlFor="login-email"
            className="login-field login-rise"
            style={{ animationDelay: "280ms" }}
          >
            <span>Email</span>
            <div className="login-input-wrap">
              <Icon.mail width="16" height="16" className="login-input-icon" />
              <input
                id="login-email"
                type="email"
                autoComplete="email"
                required
                value={email}
                onChange={(e) => setEmail(e.target.value)}
                disabled={submitting}
                placeholder="you@example.com"
                className="login-input"
              />
            </div>
          </label>

          <label
            htmlFor="login-password"
            className="login-field login-rise"
            style={{ animationDelay: "320ms" }}
          >
            <span>Password</span>
            <div className="login-input-wrap">
              <Icon.lock width="16" height="16" className="login-input-icon" />
              <input
                id="login-password"
                type={showPassword ? "text" : "password"}
                autoComplete="current-password"
                required
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                disabled={submitting}
                placeholder="••••••••"
                className="login-input has-reveal"
              />
              <button
                type="button"
                className="login-reveal"
                onClick={() => setShowPassword((s) => !s)}
                aria-label={showPassword ? "Hide password" : "Show password"}
                aria-pressed={showPassword}
                tabIndex={-1}
              >
                <Icon.eye width="17" height="17" />
              </button>
            </div>
          </label>

          {error && (
            <div role="alert" className="login-error login-rise">
              <Icon.alert width="16" height="16" style={{ flexShrink: 0, marginTop: 1 }} />
              <span>{error}</span>
            </div>
          )}

          <button
            type="submit"
            disabled={!canSubmit}
            className="login-submit login-rise"
            style={{ animationDelay: "360ms" }}
          >
            {submitting ? (
              <>
                <span className="spin" aria-hidden="true" />
                Signing in…
              </>
            ) : (
              <>
                Sign in
                <Icon.arrowRight width="17" height="17" />
              </>
            )}
          </button>
        </form>
      </div>
    </div>
  );
}

/* Login-only icon for the analysis capability row, in the icon set's stroked style. */
function ReportIcon(props: SVGProps<SVGSVGElement>) {
  return (
    <svg
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth={1.7}
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
      {...props}
    >
      <rect x="4" y="3.5" width="16" height="17" rx="2.5" />
      <path d="M8.5 14.5V17" />
      <path d="M12 11v6" />
      <path d="M15.5 7.5V17" />
    </svg>
  );
}

/* Decorative topographic contour rings behind the brand copy. */
function ContourRings() {
  const rings = [0, 1, 2, 3, 4, 5, 6, 7];
  return (
    <svg
      className="login-topo"
      viewBox="0 0 400 400"
      fill="none"
      aria-hidden="true"
      preserveAspectRatio="xMidYMid meet"
    >
      <g transform="rotate(-18 200 200)">
        {rings.map((i) => (
          <ellipse
            key={i}
            cx={200}
            cy={200}
            rx={46 + i * 26}
            ry={32 + i * 21}
            stroke="currentColor"
            strokeWidth={1.25}
            opacity={1 - i * 0.1}
          />
        ))}
      </g>
    </svg>
  );
}
