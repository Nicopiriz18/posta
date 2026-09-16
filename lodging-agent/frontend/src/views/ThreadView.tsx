import { useEffect, useMemo, useRef, useState, type FormEvent, type KeyboardEvent } from "react";
import { ApiError, createRun, deleteThread, errorMessage, getThread, openThreadEvents, putZone, sendMessage } from "../api";
import { href, navigate } from "../App";
import BriefPanel, { GROUPS, filledGroups } from "../components/BriefPanel";
import Conversation, { buildTurns, stageOf, turnStartedAt, type UserMessage, type ZoneAction } from "../components/Conversation";
import DeleteAction from "../components/DeleteAction";
import Ficha from "../components/Ficha";
import ZoneEditor, { zoneSummary } from "../components/ZoneEditor";
import { clock, dateRange, shortDestination } from "../format";
import { sampleConversation, sampleReport } from "../mock/sampleReport";
import { briefToDraft, draftToBrief, emptyDraft, nightsBetween, type BriefDraft, type FinalReport, type PropertyVerdict, type RunEvent, type Zone } from "../types";

interface Props {
  threadId: string;
  /** Token de la ficha abierta (`#/t/:id/p/:token`), si hay. */
  property: string | null;
  /** `#/t/:id?draw=1`: abrir el editor de zona al entrar (desde "Marcar la zona en el mapa primero"). */
  draw?: boolean;
  mock: boolean;
  blocked: string | null; // claves faltantes / backend caído
}

type Conn = "idle" | "open" | "reconnecting" | "closed";

const ADJUST_PREFILL = "Quiero ajustar: ";
const DEMO_NOTE = "Modo demostración: la conversación se reproduce sola.";

function hasContent(d: BriefDraft): boolean {
  return Object.values(d).some((v) => (Array.isArray(v) ? v.length > 0 : v != null && v !== ""));
}

function prefersReducedMotion(): boolean {
  return typeof window.matchMedia === "function" && window.matchMedia("(prefers-reduced-motion: reduce)").matches;
}

function useNarrow(): boolean {
  const [narrow, setNarrow] = useState(() => typeof window.matchMedia === "function" && window.matchMedia("(max-width: 900px)").matches);
  useEffect(() => {
    if (typeof window.matchMedia !== "function") return;
    const mq = window.matchMedia("(max-width: 900px)");
    const on = () => setNarrow(mq.matches);
    mq.addEventListener("change", on);
    return () => mq.removeEventListener("change", on);
  }, []);
  return narrow;
}

function findVerdict(report: FinalReport | null, events: RunEvent[], token: string): PropertyVerdict | null {
  if (report) {
    const ranked = report.ranking.find((r) => r.verdict.property_token === token);
    if (ranked) return ranked.verdict;
    const v = report.verdicts.find((x) => x.property_token === token);
    if (v) return v;
  }
  for (let i = events.length - 1; i >= 0; i--) {
    const ev = events[i];
    if (ev.type === "verdict" && ev.data.verdict.property_token === token) return ev.data.verdict;
  }
  return null;
}

