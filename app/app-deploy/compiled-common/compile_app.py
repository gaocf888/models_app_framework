#!/usr/bin/env python3
"""Nuitka-compile the in-tree app package to .so, then drop plaintext .py.

Runs inside the compiled Docker builder (cwd / PYTHONPATH = /workspace).
Failed files stay as .py and are listed in app/.compile_whitelist.txt.
Nuitka refuses ``__init__.py`` as a compile target (must pass the package
directory); those files stay as plaintext package markers so sibling ``.so``
modules still import. Large modules are compiled the same as others.

Size-aware parallel compile (env overrides):
  COMPILE_WORKERS_SMALL / COMPILE_JOBS_SMALL   default: min(ncpu, 8) / 1
  COMPILE_WORKERS_MEDIUM / COMPILE_JOBS_MEDIUM default: min(4, ncpu//2 or 1) / 2
  COMPILE_WORKERS_LARGE / COMPILE_JOBS_LARGE   default: 1 or 2 / min(8, ncpu)
  COMPILE_MIN_BYTES                             default: 2048 (keep tinier .py)
  NUITKA_FILE_TIMEOUT                           default: 1800
"""
from __future__ import annotations

import argparse
import multiprocessing
import os
import shutil
import subprocess
import sys
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path
from time import perf_counter
from typing import Iterable, List, Sequence, Tuple

EXCLUDE_DIR_NAMES = {
    "app-deploy",
    "train",
    "test_scripts",
    "manage_scripts",
    "data_query_agent",
    "observability",
    "__pycache__",
    ".git",
}

EXCLUDE_PATH_PARTS = {"pretrained"}

SMALL_MAX_BYTES = 20_000
LARGE_MIN_BYTES = 100_000


def _env_int(name: str, default: int) -> int:
    raw = os.getenv(name, "").strip()
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def _rel_parts(path: Path, app_root: Path) -> Tuple[str, ...]:
    return path.resolve().relative_to(app_root.resolve()).parts


def is_backup_py(path: Path) -> bool:
    name = path.name
    return "_bak_" in name or name.endswith("_bak.py") or "备份" in name


def should_skip_file(path: Path, app_root: Path) -> bool:
    parts = _rel_parts(path, app_root)
    if any(p in EXCLUDE_DIR_NAMES for p in parts):
        return True
    if any(p in EXCLUDE_PATH_PARTS for p in parts):
        return True
    if is_backup_py(path):
        return True
    return False


def prune_excluded_trees(app_root: Path) -> None:
    for name in (
        "app-deploy",
        "train",
        "test_scripts",
        "manage_scripts",
        "data_query_agent",
        "observability",
    ):
        target = app_root / name
        if target.exists():
            shutil.rmtree(target)
            print(f"[compile_app] removed {target}", flush=True)
    for path in list(app_root.rglob("*.py")):
        if is_backup_py(path):
            rel = path.resolve().relative_to(app_root.resolve()).as_posix()
            path.unlink()
            print(f"[compile_app] removed backup {rel}", flush=True)
    for cache in app_root.rglob("__pycache__"):
        if cache.is_dir():
            shutil.rmtree(cache, ignore_errors=True)


def collect_py_files(app_root: Path) -> List[Path]:
    files: List[Path] = []
    for path in sorted(app_root.rglob("*.py")):
        if should_skip_file(path, app_root):
            continue
        files.append(path)
    return files


def load_keep_list(keep_file: Path | None) -> set[str]:
    keep: set[str] = set()
    if keep_file is None or not keep_file.is_file():
        return keep
    for raw in keep_file.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        keep.add(line.replace("\\", "/"))
    extra = os.getenv("COMPILE_KEEP_PY", "")
    for item in extra.split(","):
        item = item.strip().replace("\\", "/")
        if item:
            keep.add(item)
    return keep


def has_extension(py_file: Path) -> bool:
    parent = py_file.parent
    stem = py_file.stem
    return any(parent.glob(f"{stem}.so")) or any(parent.glob(f"{stem}.*.so")) or any(
        parent.glob(f"{stem}.pyd")
    ) or any(parent.glob(f"{stem}.*.pyd"))


def _cleanup_nuitka_sidecar(py_file: Path) -> None:
    parent = py_file.parent
    stem = py_file.stem
    build_dir = parent / f"{stem}.build"
    if build_dir.is_dir():
        shutil.rmtree(build_dir, ignore_errors=True)
    dist_dir = parent / f"{stem}.dist"
    if dist_dir.is_dir():
        shutil.rmtree(dist_dir, ignore_errors=True)
    for extra in parent.glob(f"{stem}.pyi"):
        extra.unlink(missing_ok=True)


