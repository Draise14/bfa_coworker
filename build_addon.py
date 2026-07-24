# SPDX-FileCopyrightText: 2026 Blender Authors
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""
Build script for the Coworker extension.

Usage:
    python build_addon.py                  # Build the addon
    python build_addon.py --install        # Build and install
    python build_addon.py --install --enable  # Build, install, and enable
"""

import argparse
import os
import shutil
import subprocess
import sys

# Paths relative to this script.
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
ADDON_DIR = os.path.join(SCRIPT_DIR, "addon", "bfa_coworker")
MCP_SRC_DIR = os.path.join(SCRIPT_DIR, "mcp")
MCP_VENV_DIR = os.path.join(MCP_SRC_DIR, ".venv")
# The addon will have a vendor/.venv/ subdirectory with blmcp + dependencies.
VENDOR_VENV_DIR = os.path.join(ADDON_DIR, "vendor", ".venv")
DIST_DIR = os.path.join(SCRIPT_DIR, "releases")

# Find Blender executable.
def find_blender() -> str:
    """Find the Blender binary."""
    blender = os.environ.get("BLENDER_BIN") or shutil.which("blender") or "blender"
    return blender


def _bundle_venv() -> None:
    """Copy the MCP .venv into vendor/.venv inside the addon."""
    print("=" * 60)
    print("Bundling MCP virtual environment into extension...")
    print("  Source: {:s}".format(MCP_VENV_DIR))
    print("  Dest:   {:s}".format(VENDOR_VENV_DIR))

    if not os.path.isdir(MCP_VENV_DIR):
        print("ERROR: {:s} not found — run 'cd mcp && uv sync' first".format(MCP_VENV_DIR))
        sys.exit(1)

    if os.path.isdir(VENDOR_VENV_DIR):
        shutil.rmtree(VENDOR_VENV_DIR)

    shutil.copytree(MCP_VENV_DIR, VENDOR_VENV_DIR)

    # Remove __pycache__ to save space.
    for root, dirs, _files in os.walk(VENDOR_VENV_DIR):
        if '__pycache__' in dirs:
            shutil.rmtree(os.path.join(root, '__pycache__'))

    print("Bundled successfully!")


def main() -> int:
    parser = argparse.ArgumentParser(description="Build the Coworker extension.")
    parser.add_argument("--install", action="store_true", help="Install after build")
    parser.add_argument("--enable", action="store_true", help="Enable after install")
    parser.add_argument("--blender", default=find_blender(), help="Blender executable path")
    parser.add_argument("--output-dir", default=DIST_DIR, help="Output directory for the .zip")
    args = parser.parse_args()

    os.makedirs(args.output_dir, exist_ok=True)

    # Step 0: Bundle the MCP .venv into vendor/.venv.
    _bundle_venv()

    # Step 1: Build.
    print("\n" + "=" * 60)
    print("Building extension from: {:s}".format(ADDON_DIR))
    print("Output to: {:s}".format(args.output_dir))
    print("=" * 60)

    build_cmd = [
        args.blender,
        "--command", "extension", "build",
        "--source-dir", ADDON_DIR,
        "--output-dir", args.output_dir,
    ]
    result = subprocess.run(build_cmd)
    if result.returncode != 0:
        print("ERROR: Build failed with exit code {:d}".format(result.returncode))
        return result.returncode

    # Find the built zip.
    zips = [f for f in os.listdir(args.output_dir) if f.endswith(".zip")]
    if not zips:
        print("ERROR: No .zip file found in {:s}".format(args.output_dir))
        return 1

    zip_path = os.path.join(args.output_dir, zips[0])
    print("Built: {:s}".format(zip_path))

    # Step 2: Install (optional).
    if args.install:
        print("\n" + "=" * 60)
        print("Installing extension...")
        print("=" * 60)
        install_cmd = [
            args.blender,
            "--background", "--factory-startup", "--online-mode",
            "--command", "extension", "install-file", zip_path,
            "--repo", "user_default",
        ]
        if args.enable:
            install_cmd.append("--enable")
        result = subprocess.run(install_cmd)
        if result.returncode != 0:
            print("ERROR: Install failed with exit code {:d}".format(result.returncode))
            return result.returncode
        print("Installed successfully!")
        if args.enable:
            print("Extension enabled!")

    print("\nDone!")
    return 0


if __name__ == "__main__":
    sys.exit(main())