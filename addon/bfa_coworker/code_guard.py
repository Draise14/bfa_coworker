# SPDX-FileCopyrightText: 2026 Blender Authors
# (Bforartists-maintained fork)
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""
Code and file guard (Tier 4, Security 3, #347): two more risky steps for
:mod:`risky_steps`, read from the code's syntax tree rather than its text, so
a path or a module name that only sits in a string, a comment or a docstring
doesn't count.

- **Writes outside the project folder.**  Each place the code writes a file
  -- ``open(..., "w"/"a"/"x"/"+")``, ``pathlib`` writes, ``shutil`` copies and
  moves, ``os`` renames, removals and new folders, ``Image.save`` /
  ``save_render``, ``filepath_raw`` / ``render.filepath``, a save or export
  operator's ``filepath`` -- is resolved without running anything: string
  literals, ``//`` (relative to the .blend, where Blender reads it so),
  ``bpy.path.abspath``, ``bpy.data.filepath``, ``os.path`` / ``pathlib``
  joins, ``tempfile.gettempdir()``, ``~`` and environment variables, and a
  variable's last plain assignment.  Inside the .blend's folder (or below)
  or the temp folder is fine; anywhere else asks.  A relative path is where
  Python would really write it: Blender's working folder.
- **The internet.**  Importing (or naming) ``urllib.request``, ``requests``,
  ``socket``, ``http.client``, ``httpx``, ``aiohttp``, ``ftplib``,
  ``smtplib`` and friends asks.  ``urllib.parse`` doesn't: it only formats
  text.

Choices:

- *An unsaved .blend* has no project folder: only the temp folder is fine,
  every other path the code names asks.  Saving it with
  ``save_as_mainfile`` is choosing its folder, not writing outside it (the
  save itself still asks, as before).
- *A path that can't be worked out* (a loop variable, a function's
  argument, a property read at run time) doesn't ask: the guard would nag on
  every script that builds names.  When its start is known
  (``f"C:/Windows/{name}"``, ``os.path.join(folder, name)``), that start is
  what counts.
- *Code that doesn't parse* can't run, so only the text rules see it.

