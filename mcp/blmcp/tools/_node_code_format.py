# SPDX-FileCopyrightText: 2026 Blender Authors
# (Bforartists-maintained fork)
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""
Node trees as code (#480): the text format, its printer and its parser.

No ``bpy`` here: the reader and builder tool-codes splice this file in
(``# @include_begin``), and the tests import it directly.  The text is never
run: each statement is parsed with :mod:`ast` and only literals, names and the
few forms below are accepted (no ``eval`` / ``exec``).

Grammar (one statement per line; a line continues while a bracket is open;
``#`` starts a comment)::

    tree NAME (TreeType, prop=value, ...)      # a node group; starts a block
    material NAME (prop=value, ...)            # a material's node tree; starts a block

    input NAME: Type(option=value, ...) = default    # group interface, in order
    output NAME: Type(option=value, ...)
    panel NAME (option=value, ...)             # following inputs/outputs go in it
    end panel

    VAR = NodeType(setting=value, Socket=value, "Socket Name"=other.Output, ...)
    frame VAR (label="...", ...): child, child, ...
    del VAR, VAR                               # update mode: remove these nodes

* ``NAME`` / ``VAR`` is a Python identifier or a double-quoted string
  (``"Group Input"``).  ``VAR`` is the node's name in Blender.
* ``NodeType`` is the ``bl_idname`` minus its tree's prefix (``SetPosition`` for
  ``GeometryNodeSetPosition``, ``Math`` for ``ShaderNodeMath``, ``GroupInput`` for
  ``NodeGroupInput``); the full ``bl_idname`` is always accepted.
  Prefixes tried, in order: see :data:`TYPE_PREFIXES`.
* Inside ``NodeType(...)`` a bare key that is one of the node's properties is a
  *setting* (``operation="MULTIPLY"``); any other key, and every quoted key, is an
  input *socket*: matched by identifier first, then by name.  The printer uses
  the name, and the identifier where names repeat (``Value_001``).
* Node-level keys: ``label``, ``mute``, ``hide``, ``color`` (custom colour),
  ``width``, ``paired_output=VAR`` (zone input -> its output node),
  ``outputs={"Value": 0.5}`` (values kept on outputs: Value, RGB...),
  ``hidden_inputs=[...]`` / ``hidden_outputs=[...]`` (sockets hidden with Ctrl+H).
* Values: numbers, strings, ``True`` / ``False`` / ``None``, tuples (vectors,
  colours), lists, sets (enum flags, ``set()`` when empty), dicts, ``1e999``
  for infinity.  Datablocks (images, objects, node groups...) by name, as a string.
  Nested structures: ``ColorRamp(interpolation="EASE", elements=[(pos, (r, g, b, a)), ...])``,
  ``CurveMapping(curves=[[(x, y), (x, y, "VECTOR")], ...])``, ``ImageUser(...)``...
  Item lists of dynamic nodes: ``repeat_items=[("Geometry", "GEOMETRY"), ("Count", "INT")]``,
  ``enum_items=["A", "B"]``, ``index_switch_items=3``.
* Links are values: ``other.Output``, ``other["Socket Name"]``, ``other[0]``
  (index among the outputs), ``muted(other.Output)`` for a muted link, and a
  list of references for a multi-input socket (in link order).

Only values that differ from a new node's defaults are printed.  A tree that
uses node groups is printed after the groups' own ``tree`` blocks.
"""

__all__ = (
    "CodeError",
    "CodeSpec",
    "IfaceSpec",
    "NodeSpec",
    "Ref",
    "Struct",
    "TYPE_PREFIXES",
    "TreeSpec",
    "diff_trees",
    "format_name",
    "format_value",
    "is_ident",
    "layout_nodes",
    "nearest_names",
    "parse_code",
    "print_code",
    "print_head",
    "print_node",
    "print_tree",
    "print_tree_header",
    "resolve_socket",
    "resolve_type_name",
    "short_type_name",
    "socket_key",
)

import ast
import difflib
import io
import json
import keyword
import math
import re
import tokenize
import warnings


# Prefixes stripped from a bl_idname for its short name, per tree type, in the
# order a short name is resolved.  A short name is printed only when it
# resolves back to the same bl_idname (so the rule is reversible).
TYPE_PREFIXES = {
    "GeometryNodeTree": ("GeometryNode", "FunctionNode", "ShaderNode", "Node"),
    "ShaderNodeTree": ("ShaderNode", "Node", "FunctionNode", "GeometryNode"),
    "CompositorNodeTree": ("CompositorNode", "Node", "FunctionNode", "ShaderNode", "GeometryNode"),
    "TextureNodeTree": ("TextureNode", "Node"),
}
_DEFAULT_PREFIXES = ("Node",)

_STATEMENT_KEYWORDS = frozenset(("tree", "material", "input", "output", "panel", "end", "frame", "del"))
_IDENT = re.compile(r"[A-Za-z_][A-Za-z0-9_]*\Z")


class CodeError(Exception):
    """The text has errors; ``errors`` lists them (each starts with ``line N:``)."""

    def __init__(self, errors):
        super().__init__("; ".join(errors))
        self.errors = list(errors)


class _Plain:
    """Equality and repr from the fields (for tests and diffs)."""

    __slots__ = ()

    def _fields(self):
        return tuple(getattr(self, k) for k in self.__slots__ if k != "line")

    def __eq__(self, other):
        return type(other) is type(self) and self._fields() == other._fields()

    def __ne__(self, other):
        return not self.__eq__(other)

    def __hash__(self):
        return hash((type(self).__name__, repr(self._fields())))

    def __repr__(self):
        return "{:s}({:s})".format(type(self).__name__, ", ".join(
            "{:s}={!r}".format(k, getattr(self, k)) for k in self.__slots__ if k != "line"))


class Ref(_Plain):
    """A reference to a node's output (``socket`` None = the node itself)."""

    __slots__ = ("node", "socket", "muted")

    def __init__(self, node, socket=None, muted=False):
        self.node = node
        self.socket = socket
        self.muted = bool(muted)


class Struct(_Plain):
    """A nested structure value, ``Name(field=value, ...)``."""

    __slots__ = ("name", "fields")

    def __init__(self, name, fields=None):
        self.name = name
        self.fields = list(fields or [])   # [(key, value)]

    def get(self, key, default=None):
        for k, v in self.fields:
            if k == key:
                return v
        return default


class NodeSpec(_Plain):
    """One node line (or a ``frame`` line: ``is_frame``, ``children``)."""

    __slots__ = ("name", "type", "args", "is_frame", "children", "line")

    def __init__(self, name, type, args=None, is_frame=False, children=None, line=0):
        self.name = name
        self.type = type
        # [(key, value, quoted)]; a key that isn't an identifier is always quoted (a socket).
        self.args = [(k, v, bool(q) or not is_ident(k)) for k, v, q in (args or [])]
        self.is_frame = bool(is_frame)
        self.children = list(children or [])
        self.line = line

    def arg(self, key, default=None):
        for k, v, _q in self.args:
            if k == key:
                return v
        return default


class IfaceSpec(_Plain):
    """An interface line: ``kind`` is "socket", "panel" or "end"."""

    __slots__ = ("kind", "in_out", "name", "type", "options", "default", "has_default", "line")

    def __init__(self, kind, in_out="", name="", type="", options=None, default=None,
                 has_default=False, line=0):
        self.kind = kind
        self.in_out = in_out
        self.name = name
        self.type = type
        self.options = list(options or [])  # [(key, value)]
        self.default = default
        self.has_default = bool(has_default)
        self.line = line


class TreeSpec(_Plain):
    """A ``tree`` / ``material`` block (``kind`` "" for text without a header)."""

    __slots__ = ("kind", "name", "tree_type", "props", "interface", "nodes", "deletes", "line")

    def __init__(self, kind="tree", name="", tree_type="", props=None, interface=None, nodes=None,
                 deletes=None, line=0):
        self.kind = kind
        self.name = name
        self.tree_type = tree_type
        self.props = list(props or [])      # [(key, value)]
        self.interface = list(interface or [])
        self.nodes = list(nodes or [])
        self.deletes = list(deletes or [])  # [name]
        self.line = line

    def node(self, name):
        for n in self.nodes:
            if n.name == name:
                return n
        return None


class CodeSpec(_Plain):
    """A whole text: its blocks in order (groups before the trees using them)."""

    __slots__ = ("trees",)

    def __init__(self, trees=None):
        self.trees = list(trees or [])


# ---------------------------------------------------------------------------
# Printing


def is_ident(text):
    """A name usable bare (an identifier that isn't a Python keyword)."""
    return bool(_IDENT.match(text or "")) and not keyword.iskeyword(text)


def _quote(text):
    return json.dumps(str(text), ensure_ascii=False)


def format_name(name):
    """A node / interface name: bare when it's an identifier, else quoted."""
    return name if is_ident(name) and name not in _STATEMENT_KEYWORDS else _quote(name)


def _format_float(value):
    if math.isinf(value):
        return "1e999" if value > 0 else "-1e999"
    if math.isnan(value):
        return "0.0"
    text = "{:.6g}".format(value)
    number = float(text)
    if number == 0.0:
        return "0.0"
    if number.is_integer() and abs(number) < 1e15:
        return "{:.1f}".format(number)
    return repr(number)


def _format_ref(ref):
    text = format_name(ref.node) if ref.node not in _STATEMENT_KEYWORDS else _quote(ref.node)
    if ref.socket is None:
        pass
    elif isinstance(ref.socket, int) and not isinstance(ref.socket, bool):
        text += "[{:d}]".format(ref.socket)
    elif is_ident(ref.socket):
        text += "." + ref.socket
    else:
        text += "[" + _quote(ref.socket) + "]"
    return "muted(" + text + ")" if ref.muted else text


def format_value(value):
    """Print one value in the format's literal syntax."""
    if isinstance(value, Ref):
        return _format_ref(value)
    if isinstance(value, Struct):
        return "{:s}({:s})".format(value.name, ", ".join(
            "{:s}={:s}".format(k, format_value(v)) for k, v in value.fields))
    if value is None or isinstance(value, bool):
        return repr(value)
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        return _format_float(value)
    if isinstance(value, str):
        return _quote(value)
    if isinstance(value, tuple):
        if len(value) == 1:
            return "(" + format_value(value[0]) + ",)"
        return "(" + ", ".join(format_value(v) for v in value) + ")"
    if isinstance(value, list):
        return "[" + ", ".join(format_value(v) for v in value) + "]"
    if isinstance(value, (set, frozenset)):
        if not value:
            return "set()"
        return "{" + ", ".join(format_value(v) for v in sorted(value, key=str)) + "}"
    if isinstance(value, dict):
        return "{" + ", ".join("{:s}: {:s}".format(_quote(k), format_value(v)) for k, v in value.items()) + "}"
    return _quote(str(value))


def _format_key(key, quoted):
    if quoted or not is_ident(key):
        return _quote(key)
    return key


def _format_args(args):
    return ", ".join("{:s}={:s}".format(_format_key(k, q), format_value(v)) for k, v, q in args)


def print_node(spec):
    """One node (or frame) line."""
    if spec.is_frame:
        head = "frame " + format_name(spec.name)
        if spec.args:
            head += " (" + _format_args(spec.args) + ")"
        if spec.children:
            head += ": " + ", ".join(format_name(c) for c in spec.children)
        return head
    return "{:s} = {:s}({:s})".format(format_name(spec.name), spec.type, _format_args(spec.args))


def _print_iface(item):
    if item.kind == "end":
        return "end panel"
    if item.kind == "panel":
        text = "panel " + format_name(item.name)
        if item.options:
            text += " (" + ", ".join("{:s}={:s}".format(k, format_value(v)) for k, v in item.options) + ")"
        return text
    text = "{:s} {:s}: {:s}".format(item.in_out, format_name(item.name), item.type)
    if item.options:
        text += "(" + ", ".join("{:s}={:s}".format(k, format_value(v)) for k, v in item.options) + ")"
    if item.has_default:
        text += " = " + format_value(item.default)
    return text


def print_tree_header(spec):
    props = ", ".join("{:s}={:s}".format(k, format_value(v)) for k, v in spec.props)
    if spec.kind == "material":
        return "material " + _quote(spec.name) + (" (" + props + ")" if props else "")
    if spec.kind == "tree":
        inner = spec.tree_type + (", " + props if props else "")
        return "tree " + _quote(spec.name) + " (" + inner + ")"
    return ""


def print_head(spec):
    """The header and interface lines of a block."""
    lines = []
    header = print_tree_header(spec)
    if header:
        lines.append(header)
    lines.extend(_print_iface(i) for i in spec.interface)
    return "\n".join(lines)


def print_tree(spec):
    """A whole block as text (header, interface, nodes, frames, deletes)."""
    head = print_head(spec)
    lines = [head] if head else []
    lines.extend(print_node(n) for n in spec.nodes)
    if spec.deletes:
        lines.append("del " + ", ".join(format_name(n) for n in spec.deletes))
    return "\n".join(lines) + "\n"


def print_code(code):
    """All blocks, separated by a blank line."""
    return "\n".join(print_tree(t) for t in code.trees)


# ---------------------------------------------------------------------------
# Parsing


def _scan(line, depth):
    """Strip a comment and count brackets outside strings; returns (text, depth)."""
    out = []
    quote = ""
    i = 0
    while i < len(line):
        ch = line[i]
        if quote:
            out.append(ch)
            if ch == "\\" and i + 1 < len(line):
                out.append(line[i + 1])
                i += 2
                continue
            if ch == quote:
                quote = ""
        elif ch in "\"'":
            quote = ch
            out.append(ch)
        elif ch == "#":
            break
        else:
            if ch in "([{":
                depth += 1
            elif ch in ")]}":
                depth -= 1
            out.append(ch)
        i += 1
    return "".join(out), depth


def _logical_lines(text):
    buf = []
    start = 0
    depth = 0
    for number, raw in enumerate(text.splitlines(), 1):
        line, depth = _scan(raw, depth)
        if not buf and not line.strip():
            depth = 0
            continue
        if not buf:
            start = number
        buf.append(line)
        if depth <= 0:
            yield start, "\n".join(buf).strip()
            buf = []
            depth = 0
    if buf:
        yield start, "\n".join(buf).strip()


def _parse_expr(text, line):
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            return ast.parse(text.strip(), mode="eval").body
    except SyntaxError as ex:
        raise CodeError(["line {:d}: {:s} in `{:s}`".format(line + max((ex.lineno or 1) - 1, 0),
                                                            ex.msg or "syntax error", " ".join(text.split())[:120])])


def _const_name(node, line, what):
    """A NAME: an identifier or a string."""
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    raise CodeError(["line {:d}: {:s} must be a name or a quoted string".format(line, what)])


def _number(node):
    if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)) and not isinstance(node.value, bool):
        return node.value
    raise ValueError


