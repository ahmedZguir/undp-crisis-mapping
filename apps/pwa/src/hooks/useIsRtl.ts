import { useTranslation } from "react-i18next";

// Falls back to `documentElement.dir` because `i18n.dir` is missing in some test mocks.
export function useIsRtl(): boolean {
  const { i18n } = useTranslation();
  return (
    (typeof i18n.dir === "function" ? i18n.dir() : null) === "rtl" ||
    (typeof document !== "undefined" && document.documentElement.dir === "rtl")
  );
}
