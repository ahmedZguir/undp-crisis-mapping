// Optional module page, disabled by default in the crisis form schema.
import {
  type SingleChoiceOption,
  SingleChoiceStep,
  type SingleChoiceStepNavProps,
} from "./SingleChoiceStep";

// Stored values are canonical English labels (see SingleChoiceStep).
const OPTIONS: SingleChoiceOption[] = [
  { value: "Fully functional", labelKey: "stepElectricity.fullyFunctional" },
  { value: "Partially functional", labelKey: "stepElectricity.partiallyFunctional" },
  { value: "Not functional", labelKey: "stepElectricity.notFunctional" },
  { value: "Unknown", labelKey: "stepElectricity.unknown" },
  { value: "No electricity infrastructure", labelKey: "stepElectricity.noInfrastructure" },
  { value: "Damaged but repairable", labelKey: "stepElectricity.damagedRepairable" },
];

export function StepElectricity(props: SingleChoiceStepNavProps) {
  return <SingleChoiceStep i18nPrefix="stepElectricity" options={OPTIONS} {...props} />;
}
