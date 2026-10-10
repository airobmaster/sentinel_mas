// What the user can do next: decide (UC-02), approve the customer request (UC-03), attach the
// customer's reply (UC-04). The API enforces the rules; the panels explain them up front.
import {
  Alert, Badge, Button, Card, Group, SegmentedControl, Select, Stack, Text, Textarea, Title,
} from "@mantine/core";
import { notifications } from "@mantine/notifications";
import { IconAlertTriangle, IconCheck, IconEyeOff, IconLock } from "@tabler/icons-react";
import { useState } from "react";

import { api, ApiError } from "../api";
import { hasRole, useAuth } from "../auth";
import { ACTION_LABEL, ESCALATE_LABEL, LEVEL_LABEL } from "../format";
import type { Action, CaseView, InfoRequest } from "../types";

function useSubmit(onSent: (kind: string) => void) {
  const [busy, setBusy] = useState(false);
  return {
    busy,
    async run(kind: string, request: () => Promise<unknown>) {
      setBusy(true);
      try {
        await request();
        notifications.show({ color: "teal", icon: <IconCheck size={16} />, title: "Accepted",
          message: `${kind} sent; the worker applies it in a moment.` });
        onSent(kind);
      } catch (e) {
        const err = e as ApiError;
        notifications.show({ color: "red", title: `Refused (${err.status ?? "error"})`, message: err.message,
          autoClose: 8000 });
      } finally {
        setBusy(false);
      }
    },
  };
}

export function DecisionPanel({ view, onSent }: { view: CaseView; onSent: (kind: string) => void }) {
  const { me } = useAuth();
  const level = view.waiting_for?.level ?? "l2";
  const codes = view.waiting_for?.reason_codes ?? {};
  const actions = (view.waiting_for?.allowed_actions ?? Object.keys(codes)) as Action[];
  const rec = view.recommendation;
  const start = rec && actions.includes(rec.recommendation) ? rec.recommendation : actions[0];
  const [action, setAction] = useState<Action>(start);
  const [reason, setReason] = useState<string | null>(
    rec && start === rec.recommendation ? rec.reason_code : (codes[start] ?? [])[0] ?? null);
  const [notes, setNotes] = useState("");
  const submit = useSubmit(onSent);
  const canDecide = hasRole(me, level);
  const options = codes[action] ?? [];
  const label = (a: Action) => (a === "escalate" ? ESCALATE_LABEL[level] ?? ACTION_LABEL[a] : ACTION_LABEL[a]);

  return (
    <Card withBorder radius="lg" p="lg" data-testid="decision-panel">
      <Group justify="space-between" mb="sm">
        <Title order={4}>Your decision</Title>
        <Badge variant="light" size="lg">{LEVEL_LABEL[level]} review</Badge>
      </Group>
      <DecisionTrail view={view} />
      {view.blind && (
        <Alert color="violet" icon={<IconEyeOff size={18} />} mb="sm" title="Blind review">
          The agents' draft and recommendation are hidden for this case until you decide (FR-107).
        </Alert>
      )}
      {!canDecide && (
        <Alert color="gray" icon={<IconLock size={18} />} mb="sm">This case is with the {LEVEL_LABEL[level]}.</Alert>
      )}
      {level === "mlro" && (
        <Text size="sm" c="dimmed" mb="sm">
          Escalated by L2. Decide whether to report the activity to the authorities in a suspicious activity report.
        </Text>
      )}
      <Stack gap="sm">
        <SegmentedControl fullWidth value={action} disabled={!canDecide}
                          onChange={(v) => { setAction(v as Action); setReason((codes[v as Action] ?? [])[0] ?? null); }}
                          data={actions.map((a) => ({ value: a, label: label(a) }))} />
        <Select label="Reason code" data={options} value={options.includes(reason ?? "") ? reason : null}
                onChange={setReason} disabled={!canDecide} allowDeselect={false} />
        <Textarea label="Notes / narrative edits (optional)" minRows={2} autosize value={notes} disabled={!canDecide}
                  onChange={(e) => setNotes(e.currentTarget.value)} />
        {rec && !view.blind && level !== "mlro" && (
          <Text size="sm" c="dimmed">
            Recommendation: <b>{ACTION_LABEL[rec.recommendation]}</b> ({rec.reason_code})
            {action !== rec.recommendation && <Badge ml="xs" color="orange" variant="light">overrides it</Badge>}
          </Text>
        )}
        <Button loading={submit.busy} disabled={!canDecide || !reason}
                onClick={() => submit.run("Decision", () => api.decide(view.case.case_id,
                  { action, reason_code: reason!, narrative_edits: notes || null }))}>
          Submit decision
        </Button>
      </Stack>
    </Card>
  );
}

