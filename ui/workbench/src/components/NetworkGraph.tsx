// Network graph (FR-104): linked customers coloured by mule score, shared devices, transfers.
import { Alert, Group, Loader, Text } from "@mantine/core";
import { useQuery } from "@tanstack/react-query";
import cytoscape from "cytoscape";
import { useEffect, useRef } from "react";

import { api } from "../api";

function scoreColour(score?: number): string {
  if (score == null) return "#E2E8F0";
  return score >= 0.6 ? "#F8B4B4" : score >= 0.3 ? "#FCE7B2" : "#E2E8F0";
}

export function NetworkGraph({ caseId }: { caseId: string }) {
  const { data, isLoading } = useQuery({ queryKey: ["network", caseId], queryFn: () => api.network(caseId) });
  const box = useRef<HTMLDivElement>(null);
  const linked = (data?.nodes.length ?? 0) > 1;

  useEffect(() => {
    if (!data || !linked || !box.current) return;
    const cy = cytoscape({
      container: box.current,
      elements: [
        ...data.nodes.map((n) => ({
          data: { id: n.id, label: n.kind === "customer" ? `${n.id}\nmule ${n.mule_score ?? "–"}` : `${n.device_type}\n${n.id}`,
                  colour: n.kind === "customer" ? scoreColour(n.mule_score) : "#DBEAFE" },
          classes: [n.kind, n.case_customer ? "case" : ""].join(" "),
        })),
        ...data.edges.map((e, i) => ({ data: { id: `e${i}`, source: e.source, target: e.target }, classes: e.kind })),
      ],
      style: [
        { selector: "node", style: { label: "data(label)", "background-color": "data(colour)", "text-wrap": "wrap",
          "text-valign": "center", "font-size": 9, width: 92, height: 42, "border-width": 1, "border-color": "#94A3B8",
          color: "#0B2545" } },
        { selector: "node.device", style: { shape: "round-rectangle", width: 84, height: 34 } },
        { selector: "node.case", style: { "border-width": 3, "border-color": "#0B2545" } },
        { selector: "edge", style: { width: 1.5, "line-color": "#64748B", "curve-style": "bezier" } },
        { selector: "edge.transfer", style: { "line-style": "dashed", "target-arrow-shape": "triangle",
          "target-arrow-color": "#64748B", label: "transfer", "font-size": 8 } },
      ],
      layout: { name: "cose", animate: false, padding: 24, nodeRepulsion: () => 9000 },
      wheelSensitivity: 0.2,
    });
    return () => cy.destroy();
  }, [data, linked]);

  if (isLoading) return <Loader size="sm" />;
  if (!linked) {
    return <Alert color="gray">{data?.customer_id} has no linked customers or shared devices within 2 hops.</Alert>;
  }
  return (
    <div>
      <div ref={box} className="sn-network" data-testid="network-graph" />
      <Group gap="lg" mt="xs">
        {[["#F8B4B4", "mule score 0.6 or more"], ["#FCE7B2", "0.3–0.6"], ["#E2E8F0", "below 0.3"], ["#DBEAFE", "shared device"]].map(([c, l]) => (
          <Group key={l} gap={6}><div style={{ width: 12, height: 12, borderRadius: 3, background: c, border: "1px solid #94A3B8" }} /><Text size="xs" c="dimmed">{l}</Text></Group>
        ))}
        <Text size="xs" c="dimmed">dashed arrow = transfer · bold outline = this case's customer</Text>
      </Group>
    </div>
  );
}