def compile_one(workspace: Path, py_file: Path, jobs: int = 1) -> Tuple[bool, str]:
    """Compile one module with Nuitka --module --nofollow-imports (full Python syntax)."""
    if py_file.name == "__init__.py":
        return False, "skip __init__.py: Nuitka requires the package directory"
    jobs_s = str(max(1, int(jobs)))
    timeout_s = max(120, int(os.getenv("NUITKA_FILE_TIMEOUT", "1800")))
    cmd = [
        sys.executable,
        "-m",
        "nuitka",
        "--module",
        "--nofollow-imports",
        "--remove-output",
        "--no-pyi-file",
        "--assume-yes-for-downloads",
        "--lto=no",
        f"--jobs={jobs_s}",
        f"--output-dir={str(py_file.parent)}",
        str(py_file),
    ]
    env = os.environ.copy()
    env["PYTHONPATH"] = str(workspace) + os.pathsep + env.get("PYTHONPATH", "")
    try:
        proc = subprocess.run(
            cmd,
            cwd=str(workspace),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout_s,
            env=env,
        )
    except subprocess.TimeoutExpired:
        _cleanup_nuitka_sidecar(py_file)
        return False, f"nuitka timeout after {timeout_s}s"
    _cleanup_nuitka_sidecar(py_file)
    if proc.returncode == 0 and has_extension(py_file):
        return True, ""
    err = (proc.stderr or proc.stdout or "nuitka failed").strip()
    if proc.returncode == 0 and not has_extension(py_file):
        err = (err + "\n(no .so produced)").strip()
    return False, err[-2000:]


def _compile_one_job(payload: Tuple[str, str, int]) -> Tuple[str, bool, str, float]:
    workspace_s, py_s, jobs = payload
    t0 = perf_counter()
    try:
        ok, err = compile_one(Path(workspace_s), Path(py_s), jobs=jobs)
    except Exception as exc:  # noqa: BLE001 — worker must never crash the pool
        return py_s, False, f"worker exception: {exc}", perf_counter() - t0
    return py_s, ok, err, perf_counter() - t0


def file_tier(size: int) -> str:
    if size < SMALL_MAX_BYTES:
        return "small"
    if size >= LARGE_MIN_BYTES:
        return "large"
    return "medium"


def strip_sources(py_files: Iterable[Path], keep_rel: set[str], app_root: Path) -> Tuple[List[str], List[str]]:
    removed: List[str] = []
    kept: List[str] = []
    for py_file in py_files:
        rel = py_file.resolve().relative_to(app_root.resolve()).as_posix()
        if rel in keep_rel or py_file.name in keep_rel:
            kept.append(rel)
            continue
        if has_extension(py_file):
            py_file.unlink()
            removed.append(rel)
            for leftover in py_file.parent.glob(f"{py_file.stem}.c"):
                leftover.unlink(missing_ok=True)
            for leftover in py_file.parent.glob(f"{py_file.stem}.cpp"):
                leftover.unlink(missing_ok=True)
            for leftover in py_file.parent.glob(f"{py_file.stem}.pyi"):
                leftover.unlink(missing_ok=True)
        else:
            kept.append(rel)
    return removed, kept


def restore_empty_init(app_root: Path) -> None:
    for dirpath, dirnames, filenames in os.walk(app_root):
        dirnames[:] = [d for d in dirnames if d not in EXCLUDE_DIR_NAMES]
        folder = Path(dirpath)
        has_py = any(name.endswith(".py") for name in filenames)
        has_so = any(name.endswith(".so") or name.endswith(".pyd") for name in filenames)
        has_init = (folder / "__init__.py").exists() or any(
            name.startswith("__init__") and (name.endswith(".so") or name.endswith(".pyd"))
            for name in filenames
        )
        if (has_py or has_so) and not has_init:
            (folder / "__init__.py").write_text("", encoding="utf-8")
            print(f"[compile_app] wrote empty {folder / '__init__.py'}", flush=True)


