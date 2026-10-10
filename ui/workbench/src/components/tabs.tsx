// Case view tabs (FR-101, FR-105): review packet with clickable evidence chips, evidence pack, findings
// per agent with time and usage, typology & policy, audit timeline, security.
import {
  Accordion, Alert, Badge, Button, Card, Code, Drawer, Group, Indicator, List, ScrollArea, SegmentedControl, SimpleGrid, Stack,
  Switch, Table, Text, TextInput, Timeline, Title,
} from "@mantine/core";
import { useQuery } from "@tanstack/react-query";
import { IconAlertTriangle, IconInfoCircle, IconShieldExclamation, IconTimeline } from "@tabler/icons-react";
import { createContext, useContext, useMemo, useState, type ReactNode } from "react";

import { api } from "../api";
import { AGENTS, currentRun, duration, investigationSeconds, time } from "../format";
import type { CaseEvent, CaseView, Evidence, QAIssue, Usage } from "../types";

// --- Evidence chips + drawer -------------------------------------------------------------------
const EvidenceContext = createContext<{ byId: Record<string, Evidence>; open: (id: string) => void }>({
  byId: {}, open: () => undefined,
});

export function EvidenceProvider({ evidence, children }: { evidence: Evidence[]; children: ReactNode }) {
  const byId = useMemo(() => Object.fromEntries(evidence.map((e) => [e.id, e])), [evidence]);
  const [openId, setOpenId] = useState<string | null>(null);
  const item = openId ? byId[openId] : undefined;
  return (
    <EvidenceContext.Provider value={{ byId, open: setOpenId }}>
      {children}
      <Drawer opened={!!openId} onClose={() => setOpenId(null)} position="right" size="md" title={<Code>{openId}</Code>}>
        {item ? (
          <Stack>
            <Group><Badge variant="light">{AGENTS[item.agent] ?? item.agent}</Badge><Badge variant="outline">{item.source}</Badge></Group>
            <Text>{item.summary}</Text>
          </Stack>
        ) : <Alert color="red" icon={<IconAlertTriangle />}>This ID is not in the case evidence.</Alert>}
      </Drawer>
    </EvidenceContext.Provider>
  );
}

export function EvidenceChip({ id }: { id: string }) {
  const { byId, open } = useContext(EvidenceContext);
  return (
    <Badge className="sn-chip" variant={byId[id] ? "light" : "filled"} color={byId[id] ? "brand" : "red"} size="sm"
           tt="none" onClick={() => open(id)} data-testid="evidence-chip">
      {id}
    </Badge>
  );
}

// --- Review --------------------------------------------------------------------------------------
const SEVERITY: Record<string, string> = { blocker: "red", major: "orange", minor: "gray" };

function IssuesTable({ issues }: { issues: QAIssue[] }) {
  return (
    <Table verticalSpacing="xs" fz="sm" withTableBorder>
      <Table.Thead><Table.Tr><Table.Th w={90}>Severity</Table.Th><Table.Th w={110}>Found by</Table.Th>
        <Table.Th w={120}>Sent to</Table.Th><Table.Th>Issue</Table.Th></Table.Tr></Table.Thead>
      <Table.Tbody>
        {issues.map((i, n) => (
          <Table.Tr key={n}>
            <Table.Td><Badge color={SEVERITY[i.severity]} variant="light">{i.severity}</Badge></Table.Td>
            <Table.Td>{i.description.startsWith("[critic") ? "QA critic" : "Code check"}</Table.Td>
            <Table.Td>{AGENTS[i.target_agent] ?? i.target_agent}</Table.Td>
            <Table.Td>{i.description.replace(/^\[critic\]\s*/, "")}</Table.Td>
          </Table.Tr>
        ))}
      </Table.Tbody>
    </Table>
  );
}

