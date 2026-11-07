import { useEffect, useState } from "react";
import { approveRepair, getOverview, injectOutage, rejectRepair, rollbackRepair } from "./api";
import { ArchitectureMap } from "./components/ArchitectureMap";
import { EvidencePanel } from "./components/EvidencePanel";
import { HypothesisBoard } from "./components/HypothesisBoard";
import { IncidentPanel } from "./components/IncidentPanel";
import type { Evidence, Incident, Overview } from "./types";

export function App() {
  const [overview, setOverview] = useState<Overview | null>(null);
  const [incident, setIncident] = useState<Incident | null>(null);
  const [scenarioId, setScenarioId] = useState("");
  const [selectedRef, setSelectedRef] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    getOverview()
      .then((payload) => {
        setOverview(payload);
        setIncident(payload.incident);
        setScenarioId(payload.incident?.scenario_id || payload.scenarios[0]?.id || "");
      })
      .catch((reason: Error) => setError(reason.message));
  }, []);

  async function run(action: () => Promise<Incident>) {
    setBusy(true);
    setError(null);
    try {
      setIncident(await action());
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "request failed");
    } finally {
      setBusy(false);
    }
  }

  function onSelect(evidence: Evidence) {
    setSelectedRef(evidence.ref);
  }

  const topology = incident?.topology ?? overview?.topology;
  const scenarios = overview?.scenarios ?? [];

  return (
    <div className="app">
      <header className="topbar">
        <div className="brand">
          <div className="mark" aria-hidden="true">+</div>
          <div>
            <h1>ChaosMedic</h1>
            <p>Incident commander for the local service lab.</p>
          </div>
        </div>
      </header>
      {error ? <div className="banner" role="alert">{error}</div> : null}
      {!overview || !topology ? (
        <p className="empty">Loading the lab…</p>
      ) : (
        <div className="layout">
          <div className="stack">
            <ArchitectureMap topology={topology} />
            <HypothesisBoard hypotheses={incident?.hypotheses ?? []} onSelect={onSelect} />
            <EvidencePanel incident={incident} selectedRef={selectedRef} />
          </div>
          <IncidentPanel
            mode={overview.mode}
            diagnoser={incident?.diagnoser ?? overview.diagnoser}
            scenarios={scenarios}
            scenarioId={scenarioId}
            onScenario={setScenarioId}
            onInject={() => run(() => injectOutage(scenarioId))}
            onApprove={() => incident && run(() => approveRepair(incident.id))}
            onReject={() => incident && run(() => rejectRepair(incident.id))}
            onRollback={() => incident && run(() => rollbackRepair(incident.id))}
            incident={incident}
            busy={busy}
          />
        </div>
      )}
    </div>
  );
}
