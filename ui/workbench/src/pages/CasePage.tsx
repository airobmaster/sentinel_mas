// Case view (FR-101): header, summary, tabs, and the action panel for what the case is waiting for.
import { Alert, Anchor, Badge, Card, Grid, Group, Loader, Stack, Tabs, Text, Title } from "@mantine/core";
import { useQuery } from "@tanstack/react-query";
import { IconArrowLeft } from "@tabler/icons-react";
import { useEffect, useState } from "react";
import { Link, useLocation, useParams } from "react-router-dom";

import { api, ApiError } from "../api";
import { ApprovalPanel, DecidedPanel, DecisionPanel, ReplyPanel } from "../components/actions";
import { LiveProgress, useCaseEvents } from "../components/live";
import { NetworkGraph } from "../components/NetworkGraph";
import {
  EvidenceProvider, EvidenceTab, FindingsTab, ReviewTab, SecurityTab, TimelineTab, TypologyTab,
} from "../components/tabs";
import { ACTION_LABEL, duration, investigationSeconds, STATUS } from "../format";
import type { CaseView } from "../types";

const RUNNING = ["new", "in_progress"];
const PENDING_NOTE: Record<string, string> = {
  Decision: "Decision accepted by the API; waiting for the worker to apply it.",
  Approval: "Approval accepted by the API; waiting for the worker to apply it.",
  Rejection: "Rejection accepted by the API; waiting for the worker to apply it.",
  "Customer reply": "Reply accepted by the API; waiting for the worker to start the follow-up.",
};

function Stat({ label, value }: { label: string; value: React.ReactNode }) {
  return <div className="sn-stat"><div className="sn-stat-label">{label}</div><div className="sn-stat-value">{value}</div></div>;
}

export function CasePage() {
  const { caseId = "" } = useParams();
  const back = (useLocation().state as { from?: string } | null)?.from ?? "/"; // the queue with its filters
  const events = useCaseEvents(caseId);
  const [pending, setPending] = useState<{ kind: string; status: string; at: string } | null>(null);
  const { data: view, error, isLoading } = useQuery({
    queryKey: ["case", caseId],
    queryFn: () => api.case(caseId),
    refetchInterval: (q) => (pending || RUNNING.includes(q.state.data?.case.status ?? "") ? 3000 : false),
  });
  const status = view?.case.status;
  useEffect(() => {
    if (pending && status && status !== pending.status) setPending(null); // the worker moved the case on
  }, [status, pending]);

  if (isLoading) return <Loader />;
  if ((error as ApiError)?.status === 404) { // not (or no longer) with this user's level (BR-17)
    return (
      <Alert color="gray" title={`${caseId} is not in your queue`}>
        {pending ? "Your decision was applied and the case has moved on to the next level." :
          "It is with another review level, or does not exist."}{" "}
        <Anchor component={Link} to={back}>Back to the work queue</Anchor>
      </Alert>
    );
  }
  if (error || !view) return <Alert color="red">Could not load {caseId}: {(error as Error)?.message}</Alert>;

  const running = RUNNING.includes(view.case.status);
  return (
    <Stack gap="md">
      <Group justify="space-between">
        <Group gap="md">
          <Anchor component={Link} to={back} c="dimmed"><Group gap={4}><IconArrowLeft size={16} />Queue</Group></Anchor>
          <Title order={2}>{view.case.case_id}</Title>
          <Badge size="lg" color={STATUS[view.case.status].color} variant="light">{STATUS[view.case.status].label}</Badge>
          {view.blind && <Badge size="lg" color="violet" variant="outline">Blind review</Badge>}
        </Group>
        <Text c="dimmed">{view.case.alert.scenario_name} · {view.case.legal_entity} · {view.case.customer_id}</Text>
      </Group>

      {view.case.status === "new" ? (
        <Alert color="gray" title="Queued">
          The alert is waiting for a worker to start the investigation. This page updates when it starts.
        </Alert>
      ) : running ? (
        <LiveProgress events={events} waitingFor="the worker is investigating" />
      ) : (
        <EvidenceProvider evidence={view.state.evidence ?? []}>
          <Summary view={view} events={events} />
          {pending && <LiveProgress events={events} waitingFor={PENDING_NOTE[pending.kind] ?? "Waiting for the worker."} since={pending.at} />}
          <Grid gap="md" align="flex-start">
            <Grid.Col span={{ base: 12, lg: 8 }}>
              <Card withBorder radius="lg" p="md">
                <Tabs defaultValue="review" keepMounted={false} variant="pills" radius="xl" className="sn-tabs">
                  <Tabs.List mb="md">
                    <Tabs.Tab value="review">Review</Tabs.Tab>
                    <Tabs.Tab value="evidence">Evidence ({view.state.evidence?.length ?? 0})</Tabs.Tab>
                    <Tabs.Tab value="findings">Findings</Tabs.Tab>
                    <Tabs.Tab value="typology">Typology &amp; policy</Tabs.Tab>
                    <Tabs.Tab value="network">Network</Tabs.Tab>
                    <Tabs.Tab value="timeline">Audit timeline</Tabs.Tab>
                    <Tabs.Tab value="security">Security</Tabs.Tab>
                  </Tabs.List>
                  <Tabs.Panel value="review"><ReviewTab view={view} /></Tabs.Panel>
                  <Tabs.Panel value="evidence"><EvidenceTab evidence={view.state.evidence ?? []} /></Tabs.Panel>
                  <Tabs.Panel value="findings"><FindingsTab view={view} events={events} /></Tabs.Panel>
                  <Tabs.Panel value="typology"><TypologyTab view={view} /></Tabs.Panel>
                  <Tabs.Panel value="network"><NetworkGraph caseId={caseId} /></Tabs.Panel>
                  <Tabs.Panel value="timeline"><TimelineTab caseId={caseId} events={events} /></Tabs.Panel>
                  <Tabs.Panel value="security"><SecurityTab view={view} /></Tabs.Panel>
                </Tabs>
              </Card>
            </Grid.Col>
            <Grid.Col span={{ base: 12, lg: 4 }} style={{ position: "sticky", top: 76 }}>
              <ActionPanel view={view} pending={!!pending}
                           onSent={(kind) => setPending({ kind, status: view.case.status, at: new Date().toISOString() })} />
            </Grid.Col>
          </Grid>
        </EvidenceProvider>
      )}
    </Stack>
  );
}

