import { useState, type FormEvent, type KeyboardEvent } from "react";
import { createThread, errorMessage, sendMessage } from "../api";
import { href, navigate } from "../App";

interface Props {
  mock: boolean;
  blocked: string | null;
}

const PLACEHOLDER = "Ej: “Somos 4, vamos a Punta del Este del 8 al 15 de enero, cerca del mar”";

/** Cada chip manda una frase de arranque razonable; el agente sigue preguntando lo que falte. */
const QUICK: { label: string; text: string }[] = [
  { label: "Fin de semana largo", text: "Quiero una escapada de fin de semana largo, 2 o 3 noches. Todavía no elegí el destino, ¿me ayudás a definirlo?" },
  { label: "Con niños", text: "Viajamos en familia con chicos. Buscamos un alojamiento cómodo para todos, con cocina si se puede." },
  { label: "Sorprendeme", text: "Sorprendeme: proponeme un destino para una escapada corta y buscamos alojamiento ahí." },
];

const STEPS = ["Contás el viaje.", "Unas pocas preguntas.", "Volvemos con las opciones que valen la pena y por qué."];

/** Copy honesto de fuentes (CLAUDE.md): Google Hotels agrega portales; la web abierta suma lo que no está ahí. Mismo texto en Fuentes y en Cómo funciona. */
const SOURCES_LINE = "Miramos en Google Hotels (Booking, Expedia, sitios directos y decenas de portales) y la web abierta: inmobiliarias, posadas y sitios propios.";

export default function HomeView({ mock, blocked }: Props) {
  const [text, setText] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const start = async (content: string) => {
    const msg = content.trim();
    if (!msg || busy) return;
    if (mock) {
      navigate("/t/mock", true);
      return;
    }
    if (blocked) return;
    setBusy(true);
    setError(null);
    try {
      const t = await createThread();
      await sendMessage(t.thread_id, msg);
      navigate(`/t/${encodeURIComponent(t.thread_id)}`);
    } catch (e) {
      setError(errorMessage(e));
      setBusy(false);
    }
  };

  /** Primero el mapa: crea el hilo, manda lo escrito (si hay) y abre el editor de zona en el hilo nuevo. */
  const startWithMap = async () => {
    if (busy) return;
    if (mock) {
      navigate("/t/mock?draw=1", true);
      return;
    }
    if (blocked) return;
    setBusy(true);
    setError(null);
    try {
      const t = await createThread();
      const msg = text.trim();
      if (msg) await sendMessage(t.thread_id, msg);
      navigate(`/t/${encodeURIComponent(t.thread_id)}?draw=1`);
    } catch (e) {
      setError(errorMessage(e));
      setBusy(false);
    }
  };

  const onSubmit = (e: FormEvent) => {
    e.preventDefault();
    void start(text);
  };
  const onKey = (e: KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      void start(text);
    }
  };

  const disabled = busy || (!mock && !!blocked);

  return (
    <div className="home">
      <section className="hero">
        <div className="hero__copy">
          <h1 className="hero__title">
            Contanos a dónde vas.
            <br />
            Buscamos en todos lados.
          </h1>
          <p className="hero__lede">
            Casas, apartamentos, hoteles, hostels, posadas. Te hacemos unas preguntas, revisamos las fuentes y volvemos con las opciones que
            valen la pena.
          </p>

          <form className="starter" onSubmit={onSubmit}>
            <label className="sr-only" htmlFor="starter-input">
              Contanos el viaje
            </label>
            <textarea
              id="starter-input"
              className="starter__input"
              rows={2}
              value={text}
              placeholder={PLACEHOLDER}
              onChange={(e) => setText(e.target.value)}
              onKeyDown={onKey}
              disabled={disabled}
              autoFocus
            />
            <div className="starter__bar">
              <div className="chips" role="group" aria-label="Para empezar rápido">
                {QUICK.map((q) => (
                  <button key={q.label} type="button" className="chip" disabled={disabled} onClick={() => void start(q.text)}>
                    {q.label}
                  </button>
                ))}
              </div>
              <button type="submit" className="btn btn--accent" disabled={disabled || !text.trim()}>
                {busy ? "Abriendo…" : "Empezar"}
              </button>
            </div>
            <div className="starter__alt">
              <button type="button" className="link-btn starter__map" disabled={disabled} onClick={() => void startWithMap()}>
                Marcar la zona en el mapa primero
              </button>
              <span className="starter__note">Dibujás el área donde querés quedarte y después seguimos con las preguntas.</span>
            </div>
            <p className="starter__note" aria-live="polite">
              {error ? (
                <span className="error">{error}</span>
              ) : blocked && !mock ? (
                blocked
              ) : mock ? (
                <>
                  Modo demostración: cualquier envío abre la <a href={href("/t/mock", true)}>conversación de ejemplo</a>.
                </>
              ) : (
                "Enter envía. Después te preguntamos lo que falte."
              )}
            </p>
          </form>

          <div className="sources">
            <span className="kicker">Fuentes</span>
            <p className="sources__line">{SOURCES_LINE}</p>
          </div>
        </div>

        <aside className="hero__side">
          <section className="how" id="como-funciona" aria-labelledby="how-h">
            <span className="kicker kicker--light" id="how-h">
              Cómo funciona
            </span>
            <ol className="how__steps">
              {STEPS.map((s, i) => (
                <li key={s}>
                  <span className="how__n">{i + 1}.</span> {s}
                </li>
              ))}
            </ol>
            <p className="how__note">{SOURCES_LINE}</p>
          </section>
          <div className="hero__aside-note">
            <p>Una búsqueda tarda entre uno y cuatro minutos y sigue en el servidor aunque cierres la pestaña.</p>
            <a href={href("/searches", mock)}>Mis búsquedas →</a>
          </div>
        </aside>
      </section>
    </div>
  );
}
