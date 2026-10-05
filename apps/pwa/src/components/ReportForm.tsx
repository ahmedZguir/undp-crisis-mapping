import { Suspense, lazy, useEffect, useMemo, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { suggestDamageClass } from "../api/ai";
import { useLatest } from "../hooks/useLatest";
import { getClientId } from "../lib/clientId";
import { DEFAULT_FORM_SCHEMA, DEFAULT_FORM_VERSION } from "../lib/defaultFormSchema";
import { resizePhotoOffMainThread } from "../lib/photoResizeClient";
import { reportContentStatus } from "../lib/reportValidation";
import type { Crisis, DamageClass, FormPage, FormSchema, FormState, ReportPayload } from "../types";
import { CrisisPickerScreen } from "./CrisisPickerScreen";
import { MapLoadingIndicator } from "./MapLoadingIndicator";
import { StepScrollContext, type StepScrollEntry } from "./stepScroll";
import { GenericPage } from "./steps/GenericPage";
import { StepCrisisNature } from "./steps/StepCrisisNature";
import { StepDebris } from "./steps/StepDebris";
import { StepDescription } from "./steps/StepDescription";
import { StepElectricity } from "./steps/StepElectricity";
import { StepHealthServices } from "./steps/StepHealthServices";
import { StepInfraType } from "./steps/StepInfraType";
import { StepPhoto } from "./steps/StepPhoto";
import { StepPressingNeeds } from "./steps/StepPressingNeeds";
import { StepReview } from "./steps/StepReview";
import { CRISIS_NATURE_OTHER } from "./steps/catalogues";

// Only maplibre pull on the report path; lazy to keep it out of first paint (lib/warm.ts).
const StepLocation = lazy(() =>
  import("./steps/StepLocation").then((m) => ({ default: m.StepLocation })),
);

// Only seen if the location step is reached before the map chunk finished warming.
function StepLocationFallback() {
  const { t } = useTranslation();
  return (
    <MapLoadingIndicator
      label={t("stepLocation.buildingsLoading", { defaultValue: "Loading map and buildings…" })}
      style={{ flex: 1, minHeight: "60svh", background: "var(--c-surface)", gap: 10 }}
    />
  );
}

interface ReportFormProps {
  crisis: Crisis | null;
  /** Labels the Review submit button. */
  online?: boolean;
  onComplete: (reportId: string, queued: boolean) => void;
  onCancel?: () => void;
  onCrisisPick?: (crisis: Crisis) => void;
  initialState?: FormState;
  initialStep?: number;
  saveDraft?: (
    state: FormState,
    step: number,
    schema?: FormSchema,
    version?: number,
    crisis?: Crisis | null,
  ) => void;
  discardDraft?: () => Promise<void>;
  onSubmit?: (payload: ReportPayload) => Promise<{ submissionId: string; queued: boolean }>;
  /** Unsaved admin schema preview: submit shows a toast and no draft is created. */
  previewMode?: boolean;
}

const initialFormState: FormState = {
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
  // Module fields, collected when the coordinator enables the matching pages.
  electricity: null,
  health_services: null,
  pressing_needs: [],
  crisis_id: "",
  latitude: null,
  longitude: null,
  route_description: "",
  photo_metadata: null,
  generic_answers: {},
};

function hasAnyInput(s: FormState): boolean {
  return (
    s.photo !== null ||
    s.damage_class !== null ||
    s.infra_type.length > 0 ||
    s.infra_name.trim() !== "" ||
    s.description.trim() !== "" ||
    s.crisis_nature !== null ||
    s.debris !== null ||
    s.latitude !== null ||
    s.route_description.trim() !== ""
  );
}

const noop = () => {};

export function ReportForm({
  crisis,
  online = true,
  onComplete,
  onCancel,
  onCrisisPick,
  initialState,
  initialStep = 0,
  saveDraft = noop,
  discardDraft = async () => {},
  onSubmit,
  previewMode = false,
}: ReportFormProps) {
  const { t } = useTranslation();

  // Fallback when the crisis carries no schema (list response) and for stale drafts.
  const schema: FormSchema = crisis?.form_schema ?? DEFAULT_FORM_SCHEMA;
  const formVersion = crisis?.form_version ?? DEFAULT_FORM_VERSION;
  const visiblePages: FormPage[] = useMemo(() => schema.pages.filter((p) => p.enabled), [schema]);
  const totalSteps = visiblePages.length + 1; // +1 for the trailing Review

  // Clamp steps from drafts saved under an older page order.
  const clampedInitialStep = Math.min(Math.max(initialStep, 0), totalSteps - 1);
  const [currentStep, setCurrentStep] = useState(clampedInitialStep);
  // Per-step scroll positions for Next/Back within this report.
  const scrollPositions = useRef(new Map<number, StepScrollEntry>());
  // After a Review edit jump, Next snaps back to Review.
  const [jumpedFromStep, setJumpedFromStep] = useState<number | null>(null);
  const [formState, setFormState] = useState<FormState>(() => {
    const base = initialState ?? { ...initialFormState, crisis_id: crisis?.id ?? "" };
    // Tolerate drafts persisted before newer fields existed.
    return { ...initialFormState, ...base };
  });
  const [activeCrisis, setActiveCrisis] = useState<Crisis | null>(crisis);
  const [showingCrisisGate, setShowingCrisisGate] = useState(!crisis);
  const [midFormCrisisChange, setMidFormCrisisChange] = useState(false);

  // Advisory AI damage-class suggestion for the current photo; kept visible after the
  // citizen picks a different class.
  const [aiSuggestedClass, setAiSuggestedClass] = useState<DamageClass | null>(null);
  const [aiClassifying, setAiClassifying] = useState(false);
  // Once the citizen picks a class, an arriving suggestion only updates the badge.
  const userPickedClassRef = useRef(false);
  // Token so a slow suggestion for an old photo cannot overwrite a newer selection.
  const suggestSeqRef = useRef(0);
  // Raw capture previewed while the compression worker runs.
  const [pendingPreviewPhoto, setPendingPreviewPhoto] = useState<File | null>(null);

  async function requestDamageSuggestion(photo: Blob) {
    const seq = ++suggestSeqRef.current;
    let clientId: string;
    try {
      clientId = await getClientId();
    } catch {
      return;
    }
    setAiClassifying(true);
    let suggestion: DamageClass | null = null;
    try {
      suggestion = await suggestDamageClass(photo, clientId);
    } finally {
      // Only the latest request owns the spinner flag.
      if (seq === suggestSeqRef.current) setAiClassifying(false);
    }
    if (seq !== suggestSeqRef.current || suggestion === null) return;
    setAiSuggestedClass(suggestion);
    if (!userPickedClassRef.current) {
      setFormState((prev) => ({ ...prev, damage_class: suggestion }));
    }
  }

  useEffect(() => {
    const id = crisis?.id ?? "";
    setFormState((prev) => (prev.crisis_id === id ? prev : { ...prev, crisis_id: id }));
  }, [crisis]);

  // Refs keep autosave keyed only on content: saveDraft/schema get fresh identities each
  // render, and each save re-renders via the drafts list, which loops every ~300ms and
  // floods the native SQLite bridge.
  const saveDraftRef = useLatest(saveDraft);
  const schemaRef = useLatest(schema);
  const formVersionRef = useLatest(formVersion);
  // Pin the crisis so a resumed draft submits under the crisis it was created for.
  const activeCrisisRef = useLatest(activeCrisis);

  useEffect(() => {
    // Preview mode leaves no trace.
    if (previewMode) return;
    if (hasAnyInput(formState)) {
      saveDraftRef.current(
        formState,
        currentStep,
        schemaRef.current,
        formVersionRef.current,
        activeCrisisRef.current,
      );
    }
  }, [formState, currentStep, previewMode]);

  function returnFromEditJump(): boolean {
    if (jumpedFromStep === null) return false;
    setJumpedFromStep(null);
    setCurrentStep(jumpedFromStep);
    return true;
  }

  function advance() {
    if (returnFromEditJump()) return;
    setCurrentStep((prev) => (prev < totalSteps - 1 ? prev + 1 : prev));
  }

  function retreat() {
    if (returnFromEditJump()) return;
    setCurrentStep((prev) => (prev > 0 ? prev - 1 : prev));
  }

  function jumpToEdit(target: number) {
    setJumpedFromStep(currentStep);
    setCurrentStep(target);
  }

  const contentStatus = reportContentStatus(formState);

  async function submit() {
    if (previewMode) {
      // No draft exists in preview mode, so nothing to discard.
      window.alert(t("preview.notSubmitted", { defaultValue: "Preview — not submitted" }));
      return;
    }
    // Review already disables Submit when the minimum-content gate is unmet; guard anyway.
    if (!contentStatus.submittable) return;
    const payload: ReportPayload = {
      photo: formState.photo,
      crisis_id: formState.crisis_id,
      damage_class: formState.damage_class ?? "minimal",
      infra_type: formState.infra_type,
      infra_type_other: formState.infra_type_other,
      infra_name: formState.infra_name,
      description: formState.description,
      crisis_nature: formState.crisis_nature,
      crisis_nature_type: formState.crisis_nature_type,
      crisis_nature_other: formState.crisis_nature_other,
      debris: formState.debris,
      electricity: formState.electricity,
      health_services: formState.health_services,
      pressing_needs: formState.pressing_needs,
      building_id: formState.building_id,
      latitude: formState.latitude,
      longitude: formState.longitude,
      route_description: formState.route_description,
      photo_metadata: formState.photo_metadata,
      form_version: formVersion,
      generic_answers: formState.generic_answers,
    };
    if (!onSubmit) return;
    const { submissionId, queued } = await onSubmit(payload);
    await discardDraft();
    onComplete(submissionId, queued);
  }

  if (showingCrisisGate || midFormCrisisChange) {
    return (
      <CrisisPickerScreen
        onBack={midFormCrisisChange ? () => setMidFormCrisisChange(false) : (onCancel ?? noop)}
        onPick={(picked) => {
          if (midFormCrisisChange) {
            // Keep photo (+ metadata), damage_class and description; reset everything else.
            setFormState((prev) => ({
              ...initialFormState,
              crisis_id: picked.id,
              photo: prev.photo,
              damage_class: prev.damage_class,
              photo_metadata: prev.photo_metadata,
              description: prev.description,
            }));
            setActiveCrisis(picked);
            onCrisisPick?.(picked);
            setMidFormCrisisChange(false);
            // First page after photo_and_damage.
            setCurrentStep(1);
          } else {
            setFormState((prev) => ({ ...prev, crisis_id: picked.id }));
            setActiveCrisis(picked);
            onCrisisPick?.(picked);
            setShowingCrisisGate(false);
          }
        }}
      />
    );
  }

  function renderStep() {
    if (currentStep === visiblePages.length) {
      return (
        <StepReview
          formState={formState}
          online={online}
          onEdit={jumpToEdit}
          onBack={retreat}
          onSubmit={submit}
          stepNumber={currentStep + 1}
          totalSteps={totalSteps}
          crisisName={activeCrisis?.name ?? null}
          visiblePages={visiblePages}
          content={contentStatus}
        />
      );
    }
    const page = visiblePages[currentStep];
    if (!page) return null;
    const nav = {
      onNext: advance,
      onBack: retreat,
      stepNumber: currentStep + 1,
      totalSteps,
    };
    // Built-in select pages are required unless the coordinator marked them optional.
    // Locked pages (photo/location) and the description page ignore this.
    const required = page.required ?? true;
    switch (page.kind) {
      case "photo_and_damage":
        return (
          <StepPhoto
            value={formState.photo}
            previewFile={pendingPreviewPhoto}
            onChange={async (file) => {
              // A new photo clears the stale badge; an explicit citizen choice is preserved.
              setAiSuggestedClass(null);
              // Preview the raw capture now; formState.photo stays null until the worker finishes
              // so the outbox only ever gets the compressed blob.
              setPendingPreviewPhoto(file);
              let forSuggestion: Blob = file;
              try {
                const resized = await resizePhotoOffMainThread(file);
                // The worker may emit WebP or JPEG depending on platform support.
                const ext = resized.type === "image/webp" ? ".webp" : ".jpg";
                const resizedFile = new File([resized], file.name.replace(/\.\w+$/, ext), {
                  type: resized.type || "image/jpeg",
                  lastModified: file.lastModified,
                });
                forSuggestion = resizedFile;
                setFormState((prev) => ({ ...prev, photo: resizedFile }));
              } catch {
                // Fall back to the original so the user can still submit.
                setFormState((prev) => ({ ...prev, photo: file }));
              }
              setPendingPreviewPhoto(null);
              // Best-effort, reuses the compressed image; never blocks the form.
              void requestDamageSuggestion(forSuggestion);
            }}
            onMetadataChange={(meta) => setFormState((prev) => ({ ...prev, photo_metadata: meta }))}
            damageClass={formState.damage_class}
            aiSuggestedClass={aiSuggestedClass}
            aiClassifying={aiClassifying}
            onChangeDamageClass={(v) => {
              // Citizen choice wins from now on; the AI badge stays for comparison.
              userPickedClassRef.current = true;
              setFormState((prev) => ({ ...prev, damage_class: v }));
            }}
            {...nav}
            onBack={onCancel ?? noop}
          />
        );
      case "description":
        return (
          <StepDescription
            photo={formState.photo}
            onRetake={() => {
              suggestSeqRef.current++;
              setAiSuggestedClass(null);
              setFormState((prev) => ({ ...prev, photo: null, photo_metadata: null }));
              // Retake always returns to step 0, even from an edit jump.
              setJumpedFromStep(null);
              setCurrentStep(0);
            }}
            description={formState.description}
            onChangeDescription={(v) => setFormState((prev) => ({ ...prev, description: v }))}
            {...nav}
          />
        );
      case "location":
        return (
          <Suspense fallback={<StepLocationFallback />}>
            <StepLocation
              latitude={formState.latitude}
              longitude={formState.longitude}
              crisis={activeCrisis}
              infraName={formState.infra_name}
              onChangeInfraName={(v) => setFormState((prev) => ({ ...prev, infra_name: v }))}
              routeDescription={formState.route_description}
              onChangeRouteDescription={(v) =>
                setFormState((prev) =>
                  // GPS pin and route description are mutually exclusive.
                  v.trim() !== ""
                    ? {
                        ...prev,
                        route_description: v,
                        latitude: null,
                        longitude: null,
                        building_id: undefined,
                      }
                    : { ...prev, route_description: v },
                )
              }
              onChange={(lat, lng) =>
                setFormState((prev) => ({
                  ...prev,
                  latitude: lat,
                  longitude: lng,
                  route_description: "",
                }))
              }
              onBuildingSelect={(buildingId) =>
                setFormState((prev) => ({ ...prev, building_id: buildingId ?? undefined }))
              }
              {...nav}
              onChangeCrisis={() => setMidFormCrisisChange(true)}
            />
          </Suspense>
        );
      case "debris":
        return (
          <StepDebris
            value={formState.debris}
            onChange={(v) => setFormState((prev) => ({ ...prev, debris: v }))}
            {...nav}
            required={required}
          />
        );
      case "infra_type":
        return (
          <StepInfraType
            value={formState.infra_type}
            otherValue={formState.infra_type_other}
            onOtherChange={(v) => setFormState((prev) => ({ ...prev, infra_type_other: v }))}
            onChange={(v) =>
              setFormState((prev) => ({
                ...prev,
                infra_type: v,
                infra_type_other: v.includes("other") ? prev.infra_type_other : "",
              }))
            }
            {...nav}
            required={required}
          />
        );
      case "crisis_nature":
        return (
          <StepCrisisNature
            crisisNature={formState.crisis_nature}
            crisisNatureOther={formState.crisis_nature_other}
            onChangeCrisisNatureType={(natureType) =>
              setFormState((prev) => ({
                ...prev,
                crisis_nature_type: natureType,
                crisis_nature: null,
              }))
            }
            onChangeCrisisNature={(v) =>
              setFormState((prev) => ({
                ...prev,
                crisis_nature: v,
                crisis_nature_other: v === CRISIS_NATURE_OTHER ? prev.crisis_nature_other : "",
              }))
            }
            onChangeCrisisNatureOther={(v) =>
              setFormState((prev) => ({ ...prev, crisis_nature_other: v }))
            }
            {...nav}
            required={required}
            // Review CTA only on the last non-review page.
            nextLabel={
              currentStep === visiblePages.length - 1
                ? t("stepInfraDetails.reviewAndSubmit")
                : undefined
            }
          />
        );
      case "electricity":
        return (
          <StepElectricity
            value={formState.electricity}
            onChange={(v) => setFormState((prev) => ({ ...prev, electricity: v }))}
            {...nav}
          />
        );
      case "health_services":
        return (
          <StepHealthServices
            value={formState.health_services}
            onChange={(v) => setFormState((prev) => ({ ...prev, health_services: v }))}
            {...nav}
          />
        );
      case "pressing_needs":
        return (
          <StepPressingNeeds
            value={formState.pressing_needs}
            onChange={(v) => setFormState((prev) => ({ ...prev, pressing_needs: v }))}
            {...nav}
          />
        );
      case "generic":
        return (
          <GenericPage
            page={page}
            answers={formState.generic_answers}
            onChange={(questionLabel, value) =>
              setFormState((prev) => ({
                ...prev,
                generic_answers: { ...prev.generic_answers, [questionLabel]: value },
              }))
            }
            {...nav}
          />
        );
      default:
        return null;
    }
  }

  return (
    <StepScrollContext.Provider value={scrollPositions.current}>
      {renderStep()}
    </StepScrollContext.Provider>
  );
}
