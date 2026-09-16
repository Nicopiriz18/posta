import { useMemo, useState, type ReactNode } from "react";
import type { FinalReport, RunAssertions, RunEvent, RunFailedData, RunFinishedData, TurnDoneData, Zone } from "../types";
import { clock, shortDate } from "../format";
import Results from "./Results";
import SearchingPanel from "./SearchingPanel";
import Timeline from "./Timeline";

/* ---------- Modelo: mensajes + eventos → turnos → ítems del flujo ---------- */

export interface UserMessage {
  content: string;
  ts: string; // ISO
}

/** Un turno = un mensaje del usuario y todo lo que el agente emitió hasta `turn_done`. */
export interface Turn {
  user: UserMessage | null;
  events: RunEvent[];
  done: TurnDoneData | null;
}

/**
 * Los mensajes del usuario no viajan por el SSE: vienen de GET /api/threads/{id} (o del envío local).
 * Los eventos sí, y `turn_done` los delimita. El turno k corresponde al mensaje k del usuario.
 */
export function buildTurns(users: UserMessage[], events: RunEvent[]): Turn[] {
  const turns: Turn[] = [];
  let cur: Turn = { user: null, events: [], done: null };
  for (const ev of events) {
    cur.events.push(ev);
    if (ev.type === "turn_done") {
      cur.done = ev.data;
      turns.push(cur);
      cur = { user: null, events: [], done: null };
    }
  }
  if (cur.events.length) turns.push(cur);
  users.forEach((u, i) => {
    if (i < turns.length) turns[i].user = u;
    else turns.push({ user: u, events: [], done: null });
  });
  return turns;
}

/** Momento en que arrancó un turno, para el cronómetro. */
export function turnStartedAt(turn: Turn, fallback: number): number {
  const iso = turn.user?.ts ?? turn.events[0]?.ts;
  const t = iso ? Date.parse(iso) : NaN;
  return Number.isNaN(t) ? fallback : t;
}

type NoteLevel = "info" | "warning" | "error";

export interface ResearchItem {
  kind: "research";
  key: string;
  events: RunEvent[];
  finished: RunFinishedData | null;
  failed: RunFailedData | null;
  open: boolean;
}

type Item =
  | { kind: "user"; key: string; msg: UserMessage }
  | { kind: "assistant"; key: string; text: string; ts: string; suggestions: string[] | null }
  | ResearchItem
  | { kind: "note"; key: string; level: NoteLevel; title: string; text: string };

function buildItems(turns: Turn[]): Item[] {
  const items: Item[] = [];
  turns.forEach((turn, t) => {
    if (turn.user) items.push({ kind: "user", key: `u${t}`, msg: turn.user });
    let research: ResearchItem | null = null;
    let pendingSuggestions: string[] | null = null;
    for (let i = 0; i < turn.events.length; i++) {
      const ev = turn.events[i];
      const key = `t${t}e${i}`;
      switch (ev.type) {
        case "suggestions":
          pendingSuggestions = ev.data.options?.filter((o) => typeof o === "string" && o.trim()) ?? null;
          break;
        case "assistant": {
          const item: Item = { kind: "assistant", key, text: ev.message, ts: ev.ts, suggestions: pendingSuggestions };
          pendingSuggestions = null;
          // El "arranco" llega justo después de run_started: va antes del bloque, no adentro.
          const lastItem = items[items.length - 1];
          if (research && research.open && research.events.length <= 1 && lastItem === research) {
            items.splice(items.length - 1, 0, item);
          } else {
            items.push(item);
          }
          break;
        }
        case "run_started":
          research = { kind: "research", key, events: [ev], finished: null, failed: null, open: true };
          items.push(research);
          break;
        case "run_finished":
          if (research) {
            research.events.push(ev);
            research.finished = ev.data;
            research.open = false;
            research = null;
          } else {
            items.push({ kind: "research", key, events: [ev], finished: ev.data, failed: null, open: false });
          }
          break;
        case "run_failed":
          if (research) {
            research.events.push(ev);
            research.failed = ev.data;
            research.open = false;
            research = null;
          } else {
            items.push({ kind: "note", key, level: "error", title: "La búsqueda falló", text: ev.data.error || ev.message });
          }
          break;
        case "turn_done":
          if (research) {
            research.open = false; // el turno terminó sin run_finished: quedó a medias
            research = null;
          }
          if (ev.data.status !== "ok") {
            items.push({
              kind: "note",
              key,
              level: "error",
              title: ev.data.status === "timeout" ? "Se cortó por tiempo" : "No pudimos terminar",
              text: ev.data.error || ev.message || "Podés volver a intentarlo con otro mensaje.",
            });
          }
          break;
        case "warning":
          if (research) research.events.push(ev);
          else items.push({ kind: "note", key, level: "warning", title: "Aviso", text: ev.message || ev.data.error || "" });
          break;
        case "message":
          if (research) research.events.push(ev);
          else if (ev.message) items.push({ kind: "note", key, level: "info", title: "", text: ev.message });
          break;
        case "brief_saved":
          break; // va al panel
        default:
          // tool_call, tool_result, subagentes, veredictos, telemetría: solo tienen sentido dentro de una búsqueda.
          if (research) research.events.push(ev);
      }
    }
  });
  // Las respuestas rápidas solo valen para la última pregunta del agente: cualquier mensaje posterior las borra.
  const lastAssistant = [...items].reverse().find((it) => it.kind === "assistant");
  for (const it of items) {
    if (it.kind === "assistant" && it !== lastAssistant) it.suggestions = null;
  }
  const last = items[items.length - 1];
  if (lastAssistant && lastAssistant.kind === "assistant" && last !== lastAssistant) lastAssistant.suggestions = null;
  return items;
}