def _value(node, line, keys=None):
    """Convert an ast expression to a format value (restricted grammar)."""
    if isinstance(node, ast.Constant):
        if isinstance(node.value, (str, int, float, bool)) or node.value is None:
            return node.value
    elif isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.USub, ast.UAdd)):
        try:
            number = _number(node.operand)
        except ValueError:
            pass
        else:
            return -number if isinstance(node.op, ast.USub) else number
    elif isinstance(node, ast.Tuple):
        return tuple(_value(e, line) for e in node.elts)
    elif isinstance(node, ast.List):
        return [_value(e, line) for e in node.elts]
    elif isinstance(node, ast.Set):
        items = [_value(e, line) for e in node.elts]
        if all(isinstance(i, str) for i in items):
            return set(items)
    elif isinstance(node, ast.Dict):
        if all(isinstance(k, ast.Constant) and isinstance(k.value, str) for k in node.keys):
            return {k.value: _value(v, line) for k, v in zip(node.keys, node.values)}
    elif isinstance(node, ast.Name):
        return Ref(node.id)
    elif isinstance(node, ast.Attribute):
        if isinstance(node.value, (ast.Name, ast.Constant)):
            return Ref(_const_name(node.value, line, "a node reference"), node.attr)
    elif isinstance(node, ast.Subscript):
        index = node.slice
        if type(index).__name__ == "Index":  # Python 3.8
            index = index.value
        if isinstance(node.value, (ast.Name, ast.Constant)) and isinstance(index, ast.Constant) \
                and isinstance(index.value, (str, int)) and not isinstance(index.value, bool):
            return Ref(_const_name(node.value, line, "a node reference"), index.value)
    elif isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
        name = node.func.id
        if name == "muted" and len(node.args) == 1 and not node.keywords:
            ref = _value(node.args[0], line)
            if isinstance(ref, Ref):
                return Ref(ref.node, ref.socket, muted=True)
        elif name == "set" and not node.args and not node.keywords:
            return set()
        elif not node.args and all(k.arg for k in node.keywords):
            return Struct(name, [(k.arg, _value(k.value, line)) for k in node.keywords])
    try:
        text = ast.unparse(node)
    except Exception:  # pylint: disable=broad-except
        text = type(node).__name__
    raise CodeError(["line {:d}: unsupported value `{:s}` (use numbers, strings, tuples, lists, "
                     "node.Socket references or Name(key=value))".format(line, text)])