export default function ThreadView({ threadId, property, draw = false, mock, blocked }: Props) {
  const [loaded, setLoaded] = useState(mock);
  const [mapOpen, setMapOpen] = useState(draw);
  const [zoneSaved, setZoneSaved] = useState<Zone | null>(null); // confirmación compacta en el flujo tras "Guardar zona"
  const [loadError, setLoadError] = useState<string | null>(null);
  const [users, setUsers] = useState<UserMessage[]>([]);
  const [events, setEvents] = useState<RunEvent[]>([]);
  const [archived, setArchived] = useState(false);
  const [serverRunning, setServerRunning] = useState(false); // GET dijo "running" y todavía no llegó el replay
  const [conn, setConn] = useState<Conn>("idle");
  const [detailReport, setDetailReport] = useState<FinalReport | null>(mock ? sampleReport : null); // en demo la ficha abre aunque el guion no haya llegado al reporte

  const [draft, setDraft] = useState<BriefDraft>(emptyDraft);
  const [missing, setMissing] = useState<string[]>([]);
  const [confirmed, setConfirmed] = useState(false);

  const [input, setInput] = useState("");
  const [sending, setSending] = useState(false);
  const [sendError, setSendError] = useState<string | null>(null);
  const [hint, setHint] = useState<string | null>(null);
  const [launching, setLaunching] = useState(false);
  const [launchError, setLaunchError] = useState<string | null>(null);
  const [deleteError, setDeleteError] = useState<string | null>(null);
  const [now, setNow] = useState(() => Date.now());

  const closeRef = useRef<(() => void) | null>(null);
  const inputRef = useRef<HTMLTextAreaElement>(null);
  const stickRef = useRef(true); // el usuario está al pie: seguimos el flujo
  const wasRunning = useRef(false);
  const narrow = useNarrow();

  const turns = useMemo(() => buildTurns(users, events), [users, events]);
  const lastTurn = turns[turns.length - 1];
  const running = sending || (lastTurn ? lastTurn.done === null : serverRunning);
  const { stage, report: liveReport } = useMemo(() => stageOf(turns), [turns]);
  const report = liveReport ?? detailReport;

  const handleEvent = (ev: RunEvent) => {
    setEvents((prev) => [...prev, ev]);
    if (ev.type === "brief_saved") {
      const d = ev.data;
      if (d.brief) setDraft(briefToDraft(d.brief));
      else if (d.draft) setDraft(d.draft);
      setMissing(d.missing ?? []);
      if (d.confirmed != null) setConfirmed(d.confirmed);
    } else if (ev.type === "run_started") {
      setDraft(briefToDraft(ev.data.brief));
      setMissing([]);
      setConfirmed(true);
    } else if (ev.type === "run_finished") {
      setDetailReport(ev.data.report);
    } else if (ev.type === "turn_done") {
      setServerRunning(false);
    }
  };

  // Carga inicial: GET para mensajes/brief/estado, después UN SSE (replay + vivo) que queda abierto hasta desmontar.
  useEffect(() => {
    if (mock) return;
    let cancelled = false;
    getThread(threadId)
      .then((d) => {
        if (cancelled) return;
        setUsers(d.messages.filter((m) => m.role === "user").map((m) => ({ content: m.content, ts: m.ts })));
        if (d.brief) setDraft(briefToDraft(d.brief));
        else setDraft(hasContent(d.draft) ? d.draft : emptyDraft());
        setConfirmed(d.confirmed);
        setArchived(d.status === "archived");
        setServerRunning(d.status === "running");
        setDetailReport(d.report);
        setLoaded(true);
        closeRef.current = openThreadEvents(threadId, {
          onEvent: handleEvent,
          onOpen: (reconnected) => {
            setConn("open");
            if (reconnected) setEvents([]); // el backend vuelve a mandar todo el historial
          },
          onDone: () => setConn("closed"),
          onError: () => setConn("reconnecting"),
        });
      })
      .catch((e) => {
        if (!cancelled) setLoadError(errorMessage(e));
      });
    return () => {
      cancelled = true;
      closeRef.current?.();
      closeRef.current = null;
    };
  }, [threadId, mock]);

  // Modo demostración: reproduce la conversación guionada, con pausas donde hay algo que mirar.
  useEffect(() => {
    if (!mock) return;
    const steps = sampleConversation(sampleReport);
    let i = 0;
    let timer = 0;
    let turns = 0;
    const next = () => {
      if (i >= steps.length) return;
      const step = steps[i++];
      const ts = new Date().toISOString();
      if ("user" in step) {
        setUsers((prev) => [...prev, { content: step.user, ts }]);
        setMapOpen(false); // el usuario sigue la conversación: la tarjeta se cierra
        timer = window.setTimeout(next, 1100);
        return;
      }
      handleEvent({ ...step, ts } as RunEvent);
      let pause =
        step.type === "assistant" ? 2600 : step.type === "turn_done" ? 1200 : step.type === "run_finished" ? 700 : step.type === "verdict" || step.type === "phase" ? 900 : 260;
      if (step.type === "turn_done" && ++turns === 2) {
        setMapOpen(true); // paso previo a la búsqueda: el mapa aparece en el chat y el guion espera un rato
        pause = 9000;
      }
      timer = window.setTimeout(next, pause);
    };
    timer = window.setTimeout(next, 500);
    return () => window.clearTimeout(timer);
  }, [mock, threadId]);

  // Reloj mientras el agente trabaja
  useEffect(() => {
    if (!running) return;
    setNow(Date.now());
    const id = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(id);
  }, [running]);

  // Seguir el flujo si el usuario está al pie; devolver el foco al input cuando el agente termina.
  useEffect(() => {
    const onScroll = () => {
      stickRef.current = window.innerHeight + window.scrollY >= document.documentElement.scrollHeight - 200;
    };
    window.addEventListener("scroll", onScroll, { passive: true });
    return () => window.removeEventListener("scroll", onScroll);
  }, []);
  useEffect(() => {
    if (property) return;
    if (running && stickRef.current) {
      window.scrollTo({ top: document.documentElement.scrollHeight, behavior: prefersReducedMotion() ? "auto" : "smooth" });
    }
  }, [events.length, users.length, running, property]);
  useEffect(() => {
    if (wasRunning.current && !running && !archived && !mock && !property) inputRef.current?.focus();
    wasRunning.current = running;
  }, [running, archived, mock, property]);

  const canChat = loaded && !archived && !mock && !blocked;
  const canSend = canChat && !running;

  const send = async (text: string) => {
    const content = text.trim();
    if (!content || running) return;
    if (mock) {
      setHint(DEMO_NOTE);
      return;
    }
    if (!canChat) return;
    const optimistic: UserMessage = { content, ts: new Date().toISOString() };
    setUsers((prev) => [...prev, optimistic]);
    setInput("");
    setSendError(null);
    setSending(true);
    stickRef.current = true;
    try {
      await sendMessage(threadId, content);
    } catch (e) {
      setSendError(errorMessage(e));
      setUsers((prev) => prev.filter((u) => u !== optimistic)); // deshacer para que pueda reintentar
      setInput(content);
    } finally {
      setSending(false);
    }
  };

  const openMap = () => {
    setZoneSaved(null);
    setMapOpen(true);
  };
  const closeMap = () => {
    setMapOpen(false);
    // Sin el flag en la URL, recargar no vuelve a abrir el editor. replaceState no dispara hashchange.
    if (/[?&]draw=1/.test(window.location.hash)) window.history.replaceState(null, "", href(`/t/${encodeURIComponent(threadId)}`, mock));
  };

  /** Zona dibujada en el mapa: PUT /zone y un mensaje al agente para que la incorpore al resumen. */
  const saveZone = async (zone: Zone | null) => {
    setZoneSaved(zone);
    if (mock) {
      setDraft((d) => ({ ...d, zone }));
      setHint(DEMO_NOTE);
      return;
    }
    const detail = await putZone(threadId, zone);
    setDraft(detail.brief ? briefToDraft(detail.brief) : detail.draft);
    setConfirmed(detail.confirmed);
    const text = zone
      ? zone.name
        ? `Marqué en el mapa la zona donde quiero hospedarme: ${zone.name}`
        : "Marqué una zona en el mapa donde quiero hospedarme."
      : "Saqué la zona que había marcado en el mapa.";
    await send(text);
  };

  const onSubmit = (e: FormEvent) => {
    e.preventDefault();
    void send(input);
  };
  const onKey = (e: KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      void send(input);
    }
  };

  /** "Ajustar" en los resultados: deja el cursor en el cuadro con un arranque de mensaje. */
  const adjust = () => {
    setInput((prev) => (prev.trim() ? prev : ADJUST_PREFILL));
    window.setTimeout(() => {
      const el = inputRef.current;
      if (!el) return;
      el.focus();
      el.setSelectionRange(el.value.length, el.value.length);
      el.scrollIntoView({ block: "center", behavior: prefersReducedMotion() ? "auto" : "smooth" });
    }, 0);
  };

  /** Pregunta desde la ficha: se manda en el hilo y se vuelve a la conversación. */
  const ask = (text: string) => {
    void send(text);
    navigate(`/t/${encodeURIComponent(threadId)}`, mock);
  };

  /** Atajo: salta la conversación y lanza la búsqueda en un hilo nuevo con el brief tal como está en el panel. */
  const launch = async () => {
    const brief = draftToBrief(draft);
    if (!brief) return;
    setLaunching(true);
    setLaunchError(null);
    try {
      const created = await createRun(brief);
      navigate(`/t/${encodeURIComponent(created.thread_id)}`);
    } catch (e) {
      setLaunchError(errorMessage(e));
      setLaunching(false);
    }
  };

  /** Borra la conversación (y su carpeta de corrida) y vuelve al listado. */
  const remove = async () => {
    setDeleteError(null);
    try {
      await deleteThread(threadId);
    } catch (e) {
      if (!(e instanceof ApiError && e.status === 404)) {
        setDeleteError(errorMessage(e));
        return;
      }
    }
    closeRef.current?.(); // cerrar el SSE antes de irnos: si no, intenta reconectar a un hilo que ya no está
    closeRef.current = null;
    navigate("/searches");
  };

  const elapsed = lastTurn && running ? Math.max(0, (now - turnStartedAt(lastTurn, now)) / 1000) : 0;
  const inputDisabled = !canChat || running;
  const filled = filledGroups(draft);
  const briefValid = draftToBrief(draft) != null;
  const showInterviewFooter = stage === "interview" && !confirmed && !archived && loaded;

  const composerNote = running
    ? stage === "searching"
      ? `Buscando · ${clock(elapsed)}`
      : `Pensando · ${clock(elapsed)}`
    : archived
      ? "Conversación archivada, solo lectura."
      : hint
        ? hint
        : mock
          ? DEMO_NOTE
          : blocked
            ? blocked
            : !loaded
              ? "Cargando la conversación…"
              : "Enter envía · Shift+Enter salta de línea";

  const nights = report ? (report.brief.nights ?? nightsBetween(report.brief.check_in, report.brief.check_out) ?? 0) : 0;
  const fichaVerdict = property ? findVerdict(report, events, property) : null;

  const empty = (
    <div className="flow-empty">
      <p className="flow-empty__title">Contá a dónde viajás y cuándo.</p>
      <p>Preguntamos lo que falte, confirmamos y recién ahí buscamos.</p>
    </div>
  );

  const bar = (
    <div className="thread__bar">
      <a className="thread__back" href={href("/searches", mock)}>
        ← Mis búsquedas
      </a>
      <div className="thread__status">
        {stage === "interview" && !confirmed ? (
          <span className="progress" aria-label={`${filled} de ${GROUPS} datos`}>
            <span className="progress__label">
              {filled} de {GROUPS} datos
            </span>
            <span className="progress__bar" aria-hidden="true">
              <span style={{ width: `${(filled / GROUPS) * 100}%` }} />
            </span>
          </span>
        ) : stage === "searching" ? (
          <span className="live-dot-label">
            <span className="live-dot" aria-hidden="true" />
            buscando
          </span>
        ) : (
          draft.destination && (
            <span className="thread__crumb">
              {shortDestination(draft.destination)}
              {draft.check_in && draft.check_out ? ` · ${dateRange(draft.check_in, draft.check_out)}` : ""}
            </span>
          )
        )}
        {conn === "reconnecting" && <span className="thread__conn">Conexión perdida, reintentando…</span>}
      </div>
      {loaded && !loadError && (
        <DeleteAction
          className="thread__delete"
          label="Eliminar"
          question="¿Eliminar la búsqueda? No se puede deshacer."
          onConfirm={remove}
          disabledReason={running ? "No se puede eliminar mientras el agente responde." : null}
          mock={mock}
          error={deleteError}
        />
      )}
    </div>
  );

  if (property) {
    return (
      <div className="thread thread--ficha">
        {bar}
        {fichaVerdict ? (
          <Ficha verdict={fichaVerdict} zone={report?.brief.zone ?? draft.zone ?? null} nights={nights} threadId={threadId} mock={mock} canSend={canSend || mock} onAsk={ask} />
        ) : (
          <div className="note note--warning">
            <span className="note__title">{loaded ? "No encontramos esa ficha" : "Cargando…"}</span>
            <span className="note__text">
              {loaded ? "Puede ser de otra búsqueda o de un enlace viejo. " : ""}
              <a href={href(`/t/${encodeURIComponent(threadId)}`, mock)}>Volver a los resultados</a>
            </span>
          </div>
        )}
      </div>
    );
  }

  const withSide = stage === "interview" && !loadError;
  const zoneEditable = loaded && !archived && !running && !blocked; // en demo también se puede dibujar (queda local)
  // Paso previo a la búsqueda: hilo idle, brief sin confirmar y ya con destino (para centrar el mapa).
  const zoneAction: ZoneAction | null = withSide && zoneEditable && !confirmed && Boolean(draft.destination?.trim()) ? { zone: draft.zone, onOpen: openMap } : null;
  const panel = (
    <BriefPanel
      draft={draft}
      onChange={setDraft}
      onLaunch={() => void launch()}
      launching={launching}
      launchError={launchError}
      missing={missing}
      confirmed={confirmed}
      readOnly={archived || running || mock}
      blocked={blocked}
      mapOpen={mapOpen}
      onMapToggle={() => (mapOpen ? closeMap() : openMap())}
      zoneEditable={zoneEditable}
    />
  );

  return (
    <div className={`thread${withSide ? "" : " thread--wide"}`}>
      {bar}
      <section className="thread__main">
        {loadError ? (
          <p className="error error--block" role="alert">
            {loadError}
          </p>
        ) : (
          <>
            {archived && (
              <div className="notice" role="status">
                <strong>Búsqueda archivada</strong>
                <span>Viene del historial y es de solo lectura. Para seguir, abrí una búsqueda nueva.</span>
              </div>
            )}

            <Conversation
              turns={turns}
              running={running}
              now={now}
              threadId={threadId}
              mock={mock}
              canSend={canSend || mock}
              onSend={(t) => void send(t)}
              onAdjust={adjust}
              empty={empty}
              zoneAction={zoneAction}
            />

            {sendError && (
              <div className="note note--error" role="alert">
                <span className="note__title">No se pudo enviar</span>
                <span className="note__text">{sendError}</span>
              </div>
            )}

            {mapOpen && stage === "interview" && <ZoneEditor zone={draft.zone} destination={draft.destination} readOnly={!zoneEditable} onSave={saveZone} onClose={closeMap} />}
            {!mapOpen && zoneSaved && stage === "interview" && !confirmed && (
              <p className="zone-saved" role="status">
                Zona guardada: <strong>{zoneSummary(zoneSaved)}</strong>
                {zoneEditable && (
                  <>
                    {" · "}
                    <button type="button" className="link-btn" onClick={openMap}>
                      Editar
                    </button>
                  </>
                )}
              </p>
            )}

            <form className="composer" onSubmit={onSubmit}>
              <div className="composer__box">
                <label className="sr-only" htmlFor="composer-input">
                  Mensaje para posta
                </label>
                <textarea
                  id="composer-input"
                  ref={inputRef}
                  rows={1}
                  value={input}
                  placeholder={archived ? "" : stage === "results" ? "Preguntá o pedí ajustes…" : "Escribí tu respuesta…"}
                  onChange={(e) => setInput(e.target.value)}
                  onKeyDown={onKey}
                  disabled={inputDisabled}
                />
                <button type="submit" className="composer__send" disabled={inputDisabled || !input.trim()} aria-label="Enviar">
                  Enter
                </button>
              </div>
              <div className="composer__bar">
                <span className={`composer__status${running ? " is-running" : ""}`} aria-live="polite">
                  {showInterviewFooter && !running
                    ? filled >= GROUPS
                      ? "Tenemos todo lo que hace falta."
                      : briefValid
                        ? `Faltan ${GROUPS - filled} datos, pero ya se puede buscar.`
                        : `Faltan ${GROUPS - filled} datos para arrancar.`
                    : composerNote}
                </span>
                {showInterviewFooter && (
                  <button type="button" className="btn btn--navy btn--sm" disabled={!briefValid || inputDisabled} onClick={() => void send("buscá igual")}>
                    Listo, buscá igual
                  </button>
                )}
              </div>
            </form>
          </>
        )}
      </section>

      {withSide && (
        <aside className="thread__side">
          {narrow ? (
            <details className="side-fold">
              <summary className="side-fold__summary">
                Lo que sabemos <span className="muted">· {filled} de {GROUPS} datos</span>
              </summary>
              {panel}
            </details>
          ) : (
            panel
          )}
        </aside>
      )}
    </div>
  );
}
