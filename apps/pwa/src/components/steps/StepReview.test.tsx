import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { reportContentStatus } from "../../lib/reportValidation";
import type { FormPage, FormState } from "../../types";
import { StepReview } from "./StepReview";

// Client side of the minimum-content gate: (photo OR description) AND (location OR route) AND damage_class.

function emptyState(): FormState {
  return {
    photo: null,
    damage_class: null,
    infra_type: [],
    infra_type_other: "",
    infra_name: "",
    description: "",
    crisis_nature: null,
    crisis_nature_type: null,
    crisis_nature_other: "",
    debris: null,
    electricity: null,
    health_services: null,
    pressing_needs: [],
    crisis_id: "crisis-1",
    latitude: null,
    longitude: null,
    route_description: "",
    photo_metadata: null,
    generic_answers: {},
  };
}

const pages: FormPage[] = [
  { kind: "photo_and_damage", enabled: true, locked: true },
  { kind: "location", enabled: true, locked: true },
  { kind: "description", enabled: true, locked: false },
];

function renderReview(state: FormState) {
  const onSubmit = vi.fn();
  const onEdit = vi.fn();
  render(
    <StepReview
      formState={state}
      online={true}
      onEdit={onEdit}
      onBack={vi.fn()}
      onSubmit={onSubmit}
      crisisName="Test crisis"
      visiblePages={pages}
      content={reportContentStatus(state)}
    />,
  );
  return { onSubmit, onEdit };
}

describe("StepReview submit gate", () => {
  it("disables Submit and lists every unmet condition on an empty report", async () => {
    const { onSubmit } = renderReview(emptyState());
    const submit = screen.getByRole("button", { name: "Submit" });
    expect(submit).toBeDisabled();
    await userEvent.click(submit);
    expect(onSubmit).not.toHaveBeenCalled();
    expect(screen.getByTestId("review-missing-requirements")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Add a photo or a description" })).toBeVisible();
    expect(screen.getByRole("button", { name: "Add a location or directions" })).toBeVisible();
    expect(screen.getByRole("button", { name: "Choose a damage level" })).toBeVisible();
  });

  it("edit-jumps to the page that satisfies each unmet condition", async () => {
    const { onEdit } = renderReview(emptyState());
    await userEvent.click(screen.getByRole("button", { name: "Add a photo or a description" }));
    expect(onEdit).toHaveBeenLastCalledWith(2);
    await userEvent.click(screen.getByRole("button", { name: "Add a location or directions" }));
    expect(onEdit).toHaveBeenLastCalledWith(1);
    await userEvent.click(screen.getByRole("button", { name: "Choose a damage level" }));
    expect(onEdit).toHaveBeenLastCalledWith(0);
  });

  it("accepts a description in place of a photo and a route in place of a pin", async () => {
    const state = {
      ...emptyState(),
      description: "Roof collapsed",
      route_description: "Behind the school",
      damage_class: "complete" as const,
    };
    const { onSubmit } = renderReview(state);
    expect(screen.queryByTestId("review-missing-requirements")).not.toBeInTheDocument();
    const submit = screen.getByRole("button", { name: "Submit" });
    expect(submit).toBeEnabled();
    await userEvent.click(submit);
    expect(onSubmit).toHaveBeenCalledTimes(1);
  });

  it("keeps Submit disabled when only the damage class is missing", () => {
    renderReview({
      ...emptyState(),
      description: "Cracked walls",
      latitude: 25.3,
      longitude: 51.5,
    });
    expect(screen.getByRole("button", { name: "Submit" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Choose a damage level" })).toBeVisible();
    expect(
      screen.queryByRole("button", { name: "Add a photo or a description" }),
    ).not.toBeInTheDocument();
  });
});
