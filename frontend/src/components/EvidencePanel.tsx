import { useEffect, useMemo, useState } from "react";
import type { Incident, LogLine, Span } from "../types";
import { serviceLabel } from "../labels";

const SERVICES = ["gateway", "orders", "payments", "cache", "ledger"];

export function EvidencePanel({
  incident,
  selectedRef,
}: {
  incident: Incident | null;
  selectedRef: string | null;
}) {
  const [tab, setTab] = useState<"logs" | "metrics" | "traces">("logs");
  const logs = incident?.telemetry.logs ?? [];
  const spans = incident?.telemetry.spans ?? [];
  const traceIds = useMemo(() => unique(spans.map((span) => span.trace_id)).slice(0, 4), [spans]);
  const [traceId, setTraceId] = useState<string | null>(null);
  const activeTrace = traceId && traceIds.includes(traceId) ? traceId : traceIds[0];

  useEffect(() => {
    if (!selectedRef || !incident) return;
    if (incident.telemetry.logs.some((log) => log.id === selectedRef)) {
      setTab("logs");
    } else if (incident.telemetry.spans.some((span) => span.span_id === selectedRef)) {
      setTab("traces");
      const owner = incident.telemetry.spans.find((span) => span.span_id === selectedRef);
      if (owner) setTraceId(owner.trace_id);
    } else {
      setTab("metrics");
    }
  }, [selectedRef, incident]);

  useEffect(() => {
    if (!selectedRef) return;
    document.getElementById(`ev-${selectedRef}`)?.scrollIntoView({ block: "nearest" });
  }, [selectedRef, tab, activeTrace]);

  return (
    <section className="panel" aria-labelledby="evidence-heading">
      <header className="panel-head">
        <h2 id="evidence-heading">Linked evidence</h2>
        <div className="tabs" role="tablist">
          {(["logs", "metrics", "traces"] as const).map((name) => (
            <button
              key={name}
              type="button"
              role="tab"
              aria-selected={tab === name}
              className={tab === name ? "active" : ""}
              onClick={() => setTab(name)}
            >
              {name}
            </button>
          ))}
        </div>
      </header>
      {!incident ? (
        <p className="empty">Inject an outage to load logs, metrics, and traces.</p>
      ) : null}
      {incident && tab === "logs" ? (
        <LogTable logs={logs} selectedRef={selectedRef} />
      ) : null}
      {incident && tab === "metrics" ? <MetricGrid incident={incident} /> : null}
      {incident && tab === "traces" ? (
        <TraceView
          spans={spans.filter((span) => span.trace_id === activeTrace)}
          traceIds={traceIds}
          activeTrace={activeTrace ?? ""}
          onPick={setTraceId}
          selectedRef={selectedRef}
        />
      ) : null}
    </section>
  );
}

function LogTable({ logs, selectedRef }: { logs: LogLine[]; selectedRef: string | null }) {
  return (
    <div className="table-wrap">
      <table>
        <thead>
          <tr>
            <th>Service</th>
            <th>Level</th>
            <th>Event</th>
            <th>Message</th>
            <th>Trace</th>
          </tr>
        </thead>
        <tbody>
          {logs.map((log) => (
            <tr
              key={log.id}
              id={`ev-${log.id}`}
              data-selected={selectedRef === log.id ? "true" : "false"}
              className={selectedRef === log.id ? "selected" : ""}
            >
              <td>{serviceLabel(log.service)}</td>
              <td className={`level-${log.level}`}>{log.level}</td>
              <td>{log.event}</td>
              <td>{log.message}</td>
              <td className="mono">{log.trace_id ?? "—"}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function MetricGrid({ incident }: { incident: Incident }) {
  return (
    <div className="metric-grid">
      {SERVICES.map((service) => {
        const metric = incident.telemetry.metrics[service];
        if (!metric) return null;
        return (
          <article key={service}>
            <span id={`ev-${service}.exclusive_ms`} />
            <span id={`ev-${service}.p99_ms`} />
            <span id={`ev-${service}.memory_mb`} />
            <span id={`ev-${service}.health`} />
            <span id={`ev-${service}.config`} />
            <h3>{serviceLabel(service)}</h3>
            <p>
              p99 {metric.p99_ms ?? "—"} ms · exclusive {metric.exclusive_ms ?? "—"} ms · mem{" "}
              {metric.memory_mb} MB
            </p>
            <Spark values={metric.p99_series} label="p99" />
            <Spark values={metric.memory_series} label="memory" />
          </article>
        );
      })}
    </div>
  );
}

function Spark({ values, label }: { values: number[]; label: string }) {
  const max = Math.max(...values, 1);
  const width = 180;
  const height = 36;
  const step = values.length > 1 ? width / (values.length - 1) : width;
  const points = values
    .map((value, index) => `${index * step},${height - (value / max) * (height - 4) - 2}`)
    .join(" ");
  return (
    <svg className="spark" viewBox={`0 0 ${width} ${height}`} role="img" aria-label={`${label} series`}>
      <polyline points={points} />
    </svg>
  );
}

function TraceView({
  spans,
  traceIds,
  activeTrace,
  onPick,
  selectedRef,
}: {
  spans: Span[];
  traceIds: string[];
  activeTrace: string;
  onPick: (traceId: string) => void;
  selectedRef: string | null;
}) {
  const max = Math.max(...spans.map((span) => span.duration_ms), 1);
  return (
    <div>
      <div className="trace-picker">
        {traceIds.map((id) => (
          <button key={id} type="button" className={id === activeTrace ? "active" : ""} onClick={() => onPick(id)}>
            {id}
          </button>
        ))}
      </div>
      <ul className="waterfall">
        {spans.map((span) => (
          <li
            key={span.span_id}
            id={`ev-${span.span_id}`}
            className={selectedRef === span.span_id ? "selected" : ""}
            style={{ marginLeft: `${depth(span, spans) * 16}px` }}
          >
            <span className="mono">
              {serviceLabel(span.service)} · {span.kind}
            </span>
            <span className="bar-label">{span.name}</span>
            <span className="bar" style={{ width: `${Math.max(8, (span.duration_ms / max) * 220)}px` }} />
            <span className="mono">{span.duration_ms} ms</span>
          </li>
        ))}
      </ul>
    </div>
  );
}

function depth(span: Span, spans: Span[]): number {
  let level = 0;
  let parent = span.parent_span_id;
  const guard = new Set<string>();
  while (parent && !guard.has(parent)) {
    guard.add(parent);
    level += 1;
    parent = spans.find((item) => item.span_id === parent)?.parent_span_id ?? null;
  }
  return level;
}

function unique(values: string[]): string[] {
  return [...new Set(values)];
}
