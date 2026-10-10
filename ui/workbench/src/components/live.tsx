// Live progress (FR-102): case events over server-sent events, with a running clock.
import { fetchEventSource } from "@microsoft/fetch-event-source";
import { Badge, Card, Group, Table, Text } from "@mantine/core";
import { useQueryClient } from "@tanstack/react-query";
import { useEffect, useRef, useState } from "react";

import { authHeader } from "../api";
import { AGENTS, currentRun, duration, time } from "../format";
import type { CaseEvent } from "../types";

// Events that change what the case page should show
const REFRESH_ON = ["case_started", "case_resumed", "awaiting_review", "awaiting_approval", "approval_applied",
  "decision_applied", "follow_up_started", "decision_ignored", "follow_up_ignored", "error"];

/** All events of a case: history first, then live ones as the workers publish them. */
export function useCaseEvents(caseId: string): CaseEvent[] {
  const [events, setEvents] = useState<CaseEvent[]>([]);
  const queryClient = useQueryClient();
  useEffect(() => {
    const controller = new AbortController();
    const openedAt = new Date().toISOString();
    setEvents([]);
    fetchEventSource(`/api/cases/${caseId}/stream`, {
      headers: authHeader(),
      signal: controller.signal,
      openWhenHidden: true,
      onmessage(msg) {
        if (!msg.data) return; // keep-alive ping
        const event = JSON.parse(msg.data) as CaseEvent;
        setEvents((prev) => [...prev, event]);
        if (event.at > openedAt && REFRESH_ON.includes(event.type)) {
          queryClient.invalidateQueries({ queryKey: ["case", caseId] });
          queryClient.invalidateQueries({ queryKey: ["history", caseId] });
        }
      },
    }).catch(() => undefined);
    return () => controller.abort();
  }, [caseId, queryClient]);
  return events;
}

function useNow(intervalMs = 1000): number {
  const [now, setNow] = useState(Date.now());
  useEffect(() => {
    const id = setInterval(() => setNow(Date.now()), intervalMs);
    return () => clearInterval(id);
  }, [intervalMs]);
  return now;
}

/** Spinner, running clock and the current run's events, newest first. */
export function LiveProgress({ events, waitingFor, since }: { events: CaseEvent[]; waitingFor: string; since?: string }) {
  const now = useNow();
  const run = currentRun(events);
  const start = since ?? run.find((e) => ["case_started", "follow_up_started", "case_resumed"].includes(e.type))?.at;
  const last = [...run].reverse().find((e) => e.type === "node_completed");
  const bottom = useRef<HTMLDivElement>(null);
  return (
    <Card withBorder radius="lg" p="md" className="sn-live" data-testid="live-progress">
      <Group gap="md" mb="sm">
        <div className="sn-spin" />
        <Text fw={700} style={{ fontVariantNumeric: "tabular-nums" }}>
          {start ? duration((now - new Date(start).getTime()) / 1000) : "…"}
        </Text>
        <Text>{waitingFor}{last ? ` · last completed step: ${AGENTS[last.node ?? ""] ?? last.node}` : ""}</Text>
      </Group>
      <Table verticalSpacing={4} fz="sm">
        <Table.Tbody>
          {[...run].reverse().map((e, i) => (
            <Table.Tr key={`${e.at}-${i}`}>
              <Table.Td w={90} c="dimmed">{time(e.at)}</Table.Td>
              <Table.Td w={170}><Badge variant="light" size="sm">{e.type}</Badge></Table.Td>
              <Table.Td w={140}>{e.node ? AGENTS[e.node] ?? e.node : ""}</Table.Td>
              <Table.Td c="dimmed">{e.detail ?? ""}</Table.Td>
            </Table.Tr>
          ))}
        </Table.Tbody>
      </Table>
      <div ref={bottom} />
    </Card>
  );
}
