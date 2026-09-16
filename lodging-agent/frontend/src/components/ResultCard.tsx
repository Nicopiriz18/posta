import type { PriceEvidence, PriceOffer, PropertyFacts, PropertyVerdict } from "../types";
import { href } from "../App";
import { domainOf, firstSentence, formatAmount, money, plural, priceLine, rating, truncate } from "../format";

export type Badge = "match" | "price" | null;

export function isWebProperty(v: Pick<PropertyVerdict, "property_token" | "facts">): boolean {
  return v.facts?.source === "web" || v.property_token.startsWith("web:");
}

/**
 * De dónde salió la propiedad: "Google Hotels" (discreto) o "Web · dominio" (con enlace a la página de origen).
 * No decimos "sitio directo" porque el contrato no distingue sitio propio de inmobiliaria o portal chico.
 */
export function SourceBadge({ verdict: v, compact = false }: { verdict: Pick<PropertyVerdict, "property_token" | "facts" | "link">; compact?: boolean }) {
  const f: PropertyFacts | null = v.facts;
  if (!f && !v.property_token.startsWith("web:")) return null;
  if (!isWebProperty(v)) {
    return (
      <span className={`srcbadge srcbadge--quiet${compact ? " srcbadge--compact" : ""}`} title="Ficha y precios tomados de Google Hotels">
        Google Hotels
      </span>
    );
  }
  const url = f?.url ?? v.link ?? null;
  const domain = domainOf(url) ?? "web";
  const cls = `srcbadge srcbadge--web${compact ? " srcbadge--compact" : ""}`;
  const inner = (
    <>
      <span className="srcbadge__k">Web</span>
      <span className="srcbadge__sep" aria-hidden="true">
        ·
      </span>
      <span className="srcbadge__d">{domain}</span>
    </>
  );
  return url ? (
    <a className={cls} href={url} target="_blank" rel="noreferrer noopener" title={`Encontrado en ${domain}; abre la página de origen`}>
      {inner}
    </a>
  ) : (
    <span className={cls}>{inner}</span>
  );
}

/** "dentro de la zona" / "a 350 m de la zona"; null si no hay zona o la propiedad no tiene coordenadas. */
export function zoneText(f: PropertyFacts | null): string | null {
  if (!f || f.zone_inside == null) return null;
  if (f.zone_inside) return "dentro de la zona";
  return f.zone_distance_m != null ? `a ${formatAmount(f.zone_distance_m)} m de la zona` : "fuera de la zona";
}

/** "Casa · 3 dormitorios · Capacidad para 6": tipo + datos esenciales de la ficha; si no hay, la ubicación resumida. */
export function subtitleOf(v: PropertyVerdict): string {
  const f = v.facts;
  const parts: string[] = [];
  if (f?.type) parts.push(f.type);
  if (f?.hotel_class) parts.push(`${f.hotel_class} estrellas`);
  if (f?.essential_info?.length) parts.push(...f.essential_info.slice(0, 2));
  if (parts.length) return parts.join(" · ");
  return v.location_summary ? truncate(firstSentence(v.location_summary), 90) : "";
}

export interface Pill {
  text: string;
  tone: "ok" | "warn" | "muted";
}

/** 3-4 datos: confirmados en verde, contras en naranja, "sin verificar" neutro. */
export function pillsFor(v: PropertyVerdict): Pill[] {
  const greens: Pill[] = [];
  if (v.facts?.overall_rating != null) {
    greens.push({
      text: v.facts.reviews_count ? `${rating(v.facts.overall_rating)} en ${plural(v.facts.reviews_count, "reseña", "reseñas")}` : `${rating(v.facts.overall_rating)} de puntaje`,
      tone: "ok",
    });
  }
  for (const c of [...v.hard_constraints, ...v.soft_preferences]) {
    if (c.status === "confirmed") greens.push({ text: c.constraint, tone: "ok" });
  }
  const warn: Pill | null = v.red_flags.length
    ? { text: `Alerta: ${truncate(v.red_flags[0], 48)}`, tone: "warn" }
    : v.cons.length
      ? { text: truncate(v.cons[0], 48), tone: "warn" }
      : v.over_budget
        ? { text: "Supera el presupuesto", tone: "warn" }
        : null;
  const neutral: Pill | null = v.unknowns.length ? { text: `${v.unknowns.length} sin verificar`, tone: "muted" } : null;
  const room = 4 - (warn ? 1 : 0) - (neutral ? 1 : 0);
  const out = greens.slice(0, room);
  if (warn) out.push(warn);
  if (neutral) out.push(neutral);
  return out;
}

