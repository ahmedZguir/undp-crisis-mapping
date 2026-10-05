import { type RefObject, useLayoutEffect, useRef } from "react";

/**
 * Ref to the latest committed `value`. Updated in a layout effect so an abandoned concurrent render
 * can't leave it ahead; don't read `.current` during render. Marked stable in biome.json.
 */
export function useLatest<T>(value: T): RefObject<T> {
  const ref = useRef(value);
  useLayoutEffect(() => {
    ref.current = value;
  });
  return ref;
}
