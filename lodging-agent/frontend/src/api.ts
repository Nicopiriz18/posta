import {
  EVENT_TYPES,
  type Brief,
  type DoneEvent,
  type Health,
  type MessageAccepted,
  type RunCreated,
  type RunEvent,
  type ThreadDetail,
  type ThreadSummary,
  type Zone,
} from "./types";

export const BACKEND_HINT =
  "No se pudo conectar con el backend. ¿Está corriendo `uv run uvicorn lodging.api.main:app`?";

export class ApiError extends Error {
  status: number;
  detail: unknown;
  constructor(status: number, detail: unknown) {
    super(describeDetail(status, detail));
    this.name = "ApiError";
    this.status = status;
    this.detail = detail;
  }
}

interface ValidationItem {
  loc?: unknown[];
  msg?: string;
}

function describeDetail(status: number, detail: unknown): string {
  if (typeof detail === "string" && detail) return detail;
  if (Array.isArray(detail)) {
    // 422 de FastAPI: [{loc, msg, type}]
    const parts = (detail as ValidationItem[])
      .filter((d) => d && typeof d === "object" && typeof d.msg === "string")
      .map((d) => {
        const loc = Array.isArray(d.loc) ? d.loc.filter((x) => x !== "body").join(".") : "";
        return loc ? `${loc}: ${d.msg}` : d.msg;
      });
    if (parts.length) return parts.join("; ");
  }
  if (status === 409) return "El agente todavía está respondiendo en este hilo.";
  if (status === 404) return "Ese hilo no existe (el backend se reinició o el enlace es viejo).";
  return `El backend respondió ${status}.`;
}

/** Mensaje para mostrar al usuario a partir de cualquier error. */
export function errorMessage(err: unknown): string {
  if (err instanceof ApiError) return err.message;
  if (err instanceof TypeError) return BACKEND_HINT; // fetch falló: red o backend caído
  if (err instanceof Error) return err.message;
  return "Ocurrió un error inesperado.";
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const headers: Record<string, string> = { Accept: "application/json" };
  if (init?.body) headers["Content-Type"] = "application/json";
  const res = await fetch(path, { ...init, headers });
  if (!res.ok) {
    let detail: unknown = null;
    try {
      const body: unknown = await res.json();
      detail = body && typeof body === "object" && "detail" in body ? (body as { detail: unknown }).detail : body;
    } catch {
      detail = null;
    }
    throw new ApiError(res.status, detail);
  }
  if (res.status === 204) return undefined as T; // DELETE: sin cuerpo
  return (await res.json()) as T;
}

export function getHealth(): Promise<Health> {
  return request<Health>("/api/health");
}

/* ---------- Hilos ---------- */

export function createThread(): Promise<ThreadSummary> {
  return request<ThreadSummary>("/api/threads", { method: "POST" });
}

export function listThreads(): Promise<ThreadSummary[]> {
  return request<ThreadSummary[]>("/api/threads");
}

export function getThread(threadId: string): Promise<ThreadDetail> {
  return request<ThreadDetail>(`/api/threads/${encodeURIComponent(threadId)}`);
}

/**
 * Borra la conversación del historial y su carpeta `runs/<id>/`: es permanente.
 * Falla con `ApiError` 409 (el agente todavía responde en ese hilo) o 404 (ya no existe); `message` trae el texto para el usuario.
 */
export function deleteThread(threadId: string): Promise<void> {
  return request<void>(`/api/threads/${encodeURIComponent(threadId)}`, { method: "DELETE" });
}

export function sendMessage(threadId: string, content: string): Promise<MessageAccepted> {
  return request<MessageAccepted>(`/api/threads/${encodeURIComponent(threadId)}/messages`, {
    method: "POST",
    body: JSON.stringify({ content }),
  });
}

/**
 * Guarda (o borra con `null`) la zona dibujada en el mapa. Devuelve el hilo con `draft.zone` / `brief.zone` actualizados
 * y emite `brief_saved` por el SSE. `ApiError` 409 si el agente responde o el hilo está archivado; 422 con menos de 3 vértices.
 */
export function putZone(threadId: string, zone: Zone | null): Promise<ThreadDetail> {
  return request<ThreadDetail>(`/api/threads/${encodeURIComponent(threadId)}/zone`, {
    method: "PUT",
    body: JSON.stringify(zone),
  });
}

/** Modo headless: crea un hilo con el brief ya confirmado y arranca la investigación sin preguntar. */
export function createRun(brief: Brief): Promise<RunCreated> {
  return request<RunCreated>("/api/runs", { method: "POST", body: JSON.stringify(briefForRun(brief)) });
}

/** `nights` lo computa el backend y no se acepta en la entrada: se saca antes de reenviar un brief (Rebuscar). */
export function briefForRun(brief: Brief): Brief {
  const rest: Brief & { nights?: number } = { ...brief };
  delete rest.nights;
  return rest;
}

/* ---------- SSE ---------- */

export interface EventHandlers {
  onEvent: (ev: RunEvent) => void;
  /** Se abrió (o re-abrió) la conexión. `reconnected` = true si es un reintento: el backend vuelve a mandar todo el historial. */
  onOpen?: (reconnected: boolean) => void;
  /** Solo para hilos archivados: el backend mandó el historial y cerró. */
  onDone?: (done: DoneEvent) => void;
  onError?: () => void;
}

function parseJson<T>(raw: string): T | null {
  try {
    return JSON.parse(raw) as T;
  } catch {
    return null;
  }
}

/**
 * Abre EL stream SSE de un hilo (historial + vivo, queda abierto entre turnos).
 * Un solo EventSource por hilo; devuelve la función para cerrarlo (llamar al desmontar).
 */
export function openThreadEvents(threadId: string, handlers: EventHandlers): () => void {
  const es = new EventSource(`/api/threads/${encodeURIComponent(threadId)}/events`);
  let closed = false;
  let opens = 0;

  es.onopen = () => {
    opens += 1;
    handlers.onOpen?.(opens > 1);
  };
  for (const type of EVENT_TYPES) {
    es.addEventListener(type, (e) => {
      const ev = parseJson<RunEvent>((e as MessageEvent<string>).data);
      if (ev) handlers.onEvent(ev);
    });
  }
  es.addEventListener("done", (e) => {
    const done = parseJson<DoneEvent>((e as MessageEvent<string>).data);
    closed = true;
    es.close();
    handlers.onDone?.(done ?? { status: "archived" });
  });
  es.onerror = () => {
    if (closed) return;
    // Sin `done`, EventSource reintenta solo; avisamos para mostrar estado de reconexión.
    handlers.onError?.();
  };

  return () => {
    closed = true;
    es.close();
  };
}