bpy-free (the caller passes the .blend path).
"""

from __future__ import annotations

__all__ = (
    "NET_MODULES",
    "findings",
    "network",
    "writes_outside",
)

import ast
import os
import tempfile
from typing import Iterable, Iterator

# Modules that reach the network (``urllib.parse`` and friends don't).
NET_MODULES = frozenset((
    "aiohttp", "ftplib", "http.client", "http.server", "httpx", "imaplib", "poplib", "requests",
    "smtplib", "socket", "socketserver", "telnetlib", "urllib.request", "urllib2", "urllib3",
    "websocket", "websockets", "xmlrpc.client", "xmlrpc.server",
))

_PATH_TYPES = frozenset(("Path", "PurePath", "PosixPath", "WindowsPath", "PurePosixPath", "PureWindowsPath"))
_OPEN_FUNCS = frozenset(("open", "io.open", "builtins.open", "codecs.open"))
_JOIN_FUNCS = frozenset(("os.path.join", "path.join", "join", "posixpath.join", "ntpath.join"))
_SAME_FUNCS = frozenset((
    "str", "os.fspath", "os.path.abspath", "os.path.normpath", "os.path.realpath", "path.abspath",
    "path.normpath", "abspath", "normpath", "bpy.path.native_pathsep",
))
# os / shutil calls by the arguments they change (``(index, keyword)``).
_OS_TARGETS: dict[str, tuple[tuple[int, str], ...]] = {
    "os.makedirs": ((0, "name"),), "os.mkdir": ((0, "path"),), "os.remove": ((0, "path"),),
    "os.unlink": ((0, "path"),), "os.rmdir": ((0, "path"),), "os.removedirs": ((0, "name"),),
    "os.truncate": ((0, "path"),),
    "os.rename": ((0, "src"), (1, "dst")), "os.replace": ((0, "src"), (1, "dst")),
    "os.renames": ((0, "old"), (1, "new")), "os.link": ((1, "dst"),), "os.symlink": ((1, "dst"),),
    "shutil.copy": ((1, "dst"),), "shutil.copy2": ((1, "dst"),), "shutil.copyfile": ((1, "dst"),),
    "shutil.copytree": ((1, "dst"),), "shutil.copymode": ((1, "dst"),), "shutil.copystat": ((1, "dst"),),
    "shutil.move": ((0, "src"), (1, "dst")), "shutil.rmtree": ((0, "path"),),
    "shutil.make_archive": ((0, "base_name"),), "shutil.unpack_archive": ((1, "extract_dir"),),
}
# pathlib methods that change the file system (the receiver is changed; ``(index, keyword)`` too).
_PATH_METHODS: dict[str, tuple[tuple[int, str], ...]] = {
    "write_text": (), "write_bytes": (), "touch": (), "mkdir": (), "unlink": (), "rmdir": (),
    "rename": ((0, "target"),), "replace": ((0, "target"),), "symlink_to": (), "hardlink_to": (),
}
_WRITE_FLAGS = frozenset(("O_WRONLY", "O_RDWR", "O_CREAT", "O_APPEND", "O_TRUNC"))
_DEPTH = 24


def _dotted(node: ast.AST) -> str:
    """``a.b.c`` for a name / attribute chain, "" for anything else."""
    parts: list[str] = []
    while isinstance(node, ast.Attribute):
        parts.append(node.attr)
        node = node.value
    if isinstance(node, ast.Name):
        parts.append(node.id)
        return ".".join(reversed(parts))
    return ""


def _arg(call: ast.Call, index: int, keyword: str) -> ast.AST | None:
    for kw in call.keywords:
        if kw.arg == keyword:
            return kw.value
    if 0 <= index < len(call.args) and not any(isinstance(a, ast.Starred) for a in call.args[:index + 1]):
        return call.args[index]
    return None


def _is_write_mode(mode: ast.AST | None) -> bool:
    if mode is None:
        return False                                  # "r": reading is fine
    if isinstance(mode, ast.Constant):
        return isinstance(mode.value, str) and any(c in mode.value for c in "wax+")
    return True                                       # a mode worked out at run time: it may write


class _Paths:
    """Works out what a path expression is, without running it."""

    def __init__(self, tree: ast.AST, blend_path: str, cwd: str, temp: str) -> None:
        self.blend_dir = os.path.dirname(os.path.abspath(blend_path)) if blend_path else ""
        self.blend_path = os.path.abspath(blend_path) if blend_path else ""
        self.cwd, self.temp = cwd, temp
        # Every place a plain name is bound: (line, column, value or None when it can't be known).
        self.bindings: dict[str, list[tuple[int, int, ast.AST | None]]] = {}
        for node in ast.walk(tree):
            if isinstance(node, ast.Assign):
                for target in node.targets:
                    self._bind(target, node.value if isinstance(target, ast.Name) else None, node)
            elif isinstance(node, (ast.AnnAssign, ast.AugAssign, ast.NamedExpr)):
                value = node.value if isinstance(node, ast.AnnAssign) else None
                self._bind(node.target, value, node)
            elif isinstance(node, (ast.For, ast.AsyncFor, ast.comprehension)):
                self._bind(node.target, None, node.target)
            elif isinstance(node, ast.withitem) and node.optional_vars is not None:
                self._bind(node.optional_vars, None, node.optional_vars)
            elif isinstance(node, ast.arg):
                self._bind(ast.Name(id=node.arg), None, node)
        for spots in self.bindings.values():
            spots.sort(key=lambda s: (s[0], s[1]))

    def _bind(self, target: ast.AST, value: ast.AST | None, at: ast.AST) -> None:
        if isinstance(target, ast.Name):
            line, col = getattr(at, "lineno", 0), getattr(at, "col_offset", 0)
            self.bindings.setdefault(target.id, []).append((line, col, value))
        elif isinstance(target, (ast.Tuple, ast.List)):
            for item in target.elts:
                self._bind(item.value if isinstance(item, ast.Starred) else item, None, at)

    def _value_of(self, name: ast.Name) -> ast.AST | None:
        """The name's last binding before it's used (a plain assignment), or None."""
        here = (name.lineno, name.col_offset)
        before = [s for s in self.bindings.get(name.id, ()) if (s[0], s[1]) < here]
        return before[-1][2] if before else None

    # -- the path an expression stands for: (path, exact) or None --------------------------
    def resolve(self, node: ast.AST | None, blender: bool = False, depth: int = 0) -> tuple[str, bool] | None:
        if node is None or depth > _DEPTH:
            return None
        depth += 1
        if isinstance(node, ast.Constant):
            if not isinstance(node.value, str) or not node.value:
                return None
            text = node.value
            if blender and text.startswith("//"):         # Blender reads "//" as "next to the .blend"
                return (os.path.join(self.blend_dir, text[2:]), True) if self.blend_dir else None
            return text, True
        if isinstance(node, ast.Name):
            return self.resolve(self._value_of(node), blender, depth)
        if isinstance(node, ast.JoinedStr):
            return self._concat(node.values, blender, depth)
        if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
            return self._concat([node.left, node.right], blender, depth)
        if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Div):
            return self._join([node.left, node.right], blender, depth)
        if isinstance(node, ast.Attribute):
            if _dotted(node) == "bpy.data.filepath":
                return (self.blend_path, True) if self.blend_path else None
            if node.attr == "parent":
                inner = self.resolve(node.value, blender, depth)
                return (os.path.dirname(os.path.normpath(inner[0])), inner[1]) if inner else None
            return None
        if isinstance(node, ast.Subscript) and _dotted(node.value) == "os.environ":
            return self._env(node.slice)
        if isinstance(node, ast.Call):
            return self._call(node, blender, depth)
        return None

    def _env(self, key: ast.AST | None) -> tuple[str, bool] | None:
        if isinstance(key, ast.Constant) and isinstance(key.value, str) and os.environ.get(key.value):
            return os.environ[key.value], True
        return None

    def _concat(self, parts: Iterable[ast.AST], blender: bool, depth: int) -> tuple[str, bool] | None:
        """Pieces of a string, as far as they are known (the known start counts)."""
        text = ""
        for part in parts:
            if isinstance(part, ast.FormattedValue):
                part = part.value if part.format_spec is None and part.conversion in (-1, 115) else None
            piece = self.resolve(part, blender and not text, depth) if part is not None else None
            if piece is None:
                return (text, False) if text else None
            text += piece[0]
            if not piece[1]:
                return text, False
        return (text, True) if text else None

    def _join(self, parts: Iterable[ast.AST], blender: bool, depth: int) -> tuple[str, bool] | None:
        """``os.path.join`` / ``Path(a, b)`` / ``a / b``, as far as the parts are known."""
        path = ""
        for part in parts:
            if isinstance(part, ast.Starred):
                part = None
            piece = self.resolve(part, blender and not path, depth) if part is not None else None
            if piece is None:
                return (os.path.join(path, ""), False) if path else None
            path = os.path.join(path, piece[0]) if path else piece[0]
            if not piece[1]:
                return path, False
        return (path, True) if path else None

    def _call(self, call: ast.Call, blender: bool, depth: int) -> tuple[str, bool] | None:
        name = _dotted(call.func)
        last = name.rpartition(".")[2]
        first = call.args[0] if call.args else None
        if name in _JOIN_FUNCS:
            return self._join(call.args, blender, depth)
        if last in _PATH_TYPES and name in (last, "pathlib." + last):
            return self._join(call.args, blender, depth) if call.args else (self.cwd, True)
        if name in _SAME_FUNCS:
            return self.resolve(first, blender, depth)
        if name == "bpy.path.abspath":
            return self.resolve(first, True, depth)
        if name in ("os.path.dirname", "path.dirname", "dirname"):
            inner = self.resolve(first, blender, depth)
            return (os.path.dirname(inner[0]), inner[1]) if inner else None
        if name in ("os.path.expanduser", "os.path.expandvars"):
            inner = self.resolve(first, blender, depth)
            if inner is None:
                return None
            expand = os.path.expanduser if name.endswith("user") else os.path.expandvars
            return expand(inner[0]), inner[1]
        if name in ("tempfile.gettempdir", "gettempdir"):
            return self.temp, True
        if name in ("tempfile.mkdtemp", "mkdtemp"):
            return os.path.join(self.temp, ""), False
        if name in ("os.getcwd", "Path.cwd", "pathlib.Path.cwd"):
            return self.cwd, True
        if name in ("Path.home", "pathlib.Path.home"):
            return os.path.expanduser("~"), True
        if name in ("os.getenv", "os.environ.get"):
            return self._env(first)
        if isinstance(call.func, ast.Attribute) and self.is_pathlike(call.func.value, depth):
            method = call.func.attr
            if method in ("resolve", "absolute", "expanduser"):
                inner = self.resolve(call.func.value, blender, depth)
                return (os.path.expanduser(inner[0]), inner[1]) if inner else None
            if method == "joinpath":
                return self._join([call.func.value, *call.args], blender, depth)
            if method in ("with_name", "with_suffix", "with_stem"):
                inner = self.resolve(call.func.value, blender, depth)
                return (os.path.dirname(os.path.normpath(inner[0])), False) if inner else None
        return None

    def is_pathlike(self, node: ast.AST, depth: int = 0) -> bool:
        """*node* is a ``pathlib`` path (its ``.replace`` isn't ``str.replace``)."""
        if depth > _DEPTH:
            return False
        depth += 1
        if isinstance(node, ast.Name):
            value = self._value_of(node)
            return value is not None and self.is_pathlike(value, depth)
        if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Div):
            return self.is_pathlike(node.left, depth)
        if isinstance(node, ast.Attribute) and node.attr == "parent":
            return self.is_pathlike(node.value, depth)
        if isinstance(node, ast.Call):
            name = _dotted(node.func)
            last = name.rpartition(".")[2]
            if last in _PATH_TYPES and name in (last, "pathlib." + last):
                return True
            if name in ("Path.home", "Path.cwd", "pathlib.Path.home", "pathlib.Path.cwd"):
                return True
            if isinstance(node.func, ast.Attribute) and node.func.attr in (
                    "resolve", "absolute", "expanduser", "joinpath", "with_name", "with_suffix", "with_stem"):
                return self.is_pathlike(node.func.value, depth)
        return False

    # -- where it lands ---------------------------------------------------------------------
    def absolute(self, path: str) -> str:
        return os.path.normpath(os.path.join(self.cwd, path))

    def inside_allowed(self, path: str) -> bool:
        roots = [r for r in (self.blend_dir, self.temp) if r]
        return any(_inside(path, root) for root in roots)


