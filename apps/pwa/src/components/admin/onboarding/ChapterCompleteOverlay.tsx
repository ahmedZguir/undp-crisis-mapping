// Interlude between the two walkthrough chapters, so the hand-off into chapter
// 2 reads as a deliberate step. Reuses the welcome splash's card styling.
export function ChapterCompleteOverlay({
  open,
  onContinue,
  onSkip,
}: {
  open: boolean;
  /** Begin chapter 2. */
  onContinue: () => void;
  /** Close the walkthrough without running chapter 2. */
  onSkip: () => void;
}) {
  if (!open) return null;
  return (
    <div
      className="welcome-overlay"
      // biome-ignore lint/a11y/useSemanticElements: a full-screen, conditionally-rendered splash is not a native <dialog>.
      role="dialog"
      aria-modal="true"
      aria-label="Chapter 1 complete"
    >
      <div className="welcome-card">
        <span className="interlude-check" aria-hidden="true">
          <svg
            aria-hidden="true"
            viewBox="0 0 24 24"
            fill="none"
            stroke="currentColor"
            strokeWidth="2.6"
            strokeLinecap="round"
            strokeLinejoin="round"
          >
            <path d="M20 6 9 17l-5-5" />
          </svg>
        </span>
        <span className="welcome-eyebrow">Chapter 1 of 2 complete</span>
        <h1 className="welcome-title">You can set up and manage crises</h1>
        <p className="welcome-sub">
          Creating a crisis, editing its settings, and taking it live: all covered. One chapter to
          go.
        </p>

        <ol className="welcome-chapters">
          <li className="welcome-chapter is-done">
            <span className="welcome-chapter-num" aria-hidden="true">
              <svg
                aria-hidden="true"
                viewBox="0 0 24 24"
                fill="none"
                stroke="currentColor"
                strokeWidth="3"
                strokeLinecap="round"
                strokeLinejoin="round"
              >
                <path d="M20 6 9 17l-5-5" />
              </svg>
            </span>
            <span className="welcome-chapter-text">
              <b>Set up &amp; manage crises</b>
              Done.
            </span>
          </li>
          <li className="welcome-chapter is-next">
            <span className="welcome-chapter-num">2</span>
            <span className="welcome-chapter-text">
              <b>Read &amp; analyze the dashboard</b>
              Up next: track incoming reports and dig into the data for a live crisis.
            </span>
          </li>
        </ol>

        <div className="welcome-actions">
          <button type="button" className="btn welcome-start" onClick={onContinue}>
            Start Chapter 2
          </button>
          <button type="button" className="btn secondary" onClick={onSkip}>
            Finish later
          </button>
        </div>
      </div>
    </div>
  );
}