/** Decisions already taken on the case, level by level (L1 -> L2 -> MLRO). */
export function DecisionTrail({ view }: { view: CaseView }) {
  const decisions = view.state.decisions ?? [];
  if (!decisions.length) return null;
  return (
    <Stack gap={4} mb="sm" data-testid="decision-trail">
      {decisions.map((d, i) => (
        <Text key={i} size="sm">
          <Badge size="sm" variant="outline" mr={6}>{LEVEL_LABEL[d.level ?? "l2"]}</Badge>
          {d.action === "escalate" ? ESCALATE_LABEL[d.level ?? "l2"] ?? "Escalated" : ACTION_LABEL[d.action]}
          {" "}({d.reason_code}) · {d.investigator_id}
        </Text>
      ))}
    </Stack>
  );
}
export function ApprovalPanel({ view, onSent }: { view: CaseView; onSent: (kind: string) => void }) {
  const { me } = useAuth();
  const draft = view.waiting_for!.draft as InfoRequest;
  const [message, setMessage] = useState(draft.message);
  const [questions, setQuestions] = useState(draft.questions.join("\n"));
  const submit = useSubmit(onSent);
  const isL2 = hasRole(me, "l2");
  const edited = message !== draft.message || questions !== draft.questions.join("\n");
  const send = (action: "approve" | "edit" | "reject") => submit.run(
    action === "reject" ? "Rejection" : "Approval",
    () => api.approve(view.case.case_id, action === "edit"
      ? { action, message, questions: questions.split("\n").map((q) => q.trim()).filter(Boolean) }
      : { action }));

  return (
    <Card withBorder radius="lg" p="lg" data-testid="approval-panel">
      <Group justify="space-between" mb="sm">
        <Title order={4}>Customer information request</Title>
        <Badge color={draft.rail.passed ? "teal" : "red"} variant="light">
          {draft.rail.passed ? "Tipping-off check passed" : `Tipping-off check: ${draft.rail.violations.join(", ")}`}
        </Badge>
      </Group>
      <Text size="sm" c="dimmed" mb="sm">
        Drafted by the agents. Nothing is sent to the customer until an L2 investigator approves it (UC-03).
      </Text>
      {!isL2 && <Alert color="gray" icon={<IconLock size={18} />} mb="sm">Only an L2 investigator can approve.</Alert>}
      <Stack gap="sm">
        <Textarea label="Message" autosize minRows={3} value={message} disabled={!isL2}
                  onChange={(e) => setMessage(e.currentTarget.value)} />
        <Textarea label="Questions (one per line)" autosize minRows={3} value={questions} disabled={!isL2}
                  onChange={(e) => setQuestions(e.currentTarget.value)} />
        <Group grow>
          <Button color="teal" loading={submit.busy} disabled={!isL2} onClick={() => send(edited ? "edit" : "approve")}>
            {edited ? "Approve with edits" : "Approve"}
          </Button>
          <Button color="red" variant="light" loading={submit.busy} disabled={!isL2} onClick={() => send("reject")}>
            Reject
          </Button>
        </Group>
        {edited && <Text size="xs" c="dimmed">Edited text is checked for tipping-off again before it is accepted.</Text>}
      </Stack>
    </Card>
  );
}

export function ReplyPanel({ view, onSent }: { view: CaseView; onSent: (kind: string) => void }) {
  const { me } = useAuth();
  const [text, setText] = useState("");
  const submit = useSubmit(onSent);
  const allowed = hasRole(me, view.case.assigned_role ?? "l2", "admin"); // the level that asked the customer
  return (
    <Card withBorder radius="lg" p="lg" data-testid="reply-panel">
      <Title order={4} mb={4}>Customer reply</Title>
      <Text size="sm" c="dimmed" mb="sm">
        The case is waiting for the customer. Attach their reply to start a follow-up investigation with it as
        evidence (UC-04).
      </Text>
      <Stack gap="sm">
        <Textarea placeholder="e.g. The payment was the proceeds of selling my car; the invoice is attached."
                  autosize minRows={3} value={text} disabled={!allowed} onChange={(e) => setText(e.currentTarget.value)} />
        <Button loading={submit.busy} disabled={!allowed || !text.trim()}
                onClick={() => submit.run("Customer reply", () => api.reply(view.case.case_id, text.trim()))}>
          Attach reply and start follow-up
        </Button>
      </Stack>
    </Card>
  );
}

export function DecidedPanel({ view }: { view: CaseView }) {
  const d = view.state.decision!;
  return (
    <Card withBorder radius="lg" p="lg" data-testid="decided-panel">
      <Title order={4} mb="xs">Decision recorded</Title>
      <DecisionTrail view={view} />
      {!view.state.decisions?.length && <Text><b>{ACTION_LABEL[d.action]}</b> ({d.reason_code}) by {d.investigator_id}</Text>}
      {d.agree_with_recommendation === false && (
        <Alert mt="sm" color="orange" icon={<IconAlertTriangle size={18} />}>Overrides the agents' recommendation.</Alert>
      )}
      {d.narrative_edits && <Text size="sm" mt="sm" c="dimmed">Notes: {d.narrative_edits}</Text>}
    </Card>
  );
}
