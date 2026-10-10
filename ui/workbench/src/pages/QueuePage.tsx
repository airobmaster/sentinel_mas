// Work queue (FR-100): search by case number, status tick boxes, lane and entity filters; time waiting
// with an SLA colour; alternating row colours. The filters live in the page address, so opening a case
// and coming back keeps them (and a filtered queue can be bookmarked or shared).
import {
  Badge, Card, Checkbox, Group, SegmentedControl, Select, Stack, Table, Text, TextInput, Title,
} from "@mantine/core";
import { useQuery } from "@tanstack/react-query";
import { IconSearch } from "@tabler/icons-react";
import { useNavigate, useSearchParams } from "react-router-dom";

import { api } from "../api";
import { duration, secondsSince, slaColor, STATUS } from "../format";
import type { CaseStatus } from "../types";

const DEFAULT_STATUSES: CaseStatus[] = ["in_progress", "awaiting_approval", "awaiting_review", "info_requested"];
const NONE = "none"; // marker for "no status ticked" (an empty list would fall back to the defaults)

export function QueuePage() {
  const navigate = useNavigate();
  const [query, setQuery] = useSearchParams();
  const ticked = query.getAll("status");
  const statuses = ticked.length ? ticked.filter((s) => s !== NONE) : DEFAULT_STATUSES;
  const lane = query.get("lane") ?? "all";
  const entity = query.get("entity");
  const search = query.get("q") ?? "";
  const update = (changes: Record<string, string | string[] | null>) => {
    const next = new URLSearchParams(query);
    for (const [key, value] of Object.entries(changes)) {
      next.delete(key);
      (Array.isArray(value) ? value : value && value !== "all" ? [value] : []).forEach((v) => next.append(key, v));
    }
    setQuery(next, { replace: true });
  };
  const setStatuses = (values: string[]) => update({ status: values.length ? values : [NONE] });
  const setLane = (value: string) => update({ lane: value });
  const setEntity = (value: string | null) => update({ entity: value });
  const setSearch = (value: string) => update({ q: value || null });

  const params = new URLSearchParams();
  statuses.forEach((s) => params.append("status", s));
  if (lane !== "all") params.set("lane", lane);
  if (entity) params.set("entity", entity);
  const { data = [], isLoading } = useQuery({
    queryKey: ["queue", params.toString()],
    queryFn: () => api.queue(params),
    refetchInterval: 5000,
    enabled: statuses.length > 0,
  });
  const needle = search.trim().toUpperCase();
  const rows = (statuses.length ? data : []).filter((r) => !needle || r.case_id.toUpperCase().includes(needle));

  return (
    <Card withBorder radius="lg" p="lg">
      <Group justify="space-between" align="flex-start" mb="md">
        <div>
          <Title order={3}>Work queue</Title>
          <Text size="sm" c="dimmed">{rows.length} case(s) · refreshes every 5 seconds · newest activity first</Text>
        </div>
        <Group align="end">
          <TextInput label="Case number" placeholder="e.g. G0086" leftSection={<IconSearch size={16} />} w={220}
                     value={search} onChange={(e) => setSearch(e.currentTarget.value)} data-testid="case-search" />
          <div>
            <Text size="sm" fw={500} mb={4}>Lane</Text>
            <SegmentedControl value={lane} onChange={setLane}
                              data={[{ value: "all", label: "All" }, { value: "fast", label: "Fast" },
                                     { value: "full", label: "Full" }]} />
          </div>
          <Select label="Legal entity" data={["UK", "ES"]} value={entity} onChange={setEntity} clearable
                  placeholder="All" w={120} />
        </Group>
      </Group>
      <Stack gap={6} mb="md">
        <Text size="sm" fw={500}>Status</Text>
        <Checkbox.Group value={statuses} onChange={setStatuses}>
          <Group gap="lg">
            {Object.entries(STATUS).map(([value, s]) => (
              <Checkbox key={value} value={value} label={s.label} color={s.color} size="sm" />
            ))}
          </Group>
        </Checkbox.Group>
      </Stack>
      <Table striped stripedColor="#FCEFEF" highlightOnHover highlightOnHoverColor="#F9DADA" verticalSpacing="sm"
             withTableBorder data-testid="queue">
        <Table.Thead bg="#F6E3E3">
          <Table.Tr>
            <Table.Th>Status</Table.Th>
            <Table.Th>Case</Table.Th>
            <Table.Th>Scenario</Table.Th>
            <Table.Th>Lane</Table.Th>
            <Table.Th>Entity</Table.Th>
            <Table.Th>Customer</Table.Th>
            <Table.Th>Waiting</Table.Th>
          </Table.Tr>
        </Table.Thead>
        <Table.Tbody>
          {rows.map((r) => {
            const sla = slaColor(r.status, r.updated_at);
            return (
              <Table.Tr key={r.case_id} style={{ cursor: "pointer" }} onClick={() => navigate(`/cases/${r.case_id}`, { state: { from: `/?${query}` } })}>
                <Table.Td><Badge color={STATUS[r.status].color} variant="light">{STATUS[r.status].label}</Badge></Table.Td>
                <Table.Td fw={600}>{r.case_id}</Table.Td>
                <Table.Td>{r.scenario}</Table.Td>
                <Table.Td>{r.tier ? <Badge variant="outline" color={r.tier === "full" ? "grape" : "cyan"}>{r.tier}</Badge> : "–"}</Table.Td>
                <Table.Td>{r.legal_entity}</Table.Td>
                <Table.Td c="dimmed">{r.customer_id}</Table.Td>
                <Table.Td>
                  {sla ? <Badge color={sla} variant="dot">{duration(secondsSince(r.updated_at))}</Badge>
                       : <Text size="sm" c="dimmed">{duration(secondsSince(r.updated_at))} ago</Text>}
                </Table.Td>
              </Table.Tr>
            );
          })}
          {!rows.length && !isLoading && (
            <Table.Tr><Table.Td colSpan={7}>
              <Text c="dimmed" ta="center" py="lg">{statuses.length ? "No cases match the filters." : "Tick at least one status."}</Text>
            </Table.Td></Table.Tr>
          )}
        </Table.Tbody>
      </Table>
    </Card>
  );
}