def _inside(path: str, root: str) -> bool:
    path_n, root_n = os.path.normcase(os.path.normpath(path)), os.path.normcase(os.path.normpath(root))
    try:
        return os.path.commonpath([path_n, root_n]) == root_n
    except ValueError:                                # another drive
        return False


def _targets(tree: ast.AST, paths: _Paths) -> Iterator[tuple[ast.AST, bool, bool]]:
    """``(path expression, Blender reads "//", is the .blend's own Save As)`` for each write."""
    for node in ast.walk(tree):
        if isinstance(node, (ast.Assign, ast.AnnAssign)) and node.value is not None:
            for target in (node.targets if isinstance(node, ast.Assign) else [node.target]):
                if isinstance(target, ast.Attribute) and (
                        target.attr in ("filepath_raw", "base_path") or _dotted(target).endswith("render.filepath")):
                    yield node.value, True, False
            continue
        if not isinstance(node, ast.Call):
            continue
        name = _dotted(node.func)
        method = node.func.attr if isinstance(node.func, ast.Attribute) else ""
        if name in _OPEN_FUNCS:
            if _is_write_mode(_arg(node, 1, "mode")):
                yield _arg(node, 0, "file") or _arg(node, 0, "filename"), False, False
        elif name == "os.open":
            flags = _arg(node, 1, "flags")
            if flags is not None and any(isinstance(n, ast.Attribute) and n.attr in _WRITE_FLAGS
                                         or isinstance(n, ast.Name) and n.id in _WRITE_FLAGS
                                         for n in ast.walk(flags)):
                yield _arg(node, 0, "path"), False, False
        elif name in _OS_TARGETS:
            for index, keyword in _OS_TARGETS[name]:
                yield _arg(node, index, keyword), False, False
        elif name.startswith(("bpy.ops.", "ops.")) and name.count(".") >= 2:
            module, _dot, op = name.rpartition(".")
            module = module.rpartition(".")[2]
            if module.startswith("export") or any(w in op for w in ("export", "save", "write", "mixdown")):
                own_save = module == "wm" and op in ("save_mainfile", "save_as_mainfile")
                for keyword in ("filepath", "directory"):
                    value = _arg(node, -1, keyword)
                    if value is not None:
                        yield value, True, own_save
        elif method in ("save", "save_render"):
            yield _arg(node, 0, "filepath"), True, False
        elif name.endswith("libraries.write"):
            yield _arg(node, 0, "filepath"), True, False
        elif method == "open" and paths.is_pathlike(node.func.value):
            if _is_write_mode(_arg(node, 0, "mode")):
                yield node.func.value, False, False
        elif method in _PATH_METHODS and paths.is_pathlike(node.func.value):
            yield node.func.value, False, False
            for index, keyword in _PATH_METHODS[method]:
                yield _arg(node, index, keyword), False, False


