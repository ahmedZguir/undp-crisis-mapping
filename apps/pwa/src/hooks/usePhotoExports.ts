import { useCallback, useEffect, useRef, useState } from "react";
import { createPhotoExport, getPhotoExport, listPhotoExports } from "../api/admin";
import type { SearchRequest } from "../api/search";
import { errorMessage } from "../lib/errors";
import type {
  ExportManifestFormat,
  ExportScope,
  PhotoExportDetail,
  PhotoExportListItem,
} from "../types/admin";

// A bundle can run for hours at national scale, but a watcher wants prompt
// feedback. Gated on page visibility so a background tab does not poll.
const POLL_MS = 2000;

function isTerminal(status: PhotoExportListItem["status"]): boolean {
  return status === "succeeded" || status === "failed" || status === "expired";
}

export interface UsePhotoExports {
  list: PhotoExportListItem[] | null;
  listError: string | null;
  active: PhotoExportDetail | null;
  starting: boolean;
  startError: string | null;
  // Starts a new export, or attaches to an identical running one, and polls it.
  start: (args: {
    filters: SearchRequest;
    scope: ExportScope;
    format: ExportManifestFormat;
  }) => Promise<void>;
  // Mints fresh signed part URLs at click time; the polled ones may have expired.
  fetchDetail: (exportId: string) => Promise<PhotoExportDetail>;
}

export function usePhotoExports(crisisId: string | null): UsePhotoExports {
  const [list, setList] = useState<PhotoExportListItem[] | null>(null);
  const [listError, setListError] = useState<string | null>(null);
  const [active, setActive] = useState<PhotoExportDetail | null>(null);
  const [starting, setStarting] = useState(false);
  const [startError, setStartError] = useState<string | null>(null);
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
      const next = await listPhotoExports(crisisId);
      setList(next);
      setListError(null);
      // Adopt a still-running row so an export started elsewhere shows live progress.
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
    setStartError(null);
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
        const detail = await getPhotoExport(crisisId, pollId);
        if (cancelled) return;
        setActive(detail);
        if (isTerminal(detail.status)) {
          setPollId(null);
          void refreshList();
          return;
        }
        timer = window.setTimeout(tick, POLL_MS);
      } catch {
        // Transient: keep trying. A hard failure surfaces as a terminal status.
        if (!cancelled) timer = window.setTimeout(tick, POLL_MS);
      }
    };
    void tick();
    return () => {
      cancelled = true;
      if (timer) window.clearTimeout(timer);
    };
  }, [crisisId, pollId, refreshList]);

  const start = useCallback(
    async (args: {
      filters: SearchRequest;
      scope: ExportScope;
      format: ExportManifestFormat;
    }) => {
      if (!crisisId) return;
      setStarting(true);
      setStartError(null);
      try {
        const created = await createPhotoExport(crisisId, args);
        // Seed `active` from the 201 so the UI shows "running" immediately. An
        // attached job's id is the already-running row, which is what we poll.
        setActive({
          id: created.id,
          crisis_id: crisisId,
          created_at: created.created_at,
          created_by: "",
          created_by_email: null,
          status: created.status,
          phase: null,
          progress_count: null,
          progress_total: null,
          error: null,
          ended_at: null,
          scope: args.scope,
          format: args.format,
          photo_count: null,
          total_bytes: null,
          expires_at: null,
          parts: [],
        });
        setPollId(created.id);
        void refreshList();
      } catch (err) {
        setStartError(errorMessage(err));
      } finally {
        setStarting(false);
      }
    },
    [crisisId, refreshList],
  );

  const fetchDetail = useCallback(
    (exportId: string): Promise<PhotoExportDetail> => {
      if (!crisisId) return Promise.reject(new Error("no crisis selected"));
      return getPhotoExport(crisisId, exportId);
    },
    [crisisId],
  );

  return { list, listError, active, starting, startError, start, fetchDetail };
}
