"""``airgap`` command line. Same planner and setup code as the API and the Compatibility Lab.

airgap serve                         run the native profile (UI + API on 127.0.0.1:8000)
airgap scan [--json]                 read-only hardware scan + recommendation (real)
airgap recommend --profile ID        planner on a built-in SIMULATED profile
airgap recommend --spec FILE.json    planner on a custom SIMULATED spec
airgap plan --model ID               dry-run installation plan from a real scan
airgap doctor                        prerequisites and component health
airgap user create USERNAME --role admin|editor|viewer [--groups a,b]
airgap backup OUT.tar.gz / airgap restore IN.tar.gz     (native profile data)
airgap bundle verify DIR             verify an offline bundle's manifest and signature
"""

from __future__ import annotations

import argparse
import getpass
import sys
from pathlib import Path

from airgap.capacity.catalog import load_catalog
from airgap.capacity.hardware import GIB
from airgap.capacity.planner import Assessment, PlannerOptions, assess
from airgap.capacity.probe import RealHardwareProvider
from airgap.capacity.providers import SimulatedSpec, builtin_profiles
from airgap.config import Settings
from airgap.setup.plan import SelectionRefused, build_plan
from airgap.setup.prerequisites import check_prerequisites

BANNER = "=== SIMULATION - NOT A REAL HARDWARE SCAN ==="


def _settings(args: argparse.Namespace) -> Settings:
    overrides: dict[str, object] = {"profile": "native"}
    if getattr(args, "data_dir", None):
        overrides["data_dir"] = Path(args.data_dir)
    if getattr(args, "models_dir", None):
        overrides["models_dir"] = Path(args.models_dir)
    return Settings(**overrides)  # type: ignore[arg-type]


def _gib(v: int | None) -> str:
    return "unknown" if v is None else f"{v / GIB:.1f} GiB"


def print_assessment(a: Assessment) -> None:
    r = a.report
    if a.simulated:
        print(BANNER)
    print(f"Platform: {r.platform}  CPU: {r.cpu_model or 'unknown'}  threads: {r.cpu_threads}")
    print(f"RAM: {_gib(r.ram_total_bytes)} total, {_gib(r.ram_available_bytes)} available now")
    for g in r.gpus:
        print(f"GPU: {g.name}  VRAM {_gib(g.vram_total_bytes)}  driver {g.driver_version}")
    print(f"Disk free: {_gib(r.disk_free_bytes)}")
    for rt in r.runtimes:
        print(
            f"Runtime {rt.name}: installed={rt.installed} reachable={rt.reachable} "
            f"version={rt.version}"
        )
    print(
        f"\nTier: {a.tier.value}   deployment: {a.deployment}   "
        f"Document Assistant: {'yes' if a.document_assistant_available else 'no'}"
    )
    print(f"Recommended: {a.recommended_model_id or 'none'}  (all figures are estimates)\n")
    for e in a.evaluations:
        mark = "OK " if e.eligible else "-- "
        notes = "; ".join(n.message for n in (e.rejections or e.warnings))
        print(
            f"  {mark}{e.model_id:30} {e.placement.value if e.placement else '':16} "
            f"sessions={e.max_sessions}  {notes}"
        )
    for w in a.warnings:
        print(f"! {w.message}")
    for lim in r.limitations:
        print(f"? {lim}")
    if a.simulated:
        print(BANNER)


def _real_assessment(settings: Settings, context: int | None) -> Assessment:
    report = RealHardwareProvider(settings.data_dir, settings.ollama_url).report()
    opts = PlannerOptions(context_tokens=context) if context else PlannerOptions()
    return assess(report, load_catalog(), opts)


def cmd_scan(args: argparse.Namespace) -> int:
    a = _real_assessment(_settings(args), args.context)
    print(a.model_dump_json(indent=1) if args.json else "", end="")
    if not args.json:
        print_assessment(a)
    return 0


def cmd_recommend(args: argparse.Namespace) -> int:
    if args.profile:
        profiles = builtin_profiles()
        if args.profile not in profiles:
            print(f"unknown profile; choose one of: {', '.join(profiles)}", file=sys.stderr)
            return 2
        spec = profiles[args.profile]
    elif args.spec:
        spec = SimulatedSpec.model_validate_json(Path(args.spec).read_text(encoding="utf-8"))
    else:
        print("give --profile or --spec (use `airgap scan` for this machine)", file=sys.stderr)
        return 2
    opts = PlannerOptions(context_tokens=args.context) if args.context else PlannerOptions()
    a = assess(spec.to_report(), load_catalog(), opts)
    if args.json:
        print(a.model_dump_json(indent=1))
    else:
        print_assessment(a)
    return 0


def cmd_plan(args: argparse.Namespace) -> int:
    settings = _settings(args)
    a = _real_assessment(settings, None)
    catalog = load_catalog()
    try:
        plan = build_plan(a, check_prerequisites(a.report, a, catalog), catalog, args.model)
    except SelectionRefused as exc:
        for model, reasons in exc.reasons.items():
            print(f"{model}: {'; '.join(reasons)}", file=sys.stderr)
        return 1
    if args.json:
        print(plan.model_dump_json(indent=1))
        return 0
    print("DRY RUN - nothing will be changed")
    for step in plan.steps:
        size = f" ({step.bytes / GIB:.1f} GiB)" if step.bytes else ""
        who = " [you]" if step.needs_user else ""
        print(f"- {step.title}{size}{who}: {step.detail or ''}")
    print(f"Total download: {plan.total_download_bytes / GIB:.1f} GiB (estimate)")
    print("Ready to install." if plan.ready else f"Blocked by: {', '.join(plan.blocking)}")
    return 0


