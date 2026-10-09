import type { CaseEvent, CaseStatus } from "./types";

export const STATUS: Record<CaseStatus, { label: string; color: string }> = {
  new: { label: "New", color: "gray" },
  in_progress: { label: "In progress", color: "blue" },
  awaiting_approval: { label: "Awaiting approval", color: "orange" },
  awaiting_review: { label: "Awaiting review", color: "yellow" },
  closed: { label: "Closed", color: "green" },
  escalated: { label: "Escalated (legacy)", color: "red" },
  sar_filed: { label: "SAR filed", color: "red" },
  info_requested: { label: "Info requested", color: "grape" },
  error: { label: "Error", color: "dark" },
};

export const AGENTS: Record<string, string> = {
  triage: "Triage", kyc: "KYC", txn: "Transactions", screening: "Screening", network: "Network",
  typology: "Typology", narrative: "Narrative", qa: "QA critic", customer_request: "Customer request",
  draft_info_request: "Customer request", approve_info_request: "Approval", human_review: "Human review",
  gate: "Join", rework: "Rework",
};

export const ACTION_LABEL: Record<string, string> = {
  close: "Close", escalate: "Escalate", request_info: "Request information", file_sar: "File SAR", no_sar: "No SAR",
};

export const LEVEL_LABEL: Record<string, string> = { l1: "L1 analyst", l2: "L2 investigator", mlro: "MLRO" };

/** What "escalate" means at each level (BR-16). */
export const ESCALATE_LABEL: Record<string, string> = { l1: "Escalate to L2", l2: "Escalate to MLRO" };

export function duration(seconds: number | null | undefined): string {
  if (seconds == null || Number.isNaN(seconds)) return "–";
  const s = Math.max(0, Math.round(seconds));
  const m = Math.floor(s / 60);
  if (m >= 60) return `${Math.floor(m / 60)}h ${String(m % 60).padStart(2, "0")}m`;
  return m ? `${m}m ${String(s % 60).padStart(2, "0")}s` : `${s}s`;
}

export function secondsSince(iso: string): number {
  return (Date.now() - new Date(iso).getTime()) / 1000;
}

export function time(iso: string): string {
  return new Date(iso).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit", hour12: false });
}

const START = ["case_started", "follow_up_started"];
const PAUSE = ["awaiting_review", "awaiting_approval"];

/** Events of the latest run (or follow-up run), oldest first. */
export function currentRun(events: CaseEvent[]): CaseEvent[] {
  const sorted = [...events].sort((a, b) => a.at.localeCompare(b.at));
  const start = [...sorted].reverse().find((e) => START.includes(e.type));
  return start ? sorted.filter((e) => e.at >= start.at) : sorted;
}

/** Wall-clock time from the latest run's start to the first pause after it. */
export function investigationSeconds(events: CaseEvent[]): number | null {
  const run = currentRun(events);
  const start = run.find((e) => START.includes(e.type));
  const pause = run.find((e) => PAUSE.includes(e.type));
  return start && pause ? (new Date(pause.at).getTime() - new Date(start.at).getTime()) / 1000 : null;
}

/** SLA for cases waiting on a human: amber after 1 hour, red after 8 (demo thresholds). */
export function slaColor(status: CaseStatus, updatedAt: string): string | null {
  if (!["awaiting_review", "awaiting_approval"].includes(status)) return null;
  const hours = secondsSince(updatedAt) / 3600;
  return hours >= 8 ? "red" : hours >= 1 ? "orange" : "teal";
}
