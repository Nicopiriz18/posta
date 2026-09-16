import type { PropertyVerdict, Zone } from "../types";
import { href } from "../App";
import { domainOf, money, plural, priceLine, rating } from "../format";
import { PrimaryCta, SourceBadge, isWebProperty, perNight, sameLink, secondaryBooking, subtitleOf, zoneText } from "./ResultCard";
import ZoneMap from "./ZoneMap";

interface Props {
  verdict: PropertyVerdict;
  /** Zona dibujada en el brief (para el mapa de la ficha). */
  zone: Zone | null;
  nights: number;
  threadId: string;
  mock: boolean;
  canSend: boolean;
  /** Manda una pregunta sobre este alojamiento en el mismo hilo. */
  onAsk: (text: string) => void;
}

const ASK: { label: string; text: (name: string) => string }[] = [
  { label: "¿Sirve con chicos?", text: (n) => `Sobre ${n}: ¿sirve con chicos?` },
  { label: "¿Cómo es la zona?", text: (n) => `Sobre ${n}: ¿cómo es la zona?` },
  { label: "¿Conviene reservar directo?", text: (n) => `Sobre ${n}: ¿conviene reservar directo o por un portal?` },
];

function cancellationLabel(v: PropertyVerdict): { main: string; sub: string } {
  const flag = v.price?.free_cancellation ?? v.facts?.free_cancellation ?? null;
  if (flag === true) return { main: "Gratis", sub: v.price ? `según ${v.price.source}` : "según la ficha" };
  if (flag === false) return { main: "Con cargo", sub: v.price ? `según ${v.price.source}` : "según la ficha" };
  return { main: "sin verificar", sub: "la fuente no lo informa" };
}

