# SPDX-FileCopyrightText: 2026 Blender Authors
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""
Build script for the Coworker extension.

Usage:
    python build_addon.py                  # Build the addon
    python build_addon.py --install        # Build and install
    python build_addon.py --install --enable  # Build, install, and enable

Set the environment like this in Powershell:
    $env:BLENDER_BIN="D:\Software\Blender\stable\blender-5.2.0-lts.fbe6228777e7\blender.exe"
    python build_addon.py

This will persist, or use this command with the path to the Blender executable:
    python build_addon.py --blender "D:\Software\Blender\stable\blender-5.2.0-lts.fbe6228777e7\blender.exe"

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
# The addon will have vendor/deps/ (pip-installed pure-Python deps)
# and vendor/blmcp/ (blmcp source package) instead of a bundled .venv.
# (The old vendor/python_env/ layout was not portable across machines
# because uv-created venvs hardcode the base Python path in pyvenv.cfg.)
VENDOR_DIR = os.path.join(ADDON_DIR, "vendor")
VENDOR_DEPS_DIR = os.path.join(VENDOR_DIR, "deps")
VENDOR_BLMCP_DIR = os.path.join(VENDOR_DIR, "blmcp")
# Old layout — kept for cleanup.
VENDOR_VENV_DIR = os.path.join(VENDOR_DIR, "python_env")
DIST_DIR = os.path.join(SCRIPT_DIR, "releases")

# Find Blender executable.
def find_blender() -> str:
    """Find the Blender binary."""
    blender = os.environ.get("BLENDER_BIN") or shutil.which("blender") or "blender"
    return blender


def _find_python_with_pip() -> str:
    """Find a Python executable that has ``pip`` installed.

    Tries candidates in order:
    0. Blender's bundled Python (discovered from BLENDER_BIN/bin/python or BLENDER_PYTHON env).
    1. ``sys.executable`` (current Python).
    2. ``python`` / ``python3`` from PATH (if different from current).
    3. ``uv`` (can act without pip).

    Exits with an error if nothing is found.
    """

    candidates: list[str] = []

    # 0. Blender's bundled Python – ALWAYS try first so C extensions match.
    blender_py = os.environ.get("BLENDER_PYTHON")
    if not blender_py:
        blender_bin = os.environ.get("BLENDER_BIN")
        if blender_bin:
            blender_dir = os.path.dirname(blender_bin)
            # Blender layout varies: blender.exe lives at e.g.
            # {install}/blender.exe with Python at {install}/{version}/python/bin/python.exe
            # or {install}/python/bin/python.exe.
            candidates_py: list[str] = [
                os.path.join(blender_dir, "python", "bin", "python.exe"),
            ]
            # Also check one level down (versioned folder like 5.2/python/...).
            if os.path.isdir(blender_dir):
                for entry in os.listdir(blender_dir):
                    candidate = os.path.join(blender_dir, entry, "python", "bin", "python.exe")
                    if os.path.isfile(candidate):
                        candidates_py.append(candidate)
            for c in candidates_py:
                if os.path.isfile(c):
                    blender_py = c
                    break
    if blender_py and os.path.isfile(blender_py):
        candidates.append(blender_py)

    candidates.append(sys.executable)

    # Add system python from PATH if different.
    for name in ("python", "python3"):
        py = shutil.which(name)
        if py and py not in candidates:
            candidates.append(py)

    for py in candidates:
        try:
            result = subprocess.run(
                [py, "-m", "pip", "--version"],
                capture_output=True, text=True, timeout=10,
            )
            if result.returncode == 0:
                print("  Using Python with pip: {:s}".format(py))
                return py
        except (FileNotFoundError, OSError, subprocess.TimeoutExpired):
            continue

    # Last resort: uv (can act as a pip replacement).
    uv_path = shutil.which("uv")
    if uv_path:
        print("  Using uv as pip replacement: {:s}".format(uv_path))
        return "uv"

    print("ERROR: cannot find pip. Install pip or run: uv pip install --target deps/ mcp[cli] pyyaml docutils")
    sys.exit(2)


