# SPDX-FileCopyrightText: 2026 Blender Authors
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""
Base classes and data types for generative model plugins.

Plugins subclass ``GenPlugin`` and are placed in
``gen_plugins/<media_type>/`` for auto-discovery.  Adding a new model
means dropping a single ``.py`` file — no registration code needed.

The framework collects inputs from the UI (or MCP tools), calls
``load()`` once to prepare the pipeline, then calls ``generate()``
with a fully populated ``GenInputs`` dataclass.  Results are saved
to disk and routed to the appropriate Blender workspace (Sequencer,
Image Editor, or Moodboard).
"""

__all__ = (
    "GenInputSpec",
    "GenUISection",
    "GenParams",
    "GenInputs",
    "GenPlugin",
    "GenPluginError",
)

from dataclasses import dataclass, field
from enum import Enum, Flag, auto
from typing import Any


# ---------------------------------------------------------------------------
# Enums

class GenInputSpec(Flag):
    """Bitflag declaring what inputs a plugin requires.

    The UI and MCP tools use this to decide which controls to show
    and which data to collect before calling ``generate()``.
    """

    PROMPT = auto()           # Positive text prompt
    NEG_PROMPT = auto()       # Negative text prompt
    IMAGE = auto()            # Single reference image (img2img, inpaint)
    MULTI_IMAGE = auto()      # Multiple reference images
    VIDEO = auto()            # Reference video (vid2vid, control)
    AUDIO_REF = auto()        # Reference audio (voice cloning, music gen)
    AUDIO_REF_REQ = auto()    # Required reference audio
    TEXT_REF = auto()         # Reference text (transcript for TTS)
    FACE_FOLDER = auto()      # IP-Adapter face reference folder
    STYLE_FOLDER = auto()     # IP-Adapter style reference folder
    LORA = auto()             # LoRA weights
    API_KEY = auto()          # Remote API key required
    HF_TOKEN = auto()         # HuggingFace token for gated models


class GenUISection(Enum):
    """Ordered sections the UI panel renders for a plugin.

    The panel iterates ``plugin.UI_SECTIONS`` and calls the matching
    renderer.  Sections not listed are hidden — the user never sees
    controls they cannot use.
    """

    PROMPT = auto()
    NEG_PROMPT = auto()
    RESOLUTION = auto()
    FRAMES = auto()
    STEPS = auto()
    GUIDANCE = auto()
    STRENGTH = auto()
    SEED = auto()
    STYLE = auto()
    LORA = auto()
    ENHANCE = auto()          # Quality / Speed / Upscale toggles
    AUDIO_LENGTH = auto()
    LANGUAGE = auto()


# ---------------------------------------------------------------------------
# Data types

@dataclass
class GenParams:
    """Default generation parameters for a plugin.

    Plugins override these via a class-level ``DEFAULT_PARAMS``
    attribute.  The UI pre-fills controls from these defaults.
    """

    width: int = 1024
    height: int = 1024
    frames: int = 81            # For video models
    steps: int = 4
    guidance: float = 3.5
    strength: float = 0.8       # img2img denoising strength
    seed: int = -1              # -1 = random
    audio_length: float = 10.0  # Seconds for audio generation
    max_multi_images: int = 1   # Max reference images for multi-image models


@dataclass
class GenInputs:
    """Fully populated inputs passed to ``GenPlugin.generate()``.

    The framework fills this from UI controls or MCP tool arguments.
    Plugins read the fields they declared via ``INPUTS``.
    """

    prompt: str = ""
    negative_prompt: str = ""
    image_paths: list[str] = field(default_factory=list)
    video_path: str = ""
    audio_path: str = ""
    text_ref: str = ""
    width: int = 1024
    height: int = 1024
    frames: int = 81
    steps: int = 4
    guidance: float = 3.5
    strength: float = 0.8
    seed: int = -1
    audio_length: float = 10.0
    lora_paths: list[str] = field(default_factory=list)
    face_folder: str = ""
    style_folder: str = ""
    language: str = ""
    extra: dict[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Plugin base class

class GenPluginError(Exception):
    """Raised when a plugin encounters a recoverable error."""


class GenPlugin:
    """Base class for generative model plugins.

    Subclass and place in ``gen_plugins/<media_type>/`` for
    auto-discovery.  The framework calls ``load()`` once (result
    cached by ``MODEL_ID``), then ``generate()`` for each job.

    Required class attributes
    -------------------------
    ``MODEL_ID`` : ``str``
        Unique key, e.g. ``"bfl/flux-klein-9b"``.
    ``DISPLAY_NAME`` : ``str``
        Dropdown label, e.g. ``"Image: FLUX.2 Klein 9B"``.
    ``MODEL_TYPE`` : ``str``
        One of ``"image"``, ``"video"``, ``"audio"``, ``"text"``, ``"3d"``.
    ``DESCRIPTION`` : ``str``
        Tooltip text shown in the UI.

    Optional class attributes
    -------------------------
    ``INPUTS`` : ``GenInputSpec``
        Bitflag of required inputs (default: ``PROMPT``).
    ``UI_SECTIONS`` : ``list[GenUISection]``
        Ordered list of UI sections to render (default: prompt + seed).
    ``DEFAULT_PARAMS`` : ``GenParams``
        Default values for generation controls.
    """

    # ── Required Identity (override in subclass) ──

    MODEL_ID: str = ""
    DISPLAY_NAME: str = ""
    MODEL_TYPE: str = ""       # "image" | "video" | "audio" | "text" | "3d"
    DESCRIPTION: str = ""

    # ── Declarative Inputs ──

    INPUTS: GenInputSpec = GenInputSpec.PROMPT

    # ── Declarative UI ──

    UI_SECTIONS: list[GenUISection] = [
        GenUISection.PROMPT,
        GenUISection.SEED,
    ]

    # ── Default Parameters ──

    DEFAULT_PARAMS: GenParams = field(default_factory=GenParams)

    # ── Capability Flags ──

    supports_img2img: bool = False
    supports_inpaint: bool = False
    supports_batch: bool = True
    requires_input_strip: bool = False
    min_vram_gb: int = 6
    required_packages: list[str] = field(default_factory=list)

    # ── Lifecycle ──────────────────────────────────────────────────

    def is_available(self) -> bool:
        """Check whether required Python packages are installed.

        Returns ``True`` if the plugin can be used immediately.
        When ``False``, the UI shows an "Install Dependencies" prompt.
        """
        if not self.required_packages:
            return True
        for pkg in self.required_packages:
            try:
                __import__(pkg)
            except ImportError:
                return False
        return True

    def load(self, prefs, scene, **kwargs) -> dict:
        """Load the model pipeline.

        Called once per session; result is cached by ``MODEL_ID``.
        The returned dict must contain at least a ``"pipe"`` key.
        Plugins may add extra keys for their own use.

        *kwargs* includes ``"mode"`` (``"txt2img"``, ``"img2img"``,
        ``"inpaint"``, etc.) so plugins can load different pipelines
        per mode.
        """
        raise NotImplementedError(
            "Plugin {:s} must implement load()".format(self.MODEL_ID)
        )

    def generate(
        self,
        pipe_obj: dict,
        inputs: GenInputs,
        scene,
        prefs,
    ) -> str:
        """Run generation and return the absolute path to the output file.

        *pipe_obj* is the dict returned by ``load()``.
        *inputs* is a fully populated ``GenInputs``.
        *scene* is the current ``bpy.types.Scene``.
        *prefs* is the add-on preferences.

        Image plugins should save a ``.png`` and return its path.
        Video plugins should save an ``.mp4`` and return its path.
        Audio plugins should save a ``.wav`` or ``.mp3`` and return its path.
        Text plugins should save a ``.txt`` and return its path.
        """
        raise NotImplementedError(
            "Plugin {:s} must implement generate()".format(self.MODEL_ID)
        )

    def unload(self, pipe_obj: dict) -> None:
        """Release GPU memory held by the pipeline.

        Called when switching models or shutting down.  The default
        implementation does nothing — override if your pipeline holds
        GPU resources that need explicit cleanup.
        """
        _ = pipe_obj

    # ── Optional UI Overrides ──────────────────────────────────────

    def draw_custom_ui(self, col, context) -> bool:
        """Draw custom UI controls for this plugin.

        *col* is a ``UILayout`` column.  Return ``True`` if you
        handled the entire input section (the framework will skip
        its standard input controls).  Return ``False`` to append
        your controls after the standard ones.
        """
        _ = col, context
        return False

    def draw_post_enhance_ui(self, col, context) -> None:
        """Draw additional controls after the Enhance row."""
        _ = col, context

    def draw_post_seed_ui(self, col, context) -> None:
        """Draw additional controls after the Seed row."""
        _ = col, context