def _parse(code: str) -> ast.AST | None:
    try:
        return ast.parse(code)
    except (SyntaxError, ValueError, RecursionError, MemoryError):
        return None


def writes_outside(code: str, blend_path: str = "", *, cwd: str = "", temp: str = "",
                   tree: ast.AST | None = None) -> list[str]:
    """The paths *code* would write outside the project (the .blend's folder) and the temp
    folder; a path known only by its start ends in a separator.  *blend_path* "" = unsaved."""
    tree = tree if tree is not None else _parse(code)
    if tree is None:
        return []
    paths = _Paths(tree, blend_path, cwd or os.getcwd(), temp or tempfile.gettempdir())
    found: list[str] = []
    for expr, blender, own_save in _targets(tree, paths):
        if own_save and not blend_path:
            continue                                  # saving an unsaved file picks its folder
        resolved = paths.resolve(expr, blender)
        if resolved is None:
            continue                                  # worked out at run time: not guessed at
        text, exact = resolved
        if not exact:                                 # only its start is known: judge by its folder
            cut = max(text.rfind("/"), text.rfind("\\"))
            text = text[:cut + 1] if cut >= 0 else ""
        where = paths.absolute(text) if text else paths.cwd
        if not exact:
            where = os.path.join(where, "")
        if not paths.inside_allowed(where) and where not in found:
            found.append(where)
    return found


