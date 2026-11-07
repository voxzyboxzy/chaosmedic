import type { Incident, Overview } from "./types";

async function read<T>(response: Response): Promise<T> {
  if (!response.ok) {
    let detail = response.statusText;
    try {
      const body = (await response.json()) as { detail?: string };
      if (body.detail) detail = body.detail;
    } catch {
      /* keep the status text */
    }
    throw new Error(detail);
  }
  return (await response.json()) as T;
}

export function getOverview(): Promise<Overview> {
  return fetch("/api/overview").then((response) => read<Overview>(response));
}

export function injectOutage(scenarioId: string): Promise<Incident> {
  return fetch("/api/incidents/inject", {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ scenario_id: scenarioId }),
  }).then((response) => read<Incident>(response));
}

export function approveRepair(incidentId: string): Promise<Incident> {
  return fetch(`/api/incidents/${incidentId}/approve`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ approver: "operator" }),
  }).then((response) => read<Incident>(response));
}

export function rejectRepair(incidentId: string): Promise<Incident> {
  return fetch(`/api/incidents/${incidentId}/reject`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ approver: "operator" }),
  }).then((response) => read<Incident>(response));
}

export function rollbackRepair(incidentId: string): Promise<Incident> {
  return fetch(`/api/incidents/${incidentId}/rollback`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ approver: "operator" }),
  }).then((response) => read<Incident>(response));
}

export function postmortemUrl(incidentId: string): string {
  return `/api/incidents/${incidentId}/postmortem.md`;
}
