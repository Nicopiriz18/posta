import type { Brief, BriefDraft, PriceEvidence } from "./types";

const LOCALE = "es-AR";

/** HH:MM en hora local del navegador a partir de un ISO. */
export function hhmm(iso: string): string {
  const t = Date.parse(iso);
  if (Number.isNaN(t)) return "??:??";
  return new Intl.DateTimeFormat(LOCALE, { hour: "2-digit", minute: "2-digit", hourCycle: "h23" }).format(t);
}

export function formatAmount(n: number): string {
  return new Intl.NumberFormat(LOCALE, { maximumFractionDigits: 0 }).format(n);
}

/** "US$ 1.155", "EUR 980". */
export function money(amount: number, currency: string): string {
  const cur = currency.toUpperCase();
  const sym = cur === "USD" ? "US$" : cur === "ARS" ? "$" : cur === "UYU" ? "$U" : cur === "EUR" ? "€" : cur;
  return `${sym} ${formatAmount(amount)}`;
}

/** Regla 5: "desde {currency} {total} vía {source}, verificado a las HH:MM". Sin precio: "precio no disponible". */
export function priceLine(price: PriceEvidence | null): string {
  if (!price) return "precio no disponible";
  return `desde ${price.currency} ${formatAmount(price.total)} vía ${price.source}, verificado a las ${hhmm(price.verified_at)}`;
}

/** "jue 16 oct" */
export function shortDate(ymd: string): string {
  const t = Date.parse(ymd + "T00:00:00");
  if (Number.isNaN(t)) return ymd;
  return new Intl.DateTimeFormat(LOCALE, { weekday: "short", day: "numeric", month: "short" }).format(t);
}

function dayMonth(ymd: string): { day: string; month: string } | null {
  const t = Date.parse(ymd + "T00:00:00");
  if (Number.isNaN(t)) return null;
  return {
    day: new Intl.DateTimeFormat(LOCALE, { day: "numeric" }).format(t),
    month: new Intl.DateTimeFormat(LOCALE, { month: "short" }).format(t).replace(/\.$/, ""),
  };
}

/** "8 → 15 ene" (mismo mes) o "28 dic → 3 ene". */
export function dateRange(checkIn: string, checkOut: string): string {
  const a = dayMonth(checkIn);
  const b = dayMonth(checkOut);
  if (!a || !b) return `${checkIn} → ${checkOut}`;
  return a.month === b.month ? `${a.day} → ${b.day} ${b.month}` : `${a.day} ${a.month} → ${b.day} ${b.month}`;
}

/** "16 oct 2026, 18:42" */
export function dateTime(iso: string): string {
  const t = Date.parse(iso);
  if (Number.isNaN(t)) return iso;
  return new Intl.DateTimeFormat(LOCALE, {
    day: "numeric",
    month: "short",
    year: "numeric",
    hour: "2-digit",
    minute: "2-digit",
    hourCycle: "h23",
  }).format(t);
}

/** "hace 2 horas", "hace 3 semanas", "recién". */
export function relativeTime(iso: string, now = Date.now()): string {
  const t = Date.parse(iso);
  if (Number.isNaN(t)) return "";
  const s = Math.max(0, Math.round((now - t) / 1000));
  if (s < 60) return "recién";
  const m = Math.floor(s / 60);
  if (m < 60) return `hace ${plural(m, "minuto", "minutos")}`;
  const h = Math.floor(m / 60);
  if (h < 24) return `hace ${plural(h, "hora", "horas")}`;
  const d = Math.floor(h / 24);
  if (d < 7) return `hace ${plural(d, "día", "días")}`;
  const w = Math.floor(d / 7);
  if (w < 5) return `hace ${plural(w, "semana", "semanas")}`;
  const mo = Math.floor(d / 30);
  return `hace ${plural(Math.max(1, mo), "mes", "meses")}`;
}

/** 142.6 → "2 min 23 s"; 41 → "41 s" */
export function duration(seconds: number): string {
  const s = Math.max(0, Math.round(seconds));
  const m = Math.floor(s / 60);
  const r = s % 60;
  if (m === 0) return `${r} s`;
  return `${m} min ${r.toString().padStart(2, "0")} s`;
}

/** m:ss para el cronómetro de la corrida */
export function clock(seconds: number): string {
  const s = Math.max(0, Math.floor(seconds));
  const m = Math.floor(s / 60);
  const r = s % 60;
  return `${m}:${r.toString().padStart(2, "0")}`;
}

export function plural(n: number, one: string, many: string): string {
  return `${n} ${n === 1 ? one : many}`;
}

/** "2 adultos", "2 + 2 chicos", "1 adulto". */
export function travelersLabel(adults: number | null | undefined, children: number | null | undefined): string | null {
  if (adults == null && !children) return null;
  const a = adults ?? 2;
  if (children) return `${a} + ${plural(children, "chico", "chicos")}`;
  return plural(a, "adulto", "adultos");
}

/** "US$ 600 total", "US$ 120 / noche". */
export function budgetLabel(b: Pick<Brief | BriefDraft, "currency" | "budget_total_max" | "budget_per_night_max">): string | null {
  const cur = b.currency ?? "USD";
  if (b.budget_per_night_max != null) return `${money(b.budget_per_night_max, cur)} / noche`;
  if (b.budget_total_max != null) return `${money(b.budget_total_max, cur)} total`;
  return null;
}

/** "Buenos Aires, Argentina" → "Buenos Aires". */
export function shortDestination(dest: string): string {
  const i = dest.indexOf(",");
  return (i > 0 ? dest.slice(0, i) : dest).trim();
}

/** Primera oración de un texto largo (para filas compactas). */
export function firstSentence(text: string): string {
  const m = text.match(/^.*?[.!?](\s|$)/);
  return (m ? m[0] : text).trim();
}

export function truncate(text: string, max: number): string {
  if (text.length <= max) return text;
  return text.slice(0, max - 1).trimEnd() + "…";
}

/** "https://www.ventosul.com.br/pousada?x=1" → "ventosul.com.br". Sin URL válida devuelve null. */
export function domainOf(url: string | null | undefined): string | null {
  if (!url) return null;
  try {
    const host = new URL(url).hostname.toLowerCase();
    return host.replace(/^www\./, "") || null;
  } catch {
    const m = url.match(/^(?:[a-z]+:\/\/)?(?:www\.)?([^/?#\s]+)/i);
    return m ? m[1].toLowerCase() : null;
  }
}

/** "4,8" con coma decimal. */
export function rating(n: number): string {
  return new Intl.NumberFormat(LOCALE, { minimumFractionDigits: 1, maximumFractionDigits: 1 }).format(n);
}
