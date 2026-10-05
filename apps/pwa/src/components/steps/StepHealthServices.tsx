// Optional module page, disabled by default in the crisis form schema.
import {
  type SingleChoiceOption,
  SingleChoiceStep,
  type SingleChoiceStepNavProps,
} from "./SingleChoiceStep";

// Stored values are canonical English labels (see SingleChoiceStep).
const OPTIONS: SingleChoiceOption[] = [
  { value: "Fully functioning", labelKey: "stepHealthServices.fullyFunctioning" },
  { value: "Partially functioning", labelKey: "stepHealthServices.partiallyFunctioning" },
  { value: "Not functioning", labelKey: "stepHealthServices.notFunctioning" },
  { value: "Unknown", labelKey: "stepHealthServices.unknown" },
  { value: "No health facility nearby", labelKey: "stepHealthServices.noFacilityNearby" },
];

export function StepHealthServices(props: SingleChoiceStepNavProps) {
  return <SingleChoiceStep i18nPrefix="stepHealthServices" options={OPTIONS} {...props} />;
}
