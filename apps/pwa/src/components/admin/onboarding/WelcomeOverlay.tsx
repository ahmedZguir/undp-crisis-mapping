// First-run splash shown after login until the walkthrough is finished or
// skipped. Frames the two chapters and hands off to the guided tour.
export function WelcomeOverlay({
  open,
  onStart,
  onSkip,
}: {
  open: boolean;
  onStart: () => void;
  onSkip: () => void;
}) {
  if (!open) return null;
  return (
    <div
      className="welcome-overlay"
      // biome-ignore lint/a11y/useSemanticElements: a full-screen, conditionally-rendered splash is not a native <dialog>.
      role="dialog"
      aria-modal="true"
      aria-label="Welcome"
    >
      <div className="welcome-card">
        <span className="welcome-mark">
          <img src="/favicon-admin.svg" alt="RASID Admin" width="52" height="52" />
        </span>
        <h1 className="welcome-title">Welcome to the coordinator console</h1>
        <p className="welcome-sub">
          A two-minute walkthrough in two chapters. You can skip it and explore on your own.
        </p>

        <ol className="welcome-chapters">
          <li className="welcome-chapter">
            <span className="welcome-chapter-num">1</span>
            <span className="welcome-chapter-text">
              <b>Set up &amp; manage crises</b>
              Create a crisis, edit its settings, and take it live.
            </span>
          </li>
          <li className="welcome-chapter">
            <span className="welcome-chapter-num">2</span>
            <span className="welcome-chapter-text">
              <b>Read &amp; analyze the dashboard</b>
              Track incoming reports and dig into the data for a live crisis.
            </span>
          </li>
        </ol>

        <div className="welcome-actions">
          <button type="button" className="btn welcome-start" onClick={onStart}>
            Start the walkthrough
          </button>
          <button type="button" className="btn secondary" onClick={onSkip}>
            Skip for now
          </button>
        </div>

        <p className="welcome-foot">
          You can replay this anytime from <b>Walkthrough</b> in the top bar.
        </p>
      </div>
    </div>
  );
}