export type Stage = "interview" | "searching" | "results";

/** En qué etapa está el hilo, según la última búsqueda que aparece en el flujo. */
export function stageOf(turns: Turn[]): { stage: Stage; report: FinalReport | null } {
  const items = buildItems(turns);
  for (let i = items.length - 1; i >= 0; i--) {
    const it = items[i];
    if (it.kind !== "research") continue;
    if (it.open) return { stage: "searching", report: null };
    const report = it.finished?.report ?? it.failed?.partial_report ?? null;
    return { stage: report ? "results" : "interview", report };
  }
  return { stage: "interview", report: null };
}

/* ---------- Presentación ---------- */

function Note({ level, title, text }: { level: NoteLevel; title: string; text: string }) {
  return (
    <div className={`note note--${level}`} role={level === "error" ? "alert" : "note"}>
      {title && <span className="note__title">{title}</span>}
      <span className="note__text">{text}</span>
    </div>
  );
}

/** En modo headless el "mensaje" del usuario es el brief en JSON: se muestra como nota, no como texto. */
function briefFromJson(content: string): { destination: string; check_in?: string; check_out?: string } | null {
  const s = content.trim();
  if (!s.startsWith("{")) return null;
  try {
    const o = JSON.parse(s) as Record<string, unknown>;
    if (typeof o.destination === "string") {
      return {
        destination: o.destination,
        check_in: typeof o.check_in === "string" ? o.check_in : undefined,
        check_out: typeof o.check_out === "string" ? o.check_out : undefined,
      };
    }
  } catch {
    /* no era JSON */
  }
  return null;
}

function UserMsg({ msg }: { msg: UserMessage }) {
  const brief = briefFromJson(msg.content);
  if (brief) {
    return (
      <div className="note note--info">
        <span className="note__text">
          Búsqueda directa, sin conversación: {brief.destination}
          {brief.check_in && brief.check_out ? `, ${shortDate(brief.check_in)} a ${shortDate(brief.check_out)}` : ""}.
        </span>
      </div>
    );
  }
  return (
    <div className="bubble bubble--user">
      <p className="bubble__text">{msg.content}</p>
    </div>
  );
}

function Avatar() {
  return (
    <span className="avatar" aria-hidden="true">
      p
    </span>
  );
}

/** Paso previo a la búsqueda: marcar la zona en el mapa. Va bajo el último mensaje del agente y como chip junto a las respuestas rápidas. */
export interface ZoneAction {
  zone: Zone | null;
  onOpen: () => void;
}

function ZoneCallout({ action }: { action: ZoneAction }) {
  if (action.zone) {
    return (
      <p className="zone-callout zone-callout--set">
        Zona: <strong>{action.zone.name ?? "marcada en el mapa"}</strong> ·{" "}
        <button type="button" className="link-btn" onClick={action.onOpen}>
          Editar en el mapa
        </button>
      </p>
    );
  }
  return (
    <div className="zone-callout">
      <span className="zone-callout__text">Antes de buscar, podés marcar en el mapa la zona donde preferís quedarte.</span>
      <button type="button" className="btn btn--outline btn--sm" onClick={action.onOpen}>
        Dibujar zona en el mapa
      </button>
    </div>
  );
}

function AssistantMsg({
  text,
  suggestions,
  canSend,
  onSend,
  zoneAction,
}: {
  text: string;
  suggestions: string[] | null;
  canSend: boolean;
  onSend: (t: string) => void;
  zoneAction: ZoneAction | null;
}) {
  const showZoneChip = zoneAction != null && !zoneAction.zone; // con zona ya marcada alcanza la línea "Zona: … · Editar"
  const hasChips = (suggestions && suggestions.length > 0) || showZoneChip;
  return (
    <div className="agent">
      <Avatar />
      <div className="agent__col">
        <div className="bubble bubble--agent">
          <p className="bubble__text">{text}</p>
        </div>
        {zoneAction && <ZoneCallout action={zoneAction} />}
        {hasChips && (
          <div className="chips chips--replies" role="group" aria-label="Respuestas rápidas">
            {(suggestions ?? []).map((s) => (
              <button key={s} type="button" className="chip" disabled={!canSend} onClick={() => onSend(s)}>
                {s}
              </button>
            ))}
            {showZoneChip && (
              <button type="button" className="chip chip--dashed chip--map" onClick={zoneAction.onOpen}>
                Marcar zona en el mapa
              </button>
            )}
          </div>
        )}
      </div>
    </div>
  );
}

