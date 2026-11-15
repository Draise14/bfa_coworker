# SPDX-FileCopyrightText: 2026 Blender Authors
# (Bforartists-maintained fork)
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""
The scene's compositor node tree on every build (#503).

Spliced into the tool-codes that read or set it (``# @include_begin``); the
server and the tests import it.  Blender 5.3+ (and Bforartists 5.2.x on its
5.3 base) keep the compositor as a list of effects, ``scene.compositor_effects``,
each with its own node group; ``scene.compositing_node_group`` is deprecated
there (removed in 6.0) and stands for the *active* effect's node group --
setting it makes a "Scene Effect" when the scene has none.  Older builds only
have ``compositing_node_group``.  These helpers do the same with whichever API
the build has, so a deprecated property is never touched where the new one
exists.  (No ``__all__``: spliced in, it would replace the tool-code's own;
``import *`` takes the two public functions.)
"""


def scene_compositor_tree(scene):
    """The node tree of *scene*'s compositor (its active effect's), or None.  Never creates anything."""
    effects = getattr(scene, "compositor_effects", None)
    if effects is None:
        return getattr(scene, "compositing_node_group", None)
    effect = effects.active
    return effect.node_group if effect is not None else None


def set_scene_compositor_tree(scene, tree):
    """Make *tree* *scene*'s compositor tree, as setting ``compositing_node_group`` does.

    The active effect gets *tree*; a scene without one gets a new "Scene Effect".
    """
    effects = getattr(scene, "compositor_effects", None)
    if effects is None:
        scene.compositing_node_group = tree
        return
    effect = effects.active
    if effect is None:
        effect = effects.new(name="Scene Effect")
    effect.node_group = tree
