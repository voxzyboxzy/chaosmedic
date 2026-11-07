import type { Evidence, Hypothesis } from "../types";
import { faultLabel, serviceLabel } from "../labels";

export function HypothesisBoard({
  hypotheses,
  onSelect,
}: {
  hypotheses: Hypothesis[];
  onSelect: (evidence: Evidence) => void;
}) {
  return (
    <section className="panel" aria-labelledby="hypothesis-heading">
      <header className="panel-head">
        <h2 id="hypothesis-heading">Hypotheses</h2>
        <p>Competing explanations, each with the evidence that raised or lowered it.</p>
      </header>
      {hypotheses.length === 0 ? (
        <p className="empty">No investigation yet.</p>
      ) : (
        <ol className="hypotheses">
          {hypotheses.map((item) => (
            <li key={`${item.fault}-${item.service}`} className={item.rank === 1 ? "leading" : ""}>
              <div className="hyp-top">
                <span className="rank">{item.rank}</span>
                <div>
                  <strong>
                    {faultLabel(item.fault)} · {serviceLabel(item.service)}
                  </strong>
                  <span className="conf">
                    score {item.score} · confidence {item.confidence.toFixed(2)}
                  </span>
                </div>
              </div>
              <p>{item.summary}</p>
              <EvidenceList title="For" items={item.for} onSelect={onSelect} />
              <EvidenceList title="Against" items={item.against} onSelect={onSelect} />
            </li>
          ))}
        </ol>
      )}
    </section>
  );
}

function EvidenceList({
  title,
  items,
  onSelect,
}: {
  title: string;
  items: Evidence[];
  onSelect: (evidence: Evidence) => void;
}) {
  return (
    <div className="evidence-list">
      <h3>{title}</h3>
      {items.length === 0 ? (
        <p className="muted">None recorded.</p>
      ) : (
        <ul>
          {items.map((item) => (
            <li key={`${title}-${item.ref}-${item.text}`}>
              <button type="button" onClick={() => onSelect(item)}>
                <span className="kind">{item.kind}</span>
                {item.text}
              </button>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