def _quote_keys(rhs, line):
    """Replace quoted keyword keys (``"Base Color"=``) by placeholders; returns (text, {placeholder: key})."""
    try:
        tokens = list(tokenize.generate_tokens(io.StringIO(rhs).readline))
    except (tokenize.TokenError, IndentationError, SyntaxError) as ex:
        raise CodeError(["line {:d}: {:s}".format(line, str(ex.args[0] if ex.args else ex))])
    lines = rhs.splitlines(True)
    offsets = [0]
    for text in lines:
        offsets.append(offsets[-1] + len(text))
    spans = []
    significant = [t for t in tokens if t.type not in (tokenize.NL, tokenize.NEWLINE, tokenize.COMMENT,
                                                       tokenize.INDENT, tokenize.DEDENT)]
    for i, tok in enumerate(significant[:-1]):
        nxt = significant[i + 1]
        prev = significant[i - 1] if i else None
        if tok.type == tokenize.STRING and nxt.type == tokenize.OP and nxt.string == "=" \
                and prev is not None and prev.type == tokenize.OP and prev.string in ("(", ","):
            start = offsets[tok.start[0] - 1] + tok.start[1]
            end = offsets[tok.end[0] - 1] + tok.end[1]
            spans.append((start, end, tok.string))
    keys = {}
    for n, (start, end, literal) in enumerate(reversed(spans)):
        placeholder = "__q{:d}__".format(len(spans) - 1 - n)
        try:
            keys[placeholder] = ast.literal_eval(literal)
        except (ValueError, SyntaxError):
            raise CodeError(["line {:d}: bad quoted key {:s}".format(line, literal)])
        rhs = rhs[:start] + placeholder + rhs[end:]
    return rhs, keys


