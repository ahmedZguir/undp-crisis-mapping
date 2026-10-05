import { useEffect, useState } from "react";
import { getEffectiveTimezone, subscribeTimezone } from "../lib/adminTimezone";

/** The effective display timezone (override if set, else the browser's);
 *  re-renders when the override changes. */
export function useAdminTimezone(): string {
  const [tz, setTz] = useState<string>(() => getEffectiveTimezone());

  useEffect(() => {
    return subscribeTimezone(() => setTz(getEffectiveTimezone()));
  }, []);

  return tz;
}
