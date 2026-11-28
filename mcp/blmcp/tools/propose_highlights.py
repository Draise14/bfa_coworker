# SPDX-FileCopyrightText: 2026 Blender Authors
# (Bforartists-maintained fork)
#
# SPDX-License-Identifier: GPL-3.0-or-later

# pylint: disable=C0114  # See tool doc-string.

__all__ = (
    "register",
)

from blmcp.tools_helpers import (
    toolcode_format_call,
    toolcode_load_from_filepath,
    toolcode_wrap_with_calling_convention,
)
from blmcp.tools_helpers.connection import send_code
from blmcp.tools.propose_highlights_toolcode import Params
from mcp.server.fastmcp import FastMCP  # pylint: disable=import-error,no-name-in-module
from mcp.types import ToolAnnotations  # pylint: disable=import-error,no-name-in-module

_TOOL_CALL = toolcode_wrap_with_calling_convention(toolcode_load_from_filepath(__file__))


def register(mcp: FastMCP) -> None:
    @mcp.tool(
        annotations=ToolAnnotations(
            title="Propose Highlights",
            readOnlyHint=False,
        )
    )
    def propose_highlights(
        picks: list[dict[str, object]],
        kind: str = "auto",
        max_seconds: float = 60.0,
        version: str = "",
        replace: bool = False,
    ) -> dict[str, object]:
        """
        Put pieces you chose yourself, from read_edit_sentences, into the Highlights list: the
        same checks as the search (the numbers exist, whole sentences, 15 s to the longest
        length, a little too long is cut back to a full stop, no two share frames) and the same
        list on the scene. A refused piece is listed in ``refused`` with the reason. Nothing is
        built: make_short builds one.

        Args:
            picks: ``[{first, last, title, hook, reason, scores}]``. ``first`` / ``last``: sentence
                numbers. ``title`` (60 characters), ``hook`` (the opening line, 110), ``reason``
                (one sentence). ``scores``: whole numbers 0 to 3 for ``hook``, ``alone``,
                ``complete``, ``one_idea``, ``energy``, ``tease``: do the first seconds pull,
                does it make sense alone, is it finished, is it one idea, is there energy, does it
                tease the full video. The value density is counted for you. Up to 20.
            kind: ``auto``, ``podcast``, ``tutorial`` or ``talk``: the weights of the total.
            max_seconds: The longest a short may be, 15 to 60.
            version: The ``version`` of the sentences you read: if the edit changed since, the call
                is refused and you read again.
            replace: Start a new list (default: add to the picks that are still in the edit).

        Returns:
            ``added``, ``refused`` ([{index, first, last, why, message}]), ``picks`` (the list, with
            ``id``) and ``version``.
        """
        p = Params(picks=tuple(dict(x) if isinstance(x, dict) else x for x in (picks or ())), kind=kind, max_seconds=float(max_seconds), version=version or "", replace=bool(replace))
        return send_code(toolcode_format_call(_TOOL_CALL, p), strict_json=True)
