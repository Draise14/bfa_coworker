# SPDX-FileCopyrightText: 2026 Blender Authors
# (Bforartists-maintained fork)
#
# SPDX-License-Identifier: GPL-3.0-or-later

# Included by tool-code (runs inside Blender): find the add-on's coworker_api.
def _coworker_api():
    import sys
    for name, module in list(sys.modules.items()):
        if name == "bfa_coworker.coworker_api" or name.endswith(".bfa_coworker.coworker_api"):
            return module
    raise RuntimeError("Coworker isn't running in this Blender (coworker_api not loaded)")
