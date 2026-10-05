// Human-readable message for any thrown value.
export function errorMessage(err: unknown): string {
  return err instanceof Error ? err.message : String(err);
}
