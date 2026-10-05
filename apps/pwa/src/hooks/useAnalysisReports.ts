import { useCallback, useEffect, useRef, useState } from "react";
import { createAnalysisReport, getAnalysisReport, listAnalysisReports } from "../api/admin";
import { errorMessage } from "../lib/errors";
import type { CrisisReportDetail, CrisisReportListItem } from "../types/admin";

// Detail poll for an in-flight report (self-cancelling setTimeout gated on
// page visibility). ~2s since generation takes ~30s to 2min.
const POLL_MS = 2000;

function isTerminal(status: CrisisReportListItem["status"]): boolean {
  return status === "succeeded" || status === "failed";
}

export interface UseAnalysisReports {
  // History list, newest-first. `null` until the first load resolves.
  list: CrisisReportListItem[] | null;
  listError: string | null;
  // The report being polled (just generated, or a still-running row found on
  // load). Carries live progress and, once succeeded, a signed `download_url`.
  active: CrisisReportDetail | null;
  // True while the POST is in flight, before polling begins.
  generating: boolean;
  generateError: string | null;
  generate: () => Promise<void>;
  // Mints a fresh signed `download_url` at click time; the polled one may have expired.
  fetchDetail: (reportId: string) => Promise<CrisisReportDetail>;
}

export function useAnalysisReports(crisisId: string | null): UseAnalysisReports {
  const [list, setList] = useState<CrisisReportListItem[] | null>(null);
  const [listError, setListError] = useState<string | null>(null);
  const [active, setActive] = useState<CrisisReportDetail | null>(null);
  const [generating, setGenerating] = useState(false);
  const [generateError, setGenerateError] = useState<string | null>(null);

  // State, not a ref, so changing it restarts the poll loop cleanly.
  const [pollId, setPollId] = useState<string | null>(null);

  const visibleRef = useRef(true);
  useEffect(() => {
    const onVis = () => {
      visibleRef.current = !document.hidden;
    };
    document.addEventListener("visibilitychange", onVis);
    return () => document.removeEventListener("visibilitychange", onVis);
  }, []);

  const refreshList = useCallback(async () => {
    if (!crisisId) return;
    try {
      const next = await listAnalysisReports(crisisId);
      setList(next);
      setListError(null);
      // Adopt a still-running row so a generation started elsewhere (or
      // before this mount) still shows live progress.
      setPollId((cur) => cur ?? next.find((r) => r.status === "running")?.id ?? null);
    } catch (err) {
      setListError(errorMessage(err));
    }
  }, [crisisId]);

  useEffect(() => {
    setList(null);
    setListError(null);
    setActive(null);
    setPollId(null);
    setGenerateError(null);
    void refreshList();
  }, [refreshList]);

  // Stops on terminal status and refreshes the history list once.
  useEffect(() => {
    if (!crisisId || !pollId) return;
    let cancelled = false;
    let timer: number | null = null;
    const tick = async () => {
      if (cancelled) return;
      if (!visibleRef.current) {
        timer = window.setTimeout(tick, POLL_MS);
        return;
      }
      try {
        const detail = await getAnalysisReport(crisisId, pollId);
        if (cancelled) return;
        setActive(detail);
        if (isTerminal(detail.status)) {
          setPollId(null);
          void refreshList();
          return;
        }
        timer = window.setTimeout(tick, POLL_MS);
      } catch {
        // Transient: keep trying. A hard failure surfaces as status === failed.
        if (!cancelled) timer = window.setTimeout(tick, POLL_MS);
      }
    };
    void tick();
    return () => {
      cancelled = true;
      if (timer) window.clearTimeout(timer);
    };
  }, [crisisId, pollId, refreshList]);

  const generate = useCallback(async () => {
    if (!crisisId) return;
    setGenerating(true);
    setGenerateError(null);
    try {
      const created = await createAnalysisReport(crisisId);
      // Seed `active` from the 201 so the UI shows "running" immediately.
      setActive({
        id: created.id,
        crisis_id: crisisId,
        created_at: created.created_at,
        created_by: "",
        // Unknown from the 201; the poller fills it from the detail fetch.
        created_by_email: null,
        status: created.status,
        phase: null,
        progress_count: null,
        progress_total: null,
        error: null,
        ended_at: null,
        report_count: null,
        device_count: null,
        building_count: null,
        coverage_pct: null,
        download_url: null,
      });
      setPollId(created.id);
      void refreshList();
    } catch (err) {
      setGenerateError(errorMessage(err));
    } finally {
      setGenerating(false);
    }
  }, [crisisId, refreshList]);

  const fetchDetail = useCallback(
    (reportId: string): Promise<CrisisReportDetail> => {
      if (!crisisId) return Promise.reject(new Error("no crisis selected"));
      return getAnalysisReport(crisisId, reportId);
    },
    [crisisId],
  );

  return {
    list,
    listError,
    active,
    generating,
    generateError,
    generate,
    fetchDetail,
  };
}
