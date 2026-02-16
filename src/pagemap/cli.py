"""Page Map CLI: validate, build, serve, benchmark, collect, convert commands.

Usage:
    python -m pagemap.cli validate [--url URL] [--all]
    python -m pagemap.cli build [--url URL] [--snapshots] [--output DIR]
    python -m pagemap.cli serve
    python -m pagemap.cli benchmark [--static] [--live] [--sim-live] [--sim-static] [--task ID] [--model MODEL] [--force] [--conditions CONDS]
    python -m pagemap.cli collect [--site SITE] [--type TYPE] [--count N] [--all] [--simulator]
    python -m pagemap.cli convert [--tool TOOL] [--snapshot-dir DIR] [--force] [--pilot]
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
from pathlib import Path


def _require_cli_deps() -> None:
    """Check that CLI optional dependencies are installed."""
    try:
        import yaml  # noqa: F401
        from tabulate import tabulate  # noqa: F401
    except ImportError as e:
        print(
            f"Missing CLI dependency: {e.name}\n"
            f"Install with: pip install retio-pagemap[cli]",
            file=sys.stderr,
        )
        sys.exit(1)


def _validate_output_path(path_str: str | None) -> Path | None:
    """Validate and return an output path, or None if not specified."""
    if not path_str:
        return None
    p = Path(path_str)
    parent = p.parent if p.suffix else p
    if not parent.exists():
        parent.mkdir(parents=True, exist_ok=True)
    return p


def _has_internal() -> bool:
    """Check if internal development modules are available."""
    try:
        from . import collect  # noqa: F401

        return True
    except ImportError:
        return False


def _has_benchmark() -> bool:
    """Check if benchmark modules are available."""
    try:
        from . import benchmark  # noqa: F401

        return True
    except ImportError:
        return False


def _benchmark_postflight(
    result: object,
    tasks: list[dict],
    report_func: callable,
    save_func: callable,
    result_path: Path,
    report_path: Path,
) -> str:
    """Common benchmark post-processing: evaluate, report, save."""
    from .benchmark.evaluator import evaluate_all

    evaluate_all(tasks, result.task_results)
    report = report_func(result, tasks)
    print("\n" + report)
    save_func(result, result_path)
    report_path.write_text(report)
    print(f"\nResults saved to {result_path.parent}")
    return report


def cmd_validate(args: argparse.Namespace) -> None:
    """Run AX Tree validation (Day 0)."""
    _require_cli_deps()
    import yaml

    from .validate_axtree import print_validation_report, validate_urls

    config_path = Path(__file__).parent / "config.yaml"
    with open(config_path) as f:
        config = yaml.safe_load(f)

    if args.url:
        urls = [args.url]
    elif args.all:
        urls = []
        for _site_id, site in config["sites"].items():
            if site.get("collection") == "manual_only":
                continue
            site_urls = site.get("urls", {})
            if isinstance(site_urls, dict):
                # Phase 1 format: {page_type: [urls]}
                for page_type_urls in site_urls.values():
                    urls.extend(page_type_urls[:1])
            else:
                urls.extend(site_urls[:2])
    else:
        coupang_urls = config["sites"]["coupang"]["urls"]
        if isinstance(coupang_urls, dict):
            urls = list(coupang_urls.values())[0][:1]
        else:
            urls = coupang_urls[:1]

    results = asyncio.run(validate_urls(urls))
    print_validation_report(results)

    if args.save:
        save_path = _validate_output_path(args.save)
        save_data = [
            {
                "url": r.url,
                "tier12_count": r.tier12_count,
                "tier3_count": r.tier3_count,
                "coverage": r.tier12_coverage,
            }
            for r in results
        ]
        save_path.write_text(json.dumps(save_data, ensure_ascii=False, indent=2))
        print(f"\nSaved to {save_path}")


def cmd_build(args: argparse.Namespace) -> None:
    """Build Page Maps from URLs or snapshots."""
    output_dir = _validate_output_path(args.output) or Path(__file__).parent / "data"
    output_dir.mkdir(parents=True, exist_ok=True)

    snapshot_dir = Path(args.snapshot_dir) if getattr(args, "snapshot_dir", None) else None

    if args.url:
        asyncio.run(_build_live(args.url, output_dir))
    elif args.snapshots:
        asyncio.run(_build_from_snapshots(output_dir, snapshot_dir=snapshot_dir))
    else:
        _build_offline(output_dir)


async def _build_live(url: str, output_dir: Path) -> None:
    """Build Page Map from live URL."""
    from .browser_session import BrowserSession
    from .page_map_builder import build_page_map_live
    from .serializer import to_agent_prompt, to_json

    async with BrowserSession() as session:
        page_map = await build_page_map_live(session, url)

        json_path = output_dir / "live_page_map.json"
        json_path.write_text(to_json(page_map), encoding="utf-8")

        prompt_path = output_dir / "live_page_map.txt"
        prompt_path.write_text(to_agent_prompt(page_map, include_meta=True), encoding="utf-8")

        print(f"Page Map saved to {json_path}")
        print(f"Agent prompt saved to {prompt_path}")
        print(f"\nInteractables: {page_map.total_interactables}")
        print(f"Pruned tokens: {page_map.pruned_tokens}")
        print(f"Generation: {page_map.generation_ms:.0f}ms")


async def _build_from_snapshots(output_dir: Path, snapshot_dir: Path | None = None) -> None:
    """Build Page Maps from all snapshots using browser for AX tree."""
    _require_cli_deps()
    from tabulate import tabulate

    from pagemap.preprocessing.preprocess import count_tokens

    from .browser_session import BrowserSession
    from .page_map_builder import build_page_map_from_snapshot
    from .serializer import to_agent_prompt, to_json

    if snapshot_dir is None:
        # Default: project_root / data / snapshots
        snapshot_dir = Path(__file__).parent.parent.parent / "data" / "snapshots"
    snapshots_dir = snapshot_dir

    results = []
    async with BrowserSession() as session:
        for site_dir in sorted(snapshots_dir.iterdir()):
            if not site_dir.is_dir():
                continue
            for page_dir in sorted(site_dir.iterdir()):
                if not page_dir.is_dir():
                    continue
                if not (page_dir / "raw.html").exists():
                    continue

                site_id = site_dir.name
                page_id = page_dir.name

                try:
                    page_map = await build_page_map_from_snapshot(
                        session,
                        page_dir,
                        enable_tier3=False,
                    )

                    # Save
                    out_site = output_dir / site_id
                    out_site.mkdir(parents=True, exist_ok=True)

                    json_path = out_site / f"{page_id}.json"
                    json_path.write_text(to_json(page_map), encoding="utf-8")

                    prompt_path = out_site / f"{page_id}.txt"
                    prompt = to_agent_prompt(page_map, include_meta=True)
                    prompt_path.write_text(prompt, encoding="utf-8")

                    total_tokens = count_tokens(prompt)
                    results.append(
                        [
                            site_id,
                            page_id,
                            page_map.total_interactables,
                            page_map.pruned_tokens,
                            total_tokens,
                            f"{page_map.generation_ms:.0f}ms",
                        ]
                    )
                except Exception as e:
                    print(f"  ERROR {site_id}/{page_id}: {e}")
                    results.append([site_id, page_id, "-", "-", "-", f"ERROR: {e}"])

    headers = ["Site", "Page", "Interactables", "Pruned Tok", "Total Tok", "Time"]
    print(tabulate(results, headers=headers, tablefmt="simple"))
    print(f"\nOutput: {output_dir}")


def _build_offline(output_dir: Path) -> None:
    """Build Page Maps offline (no browser, no interactables)."""
    _require_cli_deps()
    from tabulate import tabulate

    from pagemap.preprocessing.preprocess import count_tokens

    from .page_map_builder import build_page_map_offline
    from .serializer import to_agent_prompt, to_json

    snapshots_dir = Path(__file__).parent.parent.parent.parent / "data" / "snapshots"

    # Domain → schema mapping
    domain_schema = {
        "coupang": "Product",
        "musinsa": "Product",
        "29cm": "Product",
        "kurly": "Product",
        # Phase 1: Fashion e-commerce
        "wconcept": "Product",
        "ssfshop": "Product",
        "handsome": "Product",
        "zara": "Product",
        "cos": "Product",
        "hm": "Product",
        "uniqlo": "Product",
        "nike": "Product",
        # Non-ecommerce
        "naver_news": "NewsArticle",
        "bbc_korean": "NewsArticle",
        "wikipedia_ko": "WikiArticle",
        "github": "SaaSPage",
        "govkr": "GovernmentPage",
    }

    results = []
    for site_dir in sorted(snapshots_dir.iterdir()):
        if not site_dir.is_dir():
            continue
        for page_dir in sorted(site_dir.iterdir()):
            if not page_dir.is_dir():
                continue

            raw_path = page_dir / "raw.html"
            meta_path = page_dir / "snapshot.json"
            if not raw_path.exists():
                continue

            site_id = site_dir.name
            page_id = page_dir.name
            raw_html = raw_path.read_text(encoding="utf-8")

            meta = {}
            if meta_path.exists():
                meta = json.loads(meta_path.read_text(encoding="utf-8"))

            url = meta.get("url", f"file://{page_dir}")
            schema = domain_schema.get(site_id, "Product")

            try:
                page_map = build_page_map_offline(
                    raw_html=raw_html,
                    url=url,
                    site_id=site_id,
                    page_id=page_id,
                    schema_name=schema,
                )

                out_site = output_dir / site_id
                out_site.mkdir(parents=True, exist_ok=True)

                json_path = out_site / f"{page_id}.json"
                json_path.write_text(to_json(page_map), encoding="utf-8")

                prompt = to_agent_prompt(page_map, include_meta=True)
                prompt_path = out_site / f"{page_id}.txt"
                prompt_path.write_text(prompt, encoding="utf-8")

                total_tokens = count_tokens(prompt)
                results.append(
                    [
                        site_id,
                        page_id,
                        0,  # No interactables in offline mode
                        page_map.pruned_tokens,
                        total_tokens,
                        f"{page_map.generation_ms:.0f}ms",
                    ]
                )
            except Exception as e:
                print(f"  ERROR {site_id}/{page_id}: {e}")
                results.append([site_id, page_id, "-", "-", "-", f"ERROR: {e}"])

    headers = ["Site", "Page", "Interactables", "Pruned Tok", "Total Tok", "Time"]
    print(tabulate(results, headers=headers, tablefmt="simple"))
    print(f"\nOutput: {output_dir}")


def cmd_serve(args: argparse.Namespace) -> None:
    """Start MCP server."""
    from .server import main

    main()


def cmd_collect(args: argparse.Namespace) -> None:
    """Collect page snapshots for benchmarking."""
    if args.simulator:
        from .collect_sim import SimulatorController, collect_all_sim, collect_site_sim, load_config

        config = load_config()
        if args.all:
            collect_all_sim(config=config, count=args.count)
        elif args.site:
            controller = SimulatorController(config)
            # Ensure simulator is ready
            if not controller.get_booted_device_udid():
                controller.boot_device()
            controller.launch_app()
            if not controller.wait_for_ping():
                print("ERROR: App not responding. Is Retio DEBUG build installed?")
                sys.exit(1)

            page_types = [args.page_type] if args.page_type else None
            collect_site_sim(
                site_id=args.site,
                controller=controller,
                config=config,
                page_types=page_types,
                count=args.count,
            )
        else:
            print("Specify --site SITE or --all")
            sys.exit(1)
    else:
        from .collect import collect_all, collect_site

        if args.all:
            asyncio.run(collect_all(count=args.count))
        elif args.site:
            page_types = [args.page_type] if args.page_type else None
            asyncio.run(
                collect_site(
                    site_id=args.site,
                    page_types=page_types,
                    count=args.count,
                )
            )
        else:
            print("Specify --site SITE or --all")
            sys.exit(1)


def cmd_convert(args: argparse.Namespace) -> None:
    """Convert snapshots using competitor tools."""
    from .benchmark.converters import CONVERTERS, convert_all_pages

    # Determine snapshot directory
    snapshot_dir = Path(args.snapshot_dir) if args.snapshot_dir else None
    if snapshot_dir is None:
        # Default: retio project snapshots (sibling project)
        default_snap = Path(__file__).parent.parent.parent.parent / "data" / "snapshots"
        if default_snap.exists():
            snapshot_dir = default_snap
        else:
            print("ERROR: Snapshot directory not found. Use --snapshot-dir to specify.")
            sys.exit(1)

    # Determine tools
    if args.tool == "all":
        tools = list(CONVERTERS.keys())
    else:
        tools = [args.tool]

    # Determine output directory
    output_dir = _validate_output_path(args.output)

    print(f"Converting with tools: {tools}")
    print(f"Snapshot dir: {snapshot_dir}")
    if args.pilot:
        print(f"Pilot mode: {args.pilot_count} pages per site")

    results = asyncio.run(
        convert_all_pages(
            snapshot_dir=snapshot_dir,
            output_dir=output_dir,
            tools=tools,
            force=args.force,
            pilot=args.pilot,
            pilot_count=args.pilot_count,
        )
    )

    # Summary
    for tool_name, tool_results in results.items():
        success = sum(1 for r in tool_results if r.status == "success")
        errors = sum(1 for r in tool_results if r.status == "error")
        print(f"\n{tool_name}: {success} success, {errors} errors")
        if tool_results:
            avg_tokens = sum(r.token_count for r in tool_results if r.status == "success")
            count = max(1, success)
            print(f"  Avg tokens: {avg_tokens // count:,}")


def cmd_benchmark(args: argparse.Namespace) -> None:
    """Run benchmark."""
    data_dir = Path(__file__).parent / "data"
    data_dir.mkdir(parents=True, exist_ok=True)

    if args.sim_static:
        _run_sim_benchmark(args, data_dir, mode="sim_static")
    elif args.sim_live:
        _run_sim_benchmark(args, data_dir, mode="sim_live")
    elif args.live:
        _run_live_benchmark(args, data_dir)
    else:
        _run_static_benchmark(args, data_dir)


def _run_static_benchmark(args: argparse.Namespace, data_dir: Path) -> None:
    """Run static benchmark (multi-condition comparison)."""
    from .benchmark.converters import load_converted_pages
    from .benchmark.report import (
        generate_full_report,
        load_static_results,
        save_results_json,
    )
    from .benchmark.runner import ALL_CONDITIONS, BASE_CONDITIONS, load_tasks, run_static_benchmark

    result_path = data_dir / "benchmark_results.json"
    report_path = data_dir / "benchmark_report.md"
    tasks = load_tasks(mode="static")
    force = getattr(args, "force", False)
    task_filter = getattr(args, "task", None)

    # Determine conditions
    conditions_arg = getattr(args, "conditions", None)
    if conditions_arg == "all":
        conditions = list(ALL_CONDITIONS)
    elif conditions_arg:
        conditions = [c.strip() for c in conditions_arg.split(",")]
    else:
        conditions = list(BASE_CONDITIONS)

    # Filter tasks if --task specified
    if task_filter:
        tasks = [t for t in tasks if t["id"] == task_filter]
        if not tasks:
            print(f"Task '{task_filter}' not found in static tasks.")
            sys.exit(1)

    # Pre-flight: load existing results for dedup
    existing = []
    if not force and result_path.exists():
        existing = load_static_results(result_path)
        existing_pairs = {(r.task_id, r.condition) for r in existing}
        print(f"Existing results: {len(existing_pairs)} (task, condition) pairs")

        requested_set = set(conditions)
        task_ids = {t["id"] for t in tasks}
        needed_pairs = {(tid, c) for tid in task_ids for c in requested_set}
        if needed_pairs.issubset(existing_pairs):
            print(f"All {len(needed_pairs)} pairs completed. Use --force to re-run.")
            from .benchmark.runner import BenchmarkResult

            result = BenchmarkResult(task_results=existing, conditions=conditions)
            _benchmark_postflight(
                result, tasks, generate_full_report, save_results_json,
                result_path, report_path,
            )
            return

    # Load PageMap agent prompt files
    page_maps: dict[str, str] = {}
    for txt_file in data_dir.rglob("*.txt"):
        site_id = txt_file.parent.name
        page_id = txt_file.stem
        page_maps[f"{site_id}/{page_id}"] = txt_file.read_text(encoding="utf-8")

    # Load converted pages for competitor conditions
    converted_pages: dict[str, dict[str, str]] | None = None
    competitor_conds = set(conditions) & {"firecrawl", "jina_reader", "readability"}
    if competitor_conds:
        converted_pages = load_converted_pages()
        loaded_tools = list(converted_pages.keys()) if converted_pages else []
        print(f"Loaded converted pages for: {loaded_tools}")

    print(f"Running {len(tasks)} static tasks with {len(page_maps)} page maps...")
    print(f"Conditions: {conditions}")
    result = asyncio.run(
        run_static_benchmark(
            tasks=tasks,
            page_maps=page_maps,
            existing_results=existing if existing else None,
            save_path=result_path,
            converted_pages=converted_pages,
            conditions=conditions,
        )
    )

    _benchmark_postflight(
        result, tasks, generate_full_report, save_results_json,
        result_path, report_path,
    )


def _run_live_benchmark(args: argparse.Namespace, data_dir: Path) -> None:
    """Run live benchmark (multi-turn agentic loop)."""
    from .benchmark.report import (
        generate_combined_judgment,
        generate_live_report,
        load_live_results,
        save_live_results_json,
    )
    from .benchmark.runner import LiveBenchmarkResult, load_tasks, run_live_benchmark

    result_path = data_dir / "live_benchmark_results.json"
    report_path = data_dir / "live_benchmark_report.md"
    tasks = load_tasks(mode="live")
    force = getattr(args, "force", False)

    # Pre-flight: load existing results for dedup
    existing = []
    if not force and result_path.exists():
        existing = load_live_results(result_path)
        succeeded = {r.task_id for r in existing if r.answer and not r.error}
        print(f"Existing results: {len(succeeded)}/{len(tasks)} tasks completed")
        if len(succeeded) >= len(tasks):
            print(f"All {len(tasks)} tasks completed. Use --force to re-run.")
            result = LiveBenchmarkResult(task_results=existing)
            _benchmark_postflight(
                result, tasks, generate_live_report, save_live_results_json,
                result_path, report_path,
            )
            return

    print(f"Running {len(tasks)} live tasks...\n")

    result = asyncio.run(
        run_live_benchmark(
            tasks=tasks,
            existing_results=existing if existing else None,
            save_path=result_path,
        )
    )

    report = _benchmark_postflight(
        result, tasks, generate_live_report, save_live_results_json,
        result_path, report_path,
    )

    # Combined judgment (use static results if available)
    static_results_path = data_dir / "benchmark_results.json"
    static_rate = 0.0
    if static_results_path.exists():
        static_data = json.loads(static_results_path.read_text())
        pm_agg = static_data.get("aggregate", {}).get("page_map", {})
        static_rate = pm_agg.get("success_rate", 0.0)
        print(f"(Using static benchmark result: {static_rate:.1%})")

    judgment = generate_combined_judgment(static_rate, result)
    print("\n" + judgment)
    # Append judgment to report file
    report_path.write_text(report + "\n" + judgment)


def _run_sim_benchmark(args: argparse.Namespace, data_dir: Path, mode: str) -> None:
    """Run simulator-based benchmark (sim_live or sim_static)."""
    from .benchmark.report import (
        generate_live_report,
        load_live_results,
        save_live_results_json,
    )
    from .benchmark.runner import LiveBenchmarkResult, load_tasks

    result_path = data_dir / f"{mode}_benchmark_results.json"
    task_id = getattr(args, "task", None)
    model = getattr(args, "model", "claude-sonnet-4-5-20250929")
    force = getattr(args, "force", False)

    tasks = load_tasks(mode=mode)

    # Pre-flight: load existing results for dedup
    existing = []
    if not force and result_path.exists():
        existing = load_live_results(result_path)
        succeeded = {r.task_id for r in existing if r.answer and not r.error}
        target_tasks = [t for t in tasks if t["id"] == task_id] if task_id else tasks
        total = len(target_tasks)
        print(f"Existing results: {len(succeeded)}/{total} tasks completed")
        if len(succeeded) >= total:
            print(f"All {total} tasks completed. Use --force to re-run.")
            result = LiveBenchmarkResult(task_results=existing)
            _benchmark_postflight(
                result, tasks, generate_live_report, save_live_results_json,
                result_path, data_dir / f"{mode}_benchmark_report.md",
            )
            return

    if task_id:
        print(f"Running single {mode} task: {task_id}")
    else:
        print(f"Running {len(tasks)} {mode} tasks...\n")

    runner_kwargs = dict(
        tasks=tasks,
        task_id=task_id,
        model=model,
        existing_results=existing if existing else None,
        save_path=result_path,
    )

    if mode == "sim_live":
        from .benchmark.runner import run_sim_live_benchmark
        result = run_sim_live_benchmark(**runner_kwargs)
    else:
        from .benchmark.runner import run_sim_static_benchmark
        result = asyncio.run(run_sim_static_benchmark(**runner_kwargs))

    _benchmark_postflight(
        result, tasks, generate_live_report, save_live_results_json,
        result_path, data_dir / f"{mode}_benchmark_report.md",
    )


def main() -> None:
    """CLI entry point."""
    parser = argparse.ArgumentParser(
        description="Page Map CLI",
        prog="python -m pagemap.cli",
    )
    parser.add_argument("-v", "--verbose", action="store_true")
    subparsers = parser.add_subparsers(dest="command", required=True)

    # Always available
    p_build = subparsers.add_parser("build", help="Build Page Maps")
    p_build.add_argument("--url", type=str)
    p_build.add_argument("--snapshots", action="store_true", help="Build from snapshots with browser")
    p_build.add_argument("--snapshot-dir", type=str, help="Snapshot directory (default: data/snapshots)")
    p_build.add_argument("--output", type=str)

    subparsers.add_parser("serve", help="Start MCP server")

    commands = {"build": cmd_build, "serve": cmd_serve}

    # Development-only: internal tools
    if _has_internal():
        p_validate = subparsers.add_parser("validate", help="Run AX Tree validation")
        p_validate.add_argument("--url", type=str)
        p_validate.add_argument("--all", action="store_true")
        p_validate.add_argument("--save", type=str)

        p_collect = subparsers.add_parser("collect", help="Collect page snapshots")
        p_collect.add_argument("--site", type=str, help="Site ID (e.g., musinsa, zara)")
        p_collect.add_argument(
            "--type", type=str, dest="page_type", help="Page type (product_detail, search_results, listing)"
        )
        p_collect.add_argument("--count", type=int, default=3, help="Pages per type (default: 3)")
        p_collect.add_argument("--all", action="store_true", help="Collect all sites")
        p_collect.add_argument("--simulator", action="store_true", help="Use iOS Simulator instead of Playwright")

        commands["validate"] = cmd_validate
        commands["collect"] = cmd_collect

    # Development-only: benchmark tools
    if _has_benchmark():
        p_convert = subparsers.add_parser("convert", help="Convert snapshots with competitor tools")
        p_convert.add_argument(
            "--tool",
            type=str,
            default="all",
            choices=["firecrawl", "jina_reader", "readability", "all"],
            help="Converter tool to use (default: all)",
        )
        p_convert.add_argument("--snapshot-dir", type=str, help="Path to snapshot directory")
        p_convert.add_argument("--output", type=str, help="Output directory for converted files")
        p_convert.add_argument("--force", action="store_true", help="Re-convert even if already done")
        p_convert.add_argument("--pilot", action="store_true", help="Convert only a few pages per site (cost check)")
        p_convert.add_argument("--pilot-count", type=int, default=3, help="Pages per site in pilot mode (default: 3)")

        p_bench = subparsers.add_parser("benchmark", help="Run benchmark")
        p_bench.add_argument("--static", action="store_true")
        p_bench.add_argument("--live", action="store_true")
        p_bench.add_argument("--sim-live", action="store_true", help="Run simulator-based shopping simulation benchmark")
        p_bench.add_argument(
            "--sim-static", action="store_true", help="Run static benchmark on iOS Simulator collected data"
        )
        p_bench.add_argument("--task", type=str, help="Run only a specific task ID (e.g., SC_MU_01)")
        p_bench.add_argument("--model", type=str, default="claude-sonnet-4-5-20250929", help="Claude model to use")
        p_bench.add_argument("--force", action="store_true", help="Ignore existing results and re-run all tasks")
        p_bench.add_argument(
            "--conditions",
            type=str,
            default=None,
            help="Conditions to run: comma-separated list or 'all'. "
            "Options: full_playwright, page_map, truncated_pw, firecrawl, jina_reader, readability",
        )

        commands["convert"] = cmd_convert
        commands["benchmark"] = cmd_benchmark

    args = parser.parse_args()

    level = logging.DEBUG if args.verbose else logging.INFO
    logging.basicConfig(level=level, format="%(levelname)s: %(message)s")

    commands[args.command](args)


if __name__ == "__main__":
    main()
