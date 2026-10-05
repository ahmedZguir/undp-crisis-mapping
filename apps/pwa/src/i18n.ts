import i18n from "i18next";
import LanguageDetector from "i18next-browser-languagedetector";
import { initReactI18next } from "react-i18next";

// Bundled, not fetched: on native, CapacitorHttp stalled the locale fetch ~10s behind Suspense
// (blank screen). Bundling also makes translation work offline.
import ar from "../public/locales/ar/translation.json";
import en from "../public/locales/en/translation.json";
import es from "../public/locales/es/translation.json";
import fr from "../public/locales/fr/translation.json";
import ru from "../public/locales/ru/translation.json";
import zh from "../public/locales/zh/translation.json";
import { LANGUAGES } from "./lib/langGate";

const RTL_LANGS = new Set(["ar", "he", "fa", "ur"]);

function applyDocumentLanguage(lng: string): void {
  if (typeof document === "undefined") return;
  const base = lng.split("-")[0];
  document.documentElement.lang = base;
  document.documentElement.dir = RTL_LANGS.has(base) ? "rtl" : "ltr";
}

i18n
  .use(LanguageDetector)
  .use(initReactI18next)
  .init({
    fallbackLng: "en",
    supportedLngs: LANGUAGES.map((l) => l.code),
    nonExplicitSupportedLngs: true,
    defaultNS: "translation",
    resources: {
      en: { translation: en },
      ar: { translation: ar },
      fr: { translation: fr },
      es: { translation: es },
      zh: { translation: zh },
      ru: { translation: ru },
    },
    interpolation: {
      escapeValue: false,
    },
  });

// Including initial detection, so an Arabic session is RTL after a reload too.
i18n.on("initialized", () => applyDocumentLanguage(i18n.resolvedLanguage ?? i18n.language));
i18n.on("languageChanged", (lng) => applyDocumentLanguage(lng));

export default i18n;
