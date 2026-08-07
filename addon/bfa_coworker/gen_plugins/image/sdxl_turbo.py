# SPDX-FileCopyrightText: 2026 Blender Authors
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""
SDXL Turbo — single-step image generation.

Adversarial diffusion distillation enables high-quality images
in a single inference step.  Runs on 8 GB VRAM.
"""

import os
import time

from ..base import (
    GenInputs,
    GenInputSpec,
    GenParams,
    GenPlugin,
    GenPluginError,
    GenUISection,
)


class SDXLTurboPlugin(GenPlugin):
    """SDXL Turbo — single-step adversarial diffusion distillation."""

    MODEL_ID = "stability/sdxl-turbo"
    DISPLAY_NAME = "Image: SDXL Turbo"
    MODEL_TYPE = "image"
    DESCRIPTION = (
        "Single-step SDXL Turbo from Stability AI. "
        "Very fast (1-4 steps), good quality. "
        "Runs on 8 GB VRAM."
    )

    INPUTS = (
        GenInputSpec.PROMPT
        | GenInputSpec.NEG_PROMPT
        | GenInputSpec.IMAGE
    )

    UI_SECTIONS = [
        GenUISection.PROMPT,
        GenUISection.NEG_PROMPT,
        GenUISection.RESOLUTION,
        GenUISection.STEPS,
        GenUISection.GUIDANCE,
        GenUISection.STRENGTH,
        GenUISection.SEED,
    ]

    DEFAULT_PARAMS = GenParams(
        width=512,
        height=512,
        steps=1,
        guidance=0.0,   # SDXL Turbo uses 0.0 guidance
        strength=0.3,   # Lower strength for img2img
    )

    supports_img2img = True
    min_vram_gb = 8
    required_packages = ["diffusers", "torch", "transformers", "PIL"]

    # ── Lifecycle ──────────────────────────────────────────────────

    def load(self, prefs, scene, **kwargs):
        """Load the SDXL Turbo pipeline."""
        import torch
        from diffusers import AutoPipelineForText2Image

        mode = kwargs.get("mode", "txt2img")
        repo_id = "stabilityai/sdxl-turbo"

        print(
            "[🛠️Coworker] SDXLTurbo: loading {:s} (mode={:s})".format(
                repo_id, mode
            )
        )

        try:
            pipe = AutoPipelineForText2Image.from_pretrained(
                repo_id,
                torch_dtype=torch.float16,
                variant="fp16",
            )
        except Exception as ex:
            raise GenPluginError(
                "Failed to load SDXL Turbo: {:s}".format(str(ex))
            ) from ex

        if torch.cuda.is_available():
            pipe.to("cuda")
        else:
            pipe.enable_model_cpu_offload()

        return {"pipe": pipe, "mode": mode}

    def generate(self, pipe_obj, inputs, scene, prefs):
        """Run SDXL Turbo inference."""
        import torch
        from PIL import Image as PILImage

        pipe = pipe_obj["pipe"]

        kwargs = {
            "prompt": inputs.prompt,
            "num_inference_steps": max(1, inputs.steps),
            "guidance_scale": inputs.guidance,
            "width": inputs.width,
            "height": inputs.height,
        }

        if inputs.negative_prompt:
            kwargs["negative_prompt"] = inputs.negative_prompt

        if inputs.seed >= 0:
            kwargs["generator"] = torch.Generator(
                "cuda" if torch.cuda.is_available() else "cpu"
            ).manual_seed(inputs.seed)

        # Image-to-image.
        if inputs.image_paths:
            init_image = PILImage.open(inputs.image_paths[0]).convert("RGB")
            init_image = init_image.resize((inputs.width, inputs.height))
            kwargs["image"] = init_image
            kwargs["strength"] = inputs.strength

        print(
            "[🛠️Coworker] SDXLTurbo: generating {:d}x{:d} image...".format(
                inputs.width, inputs.height
            )
        )
        with torch.inference_mode():
            result = pipe(**kwargs).images[0]

        # Save.
        from ..gen_controller import get_output_dir
        output_dir = get_output_dir()
        filename = "sdxl_turbo_{:d}.png".format(
            int(time.time() * 1000)
        )
        output_path = os.path.join(output_dir, filename)
        result.save(output_path)

        print(
            "[🛠️Coworker] SDXLTurbo: saved to {:s}".format(output_path)
        )
        return output_path

    def unload(self, pipe_obj):
        """Free GPU memory."""
        pipe = pipe_obj.get("pipe")
        if pipe is not None and hasattr(pipe, "to"):
            try:
                pipe.to("cpu")
            except Exception:
                pass
        import gc
        gc.collect()
        if hasattr(__import__("torch"), "cuda"):
            __import__("torch").cuda.empty_cache()