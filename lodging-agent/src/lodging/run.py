"""CLI: `uv run python -m lodging.run --brief <path>`.

Corre el pipeline e imprime progreso + reporte. Escribe `runs/<id>/{brief,final,assertions}.json`.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
from pathlib import Path

from dotenv import load_dotenv

from lodging import config
from lodging.schemas import Brief, FinalReport, RunEvent


def _print_event(ev: RunEvent, verbose: bool = False) -> None:
    if ev.type == "model" and not verbose:
        return  # telemetría del modelo: solo con -v (queda siempre en events.jsonl)
    if ev.type == "turn_done":
        return
    tags = {
        "tool_call": "→",
        "tool_result": "←",
        "verdict": "★",
        "warning": "!",
        "phase": "■",
        "assistant": "💬",
        "model": "⏱",
    }
    print(f"{ev.ts[11:19]} {tags.get(ev.type, '·')} [{ev.agent}] {ev.message}", flush=True)


def print_report(report: FinalReport) -> None:
    lang = report.language
    print("\n" + "=" * 78)
    print(
        f"REPORTE {report.run_id} — {report.brief.destination} ({report.brief.check_in} → {report.brief.check_out})"
    )
    print("=" * 78)
    print(report.summary, "\n")
    for r in report.ranking:
        v = r.verdict
        price = (
            v.price.display(lang)
            if v.price
            else ("price not available" if lang.startswith("en") else "precio no disponible")
        )
        print(f"#{r.rank}  {v.name}  — fit {v.fit_score}/100")
        print(f"     {price}")
        if v.link:
            print(f"     {v.link}")
        print(f"     {r.rationale}")
        if v.pros:
            print("     + " + " | ".join(v.pros[:4]))
        if v.cons:
            print("     - " + " | ".join(v.cons[:4]))
        if v.unknowns:
            print("     ? " + " | ".join(v.unknowns[:4]))
        print()
    if report.discarded:
        print("Descartados:")
        for d in report.discarded:
            print(f"  · {d.name} [{d.stage}]: {d.reason}")
    if report.caveats:
        print("\nCaveats:")
        for c in report.caveats:
            print(f"  · {c}")
    s = report.stats
    print(
        f"\n{s.candidates_found} candidatos, {s.shortlisted} preseleccionados, {s.analyzed} analizados, "
        f"{s.serpapi_calls} llamadas SerpAPI, {s.duration_seconds}s" + (" (TIMEOUT)" if s.timed_out else "")
    )


async def _main(args: argparse.Namespace) -> int:
    from lodging.runner import run_brief
    from lodging.runtime import RunContext

    brief = Brief.model_validate(json.loads(Path(args.brief).read_text(encoding="utf-8")))
    ctx = RunContext()  # el agente guarda y confirma el brief él mismo (modo headless)
    ctx.queue = asyncio.Queue()

    async def printer():
        while True:
            ev = await ctx.queue.get()
            if ev is None:
                return
            if not args.quiet:
                _print_event(ev, verbose=args.verbose)

    printer_task = asyncio.create_task(printer())
    result = await run_brief(brief, ctx=ctx, timeout=args.timeout)
    await printer_task
    print_report(result.report)
    if result.assertions:
        failed = [k for k, v in result.assertions["checks"].items() if not v["ok"]]
        print(f"\nAssertions: {'OK' if not failed else 'FALLAN ' + ', '.join(failed)}")
    print(f"Artefactos en {config.get_settings().runs_dir / ctx.run_id}")
    return 0 if result.report.ranking else 1


def main(argv: list[str] | None = None) -> int:
    load_dotenv()
    p = argparse.ArgumentParser(description="Deep research de alojamientos a partir de un brief JSON.")
    p.add_argument("--brief", required=True, help="Ruta al brief JSON (ver evals/goldens/)")
    p.add_argument("--timeout", type=float, default=config.RUN_TIMEOUT_SECONDS)
    p.add_argument("--quiet", action="store_true", help="No imprimir el progreso")
    p.add_argument("-v", "--verbose", action="store_true")
    args = p.parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.WARNING, format="%(levelname)s %(name)s: %(message)s"
    )
    s = config.get_settings()
    missing = [
        k for k, v in (("SERPAPI_KEY", s.serpapi_key), ("ANTHROPIC_API_KEY", s.anthropic_api_key)) if not v
    ]
    if missing:
        print(f"Faltan variables en .env: {', '.join(missing)}", file=sys.stderr)
        return 2
    return asyncio.run(_main(args))


if __name__ == "__main__":
    sys.exit(main())