export function ReviewTab({ view }: { view: CaseView }) {
  const s = view.state;
  const issues = s.qa_issues ?? [];
  const serious = issues.filter((i) => i.severity !== "minor");
  const notes = issues.filter((i) => i.severity === "minor");
  const injected = (s.security_events ?? []).some((e) => e.kind === "injection_detected");
  return (
    <Stack gap="md">
      {s.follow_up && (
        <Alert color="blue" icon={<IconInfoCircle />} title={`Follow-up round ${s.follow_up.round}`}>
          Customer reply: “{s.follow_up.reply_text}”
        </Alert>
      )}
      {injected && (
        <Alert color="orange" icon={<IconShieldExclamation />}>
          A record in this case contained instruction-like text. It was fenced off as data and did not change what
          the agents could do (see Security).
        </Alert>
      )}
      {view.blind && (
        <Alert color="violet" title="Blind review">The narrative and recommendation are hidden until you decide.</Alert>
      )}
      {serious.length > 0 && (
        <div>
          <Text fw={600} mb={6}>⚠️ Unresolved QA issues ({serious.length})</Text>
          <IssuesTable issues={serious} />
        </div>
      )}
      {s.narrative && (
        <Card withBorder radius="md">
          <Title order={5} mb="xs">Summary</Title>
          <Text mb="md">{s.narrative.summary}</Text>
          <Title order={5} mb="xs">Claims</Title>
          <List type="ordered" spacing="sm">
            {s.narrative.claims.map((c, i) => (
              <List.Item key={i}>
                <Text span>{c.text} </Text>
                <Group gap={4} mt={4}>{c.evidence_ids.map((id) => <EvidenceChip key={id} id={id} />)}</Group>
              </List.Item>
            ))}
          </List>
          {s.narrative.open_questions?.length > 0 && (
            <>
              <Title order={5} mt="md" mb="xs">Open questions</Title>
              <List>{s.narrative.open_questions.map((q, i) => <List.Item key={i}>{q}</List.Item>)}</List>
            </>
          )}
        </Card>
      )}
      {notes.length > 0 && (
        <Accordion variant="contained">
          <Accordion.Item value="notes">
            <Accordion.Control>QA notes ({notes.length} minor)</Accordion.Control>
            <Accordion.Panel><IssuesTable issues={notes} /></Accordion.Panel>
          </Accordion.Item>
        </Accordion>
      )}
      {s.info_request && view.waiting_for?.kind !== "approval" && (
        <Card withBorder radius="md">
          <Group justify="space-between"><Title order={5}>Customer information request</Title>
            <Badge variant="light">{s.info_request.status.replace(/_/g, " ")}</Badge></Group>
          <Text mt="xs">{s.info_request.message}</Text>
          <List mt="xs">{s.info_request.questions.map((q, i) => <List.Item key={i}>{q}</List.Item>)}</List>
          {s.info_request.approver_id && <Text size="xs" c="dimmed" mt="xs">Decided by {s.info_request.approver_id}</Text>}
        </Card>
      )}
    </Stack>
  );
}

// --- Evidence pack -------------------------------------------------------------------------------
export function EvidenceTab({ evidence }: { evidence: Evidence[] }) {
  const [agent, setAgent] = useState("all");
  const [q, setQ] = useState("");
  const agents = ["all", ...Array.from(new Set(evidence.map((e) => e.agent)))];
  const rows = evidence.filter((e) => (agent === "all" || e.agent === agent)
    && (!q || `${e.id} ${e.summary}`.toLowerCase().includes(q.toLowerCase())));
  return (
    <Stack>
      <Group>
        <SegmentedControl value={agent} onChange={setAgent} data={agents.map((a) => ({ value: a, label: a === "all" ? "All" : AGENTS[a] ?? a }))} />
        <TextInput placeholder="Search evidence" value={q} onChange={(e) => setQ(e.currentTarget.value)} w={260} />
        <Text size="sm" c="dimmed">{rows.length} of {evidence.length}</Text>
      </Group>
      <Table verticalSpacing="xs" fz="sm" highlightOnHover>
        <Table.Thead><Table.Tr><Table.Th>ID</Table.Th><Table.Th>Agent</Table.Th><Table.Th>Source</Table.Th><Table.Th>Summary</Table.Th></Table.Tr></Table.Thead>
        <Table.Tbody>
          {rows.map((e) => (
            <Table.Tr key={e.id}>
              <Table.Td><EvidenceChip id={e.id} /></Table.Td>
              <Table.Td>{AGENTS[e.agent] ?? e.agent}</Table.Td>
              <Table.Td c="dimmed">{e.source}</Table.Td>
              <Table.Td>{e.summary}</Table.Td>
            </Table.Tr>
          ))}
        </Table.Tbody>
      </Table>
    </Stack>
  );
}

// --- Findings with time and usage ---------------------------------------------------------------
const USAGE_COLUMNS: [keyof Usage, string][] = [["seconds", "Seconds"], ["model_calls", "Model calls"],
  ["tool_calls", "Tool calls"], ["input_tokens", "Input tokens"], ["output_tokens", "Output tokens"]];

