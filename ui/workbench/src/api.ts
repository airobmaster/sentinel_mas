// Calls to the Sentinel API under /api (proxied to the API service). Every call carries the
// signed-in user's access token; the API decides what the user may do and see.
import type {
  CaseEvent, CaseView, Checkpoint, Me, NetworkGraph, QALabel, QueueRow,
} from "./types";

let accessToken: string | null = null;

export function setAccessToken(token: string | null) {
  accessToken = token;
}

export function authHeader(): Record<string, string> {
  return accessToken ? { Authorization: `Bearer ${accessToken}` } : {};
}

let unauthorized: (() => void) | null = null;

/** Called when the API rejects the token (expired or revoked); returns an unsubscribe function. */
export function onUnauthorized(handler: () => void): () => void {
  unauthorized = handler;
  return () => { unauthorized = null; };
}

export class ApiError extends Error {
  constructor(public status: number, message: string) {
    super(message);
  }
}

async function call<T>(method: string, path: string, body?: unknown): Promise<T> {
  const resp = await fetch(`/api${path}`, {
    method,
    headers: { ...authHeader(), ...(body ? { "Content-Type": "application/json" } : {}) },
    body: body ? JSON.stringify(body) : undefined,
  });
  if (resp.status === 401 && accessToken && !path.startsWith("/auth")) unauthorized?.();
  if (!resp.ok) {
    let detail = resp.statusText;
    try {
      const json = await resp.json();
      detail = typeof json.detail === "string" ? json.detail : JSON.stringify(json.detail);
    } catch {
      /* not JSON */
    }
    throw new ApiError(resp.status, detail);
  }
  return resp.json() as Promise<T>;
}

export const api = {
  authConfig: () => call<{ mode: "dev" | "cognito"; authority?: string; client_id?: string; domain?: string;
    demo_role_switch?: boolean; grafana_url?: string }>("GET", "/auth/config"),
  devToken: (role: string) => call<{ access_token: string }>("POST", "/auth/dev-token", { role }),
  demoSignIn: (role: string) => call<{ access_token: string }>("POST", "/auth/demo-sign-in", { role }),
  me: () => call<Me>("GET", "/me"),
  queue: (params: URLSearchParams) => call<QueueRow[]>("GET", `/cases?${params}`),
  case: (id: string) => call<CaseView>("GET", `/cases/${id}`),
  events: (id: string) => call<CaseEvent[]>("GET", `/cases/${id}/events`),
  history: (id: string) => call<{ thread_id: string; checkpoints: Checkpoint[] }>("GET", `/cases/${id}/history`),
  network: (id: string) => call<NetworkGraph>("GET", `/cases/${id}/network`),
  decide: (id: string, body: { action: string; reason_code: string; narrative_edits?: string | null }) =>
    call("POST", `/cases/${id}/decision`, body),
  approve: (id: string, body: { action: string; message?: string; questions?: string[] }) =>
    call("POST", `/cases/${id}/approval`, body),
  reply: (id: string, reply_text: string) => call("POST", `/cases/${id}/reply`, { reply_text }),
  qaSample: (n = 10) => call<QueueRow[]>("GET", `/qa/sample?n=${n}`),
  qaLabel: (id: string, label: QALabel) => call("POST", `/cases/${id}/qa-label`, label),
};
