"""The one-file exe must include every module that is only imported when first needed.

PyInstaller follows ordinary (module-level) imports from the entry point. The registry imports
each tool inside a function, and a few modules are imported inside methods, so PyInstaller does
not see them. Missing ones only fail when the exe is used, so the build spec lists them and this
test keeps that list complete: every studio module that is imported inside a function and is NOT
reachable by ordinary imports from the entry point must be listed in `hiddenimports`.
"""

import ast
import os

from conftest import PROJECT_ROOT

ENTRY = "macro_studio.__main__"


def source_of(module):
    path = os.path.join(PROJECT_ROOT, *module.split("."))
    for candidate in (path + ".py", os.path.join(path, "__init__.py")):
        if os.path.isfile(candidate):
            with open(candidate, encoding="utf-8") as handle:
                return candidate, handle.read()
    return None, None


def module_level_imports(module):
    """Studio modules imported by `module` outside any function or class body."""
    _path, text = source_of(module)
    if text is None:
        return set()
    found = set()

    def visit(nodes):
        for node in nodes:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                continue
            if isinstance(node, ast.Import):
                found.update(a.name for a in node.names if a.name.startswith("macro_studio"))
            elif isinstance(node, ast.ImportFrom) and node.module and \
                    node.module.startswith("macro_studio"):
                found.add(node.module)
                found.update(f"{node.module}.{a.name}" for a in node.names)
            for child in ("body", "orelse", "finalbody", "handlers"):
                visit(getattr(node, child, []) or [])
    visit(ast.parse(text).body)
    return {m for m in found if source_of(m)[0]}


def listed_in_spec():
    """Studio modules named in the spec's hiddenimports (PyInstaller then follows their imports)."""
    import re
    return set(re.findall(r"'(macro_studio\.[A-Za-z0-9_.]+)'", spec_text()))


def reachable_from_entry():
    seen, todo = set(), [ENTRY, *listed_in_spec()]
    while todo:
        module = todo.pop()
        if module in seen:
            continue
        seen.add(module)
        parent = module.rsplit(".", 1)[0]
        if "." in module and parent not in seen:
            todo.append(parent)                       # a package's __init__ runs first
        todo.extend(module_level_imports(module))
    return seen


def lazily_imported_modules():
    found = set()
    for folder, _dirs, files in os.walk(os.path.join(PROJECT_ROOT, "macro_studio")):
        for name in files:
            if not name.endswith(".py"):
                continue
            with open(os.path.join(folder, name), encoding="utf-8") as handle:
                tree = ast.parse(handle.read())
            for function in ast.walk(tree):
                if isinstance(function, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    for node in ast.walk(function):
                        if isinstance(node, ast.ImportFrom) and node.module and \
                                node.module.startswith("macro_studio."):
                            found.add(node.module)
    return found


def spec_text():
    with open(os.path.join(PROJECT_ROOT, "RobloxMacroStudio.spec"), encoding="utf-8") as handle:
        return handle.read()


def test_every_lazily_loaded_studio_module_is_in_the_build_spec():
    reachable = reachable_from_entry()
    spec = spec_text()
    missing = sorted(m for m in lazily_imported_modules()
                     if m not in reachable and f"'{m}'" not in spec)
    assert not missing, f"add to hiddenimports in RobloxMacroStudio.spec: {missing}"


def test_the_reachability_check_itself_finds_the_obvious_cases():
    reachable = reachable_from_entry()
    assert "macro_studio.app" in reachable and "macro_studio.core.ui_theme" in reachable
    assert "macro_studio.runners.goliath" in reachable           # via the listed macro tool


def test_the_standard_library_modules_used_only_on_demand_are_listed():
    assert "'winreg'" in spec_text()            # start with Windows imports it inside a function