def write_report(
    app_root: Path,
    *,
    compiled_ok: List[str],
    whitelist: List[str],
    keep_preset: set[str],
    init_kept: List[str],
    tiny_kept: List[str],
) -> None:
    report = app_root / ".compile_report.txt"
    try:
        import nuitka

        nuitka_ver = getattr(nuitka, "__version__", "?")
    except Exception:
        nuitka_ver = "?"
    lines = [
        "models-app Nuitka compile report",
        f"python={sys.version.replace(chr(10), ' ')}",
        f"nuitka={nuitka_ver}",
        f"compiled_ok={len(compiled_ok)}",
        f"whitelist_kept_py={len(whitelist)}",
        f"init_py_package_markers={len(init_kept)}",
        f"tiny_kept_py={len(tiny_kept)}",
        "",
        "== compiled (source removed) ==",
        *compiled_ok,
        "",
        "== kept .py (compile failed or preset) ==",
        *whitelist,
        "",
        "== kept __init__.py (Nuitka package markers, not failures) ==",
        *init_kept,
        "",
        "== kept tiny .py (below COMPILE_MIN_BYTES) ==",
        *tiny_kept,
        "",
        "== preset keep list ==",
        *sorted(keep_preset),
        "",
    ]
    report.write_text("\n".join(lines) + "\n", encoding="utf-8")
    whitelist_path = app_root / ".compile_whitelist.txt"
    whitelist_path.write_text("\n".join(whitelist) + ("\n" if whitelist else ""), encoding="utf-8")
    print(f"[compile_app] report {report}", flush=True)
    print(f"[compile_app] whitelist {whitelist_path}", flush=True)


