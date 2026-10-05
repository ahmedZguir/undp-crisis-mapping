import { describe, expect, it } from "vitest";
import {
  type NoticeInputs,
  type SeenBaseline,
  badgeFromUnseen,
  computeUnseen,
} from "./reportNotice";

const emptySeen: SeenBaseline = {
  submittedIds: [],
  badgeSlugs: [],
  queuedIds: [],
  failedIds: [],
  draftIds: [],
};

function inputs(over: Partial<NoticeInputs> = {}): NoticeInputs {
  return {
    submittedIds: [],
    badgeSlugs: [],
    outbox: [],
    draftIds: [],
    ...over,
  };
}

describe("computeUnseen", () => {
  it("flags only items absent from the baseline", () => {
    const seen: SeenBaseline = { ...emptySeen, submittedIds: ["r1"] };
    const u = computeUnseen(inputs({ submittedIds: ["r1", "r2"] }), seen);
    expect([...u.submitted]).toEqual(["r2"]);
  });

  it("treats submitting as queued", () => {
    const u = computeUnseen(inputs({ outbox: [{ id: "o1", status: "submitting" }] }), emptySeen);
    expect([...u.queued]).toEqual(["o1"]);
    expect(u.failed.size).toBe(0);
  });

  it("re-alerts a queued row once it transitions to failed", () => {
    // The row's id was seen while queued, but never seen as failed.
    const seen: SeenBaseline = { ...emptySeen, queuedIds: ["o1"] };
    const u = computeUnseen(inputs({ outbox: [{ id: "o1", status: "failed" }] }), seen);
    expect([...u.failed]).toEqual(["o1"]);
    expect(u.queued.size).toBe(0);
  });

  it("separates badges and drafts", () => {
    const u = computeUnseen(inputs({ badgeSlugs: ["first-report"], draftIds: ["d1"] }), emptySeen);
    expect([...u.badges]).toEqual(["first-report"]);
    expect([...u.drafts]).toEqual(["d1"]);
  });
});

describe("badgeFromUnseen", () => {
  const base = {
    submitted: new Set<string>(),
    badges: new Set<string>(),
    queued: new Set<string>(),
    failed: new Set<string>(),
    drafts: new Set<string>(),
  };

  it("returns null when nothing is unseen", () => {
    expect(badgeFromUnseen(base)).toBeNull();
  });

  it("prioritises red over yellow over green", () => {
    expect(
      badgeFromUnseen({
        ...base,
        failed: new Set(["a"]),
        queued: new Set(["b", "c"]),
        submitted: new Set(["d"]),
      }),
    ).toEqual({ color: "red", count: 1 });

    expect(
      badgeFromUnseen({ ...base, queued: new Set(["b", "c"]), submitted: new Set(["d"]) }),
    ).toEqual({ color: "yellow", count: 2 });
  });

  it("sums submitted + badges for the green count", () => {
    expect(
      badgeFromUnseen({ ...base, submitted: new Set(["a", "b"]), badges: new Set(["x"]) }),
    ).toEqual({ color: "green", count: 3 });
  });

  it("ignores drafts for the tab dot", () => {
    expect(badgeFromUnseen({ ...base, drafts: new Set(["d1", "d2"]) })).toBeNull();
  });
});
