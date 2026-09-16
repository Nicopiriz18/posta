import { useMemo } from "react";
import type { Phase, RunEvent, ToolName, Zone } from "../types";
import { clock, domainOf, plural, shortDestination, truncate } from "../format";
import { ToolIcon } from "./Timeline";

/** Contadores en vivo, derivados solo de los eventos de la búsqueda. */
export interface Progress {
  destination: string | null;
  zone: Zone | null; // zona dibujada en el brief de run_started
  phase: Phase | null;
  searches: number; // fuentes consultadas: tool_call search_hotels + web_search
  hotelSearches: number; // solo search_hotels
  webSearches: number; // solo web_search
  pagesRead: number; // tool_result fetch_page ok
  webCandidates: number; // tool_result add_web_candidate ok (nuevos o unificados)
  seen: number; // suma de "N candidatos" / "N nuevos" en los tool_result de search_hotels
  shortlist: number | null; // subagent_finished de hotel-discovery → data.shortlist
  analystsStarted: number;
  analystsDone: number;
  startedAt: number; // ms
  endedAt: number | null;
}

function firstNumber(s: string): number {
  const nuevos = s.match(/(\d+)\s+nuev/);
  if (nuevos) return Number(nuevos[1]);
  const m = s.match(/\d+/);
  return m ? Number(m[0]) : 0;
}

export function progressOf(events: RunEvent[]): Progress {
  const p: Progress = {
    destination: null,
    zone: null,
    phase: null,
    searches: 0,
    hotelSearches: 0,
    webSearches: 0,
    pagesRead: 0,
    webCandidates: 0,
    seen: 0,
    shortlist: null,
    analystsStarted: 0,
    analystsDone: 0,
    startedAt: NaN,
    endedAt: null,
  };
  for (const ev of events) {
    switch (ev.type) {
      case "run_started":
        p.destination = ev.data.brief?.destination ?? null;
        p.zone = ev.data.brief?.zone ?? null;
        p.startedAt = Date.parse(ev.ts);
        break;
      case "phase":
        p.phase = ev.data.phase;
        break;
      case "tool_call":
        if (ev.data.tool === "search_hotels") p.hotelSearches += 1;
        if (ev.data.tool === "web_search") p.webSearches += 1;
        break;
      case "tool_result":
        if (ev.data.tool === "search_hotels" && ev.data.ok) p.seen += firstNumber(ev.data.summary ?? "");
        if (ev.data.tool === "fetch_page" && ev.data.ok) p.pagesRead += 1;
        if (ev.data.tool === "add_web_candidate" && ev.data.ok) p.webCandidates += 1;
        break;
      case "subagent_started":
        if (ev.data.subagent === "property-analyst") p.analystsStarted += 1;
        break;
      case "subagent_finished":
        if (ev.data.subagent === "hotel-discovery" && typeof ev.data.shortlist === "number") p.shortlist = ev.data.shortlist;
        if (ev.data.subagent === "property-analyst") p.analystsDone += 1;
        break;
      case "run_finished":
        p.seen = ev.data.report.stats.candidates_found || p.seen;
        p.shortlist = ev.data.report.stats.shortlisted || p.shortlist;
        p.endedAt = Date.parse(ev.ts);
        break;
      case "run_failed":
        p.endedAt = Date.parse(ev.ts);
        break;
      default:
        break;
    }
  }
  p.searches = p.hotelSearches + p.webSearches;
  return p;
}

const PHASE_CHIP: Record<Phase, string> = {
  discovery: "Buscando candidatos",
  selection: "Eligiendo cuáles mirar a fondo",
  analysis: "Leyendo reseñas y comparando precios",
  consolidation: "Armando el ranking",
};

interface Line {
  key: string;
  text: string;
  tool: ToolName | null; // ícono propio para las herramientas web
}

/** Texto propio para las herramientas web; para el resto, el `message` humano del evento. */
function toolCallLine(ev: Extract<RunEvent, { type: "tool_call" }>): string | null {
  const args = ev.data.args ?? {};
  switch (ev.data.tool) {
    case "web_search": {
      const q = typeof args.query === "string" ? args.query : "";
      return q ? `Buscando en la web: “${truncate(q, 70)}”` : "Buscando en la web";
    }
    case "fetch_page": {
      const d = domainOf(typeof args.url === "string" ? args.url : null);
      return d ? `Leyendo ${d}` : "Leyendo una página";
    }
    case "add_web_candidate":
      return null; // la línea útil es el resultado ("nombre vía dominio" / "unificado con X")
    default:
      return ev.message || null;
  }
}