def _run_phase(
    *,
    workspace: Path,
    app_root: Path,
    files: Sequence[Path],
    jobs: int,
    workers: int,
    label: str,
) -> List[Tuple[Path, str]]:
    if not files:
        return []
    workers = max(1, min(int(workers), len(files)))
    jobs = max(1, int(jobs))
    print(
        f"[compile_app] phase={label} files={len(files)} workers={workers} nuitka_jobs={jobs}",
        flush=True,
    )
    failed: List[Tuple[Path, str]] = []
    done = 0
    total = len(files)
    payloads = [(str(workspace), str(path), jobs) for path in files]
    printed_fail = 0
    ctx = multiprocessing.get_context("spawn")
    with ProcessPoolExecutor(max_workers=workers, mp_context=ctx) as pool:
        futs = [pool.submit(_compile_one_job, pl) for pl in payloads]
        for fut in as_completed(futs):
            py_s, ok, err, elapsed = fut.result()
            path = Path(py_s)
            rel = path.resolve().relative_to(app_root.resolve()).as_posix()
            done += 1
            if ok:
                print(
                    f"[compile_app] ({done}/{total}) ok {rel} {elapsed:.1f}s",
                    flush=True,
                )
                continue
            print(f"[compile_app] ({done}/{total}) FAIL keep-py {rel} {elapsed:.1f}s", flush=True)
            if printed_fail < 8 and err:
                printed_fail += 1
                for line in err.splitlines():
                    low = line.lower()
                    if "error" in low or "exception" in low or "fatal" in low:
                        sys.stderr.write("  " + line[:240] + "\n")
                        break
                else:
                    sys.stderr.write("  " + err[:240].replace("\n", " ") + "\n")
            failed.append((path, err))
    return failed


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "workspace",
        nargs="?",
        default="/workspace",
        help="PYTHONPATH root (default /workspace)",
    )
    parser.add_argument(
        "--app-dir",
        default="",
        help="app package directory (default <workspace>/app)",
    )
    parser.add_argument(
        "--keep-file",
        default="",
        help="optional list of app-relative .py paths to skip compiling",
    )
    args = parser.parse_args()

    workspace = Path(args.workspace).resolve()
    app_root = Path(args.app_dir).resolve() if args.app_dir else (workspace / "app")
    if not app_root.is_dir():
        print(f"[compile_app] app dir not found: {app_root}", file=sys.stderr)
        return 2

    os.chdir(workspace)
    sys.path.insert(0, str(workspace))
    prune_excluded_trees(app_root)

    keep_file = Path(args.keep_file) if args.keep_file else (
        Path(__file__).resolve().parent / "compile_keep_py.txt"
    )
    keep_preset = load_keep_list(keep_file if keep_file.is_file() else None)
    min_bytes = max(0, _env_int("COMPILE_MIN_BYTES", 2048))

    py_files = collect_py_files(app_root)
    to_compile: List[Path] = []
    preset_kept: List[str] = []
    init_kept: List[str] = []
    tiny_kept: List[str] = []
    for path in py_files:
        rel = path.resolve().relative_to(app_root).as_posix()
        if rel in keep_preset:
            preset_kept.append(rel)
            continue
        # Nuitka: "to compile a package, specify its directory, but not the __init__.py".
        if path.name == "__init__.py":
            init_kept.append(rel)
            continue
        if path.name == "main.py" and path.parent.resolve() == app_root.resolve():
            preset_kept.append(rel)
            continue
        size = path.stat().st_size
        if min_bytes > 0 and size < min_bytes:
            tiny_kept.append(rel)
            continue
        to_compile.append(path)

    ncpu = os.cpu_count() or 2
    small_workers = max(1, _env_int("COMPILE_WORKERS_SMALL", max(2, min(ncpu, 8))))
    small_jobs = max(1, _env_int("COMPILE_JOBS_SMALL", 1))
    medium_workers = max(1, _env_int("COMPILE_WORKERS_MEDIUM", max(1, min(4, ncpu // 2 or 1))))
    medium_jobs = max(1, _env_int("COMPILE_JOBS_MEDIUM", 2))
    large_workers = max(1, _env_int("COMPILE_WORKERS_LARGE", 1 if ncpu < 24 else 2))
    large_jobs = max(1, _env_int("COMPILE_JOBS_LARGE", max(1, min(8, ncpu))))

    print(
        f"[compile_app] candidates={len(py_files)} compile={len(to_compile)} "
        f"preset_keep={len(preset_kept)} init_keep={len(init_kept)} "
        f"tiny_keep={len(tiny_kept)} min_bytes={min_bytes} ncpu={ncpu}",
        flush=True,
    )
    try:
        import nuitka

        print(f"[compile_app] nuitka={getattr(nuitka, '__version__', '?')}", flush=True)
    except Exception as exc:
        print(f"[compile_app] nuitka import failed: {exc}", file=sys.stderr)
        return 4

    if not to_compile and not preset_kept and not tiny_kept:
        print("[compile_app] no .py files to compile", file=sys.stderr)
        return 2

    small_files = [p for p in to_compile if file_tier(p.stat().st_size) == "small"]
    medium_files = [p for p in to_compile if file_tier(p.stat().st_size) == "medium"]
    large_files = [p for p in to_compile if file_tier(p.stat().st_size) == "large"]
    print(
        f"[compile_app] tiers small={len(small_files)} medium={len(medium_files)} "
        f"large={len(large_files)}",
        flush=True,
    )

    failed: List[Tuple[Path, str]] = []
    t0 = perf_counter()
    failed.extend(
        _run_phase(
            workspace=workspace,
            app_root=app_root,
            files=small_files,
            jobs=small_jobs,
            workers=small_workers,
            label="small",
        )
    )
    failed.extend(
        _run_phase(
            workspace=workspace,
            app_root=app_root,
            files=medium_files,
            jobs=medium_jobs,
            workers=medium_workers,
            label="medium",
        )
    )
    failed.extend(
        _run_phase(
            workspace=workspace,
            app_root=app_root,
            files=large_files,
            jobs=large_jobs,
            workers=large_workers,
            label="large",
        )
    )
    print(f"[compile_app] compile_wall_s={perf_counter() - t0:.1f}", flush=True)

    failed_rel = {p.resolve().relative_to(app_root).as_posix() for p, _ in failed}
    keep_rel = set(keep_preset) | failed_rel | set(init_kept) | set(tiny_kept)
    extra_keep_files = [
        app_root / rel
        for rel in (preset_kept + init_kept + tiny_kept)
        if (app_root / rel).exists()
    ]
    removed, kept = strip_sources(to_compile + extra_keep_files, keep_rel, app_root)
    skip_from_whitelist = set(init_kept) | set(tiny_kept)
    kept_without_markers = [p for p in kept if p not in skip_from_whitelist]

    restore_empty_init(app_root)
    write_report(
        app_root,
        compiled_ok=removed,
        whitelist=sorted(set(kept_without_markers)),
        keep_preset=keep_preset,
        init_kept=init_kept,
        tiny_kept=tiny_kept,
    )

    main_ok = has_extension(app_root / "main.py") or (app_root / "main.py").exists()
    if not main_ok:
        print("[compile_app] app.main missing after compile", file=sys.stderr)
        return 3
    if not removed:
        print("[compile_app] compiled 0 modules; Nuitka toolchain likely broken", file=sys.stderr)
        return 4
    print(
        f"[compile_app] done compiled={len(removed)} kept_py={len(kept_without_markers)} "
        f"init_py={len(init_kept)} tiny_py={len(tiny_kept)}",
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
