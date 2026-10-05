import { useCallback, useEffect, useState } from "react";
import {
  type OutboxRow,
  cancelOutboxRow as dbCancelOutboxRow,
  editFailedOutboxBackToDraft,
  loadOutbox,
} from "../lib/db";
import { subscribeStoreChanges } from "../lib/db/notify";
import { isPending } from "../lib/outbox-core";
import type { FormState } from "../types";

interface UseOutbox {
  rows: OutboxRow[];
  queuedCount: number;
  cancel: (id: string) => Promise<boolean>;
  retryAsDraft: (id: string) => Promise<string | null>;
}

function payloadToFormState(payload: OutboxRow["payload"]): Omit<FormState, "photo"> {
  const { client_submission_id: _client_submission_id, ...rest } = payload;
  return {
    damage_class: rest.damage_class ?? null,
    infra_type: rest.infra_type ?? [],
    infra_type_other: rest.infra_type_other ?? "",
    infra_name: rest.infra_name ?? "",
    description: rest.description ?? "",
    crisis_nature: rest.crisis_nature ?? null,
    crisis_nature_type: rest.crisis_nature_type ?? null,
    crisis_nature_other: rest.crisis_nature_other ?? "",
    debris: rest.debris ?? null,
    electricity: rest.electricity ?? null,
    health_services: rest.health_services ?? null,
    pressing_needs: rest.pressing_needs ?? [],
    crisis_id: rest.crisis_id ?? "",
    building_id: rest.building_id,
    latitude: rest.latitude ?? null,
    longitude: rest.longitude ?? null,
    route_description: rest.route_description ?? "",
    photo_metadata: rest.photo_metadata ?? null,
    generic_answers: rest.generic_answers ?? {},
  };
}

export function useOutbox(): UseOutbox {
  const [rows, setRows] = useState<OutboxRow[]>([]);

  const refresh = useCallback(async () => {
    const all = await loadOutbox();
    setRows(all);
  }, []);

  useEffect(() => {
    void refresh();
    return subscribeStoreChanges(["outbox", "drafts+outbox"], () => void refresh());
  }, [refresh]);

  const cancel = useCallback(async (id: string) => {
    return dbCancelOutboxRow(id);
  }, []);

  const retryAsDraft = useCallback(async (id: string) => {
    const draft = await editFailedOutboxBackToDraft(id, (row) => payloadToFormState(row.payload));
    return draft?.id ?? null;
  }, []);

  return {
    rows,
    queuedCount: rows.filter((r) => isPending(r.status)).length,
    cancel,
    retryAsDraft,
  };
}
