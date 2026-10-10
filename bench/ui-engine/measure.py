"""Package each candidate as a zip, unzip into a fresh folder and time launches.

Usage:
  python measure.py --label windows --candidate "Qt Quick=dist/bench-quick:bench-quick" ...

A candidate is NAME=FOLDER:LAUNCHER, LAUNCHER relative to FOLDER (".exe" is
added on Windows). Each candidate folder is zipped (deflate, level 9), the zip
is extracted into a new temp folder, then launched once ("first run after
unzip": files never executed before, Defender/Gatekeeper/XProtect scan
included, page cache already warm from the extraction) and RUNS more times
(warm). Times are wall clock from just before Popen to the marks the app
writes into BENCH_OUT: window_shown (first frame) and core_answer.
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import statistics
import subprocess
import sys
import tempfile
import time
import zipfile
from pathlib import Path


def zip_folder(folder: Path, out: Path) -> int:
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as zf:
        for path in sorted(folder.rglob("*")):
            if path.is_symlink() or path.is_file():
                info = zipfile.ZipInfo.from_file(path, path.relative_to(folder.parent).as_posix())
                if path.is_symlink():
                    info.external_attr = 0o120777 << 16
                    zf.writestr(info, os.readlink(path))
                else:
                    info.compress_type = zipfile.ZIP_DEFLATED
                    with open(path, "rb") as fh:
                        zf.writestr(info, fh.read(), compresslevel=9)
    return out.stat().st_size


def unzip(archive: Path, dest: Path) -> None:
    with zipfile.ZipFile(archive) as zf:
        for info in zf.infolist():
            target = dest / info.filename
            mode = info.external_attr >> 16
            target.parent.mkdir(parents=True, exist_ok=True)
            if (mode & 0o170000) == 0o120000:
                os.symlink(zf.read(info).decode(), target)
                continue
            with zf.open(info) as src, open(target, "wb") as dst:
                dst.write(src.read())
            if mode:
                os.chmod(target, mode & 0o777)


def folder_size(folder: Path) -> int:
    return sum(p.stat().st_size for p in folder.rglob("*") if p.is_file() and not p.is_symlink())


def launch(exe: Path, timeout: float) -> dict:
    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp) / "marks.json"
        env = dict(os.environ, BENCH_OUT=str(out))
        t0 = time.time() * 1000.0
        proc = subprocess.Popen([str(exe)], cwd=str(exe.parent), env=env,
                                stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
        try:
            _, err = proc.communicate(timeout=timeout)
        except subprocess.TimeoutExpired:
            proc.kill()
            _, err = proc.communicate()
            return {"error": "timeout", "stderr": err.decode(errors="replace")[-800:]}
        t_exit = time.time() * 1000.0
        if not out.is_file():
            return {"error": f"no marks, exit {proc.returncode}", "stderr": err.decode(errors="replace")[-800:]}
        marks = json.loads(out.read_text(encoding="utf-8"))
    result = {"exit_ms": round(t_exit - t0)}
    for key in ("process_start", "window_shown", "core_answer", "sidecar_start"):
        if key in marks:
            result[key + "_ms"] = round(marks[key] - t0)
    for key in ("core_import_ms", "core_version", "error"):
        if key in marks:
            result[key] = marks[key]
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--label", required=True)
    parser.add_argument("--candidate", action="append", required=True)
    parser.add_argument("--runs", type=int, default=5)
    parser.add_argument("--timeout", type=float, default=90)
    parser.add_argument("--out", default="bench-results")
    args = parser.parse_args()

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    report = {"label": args.label, "platform": platform.platform(), "machine": platform.machine(),
              "python": sys.version.split()[0], "runs": args.runs, "candidates": []}
    for spec in args.candidate:
        name, rest = spec.split("=", 1)
        folder_s, launcher = rest.rsplit(":", 1)
        folder = Path(folder_s).resolve()
        if sys.platform == "win32" and not launcher.endswith(".exe"):
            launcher += ".exe"
        entry = {"name": name}
        archive = out_dir / (name.replace(" ", "_").replace("/", "_").replace("+", "_") + ".zip")
        entry["zip_bytes"] = zip_folder(folder, archive)
        entry["unpacked_bytes"] = folder_size(folder)
        archive_unlink = True
        with tempfile.TemporaryDirectory() as tmp:
            dest = Path(tmp)
            unzip(archive, dest)
            exe = dest / folder.name / launcher
            first = launch(exe, args.timeout)
            warm = [launch(exe, args.timeout) for _ in range(args.runs)]
        if archive_unlink:
            archive.unlink()
        entry["first_run"] = first
        entry["warm_runs"] = warm
        for key in ("window_shown_ms", "core_answer_ms"):
            values = [r[key] for r in warm if key in r]
            if values:
                entry["warm_median_" + key] = round(statistics.median(values))
        report["candidates"].append(entry)
        print(json.dumps(entry, ensure_ascii=False), flush=True)

    (out_dir / f"ui-engine-{args.label}.json").write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    lines = [f"### {args.label} ({report['platform']}, {report['machine']})", "",
             "| Candidate | zip, MB | unpacked, MB | 1st run: window / core, ms | warm median: window / core, ms |",
             "|---|---:|---:|---:|---:|"]
    for c in report["candidates"]:
        f = c["first_run"]
        first = f"{f.get('window_shown_ms', '—')} / {f.get('core_answer_ms', '—')}" if "error" not in f else f"error: {f['error']}"
        warm = f"{c.get('warm_median_window_shown_ms', '—')} / {c.get('warm_median_core_answer_ms', '—')}"
        lines.append(f"| {c['name']} | {c['zip_bytes']/1e6:.1f} | {c['unpacked_bytes']/1e6:.1f} | {first} | {warm} |")
    md = "\n".join(lines) + "\n"
    (out_dir / f"ui-engine-{args.label}.md").write_text(md, encoding="utf-8")
    print(md)
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a", encoding="utf-8") as fh:
            fh.write(md)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
