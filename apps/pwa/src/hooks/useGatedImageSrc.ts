// Image URL that may sit behind the ngrok Basic-auth gate. Dormant while NATIVE_NGROK_BASIC_AUTH is
// empty (passthrough). When set, native fetches via the credentialed fetch and returns a blob: URL,
// because a native <img> bypasses CapacitorHttp and lib/nativeApiAuth and would be 401'd.

import { useEffect, useState } from "react";
import { NATIVE_NGROK_BASIC_AUTH } from "../config";
import { isNativePlatform } from "../platform/platformInfo";
import { useLatest } from "./useLatest";

const NEEDS_BLOB_FETCH = isNativePlatform() && Boolean(NATIVE_NGROK_BASIC_AUTH);

export interface GatedImage {
  // null: no URL, native fetch in flight, or failed.
  src: string | null;
  // Native only; on web <img onError> drives failure.
  failed: boolean;
}

// Bump `epoch` to re-fetch an unchanged URL (signed URLs can repeat). `onError` is native-only.
export function useGatedImageSrc(
  url: string | null | undefined,
  epoch = 0,
  onError?: () => void,
): GatedImage {
  const onErrorRef = useLatest(onError);

  const [state, setState] = useState<GatedImage>(
    NEEDS_BLOB_FETCH ? { src: null, failed: false } : { src: url ?? null, failed: false },
  );

  // biome-ignore lint/correctness/useExhaustiveDependencies: epoch is an explicit re-fetch trigger, not read inside the effect.
  useEffect(() => {
    if (!NEEDS_BLOB_FETCH) {
      setState({ src: url ?? null, failed: false });
      return;
    }
    if (!url) {
      setState({ src: null, failed: false });
      return;
    }

    let cancelled = false;
    let objectUrl: string | null = null;
    setState({ src: null, failed: false });

    (async () => {
      try {
        const res = await fetch(url);
        if (!res.ok) throw new Error(`image HTTP ${res.status}`);
        const blob = await res.blob();
        if (cancelled) return;
        objectUrl = URL.createObjectURL(blob);
        setState({ src: objectUrl, failed: false });
      } catch {
        if (cancelled) return;
        setState({ src: null, failed: true });
        onErrorRef.current?.();
      }
    })();

    return () => {
      cancelled = true;
      if (objectUrl) URL.revokeObjectURL(objectUrl);
    };
  }, [url, epoch]);

  return state;
}