export interface Comparison {
  text: string;
  tone: "ok" | "muted";
}

function sameSource(a: string, b: string): boolean {
  return a.trim().toLowerCase() === b.trim().toLowerCase();
}

/** Cómo se compara el precio verificado con las demás fuentes de la ficha. */
export function compareOffers(price: PriceEvidence, offers: PriceOffer[]): Comparison | null {
  const others = offers.filter((o) => o.total != null && !sameSource(o.source, price.source)) as (PriceOffer & { total: number })[];
  if (!others.length) return null;
  const cheapest = others.reduce((a, b) => (b.total < a.total ? b : a));
  if (cheapest.total < price.total - 1) return { text: `${money(price.total - cheapest.total, price.currency)} más barato en ${cheapest.source}`, tone: "ok" };
  const dearest = others.reduce((a, b) => (b.total > a.total ? b : a));
  if (dearest.total > price.total + 1) return { text: `${money(dearest.total - price.total, price.currency)} menos que en ${dearest.source}`, tone: "ok" };
  return { text: `mismo precio en ${others.length + 1} fuentes`, tone: "muted" };
}

/** Mismo enlace ignorando espacios y barra final. */
export function sameLink(a: string | null | undefined, b: string | null | undefined): boolean {
  if (!a || !b) return false;
  const norm = (u: string) => u.trim().replace(/\/+$/, "");
  return norm(a) === norm(b);
}

export type PrimaryLink = { kind: "booking"; link: string; source: string } | { kind: "google"; link: string };

/**
 * Acción principal: `facts.booking_url`, el enlace exacto de ESTA propiedad (lo elige el backend: página propia para
 * candidatos web, portal real para los de Google). Si no hay, la búsqueda en Google Hotels; si tampoco, null
 * ("sin enlace directo"). `verdict.link` puede ser un comparador (vio, bluepillow…): nunca va acá.
 */
export function primaryLink(v: PropertyVerdict): PrimaryLink | null {
  const f = v.facts;
  if (f?.booking_url) return { kind: "booking", link: f.booking_url, source: f.booking_source ?? domainOf(f.booking_url) ?? "la fuente" };
  if (f?.google_hotels_url) return { kind: "google", link: f.google_hotels_url };
  return null;
}

/** "Reservar en {source}": la oferta más barata con enlace, solo si lleva a otro lado que la acción principal. */
export function secondaryBooking(v: PropertyVerdict): { source: string; link: string } | null {
  const withLink = (v.facts?.offers ?? []).filter((o): o is PriceOffer & { link: string } => !!o.link);
  if (!withLink.length) return null;
  const best = withLink.reduce((a, b) => ((b.total ?? Infinity) < (a.total ?? Infinity) ? b : a));
  if (sameLink(best.link, v.facts?.booking_url)) return null;
  return { source: best.source, link: best.link };
}

/** CTA principal (nueva pestaña) con la leyenda "en {fuente}"; fallback a Google Hotels; si no hay nada, "sin enlace directo". */
export function PrimaryCta({ verdict: v, className }: { verdict: PropertyVerdict; className: string }) {
  const p = primaryLink(v);
  if (!p) return <span className="cta__none">sin enlace directo</span>;
  if (p.kind === "google") {
    return (
      <a className={className} href={p.link} target="_blank" rel="noreferrer noopener" title={`Busca ${v.name} en Google Hotels`}>
        Buscar en Google Hotels
      </a>
    );
  }
  return (
    <span className="cta">
      <a className={className} href={p.link} target="_blank" rel="noreferrer noopener" title={`Abre la página de ${v.name} en ${p.source}`}>
        Ver el alojamiento
      </a>
      <span className="cta__cap">en {p.source}</span>
    </span>
  );
}

export function perNight(v: PropertyVerdict, nights: number): number | null {
  if (!v.price) return null;
  if (v.price.per_night != null) return v.price.per_night;
  return nights > 0 ? v.price.total / nights : null;
}

interface Props {
  verdict: PropertyVerdict;
  rank: number;
  rationale: string;
  badge: Badge;
  nights: number;
  threadId: string;
  mock: boolean;
}