export function FindingsTab({ view, events }: { view: CaseView; events: CaseEvent[] }) {
  const usage = view.state.usage ?? {};
  const timed = Object.keys(AGENTS).filter((a) => usage[a]?.seconds);
  const total = Object.fromEntries(USAGE_COLUMNS.map(([k]) => [k, timed.reduce((s, a) => s + (usage[a][k] ?? 0), 0)]));
  const fmt = (k: keyof Usage, v: number) => (k === "seconds" ? v.toFixed(1) : Math.round(v).toLocaleString());
  const findings = view.state.findings ?? {};
  return (
    <Stack>
      <Text fw={600}>Time and usage by agent · investigation {duration(investigationSeconds(events))} wall clock
        <Text span c="dimmed" fw={400}> (KYC, Transactions and Screening run in parallel)</Text></Text>
      <Table withTableBorder fz="sm" data-testid="usage-table">
        <Table.Thead><Table.Tr><Table.Th>Agent</Table.Th>{USAGE_COLUMNS.map(([, l]) => <Table.Th key={l} ta="right">{l}</Table.Th>)}</Table.Tr></Table.Thead>
        <Table.Tbody>
          {timed.map((a) => (
            <Table.Tr key={a}><Table.Td>{AGENTS[a]}</Table.Td>
              {USAGE_COLUMNS.map(([k]) => <Table.Td key={k} ta="right">{fmt(k, usage[a][k] ?? 0)}</Table.Td>)}</Table.Tr>
          ))}
          <Table.Tr className="sn-total"><Table.Td>Total</Table.Td>
            {USAGE_COLUMNS.map(([k]) => <Table.Td key={k} ta="right">{fmt(k, total[k])}</Table.Td>)}</Table.Tr>
        </Table.Tbody>
      </Table>
      <Accordion variant="separated" multiple>
        {["triage", "kyc", "txn", "screening", "network", "typology", "qa"].filter((a) => findings[a]).map((a) => (
          <Accordion.Item key={a} value={a}>
            <Accordion.Control>
              {AGENTS[a]}{usage[a]?.seconds ? <Text span c="dimmed" size="sm"> · ⏱ {usage[a].seconds.toFixed(0)}s</Text> : null}
            </Accordion.Control>
            <Accordion.Panel>
              <ScrollArea.Autosize mah={420}><Code block>{JSON.stringify(findings[a], null, 2)}</Code></ScrollArea.Autosize>
            </Accordion.Panel>
          </Accordion.Item>
        ))}
      </Accordion>
    </Stack>
  );
}

// --- Typology & policy ---------------------------------------------------------------------------
export function TypologyTab({ view }: { view: CaseView }) {
  const t = view.state.findings?.typology;
  if (!t) return <Text c="dimmed">No typology assessment yet.</Text>;
  const refs: string[] = Array.from(new Set([...(t.policy_refs ?? []), ...(t.typologies ?? []).flatMap((m: any) => m.policy_refs)]));
  return (
    <Stack>
      {t.recommendation && <Text>Recommendation <b>{t.recommendation}</b> ({t.reason_code}) · risk score <b>{t.risk_score}</b>/100</Text>}
      {t.rationale && <Text c="dimmed">{t.rationale}</Text>}
      <Table withTableBorder fz="sm">
        <Table.Thead><Table.Tr><Table.Th>Typology</Table.Th><Table.Th>Confidence</Table.Th><Table.Th>Rationale</Table.Th><Table.Th>Evidence</Table.Th></Table.Tr></Table.Thead>
        <Table.Tbody>
          {(t.typologies ?? []).map((m: any) => (
            <Table.Tr key={m.code}>
              <Table.Td fw={600}>{m.code}</Table.Td>
              <Table.Td>{Math.round(m.confidence * 100)}%</Table.Td>
              <Table.Td>{m.rationale}</Table.Td>
              <Table.Td><Group gap={4}>{m.evidence_ids.map((id: string) => <EvidenceChip key={id} id={id} />)}</Group></Table.Td>
            </Table.Tr>
          ))}
        </Table.Tbody>
      </Table>
      {refs.length > 0 && <><Text fw={600}>Policy sections relied on</Text><Group gap={6}>{refs.map((r) => <EvidenceChip key={r} id={r} />)}</Group></>}
    </Stack>
  );
}

// --- Audit timeline (UC-07) ----------------------------------------------------------------------
/** Grafana Explore with Tempo, filtered to this case's traces (every run, step, model and tool call). */
function traceLink(grafana: string, caseId: string): string {
  const pane = { datasource: "tempo", queries: [{ refId: "A", datasource: { type: "tempo", uid: "tempo" },
    queryType: "traceql", query: `{ span.sentinel.case_id = "${caseId}" }` }], range: { from: "now-7d", to: "now" } };
  return `${grafana}/explore?schemaVersion=1&orgId=1&panes=${encodeURIComponent(JSON.stringify({ a: pane }))}`;
}