export default function Ficha({ verdict: v, zone, nights, threadId, mock, canSend, onAsk }: Props) {
  const f = v.facts;
  const zoneLine = zoneText(f);
  const hasCoords = f?.latitude != null && f?.longitude != null;
  const back = href(`/t/${encodeURIComponent(threadId)}`, mock);
  const night = perNight(v, nights);
  const book = secondaryBooking(v);
  const currency = v.price?.currency ?? "USD";
  const web = isWebProperty(v);
  // Candidatos web: su página propia (facts.url; si falta, booking_url que para ellos es la misma página).
  const ownUrl = web ? (f?.url ?? f?.booking_url ?? null) : null;
  const ownDomain = domainOf(ownUrl);
  // Primero por total (la más barata arriba); las que solo publican precio por noche van después, ordenadas por noche.
  const offers = (f?.offers ?? [])
    .slice()
    .sort((a, b) => (a.total ?? Infinity) - (b.total ?? Infinity) || (a.per_night ?? Infinity) - (b.per_night ?? Infinity));
  const cheapest = offers.find((o) => o.total != null) ?? null;
  const cancel = cancellationLabel(v);
  const nearby = f?.nearby_places?.[0] ?? null;
  const sub = [subtitleOf(v), f?.address].filter(Boolean).join(" · ");
  const cons = [...v.red_flags.map((t) => ({ text: t, flag: true })), ...v.cons.map((t) => ({ text: t, flag: false }))];

  return (
    <article className="ficha" aria-label={`Ficha de ${v.name}`}>
      <div className="ficha__main">
        <a className="ficha__back" href={back}>
          ← Volver a los resultados
        </a>
        <header className="ficha__head">
          <h1 className="ficha__title">{v.name}</h1>
          {sub && <p className="ficha__sub">{sub}</p>}
          <span className="ficha__meta">
            <SourceBadge verdict={v} />
            {ownUrl && ownDomain && (
              <a className="ficha__own" href={ownUrl} target="_blank" rel="noreferrer noopener" title="Abre la página propia del alojamiento">
                Página propia: {ownDomain}
              </a>
            )}
            {zoneLine && <span className={`zone-tag${f?.zone_inside ? " zone-tag--in" : ""}`}>{zoneLine}</span>}
            {!v.recommend && <span className="pill pill--warn">Quedó fuera del ranking</span>}
          </span>
        </header>

        <div className="tiles">
          <div className="tile">
            <span className="tile__k">Reseñas</span>
            <span className="tile__v">
              {f?.overall_rating != null
                ? `${rating(f.overall_rating)}${f.reviews_count ? ` · ${plural(f.reviews_count, "opinión", "opiniones")}` : ""}`
                : web
                  ? "sin reseñas de Google"
                  : "sin puntaje"}
            </span>
            <span className="tile__s">
              {v.reviews_analyzed > 0 ? `${plural(v.reviews_analyzed, "reseña leída", "reseñas leídas")}` : web ? "no figura en Google Hotels" : "sin reseñas leídas"}
            </span>
          </div>
          <div className="tile">
            <span className="tile__k">Ubicación</span>
            <span className="tile__v">{f?.location_rating != null ? `${rating(f.location_rating)} de 5` : nearby ? nearby.name : "sin verificar"}</span>
            <span className="tile__s">{nearby ? `${nearby.name}${nearby.transportations[0] ? ` · ${nearby.transportations[0]}` : ""}` : f?.address ?? "sin lugares cercanos en la ficha"}</span>
          </div>
          <div className="tile">
            <span className="tile__k">Cancelación</span>
            <span className="tile__v">{cancel.main}</span>
            <span className="tile__s">{cancel.sub}</span>
          </div>
        </div>

        {(hasCoords || zone) && (
          <figure className="ficha__map">
            <ZoneMap
              center={null}
              zone={zone}
              markers={hasCoords && f ? [{ id: v.property_token, lat: f.latitude as number, lng: f.longitude as number, label: "•", title: v.name, subtitle: f.address ?? undefined, inside: f.zone_inside }] : []}
              readOnly
              height={220}
            />
            <figcaption className="results__mapcap">{hasCoords ? (zone ? `${v.name} y la zona ${zone.name ?? "marcada"}` : `Ubicación de ${v.name}`) : "La ficha no trae coordenadas; se muestra la zona marcada."}</figcaption>
          </figure>
        )}

        <div className="ficha__cols">
          <div className="box">
            <span className="box__h box__h--ok">Lo bueno</span>
            {v.pros.length ? v.pros.map((t, i) => <span key={i}>{t}</span>) : <span className="muted">Nada confirmado a favor todavía.</span>}
          </div>
          <div className="box">
            <span className="box__h box__h--warn">A tener en cuenta</span>
            {cons.length ? (
              cons.map((c, i) => (
                <span key={i} className={c.flag ? "box__flag" : undefined}>
                  {c.flag && <strong>Alerta: </strong>}
                  {c.text}
                </span>
              ))
            ) : (
              <span className="muted">Sin contras registradas.</span>
            )}
          </div>
        </div>

        {v.unknowns.length > 0 && (
          <div className="box box--muted">
            <span className="box__h">Sin verificar</span>
            {v.unknowns.map((t, i) => (
              <span key={i}>{t}</span>
            ))}
          </div>
        )}

        {(v.reviews_summary || v.location_summary) && (
          <div className="ficha__text">
            {v.reviews_summary && (
              <p>
                <strong>Reseñas.</strong> {v.reviews_summary}
                {v.reviews_analyzed > 0 && <span className="muted"> ({plural(v.reviews_analyzed, "reseña leída", "reseñas leídas")})</span>}
              </p>
            )}
            {v.location_summary && (
              <p>
                <strong>Ubicación.</strong> {v.location_summary}
              </p>
            )}
          </div>
        )}
      </div>

      <aside className="ficha__side">
        <div className="ficha__price">
          {v.price ? (
            <>
              <span className="ficha__amount">{money(v.price.total, v.price.currency)}</span>
              <span className="ficha__per">
                {nights > 0 ? `${plural(nights, "noche", "noches")}` : "estadía"}
                {night != null ? ` · ${money(night, v.price.currency)} por noche` : ""}
              </span>
              <span className="ficha__verified">{priceLine(v.price)}</span>
              {v.over_budget && <span className="pill pill--warn">Supera el presupuesto</span>}
            </>
          ) : (
            <span className="ficha__amount ficha__amount--none">precio no disponible</span>
          )}
        </div>

        {offers.length > 0 && (
          <div className="offers">
            <span className="kicker">El mismo alojamiento, {plural(offers.length, "precio", "precios")}</span>
            {offers.map((o, i) => {
              const best = cheapest != null && o === cheapest;
              // La fila cuyo enlace es el elegido por el backend (booking_url) es el "enlace directo" a esta propiedad.
              const direct = sameLink(o.link, f?.booking_url);
              const note = o.free_cancellation === true ? "cancelación gratis" : o.free_cancellation === false ? "sin cancelación gratis" : "cancelación sin verificar";
              // Ofertas de la web abierta: `source` ya es el dominio (ej. "ventosul.com.br") y `link` la página leída.
              const inner = (
                <>
                  <span className="offer__src">
                    <span className="offer__name">
                      {o.source}
                      {direct && <span className="offer__direct">enlace directo</span>}
                    </span>
                    <span className={`offer__note${o.free_cancellation === true ? " offer__note--ok" : ""}`}>{note}</span>
                  </span>
                  {o.total != null ? (
                    <span className="offer__total">{money(o.total, currency)}</span>
                  ) : o.per_night != null ? (
                    <span className="offer__total offer__total--night">
                      desde {money(o.per_night, currency)}
                      <span className="offer__unit"> / noche</span>
                    </span>
                  ) : (
                    <span className="offer__total offer__total--none">sin precio</span>
                  )}
                </>
              );
              return o.link ? (
                <a key={i} className={`offer${best ? " offer--best" : ""}`} href={o.link} target="_blank" rel="noreferrer noopener">
                  {inner}
                </a>
              ) : (
                <div key={i} className={`offer${best ? " offer--best" : ""}`}>
                  {inner}
                </div>
              );
            })}
          </div>
        )}

        <PrimaryCta verdict={v} className="btn btn--accent btn--block" />
        {book && (
          <a className="btn btn--outline btn--block" href={book.link} target="_blank" rel="noreferrer noopener">
            Reservar en {book.source}
          </a>
        )}
        <span className="ficha__legal">Te llevamos al sitio de la fuente. posta no cobra comisión ni toca tu pago.</span>

        <hr className="rule" />
        <div className="ask">
          <span className="ask__h">Preguntale a posta sobre este alojamiento</span>
          <div className="chips">
            {ASK.map((a) => (
              <button key={a.label} type="button" className="chip" disabled={!canSend} onClick={() => onAsk(a.text(v.name))}>
                {a.label}
              </button>
            ))}
          </div>
        </div>
      </aside>
    </article>
  );
}