def _bundle_deps_and_source() -> None:
    """Install MCP dependencies and copy blmcp source into the addon's vendor directory.

    This replaces the old approach of copying the uv-managed .venv (which was
    not portable because pyvenv.cfg hardcodes a machine-specific Python path).

    New layout::

        vendor/
        ├── deps/          # pip-installed pure-Python packages (mcp, pyyaml, docutils)
        └── blmcp/         # blmcp source package (copied from mcp/blmcp/)

    Uses sys.executable for pip install.  If the resulting compiled
    extensions don't match Blender's Python version, the addon's
    _ensure_vendor_deps() will auto-reinstall them at runtime.
    """
    print("=" * 60)
    print("Bundling MCP dependencies and source into extension...")

    # Clean old vendor/python_env/ if it exists.
    if os.path.isdir(VENDOR_VENV_DIR):
        print("  Removing old vendor/python_env/ (non-portable layout)...")
        shutil.rmtree(VENDOR_VENV_DIR)

    # Clean any previous vendor/deps/ and vendor/blmcp/.
    if os.path.isdir(VENDOR_DEPS_DIR):
        shutil.rmtree(VENDOR_DEPS_DIR)
    if os.path.isdir(VENDOR_BLMCP_DIR):
        shutil.rmtree(VENDOR_BLMCP_DIR)

    # Find a Python with pip.
    pip_python = _find_python_with_pip()

    # Step 1: Install dependencies into vendor/deps/.
    os.makedirs(VENDOR_DEPS_DIR, exist_ok=True)
    print("  Installing dependencies to {:s}...".format(VENDOR_DEPS_DIR))

    if pip_python == "uv":
        pip_cmd = [
            "uv", "pip", "install",
            "--target", VENDOR_DEPS_DIR,
            # Pin below 2.0: FastMCP was removed from mcp 2.0.0, but blmcp
            # imports ``from mcp.server.fastmcp import FastMCP``.
            "mcp[cli]>=1.2.0,<2.0.0",
            "pyyaml",
            "docutils",
        ]
        if sys.platform == "win32":
            pip_cmd.append("pywin32")
    else:
        pip_cmd = [
            pip_python, "-m", "pip", "install",
            "--target", VENDOR_DEPS_DIR,
            "--no-compile",  # Skip .pyc to save space.
            "mcp[cli]>=1.2.0,<2.0.0",
            "pyyaml",
            "docutils",
        ]
        if sys.platform == "win32":
            pip_cmd.append("pywin32")

    result = subprocess.run(pip_cmd, capture_output=True, text=True)
    if result.returncode != 0:
        print("ERROR: pip install failed with exit code {:d}".format(result.returncode))
        print("stdout:", result.stdout[-500:])
        print("stderr:", result.stderr[-500:])
        sys.exit(1)
    print("  Dependencies installed successfully.")

    # Remove __pycache__ from deps to save space.
    for root, dirs, _files in os.walk(VENDOR_DEPS_DIR):
        if '__pycache__' in dirs:
            shutil.rmtree(os.path.join(root, '__pycache__'))

    # Step 2: Copy blmcp source into vendor/blmcp/.
    blmcp_src = os.path.join(SCRIPT_DIR, "mcp", "blmcp")
    if not os.path.isdir(blmcp_src):
        print("ERROR: {:s} not found".format(blmcp_src))
        sys.exit(1)
    shutil.copytree(
        blmcp_src, VENDOR_BLMCP_DIR,
        ignore=shutil.ignore_patterns("__pycache__"),
    )
    print("  Copied blmcp package to {:s}".format(VENDOR_BLMCP_DIR))

    print("Bundled successfully!")


def main() -> int:
    parser = argparse.ArgumentParser(description="Build the Coworker extension.")
    parser.add_argument("--install", action="store_true", help="Install after build")
    parser.add_argument("--enable", action="store_true", help="Enable after install")
    parser.add_argument("--blender", default=find_blender(), help="Blender executable path")
    parser.add_argument("--output-dir", default=DIST_DIR, help="Output directory for the .zip")
    args = parser.parse_args()

    os.makedirs(args.output_dir, exist_ok=True)

    # Step 0: Bundle MCP deps and source into vendor/.
    _bundle_deps_and_source()

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

    # Find the newest built zip (by modification time).
    zips = [os.path.join(args.output_dir, f) for f in os.listdir(args.output_dir) if f.endswith(".zip")]
    if not zips:
        print("ERROR: No .zip file found in {:s}".format(args.output_dir))
        return 1

    zip_path = max(zips, key=os.path.getmtime)
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