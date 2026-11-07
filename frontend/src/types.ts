export type Status = "up" | "degraded" | "down";

export interface TopologyNode {
  id: string;
  status: Status;
  root_cause: boolean;
  error_rate: number;
  p99_ms: number | null;
  memory_mb: number;
  replicas: number;
}

export interface TopologyEdge {
  source: string;
  target: string;
  status: Status;
}

export interface Topology {
  nodes: TopologyNode[];
  edges: TopologyEdge[];
}

export interface Evidence {
  kind: "log" | "metric" | "trace";
  ref: string;
  text: string;
  trace_id?: string;
}

export interface Hypothesis {
  rank: number;
  fault: string;
  service: string;
  score: number;
  confidence: number;
  summary: string;
  for: Evidence[];
  against: Evidence[];
}

export interface Action {
  action: string;
  service: string;
  replicas?: number;
  flag?: string;
  value?: boolean;
  rationale?: string;
  description: string;
  config?: Record<string, unknown>;
}

export interface Check {
  name: string;
  ok: boolean;
  detail: string;
}

export interface LogLine {
  id: string;
  service: string;
  level: string;
  event: string;
  message: string;
  trace_id: string | null;
  span_id: string | null;
}

export interface Span {
  span_id: string;
  trace_id: string;
  parent_span_id: string | null;
  service: string;
  kind: string;
  name: string;
  duration_ms: number;
  status: string;
}

export interface ServiceMetrics {
  requests: number;
  errors: number;
  error_rate: number;
  p50_ms: number | null;
  p99_ms: number | null;
  memory_mb: number;
  replicas: number;
  exclusive_ms: number | null;
  p99_series: number[];
  error_rate_series: number[];
  memory_series: number[];
}

export interface Incident {
  id: string;
  scenario_id: string;
  title: string;
  summary: string;
  status: string;
  mode: string;
  diagnoser: string;
  fallback?: string | null;
  diagnosis_ms: number;
  tool_calls: { tool: string; args: Record<string, unknown> }[];
  hypotheses: Hypothesis[];
  proposed_action: Action | null;
  approved_action: Action | null;
  rollback_action: Action | null;
  rollback_available: boolean;
  verification: { passed: boolean; checks: Check[] } | null;
  postmortem_ready: boolean;
  postmortem: string | null;
  topology: Topology;
  telemetry: {
    logs: LogLine[];
    spans: Span[];
    metrics: Record<string, ServiceMetrics>;
    health: Record<string, { service: string; status: Status }>;
    configs: Record<string, Record<string, unknown>>;
  };
  action_description: string | null;
}

export interface Scenario {
  id: string;
  title: string;
  summary: string;
  order: number;
}

export interface Overview {
  mode: string;
  diagnoser: string;
  scenarios: Scenario[];
  topology: Topology;
  incident: Incident | null;
  otlp: Record<string, number>;
}
