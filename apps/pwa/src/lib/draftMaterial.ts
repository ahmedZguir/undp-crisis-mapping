import type { FormState } from "../types";

// Crossing this threshold promotes an in-memory form into a persisted draft row.
export function hasMaterialContent(state: FormState): boolean {
  return Boolean(
    state.photo ||
      state.damage_class ||
      state.latitude != null ||
      state.building_id ||
      state.infra_type.length > 0 ||
      state.infra_name.trim() ||
      state.description.trim() ||
      state.crisis_nature ||
      state.crisis_nature_type ||
      state.debris ||
      state.electricity ||
      state.health_services ||
      state.pressing_needs.length > 0,
  );
}