function ActionPanel({ view, pending, onSent }: { view: CaseView; pending: boolean; onSent: (kind: string) => void }) {
  if (pending) return null;
  if (view.waiting_for?.kind === "approval") return <ApprovalPanel view={view} onSent={onSent} />;
  if (view.waiting_for?.kind === "decision") return <DecisionPanel view={view} onSent={onSent} />;
  if (view.state.decision) {
    return (
      <Stack>
        <DecidedPanel view={view} />
        {view.case.status === "info_requested" && <ReplyPanel view={view} onSent={onSent} />}
      </Stack>
    );
  }
  return <Alert color="gray">This case is not waiting for anyone.</Alert>;
}

function Summary({ view, events }: { view: CaseView; events: ReturnType<typeof useCaseEvents> }) {
  const rec = view.recommendation;
  const critic = view.state.findings?.qa;
  const usage = view.state.usage ?? {};
  const tokens = Object.values(usage).reduce((s, u) => s + (u.input_tokens ?? 0) + (u.output_tokens ?? 0), 0);
  const qa = critic ? (critic.error ? "⚠ critic unavailable" : critic.passed ? "✓ passed" : "✗ issues raised") : "code checks";
  return (
    <Stack gap="sm">
      <Grid gap="sm">
        <Grid.Col span={{ base: 12, sm: 3 }}><Stat label="Recommendation" value={view.blind ? "hidden (blind review)" : rec ? ACTION_LABEL[rec.recommendation] : "–"} /></Grid.Col>
        <Grid.Col span={{ base: 12, sm: 6 }}><Stat label="Reason code" value={view.blind ? "hidden" : rec?.reason_code ?? "–"} /></Grid.Col>
        <Grid.Col span={{ base: 12, sm: 3 }}><Stat label="QA" value={qa} /></Grid.Col>
      </Grid>
      <Grid gap="sm" columns={10}>
        <Grid.Col span={{ base: 10, sm: 2 }}><Stat label="Risk score" value={view.blind ? "hidden" : rec?.risk_score ?? "–"} /></Grid.Col>
        <Grid.Col span={{ base: 10, sm: 2 }}><Stat label="Lane" value={view.case.tier ?? view.state.tier ?? "–"} /></Grid.Col>
        <Grid.Col span={{ base: 10, sm: 2 }}><Stat label="Evidence items" value={view.state.evidence?.length ?? 0} /></Grid.Col>
        <Grid.Col span={{ base: 10, sm: 2 }}><Stat label="Investigation time" value={duration(investigationSeconds(events))} /></Grid.Col>
        <Grid.Col span={{ base: 10, sm: 2 }}><Stat label="Tokens used" value={tokens.toLocaleString()} /></Grid.Col>
      </Grid>
    </Stack>
  );
}
