# SPDX-FileCopyrightText: 2026 Blender Authors
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""
Generation Controller — orchestrates generative model loading,
inference, and output routing.

Manages the plugin registry, model download/cache, async job queue,
and routes generated media to the appropriate Blender workspace
(Sequencer, Image Editor, or Moodboard).

Follows the same patterns as ``llm_manager.py``:
- ``GenConfig`` / ``GenState`` dataclass split (persisted vs runtime)
- ``GenModelPreset`` curated list (like ``ModelPreset``)
- Thread-safe access via ``get_config()`` / ``set_config()``
- Background thread for generation, timer-based UI updates
"""

__all__ = (
    "GenConfig",
    "GenState",
    "GenModelPreset",
    "GenJob",
    "GEN_MODEL_PRESETS",
    "get_config",
    "set_config",
    "get_state",
    "get_presets",
    "get_preset_by_id",
    "discover_plugins",
    "generate",
    "generate_async",
    "cancel_job",
    "get_job_status",
    "get_output_dir",
    "cleanup",
)

import os
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# Lazy imports to avoid circular dependencies.
_GenPlugin = None
_PLUGIN_REGISTRY = None


def _lazy_import_gen_plugins():
    """Lazy import of gen_plugins to avoid circular imports."""
    global _GenPlugin, _PLUGIN_REGISTRY
    if _GenPlugin is None:
        from .gen_plugins.base import GenPlugin as _GP
        from .gen_plugins import PLUGIN_REGISTRY as _PR
        _GenPlugin = _GP
        _PLUGIN_REGISTRY = _PR


# ---------------------------------------------------------------------------
# Data types

@dataclass
class GenConfig:
    """Persisted configuration for the generation system."""

    # Directory where generated media is saved.
    output_dir: str = ""

    # Directory where downloaded models are stored.
    models_dir: str = ""

    # Backend selection.
    backend: str = "local"  # "local" | "pallaidium" | "comfyui" | "remote"

    # Remote backend settings.
    remote_url: str = ""
    remote_key: str = ""

    # ComfyUI settings.
    comfyui_url: str = "http://127.0.0.1:8188"

    # Auto-download models when first used.
    auto_download: bool = True

    # HuggingFace token for gated models.
    hf_token: str = ""


@dataclass
class GenState:
    """Runtime state of the generation system."""

    # Whether the generation system is ready (deps installed).
    is_ready: bool = False

    # Currently loaded model ID (empty if none).
    loaded_model_id: str = ""

    # Currently loaded pipeline object (cached).
    loaded_pipe: dict | None = None

    # Active job queue.
    jobs: list["GenJob"] = field(default_factory=list)

    # Currently running job index (-1 if idle).
    active_job_index: int = -1

    # Error message for the last failed operation.
    error: str = ""

    # Download progress (mirrors llm_manager pattern).
    download_progress: str = ""
    download_progress_pct: float = 0.0
    download_active: bool = False


@dataclass
class GenModelPreset:
    """Metadata for a curated generative model preset.

    Follows the same pattern as ``ModelPreset`` in ``llm_manager.py``.
    """

    identifier: str          # "flux-klein-9b"
    name: str                # "FLUX.2 Klein 9B"
    model_type: str          # "image"
    hf_repo_id: str          # "BFL-ML/FLUX.2-Klein-9B"
    vram_gb: int             # 12
    disk_gb: int             # 14
    capability: str          # "text-to-image, image-to-image, inpaint"
    category: str            # "flagship" | "mid_range" | "lightweight"
    description: str         # Longer tooltip text
    plugin_class: str = ""   # Dotted path to the plugin class (optional)


@dataclass
class GenJob:
    """A single generation job in the queue."""

    job_id: str
    model_id: str
    model_type: str
    inputs: dict[str, Any]   # Serialized GenInputs
    status: str = "PENDING"  # PENDING | RUNNING | COMPLETED | FAILED
    output_path: str = ""
    error: str = ""
    progress_pct: float = 0.0
    progress_text: str = ""
    created_at: float = 0.0
    started_at: float = 0.0
    completed_at: float = 0.0

    def __post_init__(self):
        if self.created_at == 0.0:
            self.created_at = time.monotonic()


# ---------------------------------------------------------------------------
# Module-level state (thread-safe)

_config = GenConfig()
_state = GenState()
_lock = threading.Lock()


def get_config() -> GenConfig:
    """Return a copy of the current config (thread-safe)."""
    with _lock:
        return GenConfig(
            output_dir=_config.output_dir,
            models_dir=_config.models_dir,
            backend=_config.backend,
            remote_url=_config.remote_url,
            remote_key=_config.remote_key,
            comfyui_url=_config.comfyui_url,
            auto_download=_config.auto_download,
            hf_token=_config.hf_token,
        )


def set_config(cfg: GenConfig) -> None:
    """Update the config (thread-safe)."""
    global _config
    with _lock:
        _config = cfg


def get_state() -> GenState:
    """Return a copy of the current state (thread-safe)."""
    with _lock:
        return GenState(
            is_ready=_state.is_ready,
            loaded_model_id=_state.loaded_model_id,
            loaded_pipe=_state.loaded_pipe,
            jobs=list(_state.jobs),
            active_job_index=_state.active_job_index,
            error=_state.error,
            download_progress=_state.download_progress,
            download_progress_pct=_state.download_progress_pct,
            download_active=_state.download_active,
        )


def _set_error(msg: str) -> None:
    """Set the error message (thread-safe)."""
    global _state
    with _lock:
        _state.error = msg


# ---------------------------------------------------------------------------
# Model Presets (curated list, like PRESET_MODELS in llm_manager.py)

GEN_MODEL_PRESETS: list[GenModelPreset] = [
    # ── Image Models ──
    GenModelPreset(
        identifier="flux-klein-9b",
        name="FLUX.2 Klein 9B",
        model_type="image",
        hf_repo_id="BFL-ML/FLUX.2-Klein-9B",
        vram_gb=12,
        disk_gb=14,
        capability="text-to-image, image-to-image, inpaint",
        category="flagship",
        description="Fast 4-step distilled FLUX model, excellent quality",
    ),
    GenModelPreset(
        identifier="flux-klein-4b",
        name="FLUX.2 Klein 4B",
        model_type="image",
        hf_repo_id="BFL-ML/FLUX.2-Klein-4B",
        vram_gb=8,
        disk_gb=8,
        capability="text-to-image, image-to-image",
        category="mid_range",
        description="Smaller FLUX variant, good quality at lower VRAM",
    ),
    GenModelPreset(
        identifier="sdxl-turbo",
        name="SDXL Turbo",
        model_type="image",
        hf_repo_id="stabilityai/sdxl-turbo",
        vram_gb=8,
        disk_gb=7,
        capability="text-to-image, image-to-image",
        category="mid_range",
        description="Single-step SDXL, very fast, good quality",
    ),
    GenModelPreset(
        identifier="sd-1-5",
        name="Stable Diffusion 1.5",
        model_type="image",
        hf_repo_id="runwayml/stable-diffusion-v1-5",
        vram_gb=4,
        disk_gb=4,
        capability="text-to-image, image-to-image",
        category="lightweight",
        description="Classic SD 1.5, runs on 4 GB VRAM",
    ),

    # ── Video Models ──
    GenModelPreset(
        identifier="ltx-23",
        name="LTX-2.3",
        model_type="video",
        hf_repo_id="OzzyGT/LTX-2.3-Distilled",
        vram_gb=12,
        disk_gb=16,
        capability="text-to-video, image-to-video, video-to-video, extend",
        category="flagship",
        description="Distilled 9B video model with multi-anchor support",
    ),
    GenModelPreset(
        identifier="wan-21-t2v",
        name="Wan 2.1 T2V",
        model_type="video",
        hf_repo_id="Wan-AI/Wan2.1-T2V",
        vram_gb=16,
        disk_gb=14,
        capability="text-to-video",
        category="flagship",
        description="High-quality text-to-video generation",
    ),

    # ── Audio Models ──
    GenModelPreset(
        identifier="chatterbox-tts",
        name="Chatterbox TTS",
        model_type="audio",
        hf_repo_id="resemble-ai/chatterbox",
        vram_gb=4,
        disk_gb=2,
        capability="text-to-speech, voice-cloning",
        category="lightweight",
        description="Multilingual TTS with zero-shot voice cloning",
    ),
    GenModelPreset(
        identifier="faster-whisper",
        name="Faster Whisper",
        model_type="audio",
        hf_repo_id="SYSTRAN/faster-whisper-large-v3",
        vram_gb=4,
        disk_gb=3,
        capability="speech-to-text, transcription",
        category="lightweight",
        description="Fast multilingual speech recognition with timestamps",
    ),
]


def get_presets() -> list[GenModelPreset]:
    """Return all model presets."""
    return list(GEN_MODEL_PRESETS)


def get_preset_by_id(identifier: str) -> GenModelPreset | None:
    """Return the preset with the given *identifier*, or ``None``."""
    for p in GEN_MODEL_PRESETS:
        if p.identifier == identifier:
            return p
    return None


# ---------------------------------------------------------------------------
# Plugin discovery

def discover_plugins() -> dict:
    """Ensure plugins are discovered and return the registry.

    Safe to call multiple times — discovery is idempotent.
    """
    _lazy_import_gen_plugins()
    from .gen_plugins import discover
    discover()
    return _PLUGIN_REGISTRY


# ---------------------------------------------------------------------------
# Output directory

def get_output_dir() -> str:
    """Return the output directory for generated media.

    Uses the configured ``output_dir``, falling back to a default
    in the user's home directory.
    """
    cfg = get_config()
    if cfg.output_dir and os.path.isdir(cfg.output_dir):
        return cfg.output_dir

    default = os.path.join(
        Path.home(),
        "bfa_coworker_generated",
    )
    os.makedirs(default, exist_ok=True)
    return default


# ---------------------------------------------------------------------------
# Synchronous generation (blocking — call from background thread)

def generate(
    model_id: str,
    inputs: dict[str, Any],
    prefs=None,
    scene=None,
) -> str:
    """Run generation synchronously and return the output file path.

    This is a **blocking** call — it should only be called from a
    background thread.  Use ``generate_async()`` for non-blocking
    generation with progress tracking.

    Returns the absolute path to the generated file.
    Raises ``RuntimeError`` on failure.
    """
    _lazy_import_gen_plugins()
    from .gen_plugins import get_plugin
    from .gen_plugins.base import GenInputs

    plugin = get_plugin(model_id)
    if plugin is None:
        raise RuntimeError(
            "Unknown model: {:s}".format(model_id)
        )

    # Check availability.
    if not plugin.is_available():
        raise RuntimeError(
            "Model {:s} requires packages: {:s}".format(
                model_id, ", ".join(plugin.required_packages)
            )
        )

    # Build GenInputs from dict.
    gen_inputs = GenInputs(**{
        k: v for k, v in inputs.items()
        if k in GenInputs.__dataclass_fields__
    })

    # Load pipeline (cached).
    global _state
    with _lock:
        if _state.loaded_model_id != model_id or _state.loaded_pipe is None:
            # Unload previous model.
            if _state.loaded_pipe is not None:
                prev_plugin = get_plugin(_state.loaded_model_id)
                if prev_plugin is not None:
                    try:
                        prev_plugin.unload(_state.loaded_pipe)
                    except Exception:
                        pass

            pipe_obj = plugin.load(prefs, scene)
            _state.loaded_model_id = model_id
            _state.loaded_pipe = pipe_obj
        else:
            pipe_obj = _state.loaded_pipe

    # Generate.
    try:
        output_path = plugin.generate(pipe_obj, gen_inputs, scene, prefs)
    except Exception as ex:
        _set_error(str(ex))
        raise RuntimeError(
            "Generation failed: {:s}".format(str(ex))
        ) from ex

    return output_path


# ---------------------------------------------------------------------------
# Async generation (non-blocking — returns job_id)

# Background thread for processing the job queue.
_worker_thread: threading.Thread | None = None
_worker_stop = threading.Event()


def generate_async(
    model_id: str,
    inputs: dict[str, Any],
    prefs=None,
    scene=None,
) -> str:
    """Queue a generation job and return its ``job_id``.

    The job runs on a background thread.  Use ``get_job_status()``
    to poll for completion, or watch ``GenState.jobs`` via a
    Blender timer for UI updates.
    """
    _lazy_import_gen_plugins()
    from .gen_plugins import get_plugin

    plugin = get_plugin(model_id)
    if plugin is None:
        raise RuntimeError(
            "Unknown model: {:s}".format(model_id)
        )

    job_id = str(uuid.uuid4())[:8]
    job = GenJob(
        job_id=job_id,
        model_id=model_id,
        model_type=plugin.MODEL_TYPE,
        inputs=inputs,
    )

    global _state
    with _lock:
        _state.jobs.append(job)

    # Start worker thread if not already running.
    _start_worker(prefs, scene)

    return job_id


def _start_worker(prefs=None, scene=None) -> None:
    """Start the background worker thread if not already running."""
    global _worker_thread, _worker_stop
    if _worker_thread is not None and _worker_thread.is_alive():
        return

    _worker_stop.clear()
    _worker_thread = threading.Thread(
        target=_worker_loop,
        args=(prefs, scene),
        daemon=True,
        name="bfacw-gen-worker",
    )
    _worker_thread.start()


def _worker_loop(prefs, scene) -> None:
    """Background thread that processes the job queue."""
    global _state, _worker_stop

    while not _worker_stop.is_set():
        # Find next PENDING job.
        with _lock:
            pending = [
                (i, j) for i, j in enumerate(_state.jobs)
                if j.status == "PENDING"
            ]
            if not pending:
                _state.active_job_index = -1
                break
            idx, job = pending[0]
            job.status = "RUNNING"
            job.started_at = time.monotonic()
            _state.active_job_index = idx

        try:
            output_path = generate(
                job.model_id,
                job.inputs,
                prefs=prefs,
                scene=scene,
            )
            with _lock:
                job.status = "COMPLETED"
                job.output_path = output_path
                job.completed_at = time.monotonic()
                job.progress_pct = 100.0
        except Exception as ex:
            with _lock:
                job.status = "FAILED"
                job.error = str(ex)
                job.completed_at = time.monotonic()

    _worker_thread = None


def cancel_job(job_id: str) -> bool:
    """Cancel a pending or running job.  Returns ``True`` on success."""
    global _state, _worker_stop
    with _lock:
        for job in _state.jobs:
            if job.job_id == job_id:
                if job.status in ("PENDING", "RUNNING"):
                    if job.status == "RUNNING":
                        # Signal worker to stop after current job.
                        _worker_stop.set()
                    job.status = "FAILED"
                    job.error = "Cancelled by user"
                    job.completed_at = time.monotonic()
                    return True
    return False


def get_job_status(job_id: str) -> dict[str, Any] | None:
    """Return the status dict for *job_id*, or ``None``."""
    state = get_state()
    for job in state.jobs:
        if job.job_id == job_id:
            return {
                "job_id": job.job_id,
                "model_id": job.model_id,
                "status": job.status,
                "output_path": job.output_path,
                "error": job.error,
                "progress_pct": job.progress_pct,
                "progress_text": job.progress_text,
            }
    return None


# ---------------------------------------------------------------------------
# Cleanup

def cleanup() -> None:
    """Stop the worker thread and unload any loaded pipeline."""
    global _worker_stop, _state
    _worker_stop.set()

    with _lock:
        if _state.loaded_pipe is not None:
            _lazy_import_gen_plugins()
            from .gen_plugins import get_plugin
            plugin = get_plugin(_state.loaded_model_id)
            if plugin is not None:
                try:
                    plugin.unload(_state.loaded_pipe)
                except Exception:
                    pass
            _state.loaded_pipe = None
            _state.loaded_model_id = ""