def _keywords(call, line, keys):
    out = []
    for kw in call.keywords:
        if kw.arg is None:
            raise CodeError(["line {:d}: `**` isn't supported; write each key=value".format(line)])
        if kw.arg in keys:
            out.append((keys[kw.arg], _value(kw.value, line), True))
        else:
            out.append((kw.arg, _value(kw.value, line), False))
    return out


_NODE_LINE = re.compile(r"""\s*(?P<name>[A-Za-z_][A-Za-z0-9_]*|"(?:[^"\\]|\\.)*"|'(?:[^'\\]|\\.)*')\s*=(?!=)\s*(?P<rhs>.*)\Z""",
                        re.S)
_NAME_HEAD = re.compile(r"""\s*(?P<name>[A-Za-z_][A-Za-z0-9_]*|"(?:[^"\\]|\\.)*"|'(?:[^'\\]|\\.)*')\s*""", re.S)


def _name_token(text):
    return ast.literal_eval(text) if text[:1] in "\"'" else text


def _split_top(text, sep):
    """Split *text* at the first *sep* outside brackets and strings."""
    depth = 0
    quote = ""
    i = 0
    while i < len(text):
        ch = text[i]
        if quote:
            if ch == "\\":
                i += 2
                continue
            if ch == quote:
                quote = ""
        elif ch in "\"'":
            quote = ch
        elif ch in "([{":
            depth += 1
        elif ch in ")]}":
            depth -= 1
        elif ch == sep and depth == 0:
            return text[:i], text[i + 1:]
        i += 1
    return text, None


