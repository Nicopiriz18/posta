import { useMemo } from "react";
import type { Phase, RunEvent, SubagentName, ToolName, ToolResultData, WebSearchResult } from "../types";
import { domainOf, hhmm, truncate } from "../format";

export const PHASES: Phase[] = ["discovery", "selection", "analysis", "consolidation"];

export const PHASE_LABEL: Record<Phase, string> = {
  discovery: "Descubrimiento",
  selection: "Selección",
  analysis: "Análisis",
  consolidation: "Consolidación",
};

const TOOL_LABEL: Record<ToolName, string> = {
  search_hotels: "búsqueda",
  get_property_details: "ficha",
  get_property_reviews: "reseñas",
  web_search: "Buscando en la web",
  fetch_page: "Leyendo",
  add_web_candidate: "Candidato web",
};

const AGENT_LABEL: Record<string, string> = {
  orchestrator: "agente",
  "hotel-discovery": "discovery",
  "property-analyst": "analista",
  system: "sistema",
};

const SUBAGENT_LABEL: Record<SubagentName, string> = {
  "hotel-discovery": "discovery",
  "property-analyst": "analista",
};

const WEB_TOOLS: ReadonlySet<ToolName> = new Set<ToolName>(["web_search", "fetch_page", "add_web_candidate"]);

/** Ícono de 14px en `currentColor` para las herramientas web (lupa-globo, página, pin). Otras herramientas: nada. */
export function ToolIcon({ tool }: { tool: ToolName }) {
  const common = { width: 14, height: 14, viewBox: "0 0 16 16", fill: "none", stroke: "currentColor", strokeWidth: 1.5, strokeLinecap: "round" as const, strokeLinejoin: "round" as const, "aria-hidden": true, className: "tool-icon" };
  switch (tool) {
    case "web_search":
      return (
        <svg {...common}>
          <circle cx="8" cy="8" r="6" />
          <path d="M2 8h12M8 2c2 2.2 2 9.8 0 12M8 2c-2 2.2-2 9.8 0 12" />
        </svg>
      );
    case "fetch_page":
      return (
        <svg {...common}>
          <path d="M4 1.5h5.5L13 5v9.5H4z" />
          <path d="M9.5 1.5V5H13M6 8h4M6 10.5h4" />
        </svg>
      );
    case "add_web_candidate":
      return (
        <svg {...common}>
          <path d="M8 14.5s-4.5-4.3-4.5-8a4.5 4.5 0 0 1 9 0c0 3.7-4.5 8-4.5 8z" />
          <path d="M8 4.5v4M6 6.5h4" />
        </svg>
      );
    default:
      return null;
  }
}

type Entry =
  | { kind: "phase"; key: string; ts: string; phase: Phase; message: string }
  | { kind: "tool"; key: string; ts: string; agent: string; tool: ToolName; args: Record<string, unknown>; message: string; result: ToolResultData | null }
  | { kind: "subagent"; key: string; ts: string; subagent: SubagentName; property_name?: string; message: string; finished: boolean }
  | { kind: "verdict"; key: string; ts: string; name: string; recommend: boolean; fit: number; summary: string }
  | { kind: "model"; key: string; ts: string; agent: string; model: string; seconds: number | null; tokens: string }
  | { kind: "note"; key: string; ts: string; agent: string; level: "info" | "warning" | "error" | "start" | "end"; message: string };

function str(v: unknown): string {
  return typeof v === "string" ? v : "";
}

/** Etiqueta en negrita de la fila: para fetch_page lleva el dominio ("Leyendo ventosul.com.br"). */
function toolTitle(tool: ToolName, args: Record<string, unknown>): string {
  if (tool === "fetch_page") {
    const d = domainOf(str(args.url));
    return d ? `${TOOL_LABEL.fetch_page} ${d}` : `${TOOL_LABEL.fetch_page} una página`;
  }
  return TOOL_LABEL[tool] ?? tool;
}

function argsSummary(tool: ToolName, args: Record<string, unknown>): string {
  switch (tool) {
    case "search_hotels":
      return str(args.q) || str(args.query);
    case "web_search":
      return str(args.query) ? `“${truncate(str(args.query), 90)}”` : "";
    case "fetch_page": {
      // El dominio ya está en el título; acá va el resto de la ruta, recortado.
      const url = str(args.url);
      try {
        const u = new URL(url);
        const path = `${u.pathname}${u.search}`.replace(/^\/$/, "");
        return path ? truncate(path, 60) : "";
      } catch {
        return "";
      }
    }
    case "add_web_candidate":
      return str(args.name) || domainOf(str(args.url)) || "";
    default: {
      const token = str(args.property_token);
      return token ? `…${token.slice(-6)}` : "";
    }
  }
}

