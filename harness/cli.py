import argparse
import json
import sys

from harness import run as runmod
from harness import scenario as scenmod
from harness import tools
from harness.evaluate import is_healthy
from harness.paths import RUNS_DIR


def _read_checks(run_id: str):
    data = json.loads((RUNS_DIR / run_id / "checks.json").read_text())
    return data["checks"], data["score"]


def cmd_build(args):
    print(runmod.build_and_load(args.variant))


def cmd_run(args):
    runmod.run_variant(args.variant, expect_healthy=not args.allow_unhealthy)


def cmd_verify(args):
    run_id = (RUNS_DIR / f"last-{args.variant}").read_text().strip()
    checks, score = _read_checks(run_id)
    healthy = is_healthy(checks, score)
    print(f"{run_id}: score={score} healthy={healthy}")
    if args.expect_healthy and not healthy:
        sys.exit(2)


def cmd_cleanup_ns(args):
    ns = runmod.cleanup_namespace(args.variant)
    print(f"deleted namespace: {ns}")


def cmd_scenario(args):
    scenmod.run_scenario(args.id, args.variant, enforce=not args.allow_unexpected)


def cmd_scenario_cleanup_ns(args):
    ns = scenmod.cleanup_scenario_ns(args.id, args.variant)
    print(f"deleted namespace: {ns}")


def cmd_compose_check(args):
    scenmod.compose_check(args.id)


def cmd_eval(args):
    # M2 scenario suite: run each variant, then the compose check.
    for variant in ("broken", "golden"):
        scenmod.run_scenario(args.id, variant, enforce=True)
        scenmod.cleanup_scenario_ns(args.id, variant)
    scenmod.compose_check(args.id)


def cmd_agent(args):
    from harness.agents import fix_agent
    fix_agent.run(args.id, args.tier, enforce=not args.allow_unexpected)


def main(argv=None):
    p = argparse.ArgumentParser(prog="harness", description="PipelineFixRL harness")
    sub = p.add_subparsers(dest="cmd", required=True)

    b = sub.add_parser("build", help="build + kind-load the app image")
    b.add_argument("--variant", default="base")
    b.set_defaults(func=cmd_build)

    r = sub.add_parser("run", help="full workflow for a variant")
    r.add_argument("--variant", default="base")
    r.add_argument("--allow-unhealthy", action="store_true",
                   help="do not exit non-zero when the variant is unhealthy")
    r.set_defaults(func=cmd_run)

    v = sub.add_parser("verify", help="check the last run's recorded score")
    v.add_argument("--variant", default="base")
    v.add_argument("--expect-healthy", action="store_true")
    v.set_defaults(func=cmd_verify)

    c = sub.add_parser("cleanup-ns", help="delete the last run's namespace")
    c.add_argument("--variant", default="base")
    c.set_defaults(func=cmd_cleanup_ns)

    s = sub.add_parser("scenario", help="run one scenario variant and score it")
    s.add_argument("--id", required=True)
    s.add_argument("--variant", required=True, choices=["broken", "golden"])
    s.add_argument("--allow-unexpected", action="store_true",
                   help="do not exit non-zero when the result misses its expectation")
    s.set_defaults(func=cmd_scenario)

    sc = sub.add_parser("scenario-cleanup-ns", help="delete a scenario variant's namespace")
    sc.add_argument("--id", required=True)
    sc.add_argument("--variant", required=True,
                    choices=["broken", "golden", "baseline", "advanced"])
    sc.set_defaults(func=cmd_scenario_cleanup_ns)

    cc = sub.add_parser("compose-check", help="prove break.patch + golden.patch == base")
    cc.add_argument("--id", required=True)
    cc.set_defaults(func=cmd_compose_check)

    e = sub.add_parser("eval", help="run a scenario's broken+golden variants and compose check")
    e.add_argument("--id", default="scenario-001")
    e.set_defaults(func=cmd_eval)

    ag = sub.add_parser("agent", help="run a repair agent (baseline|advanced) against a scenario and score it")
    ag.add_argument("--id", required=True)
    ag.add_argument("--tier", required=True, choices=["baseline", "advanced"])
    ag.add_argument("--allow-unexpected", action="store_true",
                    help="do not exit non-zero when the result misses its expectation")
    ag.set_defaults(func=cmd_agent)

    args = p.parse_args(argv)
    args.func(args)
