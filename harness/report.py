import pathlib

from harness.evaluate import is_healthy


def write_report(run_dir: pathlib.Path, meta: dict, checks: list[dict], score: int) -> str:
    lines = [
        "PipelineFixRL run report",
        f"run_id   : {meta['run_id']}",
        f"variant  : {meta['variant']}",
        f"namespace: {meta['namespace']}",
        f"image    : {meta['image']}",
        f"started  : {meta['started_at']}",
        "",
        f"{'CHECK':<20} {'WEIGHT':>6} {'RESULT':>6}  REASON",
        "-" * 78,
    ]
    for c in checks:
        lines.append(f"{c['id']:<20} {c['weight']:>6} {c['result']:>6}  {c['reason']}")
    lines += [
        "-" * 78,
        f"SCORE: {score}",
        f"HEALTHY: {'yes' if is_healthy(checks, score) else 'no'}",
        "",
    ]
    text = "\n".join(lines)
    (run_dir / "report.txt").write_text(text)
    return text
