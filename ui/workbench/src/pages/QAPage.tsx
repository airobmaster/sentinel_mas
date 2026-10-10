// QA labelling (FR-106, UC-05): sampled decided cases scored against the rubric.
import {
  Alert, Badge, Button, Card, Grid, Group, NavLink, Rating, Stack, Switch, Text, Textarea, Title,
} from "@mantine/core";
import { notifications } from "@mantine/notifications";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";

import { api, ApiError } from "../api";
import { hasRole, useAuth } from "../auth";
import { ACTION_LABEL, STATUS } from "../format";

const RUBRIC: [string, string, string][] = [
  ["evidence_complete", "Evidence complete", "All relevant evidence gathered"],
  ["citations_accurate", "Citations accurate", "Claims match the cited evidence"],
  ["recommendation_sound", "Recommendation sound", "Follows from the evidence and policy"],
  ["narrative_clear", "Narrative clear", "Clear, neutral and complete"],
];

export function QAPage() {
  const { me } = useAuth();
  const queryClient = useQueryClient();
  const { data: sample = [], error } = useQuery({ queryKey: ["qa-sample"], queryFn: () => api.qaSample(10),
                                                  enabled: hasRole(me, "qa") });
  const [picked, setPicked] = useState<string | null>(null);
  const caseId = picked ?? sample[0]?.case_id ?? null;
  const { data: view } = useQuery({ queryKey: ["case", caseId], queryFn: () => api.case(caseId!), enabled: !!caseId });
  const [scores, setScores] = useState<Record<string, number>>({});
  const [correct, setCorrect] = useState(true);
  const [comment, setComment] = useState("");

  if (!hasRole(me, "qa")) return <Alert color="gray">QA review needs the QA reviewer role.</Alert>;
  if (error) return <Alert color="red">{(error as Error).message}</Alert>;
  if (!sample.length) return <Alert color="teal">Nothing left to review: every decided case has your label.</Alert>;

  const save = async () => {
    try {
      await api.qaLabel(caseId!, { evidence_complete: scores.evidence_complete ?? 3, citations_accurate: scores.citations_accurate ?? 3,
        recommendation_sound: scores.recommendation_sound ?? 3, narrative_clear: scores.narrative_clear ?? 3,
        decision_correct: correct, comment: comment || null });
      notifications.show({ color: "teal", title: "Label saved", message: caseId });
      setPicked(null); setScores({}); setComment(""); setCorrect(true);
      queryClient.invalidateQueries({ queryKey: ["qa-sample"] });
    } catch (e) {
      notifications.show({ color: "red", title: "Refused", message: (e as ApiError).message });
    }
  };

  const d = view?.state.decision;
  return (
    <Grid>
      <Grid.Col span={{ base: 12, md: 3 }}>
        <Card withBorder radius="lg" p="sm">
          <Title order={5} mb="xs" px="xs">Sample ({sample.length})</Title>
          {sample.map((r) => (
            <NavLink key={r.case_id} active={r.case_id === caseId} onClick={() => setPicked(r.case_id)} label={r.case_id}
                     description={r.scenario} rightSection={<Badge size="xs" color={STATUS[r.status].color}>{STATUS[r.status].label}</Badge>} />
          ))}
        </Card>
      </Grid.Col>
      <Grid.Col span={{ base: 12, md: 9 }}>
        <Card withBorder radius="lg" p="lg">
          <Title order={4}>{caseId}</Title>
          {view && (
            <Stack gap="xs" my="md">
              <Text>Recommendation: <b>{view.recommendation ? ACTION_LABEL[view.recommendation.recommendation] : "–"}</b> ({view.recommendation?.reason_code})
                · Decision: <b>{d ? ACTION_LABEL[d.action] : "–"}</b> ({d?.reason_code}) by {d?.investigator_id}</Text>
              <Text c="dimmed">{view.state.narrative?.summary}</Text>
            </Stack>
          )}
          <Stack gap="md" data-testid="qa-rubric">
            {RUBRIC.map(([key, label, help]) => (
              <Group key={key} justify="space-between">
                <div><Text fw={500}>{label}</Text><Text size="xs" c="dimmed">{help}</Text></div>
                <Rating value={scores[key] ?? 3} onChange={(v) => setScores((s) => ({ ...s, [key]: v }))} size="lg" />
              </Group>
            ))}
            <Switch label="The final decision was correct" checked={correct} onChange={(e) => setCorrect(e.currentTarget.checked)} />
            <Textarea label="Comment (optional)" value={comment} onChange={(e) => setComment(e.currentTarget.value)} autosize minRows={2} />
            <Button onClick={save} w={200}>Save label</Button>
          </Stack>
        </Card>
      </Grid.Col>
    </Grid>
  );
}
