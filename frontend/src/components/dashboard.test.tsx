import { fireEvent, render, screen } from "@testing-library/react";
import { ArchitectureMap } from "./ArchitectureMap";
import { HypothesisBoard } from "./HypothesisBoard";
import { IncidentPanel } from "./IncidentPanel";
import type { Hypothesis, Incident, Topology } from "../types";

const topology: Topology = {
  nodes: [
    { id: "gateway", status: "degraded", root_cause: false, error_rate: 1, p99_ms: 2508, memory_mb: 72, replicas: 1 },
    { id: "orders", status: "degraded", root_cause: false, error_rate: 0, p99_ms: 2632, memory_mb: 72, replicas: 1 },
    { id: "payments", status: "degraded", root_cause: true, error_rate: 0, p99_ms: 2616, memory_mb: 72, replicas: 1 },
    { id: "cache", status: "up", root_cause: false, error_rate: 0, p99_ms: 8, memory_mb: 72, replicas: 1 },
    { id: "ledger", status: "up", root_cause: false, error_rate: 0, p99_ms: 8, memory_mb: 72, replicas: 1 },
  ],
  edges: [
    { source: "gateway", target: "orders", status: "degraded" },
    { source: "orders", target: "payments", status: "degraded" },
    { source: "orders", target: "cache", status: "degraded" },
    { source: "payments", target: "ledger", status: "degraded" },
  ],
};

const hypotheses: Hypothesis[] = [
  {
    rank: 1,
    fault: "latency_spike",
    service: "payments",
    score: 8,
    confidence: 0.86,
    summary: "Server time is concentrated in payments.",
    for: [{ kind: "trace", ref: "span-1", text: "payments exclusive server time is 2608ms", trace_id: "t1" }],
    against: [],
  },
  {
    rank: 2,
    fault: "latency_spike",
    service: "gateway",
    score: -4,
    confidence: 0.05,
    summary: "Gateway is waiting.",
    for: [],
    against: [{ kind: "metric", ref: "gateway.exclusive_ms", text: "gateway exclusive server time is only 8ms" }],
  },
];

function incident(status: string): Incident {
  return {
    id: "inc_test",
    scenario_id: "checkout-timeouts",
    title: "Checkout timeouts",
    summary: "Shoppers are hitting the gateway deadline.",
    status,
    mode: "replay",
    diagnoser: "rules",
    diagnosis_ms: 0.42,
    tool_calls: [{ tool: "get_health", args: {} }],
    hypotheses,
    proposed_action:
      status === "undetermined"
        ? null
        : {
            action: "scale_service",
            service: "payments",
            replicas: 3,
            description: "Scale payments to 3 replicas",
          },
    approved_action: null,
    rollback_action: null,
    rollback_available: false,
    verification: null,
    postmortem_ready: false,
    postmortem: null,
    topology,
    telemetry: { logs: [], spans: [], metrics: {}, health: {}, configs: {} },
    action_description: "Scale payments to 3 replicas",
  };
}

describe("dashboard", () => {
  it("marks degraded services and the leading hypothesis on the map", () => {
    render(<ArchitectureMap topology={topology} />);
    const payments = document.querySelector('[data-service="payments"]');
    expect(payments).toHaveAttribute("data-status", "degraded");
    expect(payments).toHaveAttribute("data-root", "true");
    expect(document.querySelector('[data-service="cache"]')).toHaveAttribute("data-status", "up");
    expect(screen.getByText("Payments")).toBeInTheDocument();
  });

  it("shows evidence for and against a hypothesis", () => {
    const onSelect = vi.fn();
    render(<HypothesisBoard hypotheses={hypotheses} onSelect={onSelect} />);
    expect(screen.getByText(/Server time is concentrated in payments/)).toBeInTheDocument();
    expect(screen.getByText(/gateway exclusive server time is only 8ms/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /payments exclusive server time/ }));
    expect(onSelect).toHaveBeenCalledWith(hypotheses[0].for[0]);
  });

  it("keeps approval closed until a repair is proposed", () => {
    render(
      <IncidentPanel
        mode="replay"
        diagnoser="rules"
        scenarios={[{ id: "checkout-timeouts", title: "Checkout timeouts", summary: "Slow checkout", order: 1 }]}
        scenarioId="checkout-timeouts"
        onScenario={() => undefined}
        onInject={() => undefined}
        onApprove={() => undefined}
        onReject={() => undefined}
        onRollback={() => undefined}
        incident={null}
        busy={false}
      />,
    );
    expect(screen.getByRole("button", { name: "Inject outage" })).toBeEnabled();
    expect(screen.queryByRole("button", { name: "Approve repair" })).not.toBeInTheDocument();
  });

  it("enables approval only while the incident is pending", () => {
    const { rerender } = render(
      <IncidentPanel
        mode="replay"
        diagnoser="rules"
        scenarios={[{ id: "checkout-timeouts", title: "Checkout timeouts", summary: "Slow checkout", order: 1 }]}
        scenarioId="checkout-timeouts"
        onScenario={() => undefined}
        onInject={() => undefined}
        onApprove={() => undefined}
        onReject={() => undefined}
        onRollback={() => undefined}
        incident={incident("pending_approval")}
        busy={false}
      />,
    );
    expect(screen.getByRole("button", { name: "Approve repair" })).toBeEnabled();
    rerender(
      <IncidentPanel
        mode="replay"
        diagnoser="rules"
        scenarios={[{ id: "checkout-timeouts", title: "Checkout timeouts", summary: "Slow checkout", order: 1 }]}
        scenarioId="checkout-timeouts"
        onScenario={() => undefined}
        onInject={() => undefined}
        onApprove={() => undefined}
        onReject={() => undefined}
        onRollback={() => undefined}
        incident={incident("resolved")}
        busy={false}
      />,
    );
    expect(screen.getByRole("button", { name: "Approve repair" })).toBeDisabled();
  });
});