def _parse_node_line(match, line):
    name = _name_token(match.group("name"))
    rhs, keys = _quote_keys(match.group("rhs"), line)
    call = _parse_expr(rhs, line)
    if not isinstance(call, ast.Call) or not isinstance(call.func, (ast.Name, ast.Attribute)):
        raise CodeError(["line {:d}: expected `{:s} = NodeType(key=value, ...)`".format(line, format_name(name))])
    if call.args:
        raise CodeError(["line {:d}: write node inputs and settings as key=value (no positional values)".format(line)])
    type_name = call.func.id if isinstance(call.func, ast.Name) else ast.unparse(call.func).split(".")[-1]
    return NodeSpec(name, type_name, _keywords(call, line, keys), line=line)


def _parse_header(kind, rest, line):
    expr = _parse_expr(rest, line)
    props = []
    tree_type = "ShaderNodeTree" if kind == "material" else ""
    if isinstance(expr, ast.Call):
        name = _const_name(expr.func, line, "the tree name")
        if kind == "tree":
            if len(expr.args) != 1 or not isinstance(expr.args[0], ast.Name):
                raise CodeError(["line {:d}: write `tree \"Name\" (GeometryNodeTree)` (or ShaderNodeTree, "
                                 "CompositorNodeTree)".format(line)])
            tree_type = expr.args[0].id
        elif expr.args:
            raise CodeError(["line {:d}: write `material \"Name\"`".format(line)])
        props = [(k, v) for k, v, _q in _keywords(expr, line, {})]
    else:
        name = _const_name(expr, line, "the tree name")
        if kind == "tree":
            raise CodeError(["line {:d}: the tree type is missing: `tree {:s} (GeometryNodeTree)`".format(
                line, _quote(name))])
    return TreeSpec(kind, name, tree_type, props, line=line)