def network(code: str, tree: ast.AST | None = None) -> bool:
    """*code* imports or names a module that reaches the network."""
    tree = tree if tree is not None else _parse(code)
    if tree is None:
        return False

    def net(module: str) -> bool:
        return any(module == m or module.startswith(m + ".") for m in NET_MODULES)

    # The names imports bind to modules: ``import urllib`` then ``urllib.request.urlopen``.
    # (A bare ``socket.default_value`` is a node socket, not the module.)
    modules: dict[str, str] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            if any(net(alias.name) for alias in node.names):
                return True
            for alias in node.names:
                modules[alias.asname or alias.name.partition(".")[0]] = (
                    alias.name if alias.asname else alias.name.partition(".")[0])
        elif isinstance(node, ast.ImportFrom) and node.module and not node.level:
            if net(node.module) or any(net(node.module + "." + alias.name) for alias in node.names):
                return True
            for alias in node.names:
                modules[alias.asname or alias.name] = node.module + "." + alias.name
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute):
            root, _dot, rest = _dotted(node).partition(".")
            if root in modules and net(modules[root] + "." + rest):
                return True
        elif isinstance(node, ast.Call) and _dotted(node.func) in ("__import__", "importlib.import_module",
                                                                   "import_module"):
            first = node.args[0] if node.args else None
            if isinstance(first, ast.Constant) and isinstance(first.value, str) and net(first.value):
                return True
    return False


def findings(code: str, blend_path: str = "", *, cwd: str = "", temp: str = "") -> list[str]:
    """What makes *code* risky by these two rules, in plain words (``[]`` when nothing does)."""
    tree = _parse(code)
    if tree is None:
        return []
    found: list[str] = []
    outside = writes_outside(code, blend_path, cwd=cwd, temp=temp, tree=tree)
    if outside:
        shown = ", ".join(outside[:2]) + (" (+{:d} more)".format(len(outside) - 2) if len(outside) > 2 else "")
        if blend_path:
            found.append("write outside your project folder: " + shown)
        else:
            found.append("write files before the .blend is saved (no project folder yet): " + shown)
    if network(code, tree):
        found.append("connect to the internet")
    return found
