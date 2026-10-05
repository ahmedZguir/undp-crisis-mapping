// Fetch and decode the step-form icons ahead of time so the steps render without jank.

import chemIcon from "../assets/chem.png";
import civilIcon from "../assets/civil.png";
import commercialIcon from "../assets/commercial.png";
import communityIcon from "../assets/community.png";
import conflictIcon from "../assets/conflict.png";
import completeIcon from "../assets/damage-complete.webp";
import minimalIcon from "../assets/damage-minimal.webp";
import partialIcon from "../assets/damage-partial.webp";
import debrisNoIcon from "../assets/debris-no.webp";
import debrisYesIcon from "../assets/debris-yes.webp";
import eqIcon from "../assets/eq.png";
import explosIcon from "../assets/explos.png";
import floodIcon from "../assets/flood.png";
import governmentIcon from "../assets/governmental.png";
import hurricIcon from "../assets/hurric.png";
import landslideIcon from "../assets/landslide-icon.webp";
import publicIcon from "../assets/public.png";
import residentialIcon from "../assets/residential.png";
import transportIcon from "../assets/transport.png";
import tsunamiIcon from "../assets/tsunami.png";
import utilityIcon from "../assets/utility.png";
import wildfireIcon from "../assets/wildfire.png";

const ICONS = [
  // StepInfraType
  commercialIcon,
  communityIcon,
  governmentIcon,
  publicIcon,
  residentialIcon,
  transportIcon,
  utilityIcon,
  // StepCrisisNature (crisis nature)
  chemIcon,
  civilIcon,
  conflictIcon,
  eqIcon,
  explosIcon,
  floodIcon,
  hurricIcon,
  landslideIcon,
  tsunamiIcon,
  wildfireIcon,
  // StepPhoto (damage class)
  completeIcon,
  minimalIcon,
  partialIcon,
  // StepDebris
  debrisNoIcon,
  debrisYesIcon,
];

let preloadPromise: Promise<void> | null = null;
// Retained so the DECODED bitmaps stay warm: the decode, not the fetch, is what janks the step.
const retained: HTMLImageElement[] = [];

// Idempotent. Callers sequence heavier work behind this: low-priority `Image()` loads would
// otherwise be preempted by e.g. the map chunk import.
export function preloadStepIcons(): Promise<void> {
  if (preloadPromise) return preloadPromise;
  preloadPromise = Promise.all(
    ICONS.map((src) => {
      const img = new Image();
      img.decoding = "async";
      img.src = src;
      retained.push(img);
      // decode() is absent in jsdom and can reject on a detached element.
      const fallback = () =>
        new Promise<void>((resolve) => {
          img.onload = () => resolve();
          img.onerror = () => resolve();
        });
      return typeof img.decode === "function" ? img.decode().catch(fallback) : fallback();
    }),
  ).then(() => undefined);
  return preloadPromise;
}
