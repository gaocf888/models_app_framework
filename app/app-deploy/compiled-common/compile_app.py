#!/usr/bin/env python3
"""Cython-compile the in-tree app package, then drop plaintext .py.

Runs inside the compiled Docker builder (cwd / PYTHONPATH = /workspace).
Failed files stay as .py and are listed in app/.compile_whitelist.txt.
"""
from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Iterable, List, Sequence, Tuple

EXCLUDE_DIR_NAMES = {
    "app-deploy",
    "train",
    "test_scripts",
    "manage_scripts",
    "__pycache__",
    ".git",
    ".cython_build",
}

# Binary / weight trees: keep as-is, do not compile.
EXCLUDE_PATH_PARTS = {"pretrained"}

COMPILER_DIRECTIVES = {
    "language_level": "3",
    "annotation_typing": False,
    "emit_code_comments": False,
    "docstrings": False,
}


def _rel_parts(path: Path, app_root: Path) -> Tuple[str, ...]:
    return path.resolve().relative_to(app_root.resolve()).parts


def should_skip_file(path: Path, app_root: Path) -> bool:
    parts = _rel_parts(path, app_root)
    if any(p in EXCLUDE_DIR_NAMES for p in parts):
        return True
    if any(p in EXCLUDE_PATH_PARTS for p in parts):
        return True
    return False


def prune_excluded_trees(app_root: Path) -> None:
    for name in ("app-deploy", "train", "test_scripts", "manage_scripts"):
        target = app_root / name
        if target.exists():
            shutil.rmtree(target)
            print(f"[compile_app] removed {target}", flush=True)
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


def load_keep_list(keep_file: Path | None, app_root: Path) -> set[str]:
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


def _posix_rel(path: Path, workspace: Path) -> str:
    return path.resolve().relative_to(workspace.resolve()).as_posix()


def _module_name(rel_posix: str) -> str:
    if not rel_posix.endswith(".py"):
        raise ValueError(rel_posix)
    return rel_posix[: -len(".py")].replace("/", ".")


def _write_setup(setup_path: Path, specs: Sequence[Tuple[str, str]]) -> None:
    lines = [
        "from setuptools import Extension, setup",
        "from Cython.Build import cythonize",
        "",
        "directives = " + repr(COMPILER_DIRECTIVES),
        "extensions = [",
    ]
    for mod, src in specs:
        lines.append(f"    Extension({mod!r}, [{src!r}]),")
    lines.extend(
        [
            "]",
            "setup(",
            "    name='models_app_compiled',",
            "    ext_modules=cythonize(",
            "        extensions,",
            "        compiler_directives=directives,",
            "        quiet=True,",
            "        nthreads=0,",
            "    ),",
            ")",
            "",
        ]
    )
    setup_path.write_text("\n".join(lines), encoding="utf-8")


def _run_setup(workspace: Path, setup_path: Path, build_temp: Path) -> subprocess.CompletedProcess[str]:
    build_temp.mkdir(parents=True, exist_ok=True)
    return subprocess.run(
        [
            sys.executable,
            str(setup_path),
            "build_ext",
            "--inplace",
            "--build-temp",
            str(build_temp),
        ],
        cwd=str(workspace),
        capture_output=True,
        text=True,
    )


def compile_batch(workspace: Path, py_files: Sequence[Path], build_temp: Path) -> bool:
    specs = [(_module_name(_posix_rel(p, workspace)), _posix_rel(p, workspace)) for p in py_files]
    setup_path = workspace / ".cython_setup_batch.py"
    _write_setup(setup_path, specs)
    print(f"[compile_app] batch cythonize {len(specs)} modules", flush=True)
    proc = _run_setup(workspace, setup_path, build_temp)
    setup_path.unlink(missing_ok=True)
    if proc.returncode == 0:
        return True
    sys.stderr.write("[compile_app] batch failed, falling back to per-file\n")
    if proc.stderr:
        sys.stderr.write(proc.stderr[-4000:] + "\n")
    return False


def compile_one(workspace: Path, py_file: Path, build_temp: Path) -> Tuple[bool, str]:
    rel = _posix_rel(py_file, workspace)
    spec = (_module_name(rel), rel)
    setup_path = workspace / ".cython_setup_one.py"
    _write_setup(setup_path, [spec])
    proc = _run_setup(workspace, setup_path, build_temp)
    setup_path.unlink(missing_ok=True)
    if proc.returncode == 0:
        return True, ""
    err = (proc.stderr or proc.stdout or "compile failed").strip()
    return False, err[-1500:]