export function TimelineTab({ caseId, events }: { caseId: string; events: CaseEvent[] }) {
  const { data } = useQuery({ queryKey: ["history", caseId], queryFn: () => api.history(caseId) });
  const { data: config } = useQuery({ queryKey: ["auth-config"], queryFn: api.authConfig, staleTime: Infinity });
  const grafana = config?.grafana_url;
  const [all, setAll] = useState(false);
  const run = currentRun(events);
  const shown = (all ? events : run).slice().sort((a, b) => b.at.localeCompare(a.at));
  return (
    <SimpleGrid cols={{ base: 1, lg: 2 }} spacing="lg">
      <div>
        <Title order={5} mb="xs">Checkpoints · thread {data?.thread_id}</Title>
        <Group justify="space-between" mb="sm">
          <Text size="xs" c="dimmed">Every step is saved; any point can be replayed for audit.</Text>
          {grafana && <Button component="a" href={traceLink(grafana, caseId)} target="_blank" size="xs" variant="light"
                              leftSection={<IconTimeline size={14} />}>Traces in Grafana</Button>}
        </Group>
        <Timeline active={(data?.checkpoints.length ?? 1) - 1} bulletSize={18} lineWidth={2}>
          {(data?.checkpoints ?? []).map((c) => (
            <Timeline.Item key={c.checkpoint_id}
                           title={c.completed.filter((n) => n !== "__start__").length
                             ? c.completed.map((n) => AGENTS[n] ?? n).join(", ") : c.step < 0 ? "Case received" : "Run started"}>
              <Text size="xs" c="dimmed">{time(c.created_at)} · step {c.step}{c.next.length ? ` · next: ${c.next.map((n) => AGENTS[n] ?? n).join(", ")}` : " · finished"}</Text>
            </Timeline.Item>
          ))}
        </Timeline>
      </div>
      <div>
        <Group justify="space-between" mb="xs">
          <Title order={5}>Case events (newest first)</Title>
          {events.length > run.length && <Switch label={`Include earlier runs (${events.length - run.length})`} checked={all}
                                                 onChange={(e) => setAll(e.currentTarget.checked)} />}
        </Group>
        <Table fz="xs" verticalSpacing={4} layout="fixed">
          <Table.Tbody>
            {shown.map((e, i) => (
              <Table.Tr key={`${e.at}-${i}`}>
                <Table.Td w={70} c="dimmed">{time(e.at)}</Table.Td>
                <Table.Td w={150}><Badge size="sm" variant="light" tt="none">{e.type}</Badge></Table.Td>
                <Table.Td style={{ wordBreak: "break-word" }}>{e.node ? `${AGENTS[e.node] ?? e.node}: ` : ""}{e.detail ?? (e.data ? JSON.stringify(e.data) : "")}</Table.Td>
              </Table.Tr>
            ))}
          </Table.Tbody>
        </Table>
      </div>
    </SimpleGrid>
  );
}

// --- Security ------------------------------------------------------------------------------------
export function SecurityTab({ view }: { view: CaseView }) {
  const events = view.state.security_events ?? [];
  const count = (k: string) => events.filter((e) => e.kind === k).length;
  const stat = (label: string, value: ReactNode, color?: string) => (
    <div className="sn-stat"><div className="sn-stat-label">{label}</div>
      <Indicator disabled={!color} color={color} position="middle-end" offset={-12}><div className="sn-stat-value">{value}</div></Indicator></div>
  );
  return (
    <Stack>
      <SimpleGrid cols={4}>
        {stat("Injections caught", count("injection_detected"), count("injection_detected") ? "orange" : undefined)}
        {stat("Tool calls denied", count("tool_denied"), count("tool_denied") ? "red" : undefined)}
        {stat("PII values redacted", view.state.pii_values_redacted ?? 0)}
        {stat("Budget", view.state.budget_exceeded ? "exceeded" : "within limits")}
      </SimpleGrid>
      {events.length ? (
        <Table withTableBorder fz="sm">
          <Table.Thead><Table.Tr><Table.Th>Time</Table.Th><Table.Th>Event</Table.Th><Table.Th>Agent</Table.Th><Table.Th>Detail</Table.Th></Table.Tr></Table.Thead>
          <Table.Tbody>
            {events.map((e, i) => (
              <Table.Tr key={i}><Table.Td>{time(e.at)}</Table.Td><Table.Td><Badge variant="light">{e.kind}</Badge></Table.Td>
                <Table.Td>{AGENTS[e.agent] ?? e.agent}</Table.Td><Table.Td>{e.detail}</Table.Td></Table.Tr>
            ))}
          </Table.Tbody>
        </Table>
      ) : <Text c="dimmed">No security events for this case.</Text>}
    </Stack>
  );
}
