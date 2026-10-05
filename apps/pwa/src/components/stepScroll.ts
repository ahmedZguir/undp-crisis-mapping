import { createContext } from "react";

// Per-step scroll memory for the report flow: each step mounts its own StepShell, so
// position and the "seen all" latch would reset. Null outside the report flow.
export interface StepScrollEntry {
  top: number;
  seenBottom: boolean;
}

export const StepScrollContext = createContext<Map<number, StepScrollEntry> | null>(null);
