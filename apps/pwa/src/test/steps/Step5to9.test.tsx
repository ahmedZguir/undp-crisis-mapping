import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { StepDebris } from "../../components/steps/StepDebris";
import { StepElectricity } from "../../components/steps/StepElectricity";
import { StepHealthServices } from "../../components/steps/StepHealthServices";
import { StepPressingNeeds } from "../../components/steps/StepPressingNeeds";

// ---------------------------------------------------------------------------
// StepDebris
// ---------------------------------------------------------------------------

describe("StepDebris", () => {
  const baseProps = {
    value: null,
    onChange: vi.fn(),
    onNext: vi.fn(),
    onBack: vi.fn(),
  };

  it("renders Yes, No, Unknown buttons", () => {
    render(<StepDebris {...baseProps} />);
    expect(screen.getByRole("button", { name: "Yes" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "No" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Unknown/ })).toBeInTheDocument();
  });

  it("calls onChange with correct value when a button is clicked", async () => {
    const onChange = vi.fn();
    render(<StepDebris {...baseProps} onChange={onChange} />);
    await userEvent.click(screen.getByRole("button", { name: "Yes" }));
    expect(onChange).toHaveBeenCalledWith("yes");
  });

  it("calls onChange with 'no' when No is clicked", async () => {
    const onChange = vi.fn();
    render(<StepDebris {...baseProps} onChange={onChange} />);
    await userEvent.click(screen.getByRole("button", { name: "No" }));
    expect(onChange).toHaveBeenCalledWith("no");
  });

  it("calls onChange with 'unknown' when Unknown is clicked", async () => {
    const onChange = vi.fn();
    render(<StepDebris {...baseProps} onChange={onChange} />);
    await userEvent.click(screen.getByRole("button", { name: /Unknown/ }));
    expect(onChange).toHaveBeenCalledWith("unknown");
  });

  it("Next button is disabled when no value is selected", () => {
    render(<StepDebris {...baseProps} />);
    expect(screen.getByRole("button", { name: "Next" })).toBeDisabled();
  });

  it("Next button is enabled when a value is selected", () => {
    render(<StepDebris {...baseProps} value="yes" />);
    expect(screen.getByRole("button", { name: "Next" })).not.toBeDisabled();
  });

  it("does not call onNext when no value is selected", async () => {
    const onNext = vi.fn();
    render(<StepDebris {...baseProps} onNext={onNext} />);
    await userEvent.click(screen.getByRole("button", { name: "Next" }));
    expect(onNext).not.toHaveBeenCalled();
  });

  it("calls onNext when a value is selected and Next is clicked", async () => {
    const onNext = vi.fn();
    render(<StepDebris {...baseProps} value="no" onNext={onNext} />);
    await userEvent.click(screen.getByRole("button", { name: "Next" }));
    expect(onNext).toHaveBeenCalled();
  });

  it("sets aria-pressed on the selected button", () => {
    render(<StepDebris {...baseProps} value="unknown" />);
    expect(screen.getByRole("button", { name: /Unknown/ })).toHaveAttribute("aria-pressed", "true");
    expect(screen.getByRole("button", { name: "Yes" })).toHaveAttribute("aria-pressed", "false");
  });

  it("calls onBack when Back is clicked", async () => {
    const onBack = vi.fn();
    render(<StepDebris {...baseProps} onBack={onBack} />);
    await userEvent.click(screen.getByRole("button", { name: "Back" }));
    expect(onBack).toHaveBeenCalled();
  });
});

// ---------------------------------------------------------------------------
// StepElectricity
// ---------------------------------------------------------------------------

