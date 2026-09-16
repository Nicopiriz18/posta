# Frontend — posta.

Vite + React 18 + TypeScript, CSS plano. Sin router ni UI kit (hash router propio en `App.tsx`).
Diseño de referencia: `design/posta.dc.html` (prototipo de Claude Design); los tokens viven en `src/styles.css`.

## Correr

```bash
cd frontend
npm install
npm run dev        # http://localhost:5173 — proxea /api a http://localhost:8000
npm run build      # tsc + vite build → frontend/dist (FastAPI lo sirve en producción)
```

El backend tiene que estar corriendo para conversar con el agente:

```bash
uv run uvicorn lodging.api.main:app --reload
```

## Rutas

- `#/` — home: el chat es la entrada (crea el hilo y manda el primer mensaje).
- `#/como-funciona` — home, desplazada a "Cómo funciona".
- `#/searches` — Mis búsquedas (estado, resultados, precios vencidos + Rebuscar, eliminar).
- `#/t/:id` — la conversación: entrevista con respuestas rápidas y panel "Lo que sabemos"; búsqueda en curso; resultados.
- `#/t/:id/p/:token` — ficha de un alojamiento del reporte (precios por fuente, lo bueno / a tener en cuenta).

## Revisar el diseño sin backend

```
http://localhost:5173/#/t/mock?mock=1       # conversación guionada: chips, búsqueda en curso, 4 resultados
http://localhost:5173/#/searches?mock=1     # lista con una búsqueda corriendo y una con precios vencidos
```

`src/mock/sampleReport.ts` guioniza la conversación (dos turnos con `suggestions`, confirmación, búsqueda en vivo y reporte con `facts.offers` para la ficha).

## Estructura

- `src/types.ts` — espejo de `src/lodging/schemas.py` y `docs/api.md`.
- `src/api.ts` — fetch + SSE tipados (un `EventSource` por hilo); `briefForRun` saca `nights` para Rebuscar.
- `src/format.ts` — fechas ("8 → 15 ene"), dinero ("US$ 1.155"), la línea obligatoria de precio verificado.
- `src/health.ts` — estado de `GET /api/health` y el aviso de claves faltantes.
- `src/views/HomeView.tsx`, `SearchesView.tsx`, `ThreadView.tsx`.
- `src/components/Conversation.tsx` — mensajes + eventos → turnos → burbujas, chips de respuestas rápidas y bloque de búsqueda.
- `src/components/SearchingPanel.tsx` — panel "Buscando" con contadores derivados de los eventos.
- `src/components/Results.tsx`, `ResultCard.tsx` — ranking sin top fijo (tarjetas para 1-3, filas compactas después), descartados, salvedades.
- `src/components/Ficha.tsx` — detalle con "El mismo alojamiento, N precios".
- `src/components/BriefPanel.tsx` — "Lo que sabemos", filas editables y atajo `POST /api/runs`.
- `src/components/Timeline.tsx` — detalle paso a paso (plegado bajo "Ver detalle").
- `src/components/DeleteAction.tsx` — eliminar en dos pasos.
- `src/styles.css` — tokens y estilos.
