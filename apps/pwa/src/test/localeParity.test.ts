import { describe, expect, it } from "vitest";
import ar from "../../public/locales/ar/translation.json";
import en from "../../public/locales/en/translation.json";
import fr from "../../public/locales/fr/translation.json";

const LOCALES: Record<string, Record<string, string>> = { en, ar, fr };

// CLDR plural suffixes (`_one`, `_few`, ...) differ by language, so parity is checked on base keys.
const PLURAL_SUFFIX = /_(zero|one|two|few|many|other)$/;
const baseKeys = (bundle: Record<string, string>) =>
  new Set(Object.keys(bundle).map((k) => k.replace(PLURAL_SUFFIX, "")));

describe("locale parity", () => {
  const enBase = baseKeys(en);

  it("all locales cover the same base keys as EN", () => {
    for (const [code, bundle] of Object.entries(LOCALES)) {
      expect([...baseKeys(bundle)].sort(), `locale: ${code}`).toEqual([...enBase].sort());
    }
  });
});