function failedChecks(a: RunAssertions): string {
  const bad = Object.entries(a.checks)
    .filter(([, v]) => v === false || (v && typeof v === "object" && (v as { ok?: unknown }).ok === false))
    .map(([k]) => k);
  return bad.length
    ? `No pasaron: ${bad.join(", ")}. Mostramos el reporte igual; tomalo con cuidado.`
    : "Alguna verificación automática no pasó. Mostramos el reporte igual; tomalo con cuidado.";
}

interface ResearchProps {
  item: ResearchItem;
  now: number;
  threadId: string;
  mock: boolean;
  canSend: boolean;
  onSend: (text: string) => void;
  onAdjust: () => void;
}

function ResearchBlock({ item, now, threadId, mock, canSend, onSend, onAdjust }: ResearchProps) {
  const [detail, setDetail] = useState(false);
  const report = item.finished?.report ?? item.failed?.partial_report ?? null;
  const partial = !item.finished && item.failed?.partial_report != null;

  return (
    <section className={`research${item.open ? " is-live" : ""}`} aria-label="Búsqueda">
      {item.open && <SearchingPanel events={item.events} now={now} />}

      {item.failed && <Note level="error" title="La búsqueda falló" text={item.failed.error} />}
      {!item.open && !item.finished && !item.failed && (
        <Note level="warning" title="Sin resultados" text="La búsqueda terminó antes de publicar el reporte. Podés pedir que lo intente de nuevo." />
      )}

      {report && <Results report={report} partial={partial} threadId={threadId} mock={mock} canSend={canSend} onSend={onSend} onAdjust={onAdjust} />}
      {item.finished?.assertions && !item.finished.assertions.ok && (
        <Note level="warning" title="Verificaciones automáticas" text={failedChecks(item.finished.assertions)} />
      )}

      <div className="research__detail">
        <button type="button" className="link-btn" aria-expanded={detail} onClick={() => setDetail((v) => !v)}>
          {detail ? "Ocultar detalle" : "Ver detalle"}
          <span className="link-btn__sub"> · {item.open ? "qué está haciendo el agente" : "paso a paso de la búsqueda"}</span>
        </button>
        {detail && <Timeline events={item.events} live={item.open} telemetry={report != null} />}
      </div>
    </section>
  );
}

interface Props {
  turns: Turn[];
  running: boolean;
  now: number;
  threadId: string;
  mock: boolean;
  canSend: boolean;
  onSend: (text: string) => void;
  onAdjust: () => void;
  /** Se muestra cuando no hay turnos. */
  empty: ReactNode;
  /** Antes de confirmar: invitación a marcar la zona en el mapa (null = no corresponde). */
  zoneAction?: ZoneAction | null;
}

export default function Conversation({ turns, running, now, threadId, mock, canSend, onSend, onAdjust, empty, zoneAction = null }: Props) {
  const items = useMemo(() => buildItems(turns), [turns]);
  const last = items[items.length - 1];
  const lastAssistantKey = [...items].reverse().find((it) => it.kind === "assistant")?.key ?? null;
  const thinking = running && !(last && last.kind === "research" && last.open);
  const openTurn = turns[turns.length - 1];
  const thinkingSince = openTurn ? turnStartedAt(openTurn, now) : now;

  if (!items.length && !running) return <div className="flow">{empty}</div>;

  return (
    <div className="flow">
      {items.map((it) => {
        switch (it.kind) {
          case "user":
            return <UserMsg key={it.key} msg={it.msg} />;
          case "assistant":
            return <AssistantMsg key={it.key} text={it.text} suggestions={it.suggestions} canSend={canSend} onSend={onSend} zoneAction={it.key === lastAssistantKey ? zoneAction : null} />;
          case "research":
            return <ResearchBlock key={it.key} item={it} now={now} threadId={threadId} mock={mock} canSend={canSend} onSend={onSend} onAdjust={onAdjust} />;
          case "note":
            return <Note key={it.key} level={it.level} title={it.title} text={it.text} />;
        }
      })}
      {thinking && (
        <div className="agent agent--thinking" aria-live="polite">
          <Avatar />
          <div className="bubble bubble--agent bubble--thinking">
            <span className="typing" aria-hidden="true">
              <i />
              <i />
              <i />
            </span>
            <span className="bubble__clock">{clock((now - thinkingSince) / 1000)}</span>
          </div>
        </div>
      )}
    </div>
  );
}