def _parse_iface(kind, rest, line):
    head = _NAME_HEAD.match(rest)
    if not head or rest[head.end():head.end() + 1] != ":":
        raise CodeError(["line {:d}: write `{:s} Name: Type` (e.g. `{:s} Geometry: Geometry`)".format(line, kind, kind)])
    name = _name_token(head.group("name"))
    body = rest[head.end() + 1:]
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            stmt = ast.parse("_t: " + body.strip()).body[0]
    except SyntaxError as ex:
        raise CodeError(["line {:d}: {:s}".format(line, ex.msg or "syntax error")])
    ann = stmt.annotation
    options = []
    if isinstance(ann, ast.Call) and isinstance(ann.func, ast.Name) and not ann.args:
        type_name = ann.func.id
        options = [(k, v) for k, v, _q in _keywords(ann, line, {})]
    elif isinstance(ann, ast.Name):
        type_name = ann.id
    else:
        raise CodeError(["line {:d}: the socket type must be a name like Float or Geometry".format(line)])
    has_default = stmt.value is not None
    default = _value(stmt.value, line) if has_default else None
    return IfaceSpec("socket", kind, name, type_name, options, default, has_default, line=line)


def _parse_panel(rest, line):
    expr = _parse_expr(rest, line)
    if isinstance(expr, ast.Call):
        name = _const_name(expr.func, line, "the panel name")
        if expr.args:
            raise CodeError(["line {:d}: write `panel \"Name\" (default_closed=True)`".format(line)])
        return IfaceSpec("panel", name=name, options=[(k, v) for k, v, _q in _keywords(expr, line, {})], line=line)
    return IfaceSpec("panel", name=_const_name(expr, line, "the panel name"), line=line)


def _parse_names(text, line, what):
    expr = _parse_expr(text, line)
    items = expr.elts if isinstance(expr, ast.Tuple) else [expr]
    return [_const_name(i, line, what) for i in items]


def _parse_frame(rest, line):
    head, tail = _split_top(rest, ":")
    expr = _parse_expr(head, line)
    args = []
    if isinstance(expr, ast.Call):
        name = _const_name(expr.func, line, "the frame name")
        if expr.args:
            raise CodeError(["line {:d}: write `frame Name (label=\"...\"): a, b`".format(line)])
        args = _keywords(expr, line, {})
    else:
        name = _const_name(expr, line, "the frame name")
    children = _parse_names(tail, line, "a frame child") if tail is not None and tail.strip() else []
    return NodeSpec(name, "Frame", args, is_frame=True, children=children, line=line)


def parse_code(text):
    """Parse the text into a :class:`CodeSpec`; raises :class:`CodeError` (all errors, with line numbers)."""
    trees = []
    errors = []
    current = None
    panel_depth = 0

    def block(line):
        nonlocal current
        if current is None:
            current = TreeSpec(kind="", line=line)
            trees.append(current)
        return current

    for line, stmt in _logical_lines(text or ""):
        try:
            match = _NODE_LINE.match(stmt)
            word = stmt.split(None, 1)[0] if stmt.split() else ""
            rest = stmt[len(word):]
            if match:
                spec = _parse_node_line(match, line)
                tree = block(line)
                if tree.node(spec.name) is not None:
                    raise CodeError(["line {:d}: node {:s} is defined twice".format(line, format_name(spec.name))])
                tree.nodes.append(spec)
            elif word in ("tree", "material"):
                if panel_depth:
                    errors.append("line {:d}: `end panel` missing before this block".format(line))
                    panel_depth = 0
                current = _parse_header(word, rest, line)
                trees.append(current)
            elif word in ("input", "output"):
                block(line).interface.append(_parse_iface(word, rest, line))
            elif word == "panel":
                block(line).interface.append(_parse_panel(rest, line))
                panel_depth += 1
            elif word == "end":
                if rest.strip() not in ("", "panel"):
                    raise CodeError(["line {:d}: write `end panel`".format(line)])
                if not panel_depth:
                    raise CodeError(["line {:d}: `end panel` without a `panel`".format(line)])
                panel_depth -= 1
                block(line).interface.append(IfaceSpec("end", line=line))
            elif word == "frame":
                spec = _parse_frame(rest, line)
                tree = block(line)
                if tree.node(spec.name) is not None:
                    raise CodeError(["line {:d}: node {:s} is defined twice".format(line, format_name(spec.name))])
                tree.nodes.append(spec)
            elif word == "del":
                block(line).deletes.extend(_parse_names(rest, line, "a node to delete"))
            else:
                raise CodeError(["line {:d}: expected `name = NodeType(...)`, `input`, `output`, `panel`, "
                                 "`end panel`, `frame`, `del` or a `tree` / `material` header".format(line)])
        except CodeError as ex:
            errors.extend(ex.errors)
    if panel_depth:
        errors.append("end of text: `end panel` missing")
    if errors:
        raise CodeError(errors)
    return CodeSpec(trees)