describe("StepElectricity", () => {
  const baseProps = {
    value: null,
    onChange: vi.fn(),
    onNext: vi.fn(),
    onBack: vi.fn(),
  };

  it("renders all 6 options", () => {
    render(<StepElectricity {...baseProps} />);
    expect(screen.getByRole("button", { name: "Fully functional" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Partially functional" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Not functional" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Unknown" })).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: "No electricity infrastructure" }),
    ).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Damaged but repairable" })).toBeInTheDocument();
  });

  it("calls onChange with the selected option string", async () => {
    const onChange = vi.fn();
    render(<StepElectricity {...baseProps} onChange={onChange} />);
    await userEvent.click(screen.getByRole("button", { name: "Partially functional" }));
    expect(onChange).toHaveBeenCalledWith("Partially functional");
  });

  it("Next button is disabled when no value is selected", () => {
    render(<StepElectricity {...baseProps} />);
    expect(screen.getByRole("button", { name: "Next" })).toBeDisabled();
  });

  it("Next button is enabled when a value is selected", () => {
    render(<StepElectricity {...baseProps} value="Not functional" />);
    expect(screen.getByRole("button", { name: "Next" })).not.toBeDisabled();
  });

  it("does not call onNext when no value selected", async () => {
    const onNext = vi.fn();
    render(<StepElectricity {...baseProps} onNext={onNext} />);
    await userEvent.click(screen.getByRole("button", { name: "Next" }));
    expect(onNext).not.toHaveBeenCalled();
  });

  it("calls onNext when a value is selected and Next is clicked", async () => {
    const onNext = vi.fn();
    render(<StepElectricity {...baseProps} value="Fully functional" onNext={onNext} />);
    await userEvent.click(screen.getByRole("button", { name: "Next" }));
    expect(onNext).toHaveBeenCalled();
  });

  it("marks the selected option with aria-pressed=true", () => {
    render(<StepElectricity {...baseProps} value="Unknown" />);
    expect(screen.getByRole("button", { name: "Unknown" })).toHaveAttribute("aria-pressed", "true");
  });

  it("calls onBack when Back is clicked", async () => {
    const onBack = vi.fn();
    render(<StepElectricity {...baseProps} onBack={onBack} />);
    await userEvent.click(screen.getByRole("button", { name: "Back" }));
    expect(onBack).toHaveBeenCalled();
  });
});

// ---------------------------------------------------------------------------
// StepHealthServices
// ---------------------------------------------------------------------------

describe("StepHealthServices", () => {
  const baseProps = {
    value: null,
    onChange: vi.fn(),
    onNext: vi.fn(),
    onBack: vi.fn(),
  };

  it("renders all 5 options", () => {
    render(<StepHealthServices {...baseProps} />);
    expect(screen.getByRole("button", { name: "Fully functioning" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Partially functioning" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Not functioning" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Unknown" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "No health facility nearby" })).toBeInTheDocument();
  });

  it("calls onChange with the selected option string", async () => {
    const onChange = vi.fn();
    render(<StepHealthServices {...baseProps} onChange={onChange} />);
    await userEvent.click(screen.getByRole("button", { name: "Not functioning" }));
    expect(onChange).toHaveBeenCalledWith("Not functioning");
  });

  it("Next button is disabled when no value is selected", () => {
    render(<StepHealthServices {...baseProps} />);
    expect(screen.getByRole("button", { name: "Next" })).toBeDisabled();
  });

  it("Next button is enabled when a value is selected", () => {
    render(<StepHealthServices {...baseProps} value="Fully functioning" />);
    expect(screen.getByRole("button", { name: "Next" })).not.toBeDisabled();
  });

  it("does not call onNext when no value selected", async () => {
    const onNext = vi.fn();
    render(<StepHealthServices {...baseProps} onNext={onNext} />);
    await userEvent.click(screen.getByRole("button", { name: "Next" }));
    expect(onNext).not.toHaveBeenCalled();
  });

  it("calls onNext when a value is selected and Next is clicked", async () => {
    const onNext = vi.fn();
    render(<StepHealthServices {...baseProps} value="Partially functioning" onNext={onNext} />);
    await userEvent.click(screen.getByRole("button", { name: "Next" }));
    expect(onNext).toHaveBeenCalled();
  });

  it("marks the selected option with aria-pressed=true", () => {
    render(<StepHealthServices {...baseProps} value="Unknown" />);
    expect(screen.getByRole("button", { name: "Unknown" })).toHaveAttribute("aria-pressed", "true");
  });

  it("calls onBack when Back is clicked", async () => {
    const onBack = vi.fn();
    render(<StepHealthServices {...baseProps} onBack={onBack} />);
    await userEvent.click(screen.getByRole("button", { name: "Back" }));
    expect(onBack).toHaveBeenCalled();
  });
});

// ---------------------------------------------------------------------------
// StepPressingNeeds
// ---------------------------------------------------------------------------

describe("StepPressingNeeds", () => {
  const baseProps = {
    value: [],
    onChange: vi.fn(),
    onNext: vi.fn(),
    onBack: vi.fn(),
  };

  it("renders all 8 options", () => {
    render(<StepPressingNeeds {...baseProps} />);
    const options = [
      "Food",
      "Water",
      "Shelter",
      "Medical care",
      "Search and rescue",
      "Psychosocial support",
      "Cash assistance",
      "Other",
    ];
    for (const opt of options) {
      expect(screen.getByRole("button", { name: opt })).toBeInTheDocument();
    }
  });

  it("calls onChange when an option is clicked", async () => {
    const onChange = vi.fn();
    render(<StepPressingNeeds {...baseProps} onChange={onChange} />);
    await userEvent.click(screen.getByRole("button", { name: "Food" }));
    expect(onChange).toHaveBeenCalledWith(["Food"]);
  });

  it("allows selecting multiple options up to 3", async () => {
    const onChange = vi.fn();
    render(<StepPressingNeeds {...baseProps} value={["Food", "Water"]} onChange={onChange} />);
    await userEvent.click(screen.getByRole("button", { name: "Shelter" }));
    expect(onChange).toHaveBeenCalledWith(["Food", "Water", "Shelter"]);
  });

  it("deselects an already-selected option", async () => {
    const onChange = vi.fn();
    render(<StepPressingNeeds {...baseProps} value={["Food", "Water"]} onChange={onChange} />);
    await userEvent.click(screen.getByRole("button", { name: "Food" }));
    expect(onChange).toHaveBeenCalledWith(["Water"]);
  });

  it("enforces max-3 by replacing the earliest selection when a 4th is clicked", async () => {
    const onChange = vi.fn();
    render(
      <StepPressingNeeds {...baseProps} value={["Food", "Water", "Shelter"]} onChange={onChange} />,
    );
    await userEvent.click(screen.getByRole("button", { name: "Medical care" }));
    expect(onChange).toHaveBeenCalledWith(["Water", "Shelter", "Medical care"]);
  });

  it("Next button is always enabled (pressing needs is optional)", () => {
    render(<StepPressingNeeds {...baseProps} />);
    expect(screen.getByRole("button", { name: "Next" })).not.toBeDisabled();
  });

  it("calls onNext when Next is clicked even with no selections", async () => {
    const onNext = vi.fn();
    render(<StepPressingNeeds {...baseProps} onNext={onNext} />);
    await userEvent.click(screen.getByRole("button", { name: "Next" }));
    expect(onNext).toHaveBeenCalled();
  });

  it("marks selected options with aria-pressed=true", () => {
    render(<StepPressingNeeds {...baseProps} value={["Food", "Shelter"]} />);
    expect(screen.getByRole("button", { name: "Food" })).toHaveAttribute("aria-pressed", "true");
    expect(screen.getByRole("button", { name: "Shelter" })).toHaveAttribute("aria-pressed", "true");
    expect(screen.getByRole("button", { name: "Water" })).toHaveAttribute("aria-pressed", "false");
  });

  it("calls onBack when Back is clicked", async () => {
    const onBack = vi.fn();
    render(<StepPressingNeeds {...baseProps} onBack={onBack} />);
    await userEvent.click(screen.getByRole("button", { name: "Back" }));
    expect(onBack).toHaveBeenCalled();
  });
});
