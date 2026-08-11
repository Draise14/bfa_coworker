# SPDX-FileCopyrightText: 2026 Blender Authors
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""
FLUX.2 Klein 9B — fast 4-step distilled image generation.

Supports text-to-image, image-to-image, and inpainting.
Runs on 12+ GB VRAM with fp16, or 8 GB with cpu_offload.
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


class FluxKlein9BPlugin(GenPlugin):
    """FLUX.2 Klein 9B — distilled 4-step image generation."""

    MODEL_ID = "bfl/flux-klein-9b"
    DISPLAY_NAME = "Image: FLUX.2 Klein 9B"
    MODEL_TYPE = "image"
    DESCRIPTION = (
        "Fast 4-step distilled FLUX model from Black Forest Labs. "
        "Excellent quality for text-to-image, image-to-image, and inpainting. "
        "Requires 12 GB VRAM (or 8 GB with CPU offload)."
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
        width=1024,
        height=1024,
        steps=4,
        guidance=3.5,
        strength=0.8,
    )

    supports_img2img = True
    supports_inpaint = True
    min_vram_gb = 12
    required_packages = ["diffusers", "torch", "transformers", "PIL"]

    # ── Lifecycle ──────────────────────────────────────────────────

    def load(self, prefs, scene, **kwargs):
        """Load the FLUX.2 Klein 9B pipeline."""
        import torch
        from diffusers import FluxPipeline

        mode = kwargs.get("mode", "txt2img")
        repo_id = "BFL-ML/FLUX.2-Klein-9B"

        print(
            "[🛠️Coworker] FluxKlein9B: loading {:s} (mode={:s})".format(
                repo_id, mode
            )
        )

        try:
            pipe = FluxPipeline.from_pretrained(
                repo_id,
                torch_dtype=torch.bfloat16,
            )
        except Exception as ex:
            raise GenPluginError(
                "Failed to load FLUX.2 Klein 9B: {:s}\n\n"
                "Make sure you have accepted the license at:\n"
                "  https://huggingface.co/{:s}".format(
                    str(ex), repo_id
                )
            ) from ex

        # Move to GPU if available, otherwise CPU offload.
        if torch.cuda.is_available():
            pipe.to("cuda")
        else:
            pipe.enable_model_cpu_offload()

        return {"pipe": pipe, "mode": mode}

    def generate(self, pipe_obj, inputs, scene, prefs):
        """Run FLUX.2 Klein 9B inference."""
        import torch
        from PIL import Image as PILImage

        pipe = pipe_obj["pipe"]

        # Build kwargs.
        kwargs = {
            "prompt": inputs.prompt,
            "num_inference_steps": inputs.steps,
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

        # Image-to-image / inpaint mode.
        if inputs.image_paths:
            init_image = PILImage.open(inputs.image_paths[0]).convert("RGB")
            init_image = init_image.resize((inputs.width, inputs.height))
            kwargs["image"] = init_image
            kwargs["strength"] = inputs.strength

        # Run inference.
        print(
            "[🛠️Coworker] FluxKlein9B: generating {:d}x{:d} image...".format(
                inputs.width, inputs.height
            )
        )
        with torch.inference_mode():
            result = pipe(**kwargs).images[0]

        # Save.
        from ..gen_controller import get_output_dir
        output_dir = get_output_dir()
        filename = "flux_klein_9b_{:d}.png".format(
            int(time.time() * 1000)
        )
        output_path = os.path.join(output_dir, filename)
        result.save(output_path)

        print(
            "[🛠️Coworker] FluxKlein9B: saved to {:s}".format(output_path)
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