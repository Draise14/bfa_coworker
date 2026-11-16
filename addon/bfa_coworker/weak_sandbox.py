# SPDX-FileCopyrightText: 2026 Blender Authors
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""
Weak sandbox for LLM-generated code execution.

Note that this isn't really a sandbox,
more guidance that some things should not be done.

Notes:

- The reason *not* to use the prompt is that it tends not to be reliable,
  sometimes initial requests leave the context window (or are ignored for whatever reason),
  so we are better off with a simple way to prevent some things from happening.

- If the LLM (or its user) is motivated these can be worked around.
  This is more of a slap on the wrist not to try some things.

Two more things happen around the code it wraps (#548):

- The co-work scene lock is lifted while the code runs and put back before it
  returns (:func:`co_work_guard.lifted`): ``hide_select`` would otherwise make the
  agent's own ``select_set()`` a no-op and its selection operators CANCELLED.
- An operator that ends ``{'CANCELLED'}`` did nothing and raised nothing; the
  call's output says so (once per operator), so the model doesn't take the step
  for done.
"""

__all__ = (
    "WeakSandboxForLLM",
)

import sys
import threading
from typing import Any, Self


def _blocked_exit(*args: object, **kwargs: object) -> None:  # noqa: ARG001
    raise RuntimeError("sys.exit() is not allowed in LLM-generated code")


# Each entry is `(object, attr_name, replacement)`.
_OVERRIDES: tuple[tuple[object, str, object], ...] = (
    (sys, "exit", _blocked_exit),
)

# Operators that LLM-generated code must not access.
# Each entry is `("module.func", "reason")`.
#
# Use this sparingly.
# The rule of thumb for inclusion is:
#
#    The operator is guaranteed to cause problems and/or failure.
#
# There are lots of operations that are fairly questionable:
# - `bpy.ops.screen.spacedata_cleanup`.
# - `bpy.ops.wm.previews_clear`
#
# but it's not the purpose of this weak sandbox to disallow
# things the LLM probably shouldn't be doing.
#
# NOTE(@ideasman42): The scope of this may change over time,
# the statement above is a rule of thumb - to apply for now.
#
_BLOCKED_OPS: tuple[tuple[str, str], ...] = (
    ("wm.quit_blender", "Terminates the Blender process, use bpy.app.quit() if you must"),
    (
        "wm.read_factory_settings",
        "Resets all user preferences and startup file, "
        "use bpy.ops.wm.read_homefile() or "
        "bpy.ops.wm.read_homefile(use_empty=True, use_factory_startup=True) instead",
    ),
    (
        "wm.read_factory_userpref",
        "Resets all user preferences, "
        "use bpy.ops.wm.read_homefile() or "
        "bpy.ops.wm.read_homefile(use_empty=True, use_factory_startup=True) instead",
    ),
    ("wm.read_userpref", "May reset user preferences disabling this add-on, avoid calling"),
)

_BLOCKED_OPS_SET: frozenset[str] = frozenset(op for op, _reason in _BLOCKED_OPS)


def _why_cancelled(module: str) -> str:
    """The likely reason an ``object.*`` operator was cancelled ("" when none is seen)."""
    if module != "object":
        return ""
    try:
        import bpy  # noqa: WPS433
        ob = bpy.context.view_layer.objects.active
        if ob is None:
            return "there is no active object"
        if ob.hide_select:
            return "the active object {!r} can't be selected (hide_select)".format(ob.name)
        if not ob.visible_get():
            return "the active object {!r} is hidden".format(ob.name)
        if not ob.select_get():
            return "the active object {!r} isn't selected".format(ob.name)
    except Exception:  # pylint: disable=broad-exception-caught
        pass
    return ""


class _SaysCancelled:
    """An operator, called as before; when it ends ``{'CANCELLED'}`` the call's output says
    so, once per operator (#548).  Everything else reads through to the operator."""
    __slots__ = ("_op", "_name", "_module", "_said")

    def __init__(self, op: Any, module: str, func: str, said: set[str]) -> None:
        self._op, self._module, self._said = op, module, said
        self._name = "bpy.ops.{:s}.{:s}".format(module, func)

    def __call__(self, *args: Any, **kwargs: Any) -> Any:
        result = self._op(*args, **kwargs)
        if result == {"CANCELLED"} and self._name not in self._said:
            self._said.add(self._name)
            why = _why_cancelled(self._module)
            print("[Coworker] {:s}(...) returned {{'CANCELLED'}}: it did nothing{:s}. Don't report "
                  "that step as done -- fix the cause and run it again.".format(
                      self._name, " ({:s})".format(why) if why else ""))
        return result

    def __getattr__(self, name: str) -> Any:
        return getattr(self._op, name)

    def __dir__(self) -> list[str]:
        return dir(self._op)

    def __repr__(self) -> str:
        return repr(self._op)

    def __str__(self) -> str:
        return str(self._op)

    @property
    def __doc__(self) -> Any:  # type: ignore[override]  # the operator's own (its signature)
        return self._op.__doc__


def _co_work_lift() -> Any:
    """Lift the co-work scene lock for this call (#548); main thread only, and not while
    the unlock is on its way (this sandbox runs the unlock code too)."""
    if threading.current_thread() is not threading.main_thread():
        return None
    try:
        from . import co_work_guard
        if not co_work_guard.is_locked() or co_work_guard.is_releasing():
            return None
        import bpy  # noqa: WPS433
        return co_work_guard.lift(bpy.data)
    except Exception:  # pylint: disable=broad-exception-caught
        return None


def _co_work_relock(held: Any) -> None:
    if not held:
        return
    try:
        from . import co_work_guard
        import bpy  # noqa: WPS433
        co_work_guard.relock(bpy.data, held)
    except Exception:  # pylint: disable=broad-exception-caught
        pass


class WeakSandboxForLLM:
    """Context manager wrapping ``exec()`` of LLM-generated code."""
    __slots__ = (
        "_store_attrs",
        "_store_ops",
        "_lifted",
    )

    # -------------------------------------------------------------------------
    # Attribute overrides

    @staticmethod
    def override_store() -> list[tuple[object, str, object]]:
        """Save current values listed in ``_OVERRIDES`` and apply replacements."""
        saved: list[tuple[object, str, object]] = []
        for obj, attr, replacement in _OVERRIDES:
            saved.append((obj, attr, getattr(obj, attr)))
            setattr(obj, attr, replacement)
        return saved

    @staticmethod
    def override_restore(saved: list[tuple[object, str, object]]) -> None:
        """Restore values previously captured by :meth:`override_store`."""
        for obj, attr, original in saved:
            setattr(obj, attr, original)

    # -------------------------------------------------------------------------
    # Operator blocking

    @staticmethod
    def ops_blocked_store() -> tuple[Any, Any]:
        """Replace ``bpy.ops._op_create_function`` with a filtered wrapper.

        Returns ``(bpy_ops_module, original_function)`` for later restore.
        """
        import bpy.ops as _bpy_ops  # noqa: WPS433

        original = _bpy_ops._op_create_function
        said: set[str] = set()      # the cancelled operators this call already named (#548)

        def _filtered_op_create_function(module: str, func: str) -> Any:
            key = "{:s}.{:s}".format(module, func)
            if key in _BLOCKED_OPS_SET:
                reason = next(r for op, r in _BLOCKED_OPS if op == key)

                def _blocked(
                        *args: tuple[object, ...],
                        **kwargs: dict[str, object],
                ) -> None:
                    # Include the arguments as they may help the LLM pin-point the cause of the error.
                    args_str = ", ".join(
                        [repr(a) for a in args] + ["{:s}={!r}".format(k, v) for k, v in kwargs.items()]
                    )
                    raise RuntimeError(
                        "Operator 'bpy.ops.{:s}({:s})' is not allowed in LLM-generated code: {:s}".format(
                            key, args_str, reason,
                        )
                    )

                return _blocked
            return _SaysCancelled(original(module, func), module, func, said)

        _bpy_ops._op_create_function = _filtered_op_create_function
        return (_bpy_ops, original)

    @staticmethod
    def ops_blocked_restore(saved: tuple[Any, Any]) -> None:
        """Restore the original ``_op_create_function``."""
        bpy_ops_module, original = saved
        bpy_ops_module._op_create_function = original

    # -------------------------------------------------------------------------
    # Context manager

    def __enter__(self) -> Self:
        self._store_attrs = self.override_store()
        self._store_ops = self.ops_blocked_store()
        self._lifted = _co_work_lift()
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: object,
    ) -> None:
        del exc_type, exc_val, exc_tb
        try:
            _co_work_relock(self._lifted)
        finally:
            self.ops_blocked_restore(self._store_ops)
            self.override_restore(self._store_attrs)