# ---------------------------------------------------------------------------
# Names: node types, sockets, suggestions


def short_type_name(bl_idname, tree_type, known):
    """The short name for *bl_idname* in *tree_type* (reversible), or the bl_idname itself."""
    for prefix in TYPE_PREFIXES.get(tree_type, _DEFAULT_PREFIXES):
        if bl_idname.startswith(prefix) and len(bl_idname) > len(prefix):
            short = bl_idname[len(prefix):]
            if is_ident(short) and resolve_type_name(short, tree_type, known) == bl_idname:
                return short
    return bl_idname


def resolve_type_name(name, tree_type, known):
    """The bl_idname a written type name stands for (``None`` when unknown)."""
    if name in known:
        return name
    for prefix in TYPE_PREFIXES.get(tree_type, _DEFAULT_PREFIXES):
        if prefix + name in known:
            return prefix + name
    return None


def resolve_socket(key, sockets):
    """Index of the socket *key* stands for, or None.

    *sockets* is a list of ``(identifier, name, enabled)``; *key* a string
    (identifier first, then the name of an enabled socket, then any socket's
    name, then a case-insensitive name) or an int index.
    """
    if isinstance(key, int) and not isinstance(key, bool):
        return key if 0 <= key < len(sockets) else None
    for i, (ident, _name, _enabled) in enumerate(sockets):
        if ident == key:
            return i
    for i, (_ident, name, enabled) in enumerate(sockets):
        if name == key and enabled:
            return i
    for i, (_ident, name, _enabled) in enumerate(sockets):
        if name == key:
            return i
    low = str(key).lower()
    for i, (_ident, name, enabled) in enumerate(sockets):
        if name.lower() == low and enabled:
            return i
    return None


def socket_key(index, sockets):
    """The key to print for socket *index*: its name when that finds it back, else its identifier."""
    ident, name, _enabled = sockets[index]
    if name and resolve_socket(name, sockets) == index:
        return name
    return ident


def _norm(text):
    return re.sub(r"[^a-z0-9]", "", str(text).lower())


def nearest_names(word, candidates, limit=6, aliases=None):
    """The valid names closest to *word* (all of them when there are only a few).

    *aliases* maps other spellings (e.g. a node's UI label) to a candidate: a close
    alias suggests its candidate.
    """
    seen = []
    for c in candidates:
        if c not in seen:
            seen.append(c)
    if len(seen) <= limit and not aliases:
        return seen
    target = _norm(word)
    by_norm = {}
    for c in seen:
        by_norm.setdefault(_norm(c), c)
    for alias, c in (aliases or {}).items():
        by_norm.setdefault(_norm(alias), c)
    picks = []

    def add(c):
        if c not in picks:
            picks.append(c)

    for n in difflib.get_close_matches(target, list(by_norm), n=limit * 2, cutoff=0.5):
        add(by_norm[n])
    if target:
        for n, c in by_norm.items():
            if n and (target in n or n in target):
                add(c)
    if not picks:
        for n in difflib.get_close_matches(target, list(by_norm), n=limit, cutoff=0.0):
            add(by_norm[n])
    return picks[:limit]


# ---------------------------------------------------------------------------
# Layout