def has_extension(py_file: Path) -> bool:
    parent = py_file.parent
    stem = py_file.stem
    return any(parent.glob(f"{stem}.so")) or any(parent.glob(f"{stem}.*.so")) or any(
        parent.glob(f"{stem}.pyd")
    ) or any(parent.glob(f"{stem}.*.pyd"))


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
            for c_file in py_file.parent.glob(f"{py_file.stem}.c"):
                c_file.unlink(missing_ok=True)
            for cpp_file in py_file.parent.glob(f"{py_file.stem}.cpp"):
                cpp_file.unlink(missing_ok=True)
        else:
            kept.append(rel)
    return removed, kept


def restore_empty_init(app_root: Path) -> None:
    """Guarantee package import if Cython dropped __init__.py and produced no .so."""
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
) -> None:
    report = app_root / ".compile_report.txt"
    lines = [
        "models-app Cython compile report",
        f"python={sys.version.replace(chr(10), ' ')}",
        f"compiled_ok={len(compiled_ok)}",
        f"whitelist_kept_py={len(whitelist)}",
        "",
        "== compiled (source removed) ==",
        *compiled_ok,
        "",
        "== kept .py (compile failed or preset) ==",
        *whitelist,
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


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "workspace",
        nargs="?",
        default="/workspace",
        help="vLLM/app PYTHONPATH root (default /workspace)",
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
    keep_preset = load_keep_list(keep_file if keep_file.is_file() else None, app_root)

    py_files = collect_py_files(app_root)
    to_compile = []
    preset_kept: List[str] = []
    for path in py_files:
        rel = path.resolve().relative_to(app_root).as_posix()
        if rel in keep_preset:
            preset_kept.append(rel)
            continue
        to_compile.append(path)

    print(f"[compile_app] candidates={len(py_files)} compile={len(to_compile)} preset_keep={len(preset_kept)}", flush=True)
    if not to_compile and not preset_kept:
        print("[compile_app] no .py files to compile", file=sys.stderr)
        return 2

    build_temp = workspace / ".cython_build"
    failed: List[Tuple[Path, str]] = []
    if to_compile and not compile_batch(workspace, to_compile, build_temp):
        for path in to_compile:
            ok, err = compile_one(workspace, path, build_temp)
            rel = path.resolve().relative_to(app_root).as_posix()
            if ok:
                print(f"[compile_app] ok {rel}", flush=True)
            else:
                print(f"[compile_app] FAIL {rel}", flush=True)
                failed.append((path, err))
    else:
        # Verify each expected .so; anything missing goes to per-file retry.
        missing = [p for p in to_compile if not has_extension(p)]
        for path in missing:
            ok, err = compile_one(workspace, path, build_temp)
            rel = path.resolve().relative_to(app_root).as_posix()
            if ok:
                print(f"[compile_app] retry-ok {rel}", flush=True)
            else:
                print(f"[compile_app] FAIL {rel}", flush=True)
                failed.append((path, err))

    failed_rel = {p.resolve().relative_to(app_root).as_posix() for p, _ in failed}
    keep_rel = set(keep_preset) | failed_rel
    removed, kept = strip_sources(to_compile + [app_root / rel for rel in preset_kept if (app_root / rel).exists()], keep_rel, app_root)

    restore_empty_init(app_root)

    shutil.rmtree(build_temp, ignore_errors=True)
    for leftover in workspace.glob(".cython_setup_*.py"):
        leftover.unlink(missing_ok=True)

    write_report(app_root, compiled_ok=removed, whitelist=sorted(set(kept)), keep_preset=keep_preset)

    main_ok = has_extension(app_root / "main.py") or (app_root / "main.py").exists()
    if not main_ok:
        print("[compile_app] app.main missing after compile", file=sys.stderr)
        return 3
    if not removed:
        print("[compile_app] compiled 0 modules; Cython toolchain likely broken", file=sys.stderr)
        return 4
    print(f"[compile_app] done compiled={len(removed)} kept_py={len(kept)}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
