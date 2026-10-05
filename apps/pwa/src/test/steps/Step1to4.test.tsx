import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

// StepPhoto captures through the platform camera adapter; stub it so tests
// drive capture without a real file picker / native plugin.
vi.mock("../../platform/camera", () => ({
  camera: { capturePhoto: vi.fn() },
}));
import { StepInfraType } from "../../components/steps/StepInfraType";
import { StepPhoto } from "../../components/steps/StepPhoto";
import type { PhotoMetadata } from "../../lib/photoMetadata";
import { camera } from "../../platform/camera";
import type { DamageClass, InfraType } from "../../types/index";

const emptyMetadata = (): PhotoMetadata => ({
  gps: null,
  capturedAt: null,
  orientation: null,
  width: null,
  height: null,
  camera: null,
  extractedAt: "test",
});

// ---------------------------------------------------------------------------
// StepPhoto
// ---------------------------------------------------------------------------
const photoBaseProps = {
  value: null as File | null,
  onChange: vi.fn(),
  damageClass: null as DamageClass | null,
  onChangeDamageClass: vi.fn(),
  onNext: vi.fn(),
  onBack: vi.fn(),
};

describe("StepPhoto", () => {
  it("renders a capture target", () => {
    render(<StepPhoto {...photoBaseProps} />);
    expect(screen.getByRole("button", { name: /upload photo/i })).toBeInTheDocument();
  });

  it("does not use getUserMedia", () => {
    render(<StepPhoto {...photoBaseProps} />);
    expect(navigator.mediaDevices?.getUserMedia).toBeUndefined();
  });

  it("shows no preview when value is null", () => {
    render(<StepPhoto {...photoBaseProps} />);
    expect(screen.queryByRole("img", { name: /preview/i })).not.toBeInTheDocument();
  });

  it("shows thumbnail preview when value is a File", () => {
    const objectUrl = "blob:fake-url";
    vi.stubGlobal("URL", {
      createObjectURL: vi.fn(() => objectUrl),
      revokeObjectURL: vi.fn(),
    });

    const file = new File(["data"], "photo.jpg", { type: "image/jpeg" });
    render(<StepPhoto {...photoBaseProps} value={file} />);
    const img = screen.getByAltText(/preview/i) as HTMLImageElement;
    expect(img).toBeInTheDocument();
    expect(img.src).toBe(objectUrl);

    vi.unstubAllGlobals();
  });

  it("calls onChange with the captured photo", async () => {
    const user = userEvent.setup();
    const handleChange = vi.fn();
    const file = new File(["data"], "damage.jpg", { type: "image/jpeg" });
    vi.mocked(camera.capturePhoto).mockResolvedValue({
      blob: file,
      metadata: emptyMetadata(),
    });
    render(<StepPhoto {...photoBaseProps} onChange={handleChange} />);
    await user.click(screen.getByRole("button", { name: /upload photo/i }));
    expect(handleChange).toHaveBeenCalledWith(file);
  });

  it("passes EXIF metadata to onMetadataChange", async () => {
    const user = userEvent.setup();
    const onMetadataChange = vi.fn();
    const file = new File(["data"], "damage.jpg", { type: "image/jpeg" });
    const gps = { latitude: 1.5, longitude: 2.5 };
    vi.mocked(camera.capturePhoto).mockResolvedValue({
      blob: file,
      metadata: { ...emptyMetadata(), gps },
    });
    render(
      <StepPhoto {...photoBaseProps} onChange={vi.fn()} onMetadataChange={onMetadataChange} />,
    );
    await user.click(screen.getByRole("button", { name: /upload photo/i }));
    expect(onMetadataChange).toHaveBeenCalledWith(expect.objectContaining({ gps }));
  });

  it("Next button is always rendered and not disabled", () => {
    render(<StepPhoto {...photoBaseProps} />);
    expect(screen.getByRole("button", { name: /next/i })).not.toBeDisabled();
  });

  it("shows validation and blocks Next when damageClass is not set", async () => {
    const user = userEvent.setup();
    const onNext = vi.fn();
    render(<StepPhoto {...photoBaseProps} onNext={onNext} />);
    await user.click(screen.getByRole("button", { name: /next/i }));
    expect(onNext).not.toHaveBeenCalled();
    expect(screen.getByRole("alert")).toBeInTheDocument();
  });

  it("calls onNext when damageClass is set and Next is clicked", async () => {
    const user = userEvent.setup();
    const onNext = vi.fn();
    render(
      <StepPhoto {...photoBaseProps} damageClass={"partial" as DamageClass} onNext={onNext} />,
    );
    await user.click(screen.getByRole("button", { name: /next/i }));
    expect(onNext).toHaveBeenCalledOnce();
  });

  it("calls onBack when Back is clicked", async () => {
    const user = userEvent.setup();
    const onBack = vi.fn();
    render(<StepPhoto {...photoBaseProps} onBack={onBack} />);
    await user.click(screen.getByRole("button", { name: /back/i }));
    expect(onBack).toHaveBeenCalledOnce();
  });
});