export default function ResultCard({ verdict: v, rank, rationale, badge, nights, threadId, mock }: Props) {
  const fichaHref = href(`/t/${encodeURIComponent(threadId)}/p/${encodeURIComponent(v.property_token)}`, mock);
  const night = perNight(v, nights);
  const primary = primaryLink(v);
  const book = secondaryBooking(v);
  const cmp = v.price && v.facts?.offers ? compareOffers(v.price, v.facts.offers) : null;
  const shown = [primary?.kind === "booking" ? primary.source : null, book?.source ?? null].filter((s): s is string => s != null);
  const otherSources = (v.facts?.offers ?? []).map((o) => o.source).filter((s) => !shown.some((x) => sameSource(s, x)));
  const sub = subtitleOf(v);
  const zone = zoneText(v.facts);

  return (
    <article className={`rcard${rank === 1 ? " rcard--first" : ""}`} aria-label={`Opción ${rank}: ${v.name}`}>
      <div className="rcard__rank">
        <span className="rcard__num">{rank}</span>
        {badge && <span className="rcard__badge">{badge === "match" ? "mejor\nmatch" : "mejor\nprecio"}</span>}
      </div>

      <div className="rcard__body">
        <div className="rcard__title">
          <h3 className="rcard__name">{v.name}</h3>
          <span className="rcard__meta">
            {sub && <span className="rcard__sub">{sub}</span>}
            {zone && <span className={`zone-tag${v.facts?.zone_inside ? " zone-tag--in" : ""}`}>{zone}</span>}
            <SourceBadge verdict={v} />
          </span>
        </div>
        <p className="rcard__why">{rationale || v.summary}</p>
        <div className="pills">
          {pillsFor(v).map((p, i) => (
            <span key={i} className={`pill pill--${p.tone}`}>
              {p.text}
            </span>
          ))}
        </div>
      </div>

      <div className="rcard__price">
        <div className="rcard__amounts">
          {v.price ? (
            <>
              <span className="rcard__amount">{money(night ?? v.price.total, v.price.currency)}</span>
              <span className="rcard__per">{night != null ? `por noche · ${money(v.price.total, v.price.currency)} total` : "total de la estadía"}</span>
              {cmp && <span className={`rcard__cmp rcard__cmp--${cmp.tone}`}>{cmp.text}</span>}
              <span className="rcard__verified">{priceLine(v.price)}</span>
            </>
          ) : (
            <span className="rcard__noprice">precio no disponible</span>
          )}
        </div>
        <div className="rcard__actions">
          <PrimaryCta verdict={v} className={`btn ${rank === 1 ? "btn--accent" : "btn--navy"}`} />
          {book && (
            <a className="btn btn--outline" href={book.link} target="_blank" rel="noreferrer noopener">
              Reservar en {book.source}
            </a>
          )}
          <a className="btn btn--outline" href={fichaHref}>
            Ver ficha
          </a>
          {otherSources.length > 0 && <span className="rcard__also">También en {otherSources.slice(0, 3).join(", ")}</span>}
        </div>
      </div>
    </article>
  );
}

/** Puestos 4 en adelante: fila compacta. */
export function ResultRow({ verdict: v, rank, rationale, nights, threadId, mock }: Omit<Props, "badge">) {
  const fichaHref = href(`/t/${encodeURIComponent(threadId)}/p/${encodeURIComponent(v.property_token)}`, mock);
  const night = perNight(v, nights);
  const sub = subtitleOf(v);
  const zone = zoneText(v.facts);
  return (
    <article className="rrow" aria-label={`Opción ${rank}: ${v.name}`}>
      <span className="rrow__num">{rank}</span>
      <div className="rrow__body">
        <h3 className="rrow__name">{v.name}</h3>
        <span className="rrow__meta">
          {sub && <span className="rrow__sub">{sub}</span>}
          {zone && <span className={`zone-tag${v.facts?.zone_inside ? " zone-tag--in" : ""}`}>{zone}</span>}
          <SourceBadge verdict={v} compact />
        </span>
        <p className="rrow__why">{firstSentence(rationale || v.summary)}</p>
        {v.price ? (
          <span className="rrow__price">
            <strong>{money(night ?? v.price.total, v.price.currency)}</strong>
            {night != null ? " / noche" : " total"}
            <span className="rrow__verified"> · {priceLine(v.price)}</span>
          </span>
        ) : (
          <span className="rrow__price rrow__price--none">precio no disponible</span>
        )}
        <span className="rrow__links">
          <PrimaryCta verdict={v} className="rrow__link rrow__link--ext" />
          <a className="rrow__link" href={fichaHref}>
            Ver ficha →
          </a>
        </span>
      </div>
    </article>
  );
}