function tokensLabel(input: number | null, output: number | null): string {
  const parts: string[] = [];
  if (input != null) parts.push(`${input} entrada`);
  if (output != null) parts.push(`${output} salida`);
  return parts.length ? `${parts.join(", ")} tokens` : "";
}

function reduce(events: RunEvent[]): Entry[] {
  const entries: Entry[] = [];
  const pendingTools = new Map<string, number[]>(); // "agent_id|tool" → índices en entries
  const subagentIdx = new Map<string, number>();

  events.forEach((ev, i) => {
    const key = `${i}-${ev.type}`;
    switch (ev.type) {
      case "phase":
        entries.push({ kind: "phase", key, ts: ev.ts, phase: ev.data.phase, message: ev.message });
        break;
      case "tool_call": {
        const idx = entries.length;
        entries.push({ kind: "tool", key, ts: ev.ts, agent: ev.agent, tool: ev.data.tool, args: ev.data.args ?? {}, message: ev.message, result: null });
        const k = `${ev.data.agent_id ?? ev.agent}|${ev.data.tool}`;
        pendingTools.set(k, [...(pendingTools.get(k) ?? []), idx]);
        break;
      }
      case "tool_result": {
        const k = `${ev.data.agent_id ?? ev.agent}|${ev.data.tool}`;
        const idx = pendingTools.get(k)?.shift();
        if (idx != null) {
          const e = entries[idx];
          if (e.kind === "tool") e.result = ev.data;
        } else {
          entries.push({ kind: "note", key, ts: ev.ts, agent: ev.agent, level: ev.data.ok ? "info" : "warning", message: ev.data.summary || ev.message });
        }
        break;
      }
      case "subagent_started":
        subagentIdx.set(ev.data.agent_id, entries.length);
        entries.push({ kind: "subagent", key, ts: ev.ts, subagent: ev.data.subagent, property_name: ev.data.property_name, message: ev.message, finished: false });
        break;
      case "subagent_finished": {
        const idx = subagentIdx.get(ev.data.agent_id);
        if (idx != null) {
          const e = entries[idx];
          if (e.kind === "subagent") e.finished = true;
        }
        entries.push({ kind: "note", key, ts: ev.ts, agent: ev.agent, level: "info", message: ev.message || `${SUBAGENT_LABEL[ev.data.subagent]} terminó` });
        break;
      }
      case "verdict":
        entries.push({ kind: "verdict", key, ts: ev.ts, name: ev.data.verdict.name, recommend: ev.data.verdict.recommend, fit: ev.data.verdict.fit_score, summary: ev.data.verdict.summary });
        break;
      case "model":
        entries.push({ kind: "model", key, ts: ev.ts, agent: ev.agent, model: ev.data.model, seconds: ev.data.seconds, tokens: tokensLabel(ev.data.input_tokens, ev.data.output_tokens) });
        break;
      case "run_started":
        entries.push({ kind: "note", key, ts: ev.ts, agent: ev.agent, level: "start", message: ev.message || "Búsqueda iniciada" });
        break;
      case "run_finished":
        entries.push({ kind: "note", key, ts: ev.ts, agent: ev.agent, level: "end", message: ev.message || "Reporte listo" });
        break;
      case "run_failed":
        entries.push({ kind: "note", key, ts: ev.ts, agent: ev.agent, level: "error", message: ev.message || ev.data.error });
        break;
      case "warning":
        entries.push({ kind: "note", key, ts: ev.ts, agent: ev.agent, level: "warning", message: ev.message || ev.data.error || "Aviso" });
        break;
      case "message":
        entries.push({ kind: "note", key, ts: ev.ts, agent: ev.agent, level: "info", message: ev.message });
        break;
      case "assistant":
      case "suggestions":
      case "brief_saved":
      case "turn_done":
        break; // van a la conversación / al panel
    }
  });
  return entries;
}

export function currentPhase(events: RunEvent[]): Phase | null {
  for (let i = events.length - 1; i >= 0; i--) {
    const ev = events[i];
    if (ev.type === "phase") return ev.data.phase;
  }
  return null;
}

const MAX_WEB_LINKS = 5;

/** Resultados de `web_search` como enlaces chicos (título → url), debajo de la fila. */
function WebLinks({ results }: { results: WebSearchResult[] }) {
  const shown = results.slice(0, MAX_WEB_LINKS);
  if (!shown.length) return null;
  return (
    <ul className="tl__links">
      {shown.map((r, i) => (
        <li key={`${r.url}-${i}`}>
          <a href={r.url} target="_blank" rel="noreferrer noopener" title={r.url}>
            {truncate(r.title || r.url, 80)}
          </a>
          <span className="tl__muted"> · {domainOf(r.url) ?? r.url}</span>
        </li>
      ))}
      {results.length > shown.length && <li className="tl__muted">y {results.length - shown.length} más</li>}
    </ul>
  );
}