def cmd_doctor(args: argparse.Namespace) -> int:
    settings = _settings(args)
    a = _real_assessment(settings, None)
    bad = 0
    for c in check_prerequisites(a.report, a, load_catalog()):
        status = c.status.value.upper()
        print(f"[{status:12}] {c.title}{': ' + c.detail if c.detail else ''}")
        for action in c.actions:
            print(
                f"    -> {action.title}: {action.instructions}"
                + (f" ({action.url})" if action.url else "")
            )
        bad += c.blocking
    print(f"\nRecommended model: {a.recommended_model_id or 'none'} (tier {a.tier.value})")
    return 1 if bad else 0


def cmd_user(args: argparse.Namespace) -> int:
    from airgap.adapters.storage import SystemClock
    from airgap.auth.service import AuthService, UserRole, ValidationFailed
    from airgap.db.session import make_engine, make_session_factory
    from airgap.native import migrate

    settings = _settings(args)
    url = f"sqlite:///{(settings.data_dir.resolve() / 'airgap.db').as_posix()}"
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    migrate(url)
    auth = AuthService(make_session_factory(make_engine(url)), SystemClock())
    password = getpass.getpass("Password (12+ characters): ")
    if password != getpass.getpass("Repeat: "):
        print("passwords differ", file=sys.stderr)
        return 1
    try:
        p = auth.create_user(
            args.username,
            password,
            UserRole(args.role),
            args.groups.split(",") if args.groups else None,
        )
    except ValidationFailed as exc:
        print(str(exc), file=sys.stderr)
        return 1
    print(f"created {p.username} ({p.role.value}) in groups {', '.join(p.groups)}")
    return 0


def cmd_serve(args: argparse.Namespace) -> int:
    import uvicorn

    from airgap.logging_setup import configure_logging
    from airgap.native import create_native_app

    settings = _settings(args)
    host = args.host or settings.host
    if host not in {"127.0.0.1", "::1", "localhost"}:
        print(f"WARNING: listening on {host} exposes the app to your network.", file=sys.stderr)
    configure_logging()
    uvicorn.run(
        create_native_app(settings), host=host, port=args.port or settings.port, server_header=False
    )
    return 0


def cmd_backup(args: argparse.Namespace) -> int:
    from airgap.ops.backup import create_backup

    out = create_backup(_settings(args).data_dir, Path(args.out))
    print(f"backup written: {out}")
    return 0


def cmd_restore(args: argparse.Namespace) -> int:
    from airgap.ops.backup import restore_backup

    restore_backup(Path(args.archive), _settings(args).data_dir, force=args.force)
    print("restore complete; start the app to verify")
    return 0


def cmd_bundle(args: argparse.Namespace) -> int:
    from airgap.ops.bundle import BundleError, verify_bundle

    try:
        manifest = verify_bundle(Path(args.dir), Path(args.public_key) if args.public_key else None)
    except BundleError as exc:
        print(f"BUNDLE REJECTED: {exc}", file=sys.stderr)
        return 1
    print(
        f"bundle OK: {len(manifest['files'])} files verified"
        + ("" if args.public_key else " (checksums only; signature NOT checked)")
    )
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="airgap", description="AirGap RAG Kit")
    p.add_argument("--data-dir")
    p.add_argument("--models-dir")
    sub = p.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("serve")
    s.add_argument("--host")
    s.add_argument("--port", type=int)
    s.set_defaults(fn=cmd_serve)
    s = sub.add_parser("scan")
    s.add_argument("--json", action="store_true")
    s.add_argument("--context", type=int)
    s.set_defaults(fn=cmd_scan)
    s = sub.add_parser("recommend")
    s.add_argument("--profile")
    s.add_argument("--spec")
    s.add_argument("--context", type=int)
    s.add_argument("--json", action="store_true")
    s.set_defaults(fn=cmd_recommend)
    s = sub.add_parser("plan")
    s.add_argument("--model", action="append", required=True)
    s.add_argument("--json", action="store_true")
    s.set_defaults(fn=cmd_plan)
    s = sub.add_parser("doctor")
    s.set_defaults(fn=cmd_doctor)
    s = sub.add_parser("user")
    usub = s.add_subparsers(dest="user_cmd", required=True)
    u = usub.add_parser("create")
    u.add_argument("username")
    u.add_argument("--role", default="viewer", choices=["admin", "editor", "viewer"])
    u.add_argument("--groups")
    u.set_defaults(fn=cmd_user)
    s = sub.add_parser("backup")
    s.add_argument("out")
    s.set_defaults(fn=cmd_backup)
    s = sub.add_parser("restore")
    s.add_argument("archive")
    s.add_argument("--force", action="store_true", help="replace existing data (kept as .old)")
    s.set_defaults(fn=cmd_restore)
    s = sub.add_parser("bundle")
    bsub = s.add_subparsers(dest="bundle_cmd", required=True)
    b = bsub.add_parser("verify")
    b.add_argument("dir")
    b.add_argument("--public-key")
    b.set_defaults(fn=cmd_bundle)
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    code: int = args.fn(args)
    return code


if __name__ == "__main__":
    sys.exit(main())
