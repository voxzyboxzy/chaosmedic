import type { Incident, Scenario } from "../types";

export function IncidentPanel({
  mode,
  diagnoser,
  scenarios,
  scenarioId,
  onScenario,
  onInject,
  onApprove,
  onReject,
  onRollback,
  incident,
  busy,
}: {
  mode: string;
  diagnoser: string;
  scenarios: Scenario[];
  scenarioId: string;
  onScenario: (id: string) => void;
  onInject: () => void;
  onApprove: () => void;
  onReject: () => void;
  onRollback: () => void;
  incident: Incident | null;
  busy: boolean;
}) {
  const pending = incident?.status === "pending_approval" && Boolean(incident.proposed_action);
  const scenario = scenarios.find((item) => item.id === scenarioId);
  return (
    <section className="panel incident-panel" aria-labelledby="incident-heading">
      <header className="panel-head">
        <h2 id="incident-heading">Incident</h2>
        <p className="pills">
          <span>{mode}</span>
          <span>{diagnoser}</span>
          <span>allowlist only</span>
        </p>
      </header>
      <label className="field">
        Symptom
        <select
          aria-label="Scenario"
          value={scenarioId}
          onChange={(event) => onScenario(event.target.value)}
          disabled={busy || scenarios.length === 0}
        >
          {scenarios.map((item) => (
            <option key={item.id} value={item.id}>
              {item.title}
            </option>
          ))}
        </select>
      </label>
      <p className="summary">{scenario?.summary ?? "Choose a recorded symptom."}</p>
      <button type="button" className="primary" onClick={onInject} disabled={busy || !scenarioId}>
        Inject outage
      </button>
      {incident ? (
        <>
          <dl className="facts">
            <div>
              <dt>Status</dt>
              <dd>{incident.status.replaceAll("_", " ")}</dd>
            </div>
            <div>
              <dt>Title</dt>
              <dd>{incident.title}</dd>
            </div>
            <div>
              <dt>Time to diagnosis</dt>
              <dd>{incident.diagnosis_ms.toFixed(2)} ms · {incident.tool_calls.length} read-only tools</dd>
            </div>
          </dl>
          <h3>Proposed repair</h3>
          {incident.proposed_action ? (
            <div className="repair">
              <strong>{incident.action_description}</strong>
              <code>{JSON.stringify(executable(incident.proposed_action), null, 2)}</code>
              {incident.rollback_action ? (
                <p>Rollback: {incident.rollback_action.description}</p>
              ) : (
                <p>Restart has no inverse. The cleared process state stays cleared.</p>
              )}
            </div>
          ) : (
            <p className="empty">The diagnoser did not reach the confidence to propose a repair.</p>
          )}
          <div className="actions">
            <button type="button" className="primary" onClick={onApprove} disabled={!pending || busy}>
              Approve repair
            </button>
            <button type="button" onClick={onReject} disabled={!pending || busy}>
              Reject
            </button>
            <button
              type="button"
              onClick={onRollback}
              disabled={!incident.rollback_available || busy}
            >
              Undo repair
            </button>
          </div>
          {incident.verification ? (
            <div>
              <h3>Recovery checks {incident.verification.passed ? "passed" : "failed"}</h3>
              <ul className="checks">
                {incident.verification.checks.map((check) => (
                  <li key={check.name} data-ok={check.ok ? "true" : "false"}>
                    {check.ok ? "Pass" : "Fail"} · {check.detail}
                  </li>
                ))}
              </ul>
            </div>
          ) : null}
          {incident.postmortem ? (
            <div>
              <div className="row">
                <h3>Postmortem</h3>
                <a href={`/api/incidents/${incident.id}/postmortem.md`}>Download markdown</a>
              </div>
              <pre className="postmortem">{incident.postmortem}</pre>
            </div>
          ) : null}
        </>
      ) : (
        <p className="empty">The lab is healthy. Inject an outage to open an investigation.</p>
      )}
      <p className="boundary">
        Repairs are restart, rollback config, scale, or toggle an allowlisted flag. The commander
        cannot run a shell command.
      </p>
    </section>
  );
}

function executable(action: Incident["proposed_action"]): Record<string, unknown> {
  if (!action) return {};
  const { description, rationale, config, ...rest } = action;
  void description;
  void rationale;
  void config;
  return rest;
}
