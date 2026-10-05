import { STORAGE_KEYS } from "./storageKeys";
// Language list and first-run language-gate storage.

/** The six UN languages the citizen app ships translations for. */
export const LANGUAGES = [
  { code: "en", native: "English", english: "English" },
  { code: "fr", native: "Français", english: "French" },
  { code: "es", native: "Español", english: "Spanish" },
  { code: "ar", native: "العربية", english: "Arabic" },
  { code: "zh", native: "中文", english: "Chinese" },
  { code: "ru", native: "Русский", english: "Russian" },
] as const;

export type LangCode = (typeof LANGUAGES)[number]["code"];

/** Set once the citizen has picked a language; absent → show the first-run picker. */
export const LANG_PICKED_KEY = STORAGE_KEYS.langPicked.key;

/** Synchronous store for the language / consent gate flags. */
export function langStore(): Storage {
  return localStorage;
}
