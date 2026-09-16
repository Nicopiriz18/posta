import { useEffect, useLayoutEffect, useRef, useState, type FocusEvent, type KeyboardEvent } from "react";

/**
 * Acción destructiva en dos pasos, sin diálogos del navegador: "Eliminar" → "¿Eliminar? Sí / No".
 * El segundo paso se deshace solo a los ~5 s, al perder el foco o con Escape.
 * El error lo guarda el padre (`error`): así sobrevive si la fila se desmonta al borrar optimistamente y vuelve.
 */
interface Props {
  /** Texto del disparador ("Eliminar", "Eliminar conversación"). */
  label: string;
  /** Pregunta del segundo paso. */
  question?: string;
  /** Hace el borrado. Si rechaza, el padre muestra el motivo vía `error`. */
  onConfirm: () => Promise<void> | void;
  /** Por qué no se puede borrar ahora (el agente está respondiendo). Va como tooltip y aparece al hacer clic. */
  disabledReason?: string | null;
  /** Modo demostración: la acción se ve, pero no hace nada. */
  mock?: boolean;
  error?: string | null;
  className?: string;
}

const REVERT_MS = 5000;

export default function DeleteAction({ label, question = "¿Eliminar?", onConfirm, disabledReason = null, mock = false, error = null, className }: Props) {
  const [armed, setArmed] = useState(false);
  const [busy, setBusy] = useState(false);
  const [hint, setHint] = useState<string | null>(null);

  const rootRef = useRef<HTMLSpanElement>(null);
  const triggerRef = useRef<HTMLButtonElement>(null);
  const yesRef = useRef<HTMLButtonElement>(null);
  const refocusTrigger = useRef(false);
  const mounted = useRef(true);

  useEffect(() => {
    mounted.current = true;
    return () => {
      mounted.current = false;
    };
  }, []);

  // Se deshace solo si el usuario no decide.
  useEffect(() => {
    if (!armed || busy) return;
    const id = window.setTimeout(() => setArmed(false), REVERT_MS);
    return () => window.clearTimeout(id);
  }, [armed, busy]);

  useEffect(() => {
    if (!hint) return;
    const id = window.setTimeout(() => setHint(null), REVERT_MS);
    return () => window.clearTimeout(id);
  }, [hint]);

  // Foco: al armar va a "Sí" (Enter confirma, Escape cancela); al cancelar con teclado vuelve al disparador.
  // Layout effect para que el foco ya esté adentro cuando corra el chequeo diferido de onBlur.
  useLayoutEffect(() => {
    if (armed) {
      yesRef.current?.focus();
    } else if (refocusTrigger.current) {
      refocusTrigger.current = false;
      triggerRef.current?.focus();
    }
  }, [armed]);

  const blocked = mock ? "Modo demostración: no se elimina nada." : disabledReason;

  const disarm = (restoreFocus: boolean) => {
    if (busy) return;
    refocusTrigger.current = restoreFocus;
    setArmed(false);
  };

  const onTrigger = () => {
    if (mock) return;
    if (blocked) {
      setHint(blocked);
      return;
    }
    setArmed(true);
  };

  const confirm = async () => {
    if (busy) return;
    setBusy(true);
    try {
      await onConfirm();
    } finally {
      if (mounted.current) {
        setBusy(false);
        setArmed(false);
      }
    }
  };

  const onKeyDown = (e: KeyboardEvent<HTMLSpanElement>) => {
    if (e.key === "Escape" && armed) {
      e.stopPropagation();
      disarm(true);
    }
  };

  const onBlur = (e: FocusEvent<HTMLSpanElement>) => {
    if (!armed) return;
    const root = e.currentTarget;
    // Diferido: el disparador se desmonta al armar y algunos navegadores disparan blur ahí, antes de que "Sí" tome el foco.
    window.setTimeout(() => {
      if (mounted.current && rootRef.current === root && !root.contains(document.activeElement)) setArmed(false);
    }, 0);
  };

  return (
    <span ref={rootRef} className={`delete${className ? ` ${className}` : ""}`} onKeyDown={onKeyDown} onBlur={onBlur}>
      {armed ? (
        <span className="delete__confirm">
          <span className="delete__q">{question}</span>
          <button
            ref={yesRef}
            type="button"
            className="delete__btn delete__yes"
            aria-label={`Sí, ${label.toLowerCase()}`}
            disabled={busy}
            onClick={() => void confirm()}
          >
            {busy ? "Eliminando…" : "Sí"}
          </button>
          <button type="button" className="delete__btn delete__no" aria-label="No, conservar" disabled={busy} onClick={() => disarm(true)}>
            No
          </button>
        </span>
      ) : (
        <button
          ref={triggerRef}
          type="button"
          className="delete__btn delete__trigger"
          aria-disabled={blocked ? true : undefined}
          title={blocked ?? undefined}
          onClick={onTrigger}
        >
          {label}
        </button>
      )}
      {mock && <span className="delete__note">modo demostración</span>}
      {!mock && hint && (
        <span className="delete__note" role="status">
          {hint}
        </span>
      )}
      {error && (
        <span className="delete__note delete__note--error" role="alert">
          {error}
        </span>
      )}
    </span>
  );
}