def layout_nodes(names, sizes, edges, pairs=(), gap_x=80.0, gap_y=40.0):
    """Left-to-right layered positions ``{name: (x, y)}`` (top-left corners, y up).

    Each node goes in the column just left of its nearest consumer (longest
    path to an output decides the column); a zone's input is kept left of its
    output.  Columns are ordered by the position of the nodes they feed.
    """
    names = list(names)
    order = {n: i for i, n in enumerate(names)}
    succ = {n: [] for n in names}
    for a, b in list(edges) + list(pairs):
        if a in succ and b in succ and a != b and b not in succ[a]:
            succ[a].append(b)
    dist = {}
    visiting = set()

    def to_sink(n):
        if n in dist:
            return dist[n]
        if n in visiting:  # a cycle (not in valid trees): cut it here
            return 0
        visiting.add(n)
        best = 0
        for s in succ[n]:
            best = max(best, 1 + to_sink(s))
        visiting.discard(n)
        dist[n] = best
        return best

    for n in names:
        to_sink(n)
    depth = max(dist.values()) if dist else 0
    columns = {}
    for n in names:
        columns.setdefault(depth - dist[n], []).append(n)
    widths = {c: max(sizes.get(n, (140.0, 100.0))[0] for n in col) for c, col in columns.items()}
    xs = {}
    x = 0.0
    for c in range(depth + 1):
        xs[c] = x
        x += widths.get(c, 0.0) + gap_x
    pos = {}
    for c in range(depth, -1, -1):
        col = columns.get(c, [])

        def weight(n):
            ys = [pos[s][1] - sizes.get(s, (140.0, 100.0))[1] / 2.0 for s in succ[n] if s in pos]
            return (-(sum(ys) / len(ys)) if ys else 0.0, order[n])

        if c != depth:
            col = sorted(col, key=weight)
        total = sum(sizes.get(n, (140.0, 100.0))[1] for n in col) + gap_y * max(len(col) - 1, 0)
        y = total / 2.0
        for n in col:
            pos[n] = (xs[c], y)
            y -= sizes.get(n, (140.0, 100.0))[1] + gap_y
    return pos


# ---------------------------------------------------------------------------
# Change lists (dry run)


def _arg_map(spec):
    return {k: format_value(v) for k, v, _q in spec.args}


def diff_trees(old, new):
    """Human-readable changes from *old* to *new* (both :class:`TreeSpec`; *old* may be None)."""
    if old is None:
        links = sum(1 for n in new.nodes for _k, v, _q in n.args
                    if isinstance(v, Ref) or isinstance(v, list) and any(isinstance(i, Ref) for i in v))
        return ["create {:s} {:s}: {:d} nodes, {:d} interface sockets, {:d} linked inputs".format(
            new.kind or "tree", _quote(new.name), len(new.nodes),
            sum(1 for i in new.interface if i.kind == "socket"), links)]
    changes = []
    old_iface = [_print_iface(i) for i in old.interface]
    new_iface = [_print_iface(i) for i in new.interface]
    if old_iface != new_iface:
        for line in old_iface:
            if line not in new_iface:
                changes.append("interface: remove `{:s}`".format(line))
        for line in new_iface:
            if line not in old_iface:
                changes.append("interface: add `{:s}`".format(line))
        if not changes:
            changes.append("interface: reorder")
    if [(k, format_value(v)) for k, v in old.props] != [(k, format_value(v)) for k, v in new.props]:
        changes.append("tree settings: {:s}".format(", ".join(
            "{:s}={:s}".format(k, format_value(v)) for k, v in new.props) or "defaults"))
    old_nodes = {n.name: n for n in old.nodes}
    new_nodes = {n.name: n for n in new.nodes}
    for name, spec in new_nodes.items():
        before = old_nodes.get(name)
        if before is None:
            changes.append("add {:s} {:s} ({:s})".format("frame" if spec.is_frame else "node",
                                                       _quote(name), spec.type))
            continue
        if before.type != spec.type:
            changes.append("replace node {:s}: {:s} -> {:s}".format(_quote(name), before.type, spec.type))
            continue
        a, b = _arg_map(before), _arg_map(spec)
        keys = [k for k in list(b) + [k for k in a if k not in b] if a.get(k) != b.get(k)]
        if keys:
            changes.append("change node {:s}: {:s}".format(_quote(name), ", ".join(
                "{:s} {:s} -> {:s}".format(k, a.get(k, "default"), b.get(k, "default")) for k in keys)))
        if before.children != spec.children:
            changes.append("frame {:s}: {:s}".format(_quote(name), ", ".join(spec.children) or "empty"))
    for name, spec in old_nodes.items():
        if name not in new_nodes:
            changes.append("remove {:s} {:s} ({:s})".format("frame" if spec.is_frame else "node",
                                                          _quote(name), spec.type))
    return changes
