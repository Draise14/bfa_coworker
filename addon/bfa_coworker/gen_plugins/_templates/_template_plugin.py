# SPDX-FileCopyrightText: 2026 Blender Authors
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""
Template for creating new generative model plugins.

Copy this file to ``gen_plugins/<media_type>/`` and fill in the
required attributes.  The plugin will be auto-discovered on the
next panel refresh — no registration code needed.

Required
--------
- ``MODEL_ID`` — unique key, e.g. ``"author/model-name"``
- ``DISPLAY_NAME`` — dropdown label, e.g. ``"Image: My Model"``
- ``MODEL_TYPE`` — ``"image"``, ``"video"``, ``"audio"``, ``"text"``, or ``"3d"``
- ``DESCRIPTION`` — tooltip text
- ``load()`` — prepare the pipeline
- ``generate()`` — run inference

Optional
--------
- ``INPUTS`` — bitflag of required inputs (default: ``PROMPT``)
- ``UI_SECTIONS`` — ordered list of UI sections to render
- ``DEFAULT_PARAMS`` — default values for generation controls
- ``required_packages`` — list of pip packages needed
- ``min_vram_gb`` — minimum VRAM in GB
"""

import os
import time

from ..base import (
    GenInputs,
    GenInputSpec,
    GenParams,
    GenPlugin,
    GenUISection,
)


class TemplatePlugin(GenPlugin):
    """Template plugin — copy and customize."""

    # ── Required Identity ──
    MODEL_ID = "author/template-model"
    DISPLAY_NAME = "Image: Template Model"
    MODEL_TYPE = "image"
    DESCRIPTION = "A template for creating new generative plugins"

    # ── Declarative Inputs ──
    INPUTS = (
        GenInputSpec.PROMPT
        | GenInputSpec.NEG_PROMPT
        | GenInputSpec.IMAGE
    )

    # ── Declarative UI ──
    UI_SECTIONS = [
        GenUISection.PROMPT,
        GenUISection.NEG_PROMPT,
        GenUISection.RESOLUTION,
        GenUISection.STEPS,
        GenUISection.GUIDANCE,
        GenUISection.SEED,
    ]

    # ── Default Parameters ──
    DEFAULT_PARAMS = GenParams(
        width=1024,
        height=1024,
        steps=4,
        guidance=3.5,
    )

    # ── Capabilities ──
    supports_img2img = True
    min_vram_gb = 8
    required_packages = ["diffusers", "torch", "transformers"]

    # ── Lifecycle ──

    def load(self, prefs, scene, **kwargs):
        """Load the model pipeline.

        Called once per session; result cached by MODEL_ID.
        """
        import torch
        from diffusers import DiffusionPipeline

        mode = kwargs.get("mode", "txt2img")

        pipe = DiffusionPipeline.from_pretrained(
            "runwayml/stable-diffusion-v1-5",
            torch_dtype=torch.float16,
        )
        pipe.to("cuda" if torch.cuda.is_available() else "cpu")

        return {"pipe": pipe, "mode": mode}

    def generate(self, pipe_obj, inputs, scene, prefs):
        """Run generation and return the output file path."""
        import torch
        from PIL import Image

        pipe = pipe_obj["pipe"]

        # Build kwargs for the pipeline.
        kwargs = {
            "prompt": inputs.prompt,
            "num_inference_steps": inputs.steps,
            "guidance_scale": inputs.guidance,
        }

        if inputs.negative_prompt:
            kwargs["negative_prompt"] = inputs.negative_prompt

        if inputs.seed >= 0:
            kwargs["generator"] = torch.Generator(
                "cuda" if torch.cuda.is_available() else "cpu"
            ).manual_seed(inputs.seed)

        # Image-to-image mode.
        if inputs.image_paths:
            from PIL import Image as PILImage
            init_image = PILImage.open(inputs.image_paths[0]).convert("RGB")
            init_image = init_image.resize((inputs.width, inputs.height))
            kwargs["image"] = init_image
            kwargs["strength"] = inputs.strength

        # Run inference.
        with torch.inference_mode():
            result = pipe(**kwargs).images[0]

        # Save to output directory.
        from ..gen_controller import get_output_dir
        output_dir = get_output_dir()
        filename = "template_{:d}.png".format(int(time.time() * 1000))
        output_path = os.path.join(output_dir, filename)
        result.save(output_path)

        return output_path