interface Props {
  events: RunEvent[];
  live: boolean;
  /** Mostrar filas de telemetría del modelo (`model`). */
  telemetry?: boolean;
}

export default function Timeline({ events, live, telemetry = false }: Props) {
  const entries = useMemo(() => {
    const all = reduce(events);
    return telemetry ? all : all.filter((e) => e.kind !== "model");
  }, [events, telemetry]);

  if (!entries.length) {
    return <p className="tl-empty">{live ? "Esperando los primeros eventos…" : "Sin eventos registrados."}</p>;
  }

  return (
    <ol className="tl" aria-live={live ? "polite" : "off"}>
      {entries.map((e) => {
        switch (e.kind) {
          case "phase":
            return (
              <li key={e.key} className="tl__phase">
                <time className="tl__time" dateTime={e.ts}>
                  {hhmm(e.ts)}
                </time>
                <span className="tl__phase-name">{PHASE_LABEL[e.phase]}</span>
                {e.message && <span className="tl__muted">{e.message}</span>}
              </li>
            );
          case "tool": {
            const web = WEB_TOOLS.has(e.tool);
            const args = argsSummary(e.tool, e.args);
            const links = e.tool === "web_search" && e.result?.ok ? e.result.results ?? [] : [];
            return (
              <li key={e.key} className={`tl__row${web ? ` tl__row--web tl__row--${e.tool}` : ""}${e.result ? (e.result.ok ? " is-ok" : " is-err") : " is-pending"}`}>
                <time className="tl__time" dateTime={e.ts}>
                  {hhmm(e.ts)}
                </time>
                <span className="tl__agent">{AGENT_LABEL[e.agent] ?? e.agent}</span>
                <span className="tl__text">
                  {web && (
                    <span className="tl__icon">
                      <ToolIcon tool={e.tool} />
                    </span>
                  )}
                  <strong>{toolTitle(e.tool, e.args)}</strong>
                  {args && <span className="tl__muted"> {args}</span>}
                  <span className="tl__result"> — {e.result ? (e.result.ok ? e.result.summary : `falló: ${e.result.error ?? e.result.summary}`) : e.message || "en curso"}</span>
                  {links.length > 0 && <WebLinks results={links} />}
                </span>
              </li>
            );
          }
          case "subagent":
            return (
              <li key={e.key} className={`tl__row${e.finished ? " is-ok" : " is-pending"}`}>
                <time className="tl__time" dateTime={e.ts}>
                  {hhmm(e.ts)}
                </time>
                <span className="tl__agent">{SUBAGENT_LABEL[e.subagent]}</span>
                <span className="tl__text">
                  {e.property_name ?? e.message}
                  {e.finished ? " — listo" : " …"}
                </span>
              </li>
            );
          case "verdict":
            return (
              <li key={e.key} className="tl__row is-ok">
                <time className="tl__time" dateTime={e.ts}>
                  {hhmm(e.ts)}
                </time>
                <span className="tl__agent">veredicto</span>
                <span className="tl__text">
                  <strong>{e.name}</strong>
                  <span className={e.recommend ? " tl__ok" : " tl__bad"}> — {e.recommend ? "entra" : "queda afuera"}</span>
                  <span className="tl__muted"> · ajuste {e.fit}/100</span>
                  {e.summary && <span className="tl__muted"> · {e.summary}</span>}
                </span>
              </li>
            );
          case "model":
            return (
              <li key={e.key} className="tl__row tl__row--model is-ok">
                <time className="tl__time" dateTime={e.ts}>
                  {hhmm(e.ts)}
                </time>
                <span className="tl__agent">{AGENT_LABEL[e.agent] ?? e.agent}</span>
                <span className="tl__text tl__muted">
                  {e.model}
                  {e.seconds != null ? ` · ${e.seconds.toFixed(1)} s` : ""}
                  {e.tokens ? ` · ${e.tokens}` : ""}
                </span>
              </li>
            );
          case "note":
            return (
              <li key={e.key} className={`tl__row tl__row--${e.level}`}>
                <time className="tl__time" dateTime={e.ts}>
                  {hhmm(e.ts)}
                </time>
                <span className="tl__agent">{AGENT_LABEL[e.agent] ?? e.agent}</span>
                <span className="tl__text">{e.message}</span>
              </li>
            );
        }
      })}
    </ol>
  );
}
