import { useId } from "react";
import type { FinalReport, PropertyVerdict } from "../types";
import { nightsBetween } from "../types";
import { budgetLabel, dateRange, money, plural, shortDestination, travelersLabel } from "../format";
import ResultCard, { ResultRow, type Badge } from "./ResultCard";
import ZoneMap, { type MapMarker } from "./ZoneMap";

interface Props {
  report: FinalReport;
  partial?: boolean;
  threadId: string;
  mock: boolean;
  canSend: boolean;
  onSend: (text: string) => void;
  onAdjust: () => void;
}

const REFINE: { label: string; text: string }[] = [
  { label: "Más baratas", text: "Quiero opciones más baratas." },
  { label: "Más cerca del centro", text: "Quiero opciones más cerca del centro." },
  { label: "Con pileta", text: "Quiero opciones con pileta." },
];

/** "mejor match" al puesto 1; "mejor precio" al total más bajo entre las rankeadas (si no es el 1). */
function badges(report: FinalReport): Map<string, Badge> {
  const out = new Map<string, Badge>();
  const first = report.ranking[0];
  if (first) out.set(first.verdict.property_token, "match");
  const priced = report.ranking.filter((r) => r.verdict.price);
  if (priced.length > 1) {
    const cheapest = priced.reduce((a, b) => ((b.verdict.price?.total ?? Infinity) < (a.verdict.price?.total ?? Infinity) ? b : a));
    if (!out.has(cheapest.verdict.property_token)) out.set(cheapest.verdict.property_token, "price");
  }
  return out;
}

interface Discard {
  key: string;
  name: string;
  reason: string;
}

/** Descartados del reporte + analizados no recomendados que no estén ya en la lista. */
function discards(report: FinalReport): Discard[] {
  const out: Discard[] = report.discarded.map((d, i) => ({ key: `${d.property_token ?? d.name}-${i}`, name: d.name, reason: d.reason }));
  const seen = new Set(report.discarded.map((d) => (d.property_token ?? d.name).toLowerCase()));
  const ranked = new Set(report.ranking.map((r) => r.verdict.property_token));
  report.verdicts.forEach((v: PropertyVerdict) => {
    if (v.recommend || ranked.has(v.property_token)) return;
    if (seen.has(v.property_token.toLowerCase()) || seen.has(v.name.toLowerCase())) return;
    const violated = v.hard_constraints.find((c) => c.status === "violated");
    const reason = violated ? `No cumple "${violated.constraint}"${violated.evidence ? `: ${violated.evidence}` : "."}` : v.red_flags[0] ?? v.cons[0] ?? v.summary;
    out.push({ key: v.property_token, name: v.name, reason });
  });
  return out;
}

/** Rankeadas con coordenadas → marcadores numerados por puesto. */
function markersOf(report: FinalReport): MapMarker[] {
  const out: MapMarker[] = [];
  for (const r of report.ranking) {
    const f = r.verdict.facts;
    if (!f || f.latitude == null || f.longitude == null) continue;
    const p = r.verdict.price;
    out.push({
      id: r.verdict.property_token,
      lat: f.latitude,
      lng: f.longitude,
      label: String(r.rank),
      title: r.verdict.name,
      subtitle: p ? `${money(p.total, p.currency)} total vía ${p.source}` : "precio no disponible",
      inside: f.zone_inside,
    });
  }
  return out;
}

/** Descartadas por el filtro de zona (motivo "Fuera de {zona}: …"). */
function outsideZone(report: FinalReport): number {
  return report.discarded.filter((d) => /^fuera de\b/i.test(d.reason.trim())).length;
}

