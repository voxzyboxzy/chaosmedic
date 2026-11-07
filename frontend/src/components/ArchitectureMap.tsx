import type { Topology, TopologyNode } from "../types";
import { formatMs, formatPercent, serviceLabel } from "../labels";

const POSITIONS: Record<string, { x: number; y: number }> = {
  gateway: { x: 36, y: 70 },
  orders: { x: 268, y: 70 },
  payments: { x: 500, y: 70 },
  cache: { x: 268, y: 196 },
  ledger: { x: 690, y: 196 },
};

const NODE_W = 156;
const NODE_H = 84;

function center(id: string): { x: number; y: number } {
  const pos = POSITIONS[id];
  return { x: pos.x + NODE_W / 2, y: pos.y + NODE_H / 2 };
}

export function ArchitectureMap({ topology }: { topology: Topology }) {
  return (
    <section className="panel map-panel" aria-labelledby="map-heading">
      <header className="panel-head">
        <h2 id="map-heading">Architecture</h2>
        <ul className="legend">
          <li><i className="swatch up" /> Up</li>
          <li><i className="swatch degraded" /> Degraded</li>
          <li><i className="swatch down" /> Down</li>
          <li><i className="swatch root" /> Leading hypothesis</li>
        </ul>
      </header>
      <svg viewBox="0 0 880 310" role="img" aria-label="Service topology">
        {topology.edges.map((edge) => {
          const from = center(edge.source);
          const to = center(edge.target);
          return (
            <line
              key={`${edge.source}-${edge.target}`}
              x1={from.x}
              y1={from.y}
              x2={to.x}
              y2={to.y}
              className={`edge edge-${edge.status}`}
            />
          );
        })}
        {topology.nodes.map((node) => (
          <ServiceNode key={node.id} node={node} />
        ))}
      </svg>
    </section>
  );
}

function ServiceNode({ node }: { node: TopologyNode }) {
  const pos = POSITIONS[node.id];
  if (!pos) return null;
  return (
    <g
      className={`node node-${node.status}${node.root_cause ? " is-root" : ""}`}
      data-service={node.id}
      data-status={node.status}
      data-root={node.root_cause ? "true" : "false"}
      transform={`translate(${pos.x} ${pos.y})`}
    >
      <rect width={NODE_W} height={NODE_H} rx="10" />
      <text className="node-name" x="14" y="26">
        {serviceLabel(node.id)}
      </text>
      <text className="node-status" x="14" y="46">
        {node.status}
      </text>
      <text className="node-metric" x="14" y="68">
        {`p99 ${formatMs(node.p99_ms)}  err ${formatPercent(node.error_rate)}`}
      </text>
      {node.root_cause ? (
        <text className="node-root" x={NODE_W - 12} y="22" textAnchor="end">
          focus
        </text>
      ) : null}
    </g>
  );
}
