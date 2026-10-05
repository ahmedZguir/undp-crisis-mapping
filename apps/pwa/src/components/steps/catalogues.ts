// Infra-type and crisis-nature options, shared with the Review rows.
import chemIcon from "../../assets/chem.png";
import civilIcon from "../../assets/civil.png";
import commercialIcon from "../../assets/commercial.png";
import communityIcon from "../../assets/community.png";
import conflictIcon from "../../assets/conflict.png";
import eqIcon from "../../assets/eq.png";
import explosIcon from "../../assets/explos.png";
import floodIcon from "../../assets/flood.png";
import governmentIcon from "../../assets/governmental.png";
import hurricIcon from "../../assets/hurric.png";
import landslideIcon from "../../assets/landslide-icon.webp";
import publicIcon from "../../assets/public.png";
import residentialIcon from "../../assets/residential.png";
import transportIcon from "../../assets/transport.png";
import tsunamiIcon from "../../assets/tsunami.png";
import utilityIcon from "../../assets/utility.png";
import wildfireIcon from "../../assets/wildfire.png";
import type { CrisisNatureType, InfraType } from "../../types";

interface InfraTypeOption {
  labelKey: string;
  value: InfraType;
  examplesKey?: string;
  icon?: string;
}

export const INFRA_TYPE_OPTIONS: InfraTypeOption[] = [
  {
    labelKey: "stepInfraType.residential",
    value: "residential",
    examplesKey: "stepInfraType.residentialExamples",
    icon: residentialIcon,
  },
  {
    labelKey: "stepInfraType.commercial",
    value: "commercial",
    examplesKey: "stepInfraType.commercialExamples",
    icon: commercialIcon,
  },
  {
    labelKey: "stepInfraType.government",
    value: "government",
    examplesKey: "stepInfraType.governmentExamples",
    icon: governmentIcon,
  },
  {
    labelKey: "stepInfraType.utility",
    value: "utility",
    examplesKey: "stepInfraType.utilityExamples",
    icon: utilityIcon,
  },
  {
    labelKey: "stepInfraType.transport",
    value: "transport",
    examplesKey: "stepInfraType.transportExamples",
    icon: transportIcon,
  },
  {
    labelKey: "stepInfraType.community",
    value: "community",
    examplesKey: "stepInfraType.communityExamples",
    icon: communityIcon,
  },
  {
    labelKey: "stepInfraType.publicSpaces",
    value: "public_spaces",
    examplesKey: "stepInfraType.publicSpacesExamples",
    icon: publicIcon,
  },
  {
    labelKey: "stepInfraType.other",
    value: "other",
  },
];

interface CrisisNatureOption {
  /** Canonical English nature label used as the persisted value. */
  label: string;
  labelKey: string;
  sublabelKey?: string;
  icon: string;
}

interface CrisisNatureTypeOption {
  value: CrisisNatureType;
  labelKey: string;
  headerColor: string;
  subs: CrisisNatureOption[];
}

/** Persisted `crisis_nature` value for the free-text "Other" choice. */
export const CRISIS_NATURE_OTHER = "Other";

export const CRISIS_NATURE_TYPES: CrisisNatureTypeOption[] = [
  {
    value: "natural_hazards",
    labelKey: "stepInfraDetails.natural",
    headerColor: "#0369a1",
    subs: [
      { label: "Earthquake", labelKey: "stepInfraDetails.earthquake", icon: eqIcon },
      { label: "Flood", labelKey: "stepInfraDetails.flood", icon: floodIcon },
      { label: "Tsunami", labelKey: "stepInfraDetails.tsunami", icon: tsunamiIcon },
      {
        label: "Hurricane",
        labelKey: "stepInfraDetails.hurricane",
        sublabelKey: "stepInfraDetails.hurricaneSub",
        icon: hurricIcon,
      },
      { label: "Landslide", labelKey: "stepInfraDetails.landslide", icon: landslideIcon },
      { label: "Wildfire", labelKey: "stepInfraDetails.wildfire", icon: wildfireIcon },
    ],
  },
  {
    value: "technological",
    labelKey: "stepInfraDetails.technological",
    headerColor: "#6d28d9",
    subs: [
      { label: "Explosion", labelKey: "stepInfraDetails.explosion", icon: explosIcon },
      { label: "Chemical incident", labelKey: "stepInfraDetails.chemical", icon: chemIcon },
    ],
  },
  {
    value: "human_made",
    labelKey: "stepInfraDetails.humanMade",
    headerColor: "#b91c1c",
    subs: [
      { label: "Conflict", labelKey: "stepInfraDetails.conflict", icon: conflictIcon },
      { label: "Civil unrest", labelKey: "stepInfraDetails.civil", icon: civilIcon },
    ],
  },
];
