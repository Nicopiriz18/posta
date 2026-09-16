import { useEffect, useState } from "react";
import { BACKEND_HINT, getHealth } from "./api";
import type { Health } from "./types";

export type HealthState = { kind: "loading" } | { kind: "ok"; health: Health } | { kind: "down" };

export function useHealth(enabled = true): HealthState {
  const [state, setState] = useState<HealthState>({ kind: "loading" });
  useEffect(() => {
    if (!enabled) return;
    let cancelled = false;
    getHealth()
      .then((h) => {
        if (!cancelled) setState({ kind: "ok", health: h });
      })
      .catch(() => {
        if (!cancelled) setState({ kind: "down" });
      });
    return () => {
      cancelled = true;
    };
  }, [enabled]);
  return state;
}

/** Texto del aviso de salud, o null si todo está bien (o todavía no se sabe). */
export function healthBanner(h: HealthState): { title: string; body: string } | null {
  if (h.kind === "down") {
    return { title: "Sin conexión con el backend", body: BACKEND_HINT };
  }
  if (h.kind === "ok") {
    const missing: string[] = [];
    if (!h.health.anthropic_configured) missing.push("ANTHROPIC_API_KEY");
    if (!h.health.serpapi_configured) missing.push("SERPAPI_KEY");
    if (missing.length) {
      return {
        title: "Falta configurar claves",
        body: `El backend corre pero no tiene ${missing.join(" ni ")}. Agregalas en el archivo .env (ver .env.example) y reiniciá el servidor.`,
      };
    }
  }
  return null;
}

/** Motivo por el que no se puede conversar ni buscar, o null. */
export function healthBlocked(h: HealthState): string | null {
  return healthBanner(h) ? "Configurá el backend para poder buscar." : null;
}