export default function Results({ report, partial = false, threadId, mock, canSend, onSend, onAdjust }: Props) {
  const titleId = useId();
  const b = report.brief;
  const dest = shortDestination(b.destination);
  const nights = b.nights ?? nightsBetween(b.check_in, b.check_out) ?? 0;
  const n = report.ranking.length;
  const badge = badges(report);
  const trav = travelersLabel(b.adults, b.children);
  const budget = budgetLabel(b);
  const musts = (b.hard_constraints ?? []).join(" · ");
  const s = report.stats;
  const discarded = discards(report);
  const zone = b.zone ?? null;
  const markers = markersOf(report);
  const outside = outsideZone(report);
  const showMap = n > 0 && (zone != null || markers.length > 0);

  const title = n === 0 ? "No encontramos opciones que cierren" : n === 1 ? `Una opción en ${dest}` : `Tus ${n} opciones en ${dest}`;

  return (
    <section className="results" aria-labelledby={titleId}>
      <header className="results__head">
        <div className="results__lead">
          {partial && <span className="kicker kicker--warn">Reporte parcial</span>}
          <h2 id={titleId} className="results__title">
            {title}
          </h2>
          <div className="chips chips--brief">
            <span className="chip chip--static">{dateRange(b.check_in, b.check_out)}</span>
            {trav && <span className="chip chip--static">{trav}</span>}
            {budget && <span className="chip chip--static">{budget}</span>}
            {musts && <span className="chip chip--static">{musts}</span>}
            <button type="button" className="chip chip--dashed" onClick={onAdjust} disabled={!canSend}>
              Ajustar
            </button>
          </div>
        </div>
        <p className="results__stats">
          De {s.candidates_found} alojamientos revisados analizamos {s.analyzed} y te contamos por qué.
        </p>
      </header>

      {n === 0 && <p className="results__summary">{report.summary}</p>}

      {showMap && (
        <figure className="results__map">
          <ZoneMap center={null} zone={zone} markers={markers} readOnly height={320} />
          <figcaption className="results__mapcap">
            {zone ? `Zona: ${zone.name ?? "marcada en el mapa"}${zone.buffer_m ? ` · tolerancia ${zone.buffer_m} m` : ""}` : "Ubicación de las opciones"}
            {markers.length < n && ` · ${plural(n - markers.length, "opción sin coordenadas", "opciones sin coordenadas")}`}
            {outside > 0 && ` · ${outside} ${outside === 1 ? "descartada" : "descartadas"} por estar fuera de la zona`}
          </figcaption>
        </figure>
      )}

      {n > 0 && (
        <div className="results__list">
          {report.ranking.slice(0, 3).map((r) => (
            <ResultCard
              key={r.verdict.property_token}
              verdict={r.verdict}
              rank={r.rank}
              rationale={r.rationale}
              badge={badge.get(r.verdict.property_token) ?? null}
              nights={nights}
              threadId={threadId}
              mock={mock}
            />
          ))}
          {n > 3 && (
            <div className="results__rows">
              {report.ranking.slice(3).map((r) => (
                <ResultRow key={r.verdict.property_token} verdict={r.verdict} rank={r.rank} rationale={r.rationale} nights={nights} threadId={threadId} mock={mock} />
              ))}
            </div>
          )}
        </div>
      )}

      <div className="refine">
        <span className="refine__text">{n === 0 ? "Contanos qué cambiar y volvemos a buscar." : "¿Ninguna cierra? Contanos qué le falta y volvemos a buscar."}</span>
        <div className="chips">
          {REFINE.map((r) => (
            <button key={r.label} type="button" className="chip" disabled={!canSend} onClick={() => onSend(r.text)}>
              {r.label}
            </button>
          ))}
        </div>
      </div>

      {discarded.length > 0 && (
        <details className="discarded">
          <summary className="discarded__summary">Descartados ({discarded.length})</summary>
          <ul className="discarded__list">
            {discarded.map((d) => (
              <li key={d.key}>
                <span className="discarded__name">{d.name}</span>
                <span className="discarded__reason">{d.reason}</span>
              </li>
            ))}
          </ul>
        </details>
      )}

      {report.caveats.length > 0 && (
        <div className="caveats">
          <span className="kicker">A tener en cuenta</span>
          <ul>
            {report.caveats.map((c, i) => (
              <li key={i}>{c}</li>
            ))}
          </ul>
        </div>
      )}
    </section>
  );
}
