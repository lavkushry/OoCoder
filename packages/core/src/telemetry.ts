import { loadPolicy } from "./config.js";
import { oocoderHome } from "./paths.js";
import { recordObservation, recordUsage } from "./state.js";

export function noteObservation(
  repo: string,
  kind: string,
  candidates: number,
  home = oocoderHome(),
): void {
  if (!loadPolicy(home).telemetry.enabled) return;
  recordObservation(repo, kind, candidates, home);
}

export function noteSkip(repo: string, reason: string, home = oocoderHome()): void {
  noteObservation(repo, `skip:${reason}`, 1, home);
}

export function noteUsage(
  promptTokens: number,
  completionTokens: number,
  home = oocoderHome(),
): void {
  if (!loadPolicy(home).telemetry.enabled) return;
  recordUsage(promptTokens, completionTokens, home);
}
