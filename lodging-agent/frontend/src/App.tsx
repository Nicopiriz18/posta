import { useEffect, useState } from "react";
import { healthBanner, healthBlocked, useHealth } from "./health";
import HomeView from "./views/HomeView";
import SearchesView from "./views/SearchesView";
import ThreadView from "./views/ThreadView";

export type Route =
  | { view: "home"; mock: boolean; section: "how" | null }
  | { view: "searches"; mock: boolean }
  | { view: "thread"; id: string; property: string | null; mock: boolean; draw: boolean };

/** `?mock=1` puede venir antes del hash (`/?mock=1#/t/mock`) o dentro (`#/t/mock?mock=1`). */
function parseHash(hash: string, search: string): Route {
  const raw = hash.replace(/^#/, "") || "/";
  const [path, hashQuery = ""] = raw.split("?", 2);
  const hq = new URLSearchParams(hashQuery);
  const mock = new URLSearchParams(search).get("mock") === "1" || hq.get("mock") === "1";
  const draw = hq.get("draw") === "1"; // `#/t/:id?draw=1`: abre el editor de zona al entrar
  const m = path.match(/^\/(?:t|runs)\/([^/?]+)(?:\/p\/([^/?]+))?/);
  if (m) return { view: "thread", id: decodeURIComponent(m[1]), property: m[2] ? decodeURIComponent(m[2]) : null, mock, draw };
  if (/^\/searches\/?$/.test(path)) return { view: "searches", mock };
  if (/^\/como-funciona\/?$/.test(path)) return { view: "home", mock, section: "how" };
  return { view: "home", mock, section: null };
}

/** Arma un enlace conservando el modo demostración. */
export function href(path: string, mock: boolean): string {
  return `#${path}${mock ? (path.includes("?") ? "&" : "?") + "mock=1" : ""}`;
}

export function navigate(path: string, mock = false): void {
  window.location.hash = href(path, mock).slice(1);
}

function useRoute(): Route {
  const [route, setRoute] = useState<Route>(() => parseHash(window.location.hash, window.location.search));
  useEffect(() => {
    const onChange = () => setRoute(parseHash(window.location.hash, window.location.search));
    window.addEventListener("hashchange", onChange);
    return () => window.removeEventListener("hashchange", onChange);
  }, []);
  return route;
}

export function Wordmark({ className }: { className?: string }) {
  return (
    <span className={`wordmark${className ? ` ${className}` : ""}`}>
      posta<span className="wordmark__dot">.</span>
    </span>
  );
}

export default function App() {
  const route = useRoute();
  const health = useHealth(!route.mock);
  const banner = route.mock ? null : healthBanner(health);
  const blocked = route.mock ? null : healthBlocked(health);
  const mock = route.mock;

  // Al cambiar de vista se vuelve arriba (el hash router no lo hace solo).
  useEffect(() => {
    if (route.view === "home" && route.section === "how") {
      document.getElementById("como-funciona")?.scrollIntoView({ block: "start" });
      return;
    }
    window.scrollTo(0, 0);
  }, [route]);

  return (
    <div className="app">
      <header className="topbar">
        <a className="topbar__brand" href={href("/", mock)} aria-label="posta, inicio">
          <Wordmark />
        </a>
        <nav className="topbar__nav" aria-label="Principal">
          <a href={href("/como-funciona", mock)}>Cómo funciona</a>
          <a href={href("/searches", mock)} aria-current={route.view === "searches" ? "page" : undefined}>
            Mis búsquedas
          </a>
          {mock && <span className="topbar__mock">demo</span>}
        </nav>
      </header>
      <main className="page">
        {banner && (
          <div className="notice" role="status">
            <strong>{banner.title}</strong>
            <span>{banner.body}</span>
          </div>
        )}
        {route.view === "home" && <HomeView mock={mock} blocked={blocked} />}
        {route.view === "searches" && <SearchesView mock={mock} blocked={blocked} />}
        {route.view === "thread" && <ThreadView key={route.id} threadId={route.id} property={route.property} draw={route.draw} mock={mock} blocked={blocked} />}
      </main>
    </div>
  );
}