/** Últimas acciones legibles. */
function activity(events: RunEvent[]): Line[] {
  const lines: Line[] = [];
  events.forEach((ev, i) => {
    let text: string | null = null;
    let tool: ToolName | null = null;
    switch (ev.type) {
      case "phase":
      case "subagent_started":
      case "subagent_finished":
      case "message":
        text = ev.message || null;
        break;
      case "tool_call":
        text = toolCallLine(ev);
        tool = ev.data.tool;
        break;
      case "tool_result":
        if (!ev.data.ok) {
          text = `Falló: ${ev.data.error ?? ev.data.summary}`;
        } else if (ev.data.tool === "add_web_candidate") {
          text = `Candidato web: ${ev.data.summary || ev.message}`;
          tool = "add_web_candidate";
        }
        break;
      case "warning":
        text = ev.message || ev.data.error || null;
        break;
      case "verdict":
        text = `${ev.data.verdict.name}: ${ev.data.verdict.recommend ? "entra" : "queda afuera"}`;
        break;
      default:
        break;
    }
    if (text) lines.push({ key: `${i}`, text, tool });
  });
  return lines.slice(-3);
}

interface Props {
  events: RunEvent[];
  now: number;
}

export default function SearchingPanel({ events, now }: Props) {
  const p = useMemo(() => progressOf(events), [events]);
  const lines = useMemo(() => activity(events), [events]);
  const elapsed = Number.isNaN(p.startedAt) ? 0 : Math.max(0, ((p.endedAt ?? now) - p.startedAt) / 1000);
  const dest = p.destination ? shortDestination(p.destination) : "tu destino";
  const sourcesTitle = [p.hotelSearches > 0 ? plural(p.hotelSearches, "búsqueda en Google Hotels", "búsquedas en Google Hotels") : null, p.webSearches > 0 ? plural(p.webSearches, "búsqueda en la web", "búsquedas en la web") : null]
    .filter(Boolean)
    .join(" · ");

  return (
    <div className="searching" aria-live="polite">
      <div className="searching__head">
        <span className="searching__kicker">
          <span className="live-dot live-dot--light" aria-hidden="true" />
          Buscando · {clock(elapsed)}
        </span>
        <h2 className="searching__title">Estamos mirando {dest} casa por casa.</h2>
        <p className="searching__note">Esto tarda unos minutos. La búsqueda sigue en el servidor: podés volver desde Mis búsquedas.</p>
      </div>

      <div className="searching__counters">
        {p.searches > 0 && (
          <span className="counter" title={sourcesTitle || undefined}>
            {plural(p.searches, "fuente consultada", "fuentes consultadas")}
          </span>
        )}
        {p.seen > 0 && <span className="counter">{plural(p.seen, "alojamiento visto", "alojamientos vistos")}</span>}
        {p.shortlist != null && <span className="counter">{p.shortlist === 1 ? "1 pasa tus filtros" : `${p.shortlist} pasan tus filtros`}</span>}
        {p.analystsStarted > 0 ? (
          <span className="counter counter--accent">
            Analizando {p.analystsDone} de {p.analystsStarted}
          </span>
        ) : (
          <span className="counter counter--accent">{p.phase ? PHASE_CHIP[p.phase] : "Arrancando"}</span>
        )}
      </div>
      {p.zone && <p className="searching__zone">Filtrando por la zona {p.zone.name ?? "marcada en el mapa"}</p>}
      {p.pagesRead > 0 && (
        <p className="searching__pages">
          <ToolIcon tool="fetch_page" />
          {plural(p.pagesRead, "página leída", "páginas leídas")}
          {p.webCandidates > 0 && ` · ${plural(p.webCandidates, "candidato de la web", "candidatos de la web")}`}
        </p>
      )}

      <div className="searching__activity">
        {lines.length === 0 && (
          <div className="activity">
            <span className="activity__dot is-pulsing" aria-hidden="true" />
            Preparando la búsqueda
          </div>
        )}
        {lines.map((l, i) => (
          <div key={l.key} className={`activity${l.tool ? ` activity--${l.tool}` : ""}`}>
            <span className={`activity__dot${i === lines.length - 1 ? " is-pulsing" : ""}`} aria-hidden="true" />
            {l.tool && (l.tool === "web_search" || l.tool === "fetch_page" || l.tool === "add_web_candidate") && (
              <span className="activity__icon">
                <ToolIcon tool={l.tool} />
              </span>
            )}
            <span className="activity__text">{l.text}</span>
          </div>
        ))}
        <div className="sweep" aria-hidden="true">
          <span />
        </div>
      </div>
    </div>
  );
}
