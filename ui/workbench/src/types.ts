// Shapes returned by the Sentinel API (see /api/docs). Only the fields the workbench uses.

export type Role = "l1" | "l2" | "mlro" | "qa" | "sme" | "admin";
export type Level = "l1" | "l2" | "mlro";
export type CaseStatus =
  | "new" | "in_progress" | "awaiting_approval" | "awaiting_review" | "closed" | "escalated" | "sar_filed"
  | "info_requested" | "error";
export type Action = "close" | "escalate" | "request_info" | "file_sar" | "no_sar";

export interface Me {
  username: string;
  roles: Role[];
  sees_pii: boolean;
}

export interface QueueRow {
  case_id: string;
  status: CaseStatus;
  tier: "fast" | "full" | null;
  assigned_role: Level | null;
  legal_entity: string;
  customer_id: string;
  scenario: string;
  updated_at: string;
}

export interface Evidence {
  id: string;
  agent: string;
  source: string;
  summary: string;
}

export interface Claim {
  text: string;
  evidence_ids: string[];
}

export interface Narrative {
  summary: string;
  claims: Claim[];
  open_questions: string[];
  recommendation: Action;
  reason_code: string;
}

export interface QAIssue {
  target_agent: string;
  severity: "blocker" | "major" | "minor";
  description: string;
}

export interface InfoRequest {
  questions: string[];
  message: string;
  status: string;
  rail: { passed: boolean; violations: string[]; attempts: number };
  approver_id?: string;
}

export interface Usage {
  input_tokens: number;
  output_tokens: number;
  model_calls: number;
  tool_calls: number;
  seconds: number;
}

export interface SecurityEvent {
  kind: string;
  agent: string;
  detail: string;
  at: string;
}

export interface Decision {
  level?: Level;
  action: Action;
  reason_code: string;
  investigator_id: string;
  agree_with_recommendation?: boolean;
  narrative_edits?: string | null;
  decided_at?: string;
}

export interface CaseState {
  tier?: "fast" | "full";
  narrative?: Narrative;
  findings?: Record<string, any>;
  evidence?: Evidence[];
  qa_issues?: QAIssue[];
  qa_rounds?: number;
  info_request?: InfoRequest | null;
  follow_up?: { round: number; reply_text: string; reply_id: string } | null;
  decision?: Decision;
  decisions?: Decision[];
  security_events?: SecurityEvent[];
  usage?: Record<string, Usage>;
  versions?: Record<string, unknown>;
  budget_exceeded?: boolean;
  pii_values_redacted?: number;
}

export interface WaitingFor {
  kind: "decision" | "approval";
  level?: Level;
  allowed_actions: string[];
  draft?: InfoRequest;
  reason_codes?: Partial<Record<Action, string[]>>;
}

export interface CaseView {
  case: {
    case_id: string;
    status: CaseStatus;
    tier: "fast" | "full" | null;
    assigned_role: Level | null;
    legal_entity: string;
    customer_id: string;
    thread_id: string | null;
    updated_at: string;
    alert: Record<string, any>;
  };
  state: CaseState;
  recommendation: { recommendation: Action; reason_code: string; risk_score: number } | null;
  waiting_for: WaitingFor | null;
  blind?: boolean;
}

export interface CaseEvent {
  case_id: string;
  type: string;
  node?: string | null;
  detail?: string | null;
  data?: Record<string, any> | null;
  at: string;
}

export interface Checkpoint {
  checkpoint_id: string;
  created_at: string;
  step: number;
  source: string;
  next: string[];
  completed: string[];
}

export interface NetworkGraph {
  customer_id: string;
  nodes: { id: string; kind: "customer" | "device"; mule_score?: number; case_customer?: boolean; device_type?: string }[];
  edges: { source: string; target: string; kind: "uses_device" | "transfer" }[];
}

export interface QALabel {
  evidence_complete: number;
  citations_accurate: number;
  recommendation_sound: number;
  narrative_clear: number;
  decision_correct: boolean;
  comment?: string | null;
}
