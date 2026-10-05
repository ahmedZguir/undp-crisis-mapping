import "@testing-library/jest-dom";
import "fake-indexeddb/auto";

import i18n from "i18next";
import { initReactI18next } from "react-i18next";
import en from "../../public/locales/en/translation.json";

// Real EN copy so assertions like getByRole({ name: "Next" }) match instead of raw keys.
if (!i18n.isInitialized) {
  void i18n.use(initReactI18next).init({
    lng: "en",
    fallbackLng: "en",
    resources: {
      en: { translation: en },
    },
    interpolation: { escapeValue: false },
  });
}
