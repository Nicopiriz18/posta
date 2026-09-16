import { useEffect, useState } from "react";
import { ApiError, createRun, deleteThread, errorMessage, getThread, listThreads } from "../api";
import { href, navigate } from "../App";
import DeleteAction from "../components/DeleteAction";
import { dateRange, plural, relativeTime, travelersLabel } from "../format";
import { sampleSearches, sampleSearchDetails } from "../mock/sampleReport";
import type { ThreadDetail, ThreadSummary } from "../types";

interface Props {
  mock: boolean;
  blocked: string | null;
}

const RUNNING_NOTE = "No se puede eliminar mientras el agente responde.";

/** Segunda línea de la fila: viajeros · imprescindibles, si el detalle ya llegó. */
function subtitle(t: ThreadSummary, d: ThreadDetail | undefined): string {
  if (d) {
    const b = d.brief ?? d.draft;
    const parts: string[] = [];
    const trav = travelersLabel(b.adults, b.children);
    if (trav) parts.push(trav);
    const musts = (b.hard_constraints ?? []).slice(0, 3).join(", ");
    if (musts) parts.push(musts);
    if (parts.length) return parts.join(" · ");
  }
  return t.last_message || "Sin mensajes todavía";
}

function title(t: ThreadSummary): string {
  const dest = t.destination || "Sin destino todavía";
  return t.check_in && t.check_out ? `${dest} · ${dateRange(t.check_in, t.check_out)}` : dest;
}

export default function SearchesView({ mock, blocked }: Props) {
  const [threads, setThreads] = useState<ThreadSummary[] | null>(null);
  const [details, setDetails] = useState<Record<string, ThreadDetail>>({});
  const [listError, setListError] = useState<string | null>(null);
  const [removing, setRemoving] = useState<ReadonlySet<string>>(() => new Set());
  const [rowErrors, setRowErrors] = useState<Record<string, string>>({});
  const [rerunning, setRerunning] = useState<string | null>(null);

  useEffect(() => {
    if (mock) {
      setThreads(sampleSearches);
      setDetails(sampleSearchDetails);
      return;
    }
    let cancelled = false;
    listThreads()
      .then((list) => {
        if (cancelled) return;
        setThreads(list);
        // El detalle da viajeros e imprescindibles para la segunda línea; si falla, queda el último mensaje.
        list.slice(0, 20).forEach((t) => {
          getThread(t.thread_id)
            .then((d) => {
              if (!cancelled) setDetails((prev) => ({ ...prev, [t.thread_id]: d }));
            })
            .catch(() => undefined);
        });
      })
      .catch((e) => {
        if (!cancelled) setListError(errorMessage(e));
      });
    return () => {
      cancelled = true;
    };
  }, [mock]);

  const setRowError = (id: string, msg: string | null) =>
    setRowErrors((prev) => {
      const next = { ...prev };
      if (msg) next[id] = msg;
      else delete next[id];
      return next;
    });

  /** Saca la fila enseguida; si el backend dice que no (409), la fila vuelve con el motivo al lado. */
  const remove = async (t: ThreadSummary) => {
    const id = t.thread_id;
    setRowError(id, null);
    setRemoving((prev) => new Set(prev).add(id));
    try {
      await deleteThread(id);
    } catch (e) {
      if (!(e instanceof ApiError && e.status === 404)) {
        setRemoving((prev) => {
          const next = new Set(prev);
          next.delete(id);
          return next;
        });
        setRowError(id, errorMessage(e));
        return;
      }
    }
    setThreads((prev) => prev && prev.filter((x) => x.thread_id !== id));
    setRemoving((prev) => {
      const next = new Set(prev);
      next.delete(id);
      return next;
    });
  };

  /** Precios vencidos: misma búsqueda, hilo nuevo en modo headless con el brief del reporte. */
  const rerun = async (t: ThreadSummary) => {
    if (mock || blocked || rerunning) return;
    setRerunning(t.thread_id);
    setRowError(t.thread_id, null);
    try {
      const d = details[t.thread_id] ?? (await getThread(t.thread_id));
      const brief = d.report?.brief ?? d.brief;
      if (!brief) throw new Error("Esta búsqueda no tiene un brief completo para repetir.");
      const created = await createRun(brief);
      navigate(`/t/${encodeURIComponent(created.thread_id)}`);
    } catch (e) {
      setRowError(t.thread_id, errorMessage(e));
      setRerunning(null);
    }
  };

  const visible = threads?.filter((t) => !removing.has(t.thread_id)) ?? null;

  return (
    <div className="searches">
      <header className="searches__head">
        <h1 className="searches__title">Mis búsquedas</h1>
        <a className="btn btn--accent" href={href("/", mock)}>
          Nueva búsqueda
        </a>
      </header>

      {threads === null && !listError && <p className="muted">Cargando…</p>}
      {listError && <p className="error">{listError}</p>}
      {visible && visible.length === 0 && (
        <p className="searches__empty">
          Todavía no buscaste nada. <a href={href("/", mock)}>Contanos a dónde vas</a> y la primera búsqueda aparece acá.
        </p>
      )}

      {visible && visible.length > 0 && (
        <ul className="srow-list">
          {visible.map((t) => {
            const running = t.status === "running";
            const stale = t.has_report && t.prices_stale;
            const link = href(`/t/${encodeURIComponent(t.thread_id)}`, mock);
            return (
              <li key={t.thread_id} className="srow">
                <div className="srow__main">
                  <a className="srow__title" href={link}>
                    {title(t)}
                  </a>
                  <span className="srow__sub">{subtitle(t, details[t.thread_id])}</span>
                </div>
                <span className="srow__when">{running ? "buscando ahora" : relativeTime(t.created_at)}</span>
                <span className="srow__state">
                  {running ? (
                    <span className="live-dot-label">
                      <span className="live-dot" aria-hidden="true" />
                      en curso
                    </span>
                  ) : stale ? (
                    <span className="pill pill--muted">precios vencidos</span>
                  ) : t.has_report ? (
                    <span className="pill pill--ok">{t.results_count != null ? plural(t.results_count, "resultado", "resultados") : "resultados"}</span>
                  ) : (
                    <span className="pill pill--muted">{t.status === "archived" ? "archivada" : "sin resultados"}</span>
                  )}
                </span>
                <span className="srow__actions">
                  {stale && (
                    <button type="button" className="link-btn" disabled={!!rerunning || !!blocked || mock} onClick={() => void rerun(t)}>
                      {rerunning === t.thread_id ? "Abriendo…" : "Rebuscar"}
                    </button>
                  )}
                  <a className={`srow__go${t.has_report ? " srow__go--strong" : ""}`} href={link}>
                    {t.has_report ? "Ver" : "Seguir"}
                  </a>
                  <DeleteAction
                    className="srow__delete"
                    label="Eliminar"
                    onConfirm={() => remove(t)}
                    disabledReason={running ? RUNNING_NOTE : null}
                    mock={mock}
                    error={rowErrors[t.thread_id] ?? null}
                  />
                </span>
              </li>
            );
          })}
        </ul>
      )}
    </div>
  );
}