// ---------------------------------------------------------------------------
// StepInfraType
// ---------------------------------------------------------------------------
describe("StepInfraType", () => {
  const defaultProps = {
    value: [] as InfraType[],
    otherValue: "",
    onOtherChange: vi.fn(),
    onChange: vi.fn(),
    onNext: vi.fn(),
    onBack: vi.fn(),
  };

  const infraOptions = [
    "Residential Infrastructure",
    "Commercial Infrastructure",
    "Government Building",
    "Utility Infrastructure",
    "Transport and Communication Infrastructure",
    "Community Infrastructure",
    "Public Spaces",
    "Other",
  ];

  it("renders 8 infrastructure type buttons", () => {
    render(<StepInfraType {...defaultProps} />);
    for (const label of infraOptions) {
      expect(screen.getByRole("button", { name: new RegExp(label, "i") })).toBeInTheDocument();
    }
  });

  it("selected buttons have aria-pressed='true', unselected have aria-pressed='false'", () => {
    render(<StepInfraType {...defaultProps} value={["residential", "utility"] as InfraType[]} />);
    expect(screen.getByRole("button", { name: /residential infrastructure/i })).toHaveAttribute(
      "aria-pressed",
      "true",
    );
    expect(screen.getByRole("button", { name: /utility infrastructure/i })).toHaveAttribute(
      "aria-pressed",
      "true",
    );
    expect(screen.getByRole("button", { name: /commercial infrastructure/i })).toHaveAttribute(
      "aria-pressed",
      "false",
    );
  });

  it("calls onChange with array toggling items on and off", async () => {
    const user = userEvent.setup();
    const handleChange = vi.fn();
    render(<StepInfraType {...defaultProps} onChange={handleChange} />);
    await user.click(screen.getByRole("button", { name: /community infrastructure/i }));
    expect(handleChange).toHaveBeenCalledWith(["community"]);
    await user.click(screen.getByRole("button", { name: /utility infrastructure/i }));
    expect(handleChange).toHaveBeenCalledWith(["utility"]);
  });

  it("deselects an item when clicked again", async () => {
    const user = userEvent.setup();
    const handleChange = vi.fn();
    render(
      <StepInfraType
        {...defaultProps}
        value={["residential", "utility"] as InfraType[]}
        onChange={handleChange}
      />,
    );
    await user.click(screen.getByRole("button", { name: /utility infrastructure/i }));
    expect(handleChange).toHaveBeenCalledWith(["residential"]);
  });

  it("shows validation message and does not call onNext when no value selected", async () => {
    const user = userEvent.setup();
    const onNext = vi.fn();
    render(<StepInfraType {...defaultProps} onNext={onNext} />);
    await user.click(screen.getByRole("button", { name: /next/i }));
    expect(onNext).not.toHaveBeenCalled();
    expect(screen.getByRole("alert")).toBeInTheDocument();
  });

  it("calls onNext when at least one non-other value is selected", async () => {
    const user = userEvent.setup();
    const onNext = vi.fn();
    render(
      <StepInfraType
        {...defaultProps}
        value={["transport", "community"] as InfraType[]}
        onNext={onNext}
      />,
    );
    await user.click(screen.getByRole("button", { name: /next/i }));
    expect(onNext).toHaveBeenCalledOnce();
  });

  it("shows a text field when Other is among selected types", () => {
    render(<StepInfraType {...defaultProps} value={["other"] as InfraType[]} />);
    expect(
      screen.getByRole("textbox", { name: /specify other infrastructure type/i }),
    ).toBeInTheDocument();
  });

  it("does not show text field when Other is not selected", () => {
    render(<StepInfraType {...defaultProps} value={["utility"] as InfraType[]} />);
    expect(
      screen.queryByRole("textbox", { name: /specify other infrastructure type/i }),
    ).not.toBeInTheDocument();
  });

  it("allows Next when Other is selected but text field is empty", async () => {
    const user = userEvent.setup();
    const onNext = vi.fn();
    render(
      <StepInfraType
        {...defaultProps}
        value={["other"] as InfraType[]}
        otherValue=""
        onNext={onNext}
      />,
    );
    await user.click(screen.getByRole("button", { name: /next/i }));
    expect(onNext).toHaveBeenCalledOnce();
  });

  it("calls onNext when Other is selected alongside another type with an empty text field", async () => {
    const user = userEvent.setup();
    const onNext = vi.fn();
    render(
      <StepInfraType
        {...defaultProps}
        value={["residential", "other"] as InfraType[]}
        otherValue=""
        onNext={onNext}
      />,
    );
    await user.click(screen.getByRole("button", { name: /next/i }));
    expect(onNext).toHaveBeenCalledOnce();
  });

  it("calls onOtherChange when typing in the Other text field", async () => {
    const user = userEvent.setup();
    const onOtherChange = vi.fn();
    render(
      <StepInfraType
        {...defaultProps}
        value={["other"] as InfraType[]}
        otherValue=""
        onOtherChange={onOtherChange}
      />,
    );
    const input = screen.getByRole("textbox", { name: /specify other infrastructure type/i });
    await user.type(input, "W");
    expect(onOtherChange).toHaveBeenCalledWith("W");
  });

  it("calls onBack when Back is clicked", async () => {
    const user = userEvent.setup();
    const onBack = vi.fn();
    render(<StepInfraType {...defaultProps} onBack={onBack} />);
    await user.click(screen.getByRole("button", { name: /back/i }));
    expect(onBack).toHaveBeenCalledOnce();
  });
});
