export const FAULT_LABEL: Record<string, string> = {
  service_crash: "Service crash",
  latency_spike: "Latency spike",
  bad_config: "Bad configuration",
  dependency_outage: "Dependency outage",
  memory_leak: "Memory leak",
};

export const SERVICE_LABEL: Record<string, string> = {
  gateway: "Gateway",
  orders: "Orders",
  payments: "Payments",
  cache: "Cache",
  ledger: "Ledger",
};

export function faultLabel(fault: string): string {
  return FAULT_LABEL[fault] ?? fault;
}

export function serviceLabel(service: string): string {
  return SERVICE_LABEL[service] ?? service;
}

export function formatMs(value: number | null): string {
  if (value === null) return "—";
  return `${value} ms`;
}

export function formatPercent(value: number): string {
  return `${Math.round(value * 100)}%`;
}
