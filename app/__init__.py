"""AI App Platform package.

Nuitka ``--module`` extensions replace ``__loader__`` with ``nuitka_module_loader``,
which is missing ``__name__``. Python 3.12 ``importlib._bootstrap._spec_from_module``
then raises and uvicorn dies during ``import app.main``. This patch is a no-op on
source runs (no Nuitka loaders).
"""

from __future__ import annotations

import sys
from typing import Any


def _stamp_nuitka_loader(module: Any) -> None:
    if module is None:
        return
    loader = getattr(module, "__loader__", None)
    if loader is None or type(loader).__name__ != "nuitka_module_loader":
        return
    name = getattr(module, "__name__", None) or getattr(loader, "name", None) or "nuitka_module_loader"
    for obj in (loader, getattr(getattr(module, "__spec__", None), "loader", None)):
        if obj is None or type(obj).__name__ != "nuitka_module_loader":
            continue
        if not hasattr(obj, "__name__"):
            try:
                setattr(obj, "__name__", name)
            except Exception:
                pass
        if not hasattr(obj, "name"):
            try:
                setattr(obj, "name", name)
            except Exception:
                pass


def _install_nuitka_import_compat() -> None:
    bootstrap = sys.modules.get("_frozen_importlib")
    if bootstrap is None:
        try:
            import importlib._bootstrap as bootstrap  # type: ignore[no-redef]
        except Exception:
            return

    orig_spec = getattr(bootstrap, "_spec_from_module", None)
    if orig_spec is None or getattr(orig_spec, "_models_app_nuitka_patched", False):
        return

    module_spec_cls = getattr(bootstrap, "ModuleSpec", None)

    def _spec_from_module(module, loader=None, origin=None):  # type: ignore[no-untyped-def]
        _stamp_nuitka_loader(module)
        if type(module).__name__ == "nuitka_module_loader":
            loader = module
            name = getattr(loader, "name", None) or getattr(loader, "__name__", None) or "nuitka_module_loader"
            if module_spec_cls is None:
                return orig_spec(module, loader, origin)
            return module_spec_cls(name, loader, origin=origin)
        try:
            return orig_spec(module, loader, origin)
        except AttributeError as exc:
            msg = str(exc)
            if "nuitka_module_loader" not in msg and "__name__" not in msg:
                raise
            name = getattr(module, "__name__", None)
            if name is None:
                ldr = loader if loader is not None else getattr(module, "__loader__", None)
                name = getattr(ldr, "name", None) or getattr(ldr, "__name__", None) or "_unknown"
            if loader is None:
                loader = getattr(module, "__loader__", None)
            file_origin = origin or getattr(module, "__file__", None)
            if module_spec_cls is None:
                raise
            spec = module_spec_cls(name, loader, origin=file_origin)
            try:
                spec.submodule_search_locations = list(module.__path__)
            except Exception:
                spec.submodule_search_locations = None
            return spec

    _spec_from_module._models_app_nuitka_patched = True  # type: ignore[attr-defined]
    bootstrap._spec_from_module = _spec_from_module
    alias = sys.modules.get("importlib._bootstrap")
    if alias is not None:
        alias._spec_from_module = _spec_from_module

    import builtins

    orig_import = builtins.__import__
    if getattr(orig_import, "_models_app_nuitka_patched", False):
        return

    def _import(name, globals=None, locals=None, fromlist=(), level=0):  # type: ignore[no-untyped-def]
        module = orig_import(name, globals, locals, fromlist, level)
        _stamp_nuitka_loader(module)
        if fromlist and fromlist != ("*",):
            pkg_name = getattr(module, "__name__", "") or ""
            for item in fromlist:
                if not item or item == "*":
                    continue
                _stamp_nuitka_loader(sys.modules.get(f"{pkg_name}.{item}"))
        return module

    _import._models_app_nuitka_patched = True  # type: ignore[attr-defined]
    builtins.__import__ = _import


_install_nuitka_import_compat()
