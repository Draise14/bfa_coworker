# SPDX-FileCopyrightText: 2026 Blender Authors
# (Bforartists-maintained fork)
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""
Checks that the MCP server exposes the expected tool listing.
"""

__all__ = ()

import asyncio
import os
import sys
import unittest

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

# Root of the repository.
_REPO_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Complete expected tool listing.
# When a tool is added, changed, or removed this must be updated.
# Run with `--update` to regenerate from a live server query.

# BEGIN: EXPECTED_TOOLS
EXPECTED_TOOLS = [
    {
        "name": "add_captions",
        "description": "\n"
"        Caption sound or movie strips in the Video Sequencer: their speech becomes styled\n"
"        text strips (one per caption line, in sync) on a channel named \"Captions\".\n"
"\n"
"        Every given strip gets the captions of the part of its media it shows (each piece of a\n"
"        cut clip, a clip used twice).  A movie strip is heard through the sound strip of the same\n"
"        file over its time.  Muted and retimed strips, and strips with no speech or no\n"
"        transcript, are skipped and named in ``strips_skipped``.  Running it again replaces the\n"
"        captions over those strips, but keeps captions edited by hand (``kept_edited``) unless\n"
"        ``replace_edited``; it is one undo step.  Animation, position and the style choices (case, font, bold, italic, size, colours) work with every style. Word highlights follow the caption's font, bold, italic, size and position by themselves.\n"
"\n"
"        Args:\n"
"            strips: Names of the sound / movie strips to caption (none: the selected ones).\n"
"            style: CLEAN, BOLD_POP, WORD_HIGHLIGHT (the spoken word lights up) or BOXED.\n"
"            language: Spoken language code (\"en\", \"es\"...) or \"auto\".\n"
"            transcript_path: A transcript JSON file (bfa_coworker.transcript/1) to use\n"
"                instead of transcribing.\n"
"            replace_edited: Also replace captions changed by hand since they were made.\n"
"            animation: The single animation list of before, still accepted: the line animation\n"
"                (STYLE, NONE, FADE, SLIDE_UP, SLIDE_DOWN, TYPEWRITER or POP).  Prefer ``animate``,\n"
"                ``line_animation`` and ``word_animation``.\n"
"            position: BOTTOM, TOP (e.g. off the UI a tutorial shows at the bottom) or MIDDLE.\n"
"            margin: Distance from that edge in percent of the frame's height, 0-45 (0: the\n"
"                safe area of a wide or vertical frame).\n"
"            speakers: One caption channel per sound file (one file per speaker), named after\n"
"                the speaker; their captions may overlap in time.\n"
"            speaker_labels: Each caption starts with its speaker's name (\"Ana: ...\").\n"
"            speaker_settings: Per speaker ``{\"strip\": a strip of theirs, \"name\", \"style\",\n"
"                \"position\"}`` (each but \"strip\" optional); turns ``speakers`` on.\n"
"            case: Letter case of the caption text: STYLE (the style's own: capitals for BOLD_POP,\n"
"                as spoken for the others), AS_SPOKEN, UPPER, LOWER or TITLE.\n"
"            font: A font already in this file (its name) or the path of a font file; none: the\n"
"                style's own (Blender's bundled Inter).\n"
"            bold: True / False; none: as the style has it (bold only for BOLD_POP).\n"
"            italic: True / False; none: as the style has it (upright).\n"
"            size: A scale of the style's own text size, 0.25-4 (1: as the style has it).\n"
"            text_color: The text colour: \"#RRGGBB\" as seen on screen, or 3-4 numbers 0-1 (Blender's\n"
"                own values); none: the style's.\n"
"            highlight_color: The same for the spoken word of WORD_HIGHLIGHT; none: the style's.\n"
"            animate: What moves: LINE (each caption as a whole), WORD (each word at its own time),\n"
"                BOTH, NONE, or STYLE (the style's own: BOLD_POP pops in, WORD_HIGHLIGHT lights the\n"
"                spoken word, the others stand still).  None: STYLE, or what ``line_animation`` /\n"
"                ``word_animation`` imply.\n"
"            line_animation: How a caption moves as a whole: NONE, FADE, POP, SLIDE_UP, SLIDE_DOWN or\n"
"                TYPEWRITER (typed out; with word motions word by word) (STYLE: the style's own).\n"
"            word_animation: What each word does at its own time: HIGHLIGHT (the spoken word lights\n"
"                up in the highlight colour; works over the visible line, on every style), POP,\n"
"                SCALE, RISE (a small move up), FADE_IN, or HIGHLIGHT_POP / HIGHLIGHT_SCALE /\n"
"                HIGHLIGHT_RISE / HIGHLIGHT_FADE_IN, NONE.  With a motion every word is a strip of its\n"
"                own (one channel each, above the caption's) and the caption's own strip stays as the\n"
"                anchor holding the text but draws nothing; too many words per line (over 24), or too\n"
"                many keyframes, is refused with the limit named (use HIGHLIGHT or fewer words per line).\n"
"\n"
"        Returns:\n"
"            ``lines_created``, ``channel``, ``first_frame`` / ``last_frame``, the style,\n"
"            ``strips_captioned`` (names), ``strips_skipped`` (``{strip, reason}``: \"no speech\",\n"
"            \"silent\", \"retimed\", \"no transcript\" or \"muted\") and ``kept_edited``; with speakers\n"
"            ``speakers`` (``{name, channel, lines_created, style, position}``); ``message`` says it all\n"
"            in a sentence.  ``status`` \"error\" with a ``message`` saying what's wrong.\n"
"        ",
        "inputSchema": {
            "properties": {
                "strips": {
                    "anyOf": [
                        {
                            "items": {
                                "type": "string"
                            },
                            "type": "array"
                        },
                        {
                            "type": "null"
                        }
                    ],
                    "default": None,
                    "title": "Strips"
                },
                "style": {
                    "default": "CLEAN",
                    "title": "Style",
                    "type": "string"
                },
                "language": {
                    "anyOf": [
                        {
                            "type": "string"
                        },
                        {
                            "type": "null"
                        }
                    ],
                    "default": None,
                    "title": "Language"
                },
                "transcript_path": {
                    "anyOf": [
                        {
                            "type": "string"
                        },
                        {
                            "type": "null"
                        }
                    ],
                    "default": None,
                    "title": "Transcript Path"
                },
                "replace_edited": {
                    "default": False,
                    "title": "Replace Edited",
                    "type": "boolean"
                },
                "animation": {
                    "default": "STYLE",
                    "title": "Animation",
                    "type": "string"
                },
                "position": {
                    "default": "BOTTOM",
                    "title": "Position",
                    "type": "string"
                },
                "margin": {
                    "default": 0,
                    "title": "Margin",
                    "type": "number"
                },
                "speakers": {
                    "default": False,
                    "title": "Speakers",
                    "type": "boolean"
                },
                "speaker_labels": {
                    "default": False,
                    "title": "Speaker Labels",
                    "type": "boolean"
                },
                "speaker_settings": {
                    "anyOf": [
                        {
                            "items": {
                                "additionalProperties": {
                                    "type": "string"
                                },
                                "type": "object"
                            },
                            "type": "array"
                        },
                        {
                            "type": "null"
                        }
                    ],
                    "default": None,
                    "title": "Speaker Settings"
                },
                "case": {
                    "default": "STYLE",
                    "title": "Case",
                    "type": "string"
                },
                "font": {
                    "anyOf": [
                        {
                            "type": "string"
                        },
                        {
                            "type": "null"
                        }
                    ],
                    "default": None,
                    "title": "Font"
                },
                "bold": {
                    "anyOf": [
                        {
                            "type": "boolean"
                        },
                        {
                            "type": "null"
                        }
                    ],
                    "default": None,
                    "title": "Bold"
                },
                "italic": {
                    "anyOf": [
                        {
                            "type": "boolean"
                        },
                        {
                            "type": "null"
                        }
                    ],
                    "default": None,
                    "title": "Italic"
                },
                "size": {
                    "default": 1.0,
                    "title": "Size",
                    "type": "number"
                },
                "text_color": {
                    "anyOf": [
                        {
                            "type": "string"
                        },
                        {
                            "items": {
                                "type": "number"
                            },
                            "type": "array"
                        },
                        {
                            "type": "null"
                        }
                    ],
                    "default": None,
                    "title": "Text Color"
                },
                "highlight_color": {
                    "anyOf": [
                        {
                            "type": "string"
                        },
                        {
                            "items": {
                                "type": "number"
                            },
                            "type": "array"
                        },
                        {
                            "type": "null"
                        }
                    ],
                    "default": None,
                    "title": "Highlight Color"
                },
                "animate": {
                    "anyOf": [
                        {
                            "type": "string"
                        },
                        {
                            "type": "null"
                        }
                    ],
                    "default": None,
                    "title": "Animate"
                },
                "line_animation": {
                    "anyOf": [
                        {
                            "type": "string"
                        },
                        {
                            "type": "null"
                        }
                    ],
                    "default": None,
                    "title": "Line Animation"
                },
                "word_animation": {
                    "anyOf": [
                        {
                            "type": "string"
                        },
                        {
                            "type": "null"
                        }
                    ],
                    "default": None,
                    "title": "Word Animation"
                }
            },
            "title": "add_captionsArguments",
            "type": "object"
        }
    },
    {
        "name": "apply_transcript_edit",
        "description": "\n"
"        Edit a Sequencer clip by editing its transcript (Edit by Transcript), one undo step.\n"
"        Read it first with get_transcript_text. Words taken out are cut from the whole edit\n"
"        (picture, sound and every other track together, the gap closed, captions trimmed);\n"
"        lines moved to another place move their clips; a changed word changes only the\n"
"        captions. Text with no audio behind it (new words, a line rewritten so it no longer\n"
"        matches what is said, a repeated word or line the text can't tell apart) cuts nothing\n"
"        and is listed in ``flags``. A locked strip in the way: nothing changes, ``refused``\n"
"        names it. Nothing is cut by guessing: only text you removed, matched to its words.\n"
"\n"
"        Args:\n"
"            strip: The sound or movie strip (none: the active strip, or the only clip with\n"
"                a transcript).\n"
"            cut_lines: Line numbers (from get_transcript_text) to cut whole. Simplest for\n"
"                \"cut the part about ...\". Send it with ``version`` (get_transcript_text's\n"
"                ``version``) or give ``lines`` instead; if the text changed since you read it\n"
"                the answer is ``refused`` with the current ``text_now``. Not together with ``text``.\n"
"            text: Or the whole edited text (get_transcript_text's ``text``, changed): delete\n"
"                words or lines, move lines, fix words. Keep each line's time mark: it tells the\n"
"                line (a repeated sentence) apart. Not together with ``cut_lines``.\n"
"            dry_run: Only say what would change (``ops``, ``seconds``), change nothing, create nothing.\n"
"            version: The ``version`` of the text you read (see cut_lines).\n"
"            lines: Lines to cut with what you read: ``[{\"line\": 3, \"text\": \"...\"}]`` (the same\n"
"                check as ``version``, line by line).\n"
"            confirm_large_cut: An edit that cuts more than half of the words or the time is\n"
"                ``refused`` (``needs_confirm``) unless this is true: only after the user agreed.\n"
"\n"
"        Returns:\n"
"            ``status`` (ok / nothing / refused / error), ``message``, ``cut_words``,\n"
"            ``seconds`` (what the edit takes out), ``seconds_removed``, ``moved``, ``retexted``,\n"
"            ``captions_updated``, ``flags`` ([{line, text, reason, message}]), ``ops`` (the edit\n"
"            list), ``needs_confirm``, ``new_names`` (strip ->\n"
"            its pieces), ``text_now`` and ``version`` (the transcript as it plays now).\n"
"        ",
        "inputSchema": {
            "properties": {
                "strip": {
                    "anyOf": [
                        {
                            "type": "string"
                        },
                        {
                            "type": "null"
                        }
                    ],
                    "default": None,
                    "title": "Strip"
                },
                "cut_lines": {
                    "anyOf": [
                        {
                            "items": {
                                "type": "integer"
                            },
                            "type": "array"
                        },
                        {
                            "type": "null"
                        }
                    ],
                    "default": None,
                    "title": "Cut Lines"
                },
                "text": {
                    "anyOf": [
                        {
                            "type": "string"
                        },
                        {
                            "type": "null"
                        }
                    ],
                    "default": None,
                    "title": "Text"
                },
                "dry_run": {
                    "default": False,
                    "title": "Dry Run",
                    "type": "boolean"
                },
                "version": {
                    "anyOf": [
                        {
                            "type": "string"
                        },
                        {
                            "type": "null"
                        }
                    ],
                    "default": None,
                    "title": "Version"
                },
                "lines": {
                    "anyOf": [
                        {
                            "items": {
                                "additionalProperties": True,
                                "type": "object"
                            },
                            "type": "array"
                        },
                        {
                            "type": "null"
                        }
                    ],
                    "default": None,
                    "title": "Lines"
                },
                "confirm_large_cut": {
                    "default": False,
                    "title": "Confirm Large Cut",
                    "type": "boolean"
                }
            },
            "title": "apply_transcript_editArguments",
            "type": "object"
        }
    },
    {
        "name": "ask_user",
        "description": "\n"
"        Ask the user a short question in Coworker's chat, with buttons; waits for the answer.\n"
"\n"
"        Ask when a choice really matters (style, which object, overwrite or keep) - a\n"
"        question beats a wrong turn. Give 2-5 short ``choices``, the one you recommend FIRST\n"
"        (the chat marks it as the recommended path); with ``allow_text`` the user may also\n"
"        type an answer.\n"
"\n"
"        Args:\n"
"            question: One short question.\n"
"            choices: Button labels (2-5 short options), your recommended one first.\n"
"            allow_text: Let the user type an answer instead.\n"
"            wait_seconds: How long to wait (at most 280).\n"
"\n"
"        Returns:\n"
"            ``status`` (\"ok\", \"no_answer\" or \"cancelled\") and the ``answer`` text.\n"
"        ",
        "inputSchema": {
            "properties": {
                "question": {
                    "title": "Question",
                    "type": "string"
                },
                "choices": {
                    "anyOf": [
                        {
                            "items": {
                                "type": "string"
                            },
                            "type": "array"
                        },
                        {
                            "type": "null"
                        }
                    ],
                    "default": None,
                    "title": "Choices"
                },
                "allow_text": {
                    "default": True,
                    "title": "Allow Text",
                    "type": "boolean"
                },
                "wait_seconds": {
                    "default": 240.0,
                    "title": "Wait Seconds",
                    "type": "number"
                }
            },
            "required": [
                "question"
            ],
            "title": "ask_userArguments",
            "type": "object"
        }
    },
    {
        "name": "assign_material_to_objects",
        "description": "\n"
"        Assign an existing material to one or more objects by name.\n"
"\n"
"        The material must already exist in the scene (load it first with\n"
"        load_asset_in_context or create it with setup_pbr_material).\n"
"\n"
"        Args:\n"
"            material_name: Name of the material datablock in the scene.\n"
"            object_names: List of object names to assign the material to.\n"
"                If empty, assigns to the active object.\n"
"            slot_index: Material slot index to assign to (default 0 = first slot).\n"
"\n"
"        Returns:\n"
"            Status, assigned object list, and any errors.\n"
"        ",
        "inputSchema": {
            "properties": {
                "material_name": {
                    "title": "Material Name",
                    "type": "string"
                },
                "object_names": {
                    "default": [],
                    "items": {
                        "type": "string"
                    },
                    "title": "Object Names",
                    "type": "array"
                },
                "slot_index": {
                    "default": 0,
                    "title": "Slot Index",
                    "type": "integer"
                }
            },
            "required": [
                "material_name"
            ],
            "title": "assign_material_to_objectsArguments",
            "type": "object"
        }
    },
    {
        "name": "bake_textures",
        "description": "\n"
"        Bake textures with Cycles in the background; returns a ``job_id`` at once.\n"
"\n"
"        Each object needs a UV map and, in every material, an *active* Image Texture\n"
"        node holding the image to bake into (Blender's own bake workflow); the error\n"
"        says what to add when one is missing. The engine is switched to Cycles for the\n"
"        bake and back afterwards; the scene's Bake settings (passes, samples) apply.\n"
"        Wait for the result with ``get_job_status(job_id)``.\n"
"\n"
"        The bake **overwrites the pixels of the target images**, and Revert / Undo\n"
"        can't bring them back (the bake runs outside the turn's undo step): bake into\n"
"        new images, or save the old ones first, when they matter.\n"
"\n"
"        Args:\n"
"            bake_type: COMBINED, DIFFUSE, GLOSSY, TRANSMISSION, ROUGHNESS, EMIT, NORMAL,\n"
"                AO, SHADOW, POSITION, UV or ENVIRONMENT.\n"
"            object_names: Mesh objects to bake (empty = the selected meshes).\n"
"            margin: Pixels to extend the bake past UV island edges (-1 = the scene's).\n"
"\n"
"        Returns:\n"
"            ``status``, ``job_id``, the ``objects`` and target ``images``, and a ``message``.\n"
"        ",
        "inputSchema": {
            "properties": {
                "bake_type": {
                    "default": "DIFFUSE",
                    "title": "Bake Type",
                    "type": "string"
                },
                "object_names": {
                    "anyOf": [
                        {
                            "items": {
                                "type": "string"
                            },
                            "type": "array"
                        },
                        {
                            "type": "null"
                        }
                    ],
                    "default": None,
                    "title": "Object Names"
                },
                "margin": {
                    "default": -1,
                    "title": "Margin",
                    "type": "integer"
                }
            },
            "title": "bake_texturesArguments",
            "type": "object"
        }
    },
    {
        "name": "batch_keyframe_insert",
        "description": "\n"
"        Insert keyframes on multiple objects across multiple frames.\n"
"\n"
"        *keyframes_json* is a JSON string of the form::\n"
"\n"
"            {\n"
"              \"objects\": [\n"
"                {\n"
"                  \"name\": \"Cube\",\n"
"                  \"frames\": [\n"
"                    {\"frame\": 1, \"location\": [0, 0, 0], \"rotation\": [0, 0, 0]},\n"
"                    {\"frame\": 50, \"location\": [5, 0, 0], \"rotation\": [0, 0, 1.57]}\n"
"                  ]\n"
"                },\n"
"                {\n"
"                  \"name\": \"Sphere\",\n"
"                  \"frames\": [\n"
"                    {\"frame\": 1, \"location\": [0, 2, 0], \"scale\": [1, 1, 1]},\n"
"                    {\"frame\": 30, \"location\": [0, 5, 0], \"scale\": [2, 2, 2]}\n"
"                  ]\n"
"                }\n"
"              ]\n"
"            }\n"
"\n"
"        Each frame entry can include ``location``, ``rotation`` (euler radians),\n"
"        ``scale``, or custom ``data_path`` keyframes.\n"
"        Rotation is in radians, XYZ Euler.\n"
"        ",
        "inputSchema": {
            "properties": {
                "keyframes_json": {
                    "title": "Keyframes Json",
                    "type": "string"
                }
            },
            "required": [
                "keyframes_json"
            ],
            "title": "batch_keyframe_insertArguments",
            "type": "object"
        }
    },
    {
        "name": "build_node_tree_from_code",
        "description": "\n"
"        Build or rebuild a whole node tree from the text ``get_node_tree_as_code`` prints.\n"
"\n"
"        One line per node: ``name = NodeType(setting=value, Socket=value, \"Socket Name\"=other.Output)``.\n"
"        Node types are the bl_idname without its prefix (``SetPosition``, ``Math``,\n"
"        ``BsdfPrincipled``, ``GroupInput``) or the full bl_idname; a bare key that is a node\n"
"        property is a setting, every other key (and every quoted key) an input socket, by\n"
"        name or identifier.  Links: ``other.Output``, ``other[\"Socket Name\"]``, a list for a\n"
"        multi-input socket, ``muted(other.Output)``.  Interface: ``input Name: Float(min_value=0)\n"
"        = 1.0`` / ``output Geometry: Geometry``; ``frame f (label=\"Inputs\"): a, b``; zones:\n"
"        ``paired_output=other`` on the zone's input node; ``del name`` removes a node (update).\n"
"        Node groups the tree uses go first, as their own ``tree \"Name\" (Type)`` blocks.\n"
"\n"
"        Modes: ``create`` (a new node group / material; an existing name is an error),\n"
"        ``replace`` (rebuild the existing tree in place: same datablock, its users and\n"
"        interface sockets kept), ``update`` (change or add only the nodes in the text).\n"
"        Everything is checked against Blender first (a copy is built): an unknown node type,\n"
"        setting, socket or node returns the nearest valid names and changes nothing.\n"
"        Values Blender refuses are listed in ``not_set``.  One undo step.\n"
"\n"
"        Args:\n"
"            code: The text (one or more ``tree`` / ``material`` blocks; the last is the target).\n"
"            mode: \"create\", \"replace\" or \"update\".\n"
"            dry_run: Only check and list the changes.\n"
"            tree_name: Name for the target instead of the one in its header (e.g. to build a\n"
"                copy), or the tree to update when the text has no header.\n"
"            layout: \"auto\" (arrange left to right by links; the default for create) or \"keep\"\n"
"                (nodes that already exist keep their place; the default for replace / update).\n"
"\n"
"        Returns:\n"
"            ``status`` (\"ok\", \"dry_run\" or \"error\"), ``changes``, ``not_set``, ``errors``.\n"
"        ",
        "inputSchema": {
            "properties": {
                "code": {
                    "title": "Code",
                    "type": "string"
                },
                "mode": {
                    "default": "create",
                    "title": "Mode",
                    "type": "string"
                },
                "dry_run": {
                    "default": False,
                    "title": "Dry Run",
                    "type": "boolean"
                },
                "tree_name": {
                    "default": "",
                    "title": "Tree Name",
                    "type": "string"
                },
                "layout": {
                    "default": "",
                    "title": "Layout",
                    "type": "string"
                }
            },
            "required": [
                "code"
            ],
            "title": "build_node_tree_from_codeArguments",
            "type": "object"
        }
    },
    {
        "name": "delegate_task",
        "description": "\n"
"        Hand one independent part of the job to a helper chat in Coworker (a sub-agent).\n"
"\n"
"        The helper opens as its own chat tab with the task as its first message and answers\n"
"        at once: don't wait or poll. When every helper you started has finished, their\n"
"        results come back to this chat as one message, and you carry on from there. On a\n"
"        local model helpers run one after another; online they may run side by side.\n"
"\n"
"        Delegate independent parts (different editors, a long side job). Don't delegate\n"
"        small jobs or steps that need each other's results. Helpers can't start helpers.\n"
"\n"
"        Args:\n"
"            task: What the helper should do, complete on its own (names, values, where).\n"
"            editor: The editor whose context it works in: VIEW_3D, NODE_EDITOR,\n"
"                SEQUENCE_EDITOR, TEXT_EDITOR, IMAGE_EDITOR, CLIP_EDITOR, DOPESHEET_EDITOR,\n"
"                GRAPH_EDITOR, NLA_EDITOR, SPREADSHEET, or \"\" for All editors.\n"
"            title: A short title for its tab (a few words).\n"
"\n"
"        Returns:\n"
"            ``status`` (\"ok\" or \"error\"), the helper's ``helper_id`` and ``title``, and a message.\n"
"        ",
        "inputSchema": {
            "properties": {
                "task": {
                    "title": "Task",
                    "type": "string"
                },
                "editor": {
                    "default": "",
                    "title": "Editor",
                    "type": "string"
                },
                "title": {
                    "default": "",
                    "title": "Title",
                    "type": "string"
                }
            },
            "required": [
                "task"
            ],
            "title": "delegate_taskArguments",
            "type": "object"
        }
    },
    {
        "name": "download_polyhaven_asset",
        "description": "\n"
"        Download a Poly Haven asset and import it into the current Blender scene.\n"
"\n"
"        For **HDRIs**: creates a world environment shader with the HDRI mapped\n"
"        to a background node.\n"
"\n"
"        For **textures**: downloads all PBR maps (Diffuse, Normal, Roughness,\n"
"        AO, Displacement) and builds a complete Principled BSDF material with\n"
"        all maps properly connected.  Uses the ``arm`` packed texture as a\n"
"        fallback for missing Roughness/Metallic maps.\n"
"\n"
"        For **models**: imports the 3D model (glTF/FBX/OBJ) with textures.\n"
"        If a ``.blend`` file is available, appends objects/collections instead.\n"
"\n"
"        The resolution is typically set by the user in Blender addon\n"
"        preferences and injected automatically.  You can override it here\n"
"        if needed.\n"
"\n"
"        Args:\n"
"            asset_id: The asset ID from ``search_polyhaven_assets``\n"
"                (e.g. ``\"concrete_floor_01\"``).\n"
"            asset_type: ``\"hdris\"``, ``\"textures\"``, or ``\"models\"``.\n"
"            resolution: Download resolution -- ``\"512\"``, ``\"1k\"``, ``\"2k\"``,\n"
"                ``\"4k\"``, or ``\"8k\"`` (HDRIs/textures).  Models always use\n"
"                the resolution that best matches.\n"
"\n"
"        Returns:\n"
"            A summary of what was downloaded and imported, including which\n"
"            PBR maps were used.\n"
"        ",
        "inputSchema": {
            "properties": {
                "asset_id": {
                    "title": "Asset Id",
                    "type": "string"
                },
                "asset_type": {
                    "default": "hdris",
                    "title": "Asset Type",
                    "type": "string"
                },
                "resolution": {
                    "default": "2k",
                    "title": "Resolution",
                    "type": "string"
                }
            },
            "required": [
                "asset_id"
            ],
            "title": "download_polyhaven_assetArguments",
            "type": "object"
        }
    },
    {
        "name": "execute_blender_code",
        "description": "\n"
"        Execute Python code in the connected Blender instance.\n"
"\n"
"        Use it for what the dedicated tools don't cover: creating and editing\n"
"        objects, modifiers, materials, animation.  Check first whether one of\n"
"        these fits -- they are faster and pre-tested on Blender 5.x:\n"
"        get_objects_summary / get_object_detail_summary (inspect the scene),\n"
"        execute_blender_plan (primitives, modifiers, lights, camera, keys),\n"
"        setup_pbr_material, download_polyhaven_asset, three_point_lighting_rig,\n"
"        search_api_docs (when unsure of an API).\n"
"\n"
"        The code runs with full access to ``bpy`` on Blender's main thread\n"
"        (``bpy.context`` has the window, scene and active object).  Keep\n"
"        scripts short (about 40 lines).  To return data, assign a\n"
"        JSON-serialisable dict to ``result``.  An ``error`` lists the fix;\n"
"        ``preflight_notes`` in a successful result are advice, not failures.\n"
"        Deferred completion via ``check_is_finished`` is only supported by the\n"
"        interactive addon server, and is rejected in background mode.\n"
"        ",
        "inputSchema": {
            "properties": {
                "code": {
                    "title": "Code",
                    "type": "string"
                }
            },
            "required": [
                "code"
            ],
            "title": "execute_blender_codeArguments",
            "type": "object"
        }
    },
    {
        "name": "execute_blender_code_for_cli",
        "description": "\n"
"        Execute Python code in a background Blender process.\n"
"\n"
"        Opens *blend_file* with ``blender --background`` and runs *code*.\n"
"        Assign a dict to ``result`` to return data.\n"
"        ",
        "inputSchema": {
            "properties": {
                "blend_file": {
                    "title": "Blend File",
                    "type": "string"
                },
                "code": {
                    "title": "Code",
                    "type": "string"
                }
            },
            "required": [
                "blend_file",
                "code"
            ],
            "title": "execute_blender_code_for_cliArguments",
            "type": "object"
        }
    },
    {
        "name": "execute_blender_plan",
        "description": "\n"
"        Execute a structured plan of Blender operations.\n"
"\n"
"        Each step is a dict with either:\n"
"        - {\"template\": \"name\", \"params\": {...}} -- use a tested template\n"
"        - {\"code\": \"...\"} -- custom Python code\n"
"\n"
"        Available templates: create_torus, create_cube, create_uv_sphere,\n"
"        create_cylinder, create_plane, add_material, three_point_lighting,\n"
"        add_subsurf, add_array, add_bevel, add_solidify, add_smooth,\n"
"        add_remesh, smooth_shade, auto_smooth, set_render_engine,\n"
"        setup_camera, keyframe_location, keyframe_rotation.\n"
"\n"
"        Templates are pre-tested for Blender 5.3 and auto-correct common\n"
"        mistakes.  Use this instead of execute_blender_code when possible.\n"
"        ",
        "inputSchema": {
            "properties": {
                "steps": {
                    "items": {},
                    "title": "Steps",
                    "type": "array"
                }
            },
            "required": [
                "steps"
            ],
            "title": "execute_blender_planArguments",
            "type": "object"
        }
    },
    {
        "name": "list_blender_templates",
        "description": "\n"
"        List all available Blender code templates with their default parameters.\n"
"\n"
"        Use execute_blender_plan() with template names from this list.\n"
"        Each template is pre-tested for Blender 5.3 and handles common\n"
"        API pitfalls automatically.\n"
"        ",
        "inputSchema": {
            "properties": {},
            "title": "list_blender_templatesArguments",
            "type": "object"
        }
    },
    {
        "name": "get_active_node_tree",
        "description": "\n"
"        Serialize a node tree for the LLM.\n"
"\n"
"        Resolves the target tree the same way the Shader/Geometry\n"
"        Nodes/Compositor editors do:\n"
"\n"
"        - ``\"ShaderNodeTree\"`` - the active object's active material.\n"
"        - ``\"GeometryNodeTree\"`` - the active object's active Geometry\n"
"          Nodes modifier.\n"
"        - ``\"CompositorNodeTree\"`` - the scene's compositor node group\n"
"          (its active compositor effect's).\n"
"\n"
"        Read only: a missing tree is reported, never created.\n"
"        - ``node_tree_name`` - overrides resolution and reads that exact\n"
"          tree from ``bpy.data.node_groups``.\n"
"\n"
"        Returns a compact summary: nodes (name, type, label, location,\n"
"        muted state), their input/output sockets (name + type + default),\n"
"        links (from-to by node and socket name), and frames.\n"
"\n"
"        Args:\n"
"            tree_type: One of ``\"ShaderNodeTree\"``, ``\"CompositorNodeTree\"``,\n"
"                ``\"GeometryNodeTree\"`` (empty = auto-detect first available).\n"
"            node_tree_name: Optional explicit ``bpy.data.node_groups`` name.\n"
"\n"
"        Returns:\n"
"            Status, tree metadata, and node/link/frame lists.\n"
"        ",
        "inputSchema": {
            "properties": {
                "tree_type": {
                    "default": "",
                    "title": "Tree Type",
                    "type": "string"
                },
                "node_tree_name": {
                    "default": "",
                    "title": "Node Tree Name",
                    "type": "string"
                }
            },
            "title": "get_active_node_treeArguments",
            "type": "object"
        }
    },
    {
        "name": "get_asset_libraries",
        "description": "\n"
"        List all configured asset libraries in Blender.\n"
"\n"
"        Returns each library's name, path, and total asset count.\n"
"        Uses bpy.context.preferences.filepaths.asset_libraries to enumerate.\n"
"        ",
        "inputSchema": {
            "properties": {},
            "title": "get_asset_librariesArguments",
            "type": "object"
        }
    },
    {
        "name": "get_asset_tags",
        "description": "\n"
"        Get detailed tags and metadata for an asset, including node group editor type.\n"
"\n"
"        For NODETREE assets, returns the editor type (GeometryNodeTree, ShaderNodeTree,\n"
"        CompositorNodeTree) and other metadata like color tags.\n"
"\n"
"        Args:\n"
"            library_name: Name of the asset library containing the asset.\n"
"            asset_name: Name of the asset to inspect.\n"
"            asset_type: Optional type hint (MATERIAL, NODETREE, OBJECT, WORLD, ACTION).\n"
"                        Auto-detected if omitted.\n"
"\n"
"        Returns:\n"
"            Dict with tags, editor_type (for node groups), color_tag, and other metadata, and the\n"
"            asset's catalog: catalog (its path, from the library's catalog file) and catalog_id\n"
"            (its UUID). Both are empty for an asset without a catalog.\n"
"        ",
        "inputSchema": {
            "properties": {
                "library_name": {
                    "title": "Library Name",
                    "type": "string"
                },
                "asset_name": {
                    "title": "Asset Name",
                    "type": "string"
                },
                "asset_type": {
                    "default": "",
                    "title": "Asset Type",
                    "type": "string"
                }
            },
            "required": [
                "library_name",
                "asset_name"
            ],
            "title": "get_asset_tagsArguments",
            "type": "object"
        }
    },
    {
        "name": "get_attached_image",
        "description": "\n"
"        The image the user attached in Coworker's chat in Blender (dropped, picked, a render or a screen).\n"
"\n"
"        Call it when the user says they attached, dropped or captured an image in Blender\n"
"        and asks you to look at it.\n"
"        ",
        "inputSchema": {
            "properties": {},
            "title": "get_attached_imageArguments",
            "type": "object"
        }
    },
    {
        "name": "get_blendfile_summary_datablocks",
        "description": "\n"
"        Return a summary of the blend file: data-block counts, active workspace, and render engine.\n"
"        ",
        "inputSchema": {
            "properties": {},
            "title": "get_blendfile_summary_datablocksArguments",
            "type": "object"
        }
    },
    {
        "name": "get_blendfile_summary_datablocks_for_cli",
        "description": "\n"
"        Return a data-block summary by opening *blend_file* in background Blender.\n"
"        ",
        "inputSchema": {
            "properties": {
                "blend_file": {
                    "title": "Blend File",
                    "type": "string"
                }
            },
            "required": [
                "blend_file"
            ],
            "title": "get_blendfile_summary_datablocks_for_cliArguments",
            "type": "object"
        }
    },
    {
        "name": "get_blendfile_summary_missing_files",
        "description": "\n"
"        Report external file references that are missing from disk\n"
"        (images, libraries, fonts, sounds, movie clips, caches, sequences).\n"
"        ",
        "inputSchema": {
            "properties": {},
            "title": "get_blendfile_summary_missing_filesArguments",
            "type": "object"
        }
    },
    {
        "name": "get_blendfile_summary_missing_files_for_cli",
        "description": "\n"
"        Report missing file references by opening *blend_file* in background Blender.\n"
"        ",
        "inputSchema": {
            "properties": {
                "blend_file": {
                    "title": "Blend File",
                    "type": "string"
                }
            },
            "required": [
                "blend_file"
            ],
            "title": "get_blendfile_summary_missing_files_for_cliArguments",
            "type": "object"
        }
    },
    {
        "name": "get_blendfile_summary_of_linked_libraries",
        "description": "\n"
"        Return a tree of directly and indirectly linked library files.\n"
"        ",
        "inputSchema": {
            "properties": {},
            "title": "get_blendfile_summary_of_linked_librariesArguments",
            "type": "object"
        }
    },
    {
        "name": "get_blendfile_summary_of_linked_libraries_for_cli",
        "description": "\n"
"        Return linked-library info by opening *blend_file* in background Blender.\n"
"        ",
        "inputSchema": {
            "properties": {
                "blend_file": {
                    "title": "Blend File",
                    "type": "string"
                }
            },
            "required": [
                "blend_file"
            ],
            "title": "get_blendfile_summary_of_linked_libraries_for_cliArguments",
            "type": "object"
        }
    },
    {
        "name": "get_blendfile_summary_path_info",
        "description": "\n"
"        Simple/fast access to the blend file's path, save status, age, and backups.\n"
"        ",
        "inputSchema": {
            "properties": {},
            "title": "get_blendfile_summary_path_infoArguments",
            "type": "object"
        }
    },
    {
        "name": "get_blendfile_summary_path_info_for_cli",
        "description": "\n"
"        Return path info by opening *blend_file* in background Blender.\n"
"        ",
        "inputSchema": {
            "properties": {
                "blend_file": {
                    "title": "Blend File",
                    "type": "string"
                }
            },
            "required": [
                "blend_file"
            ],
            "title": "get_blendfile_summary_path_info_for_cliArguments",
            "type": "object"
        }
    },
    {
        "name": "get_blendfile_summary_usage_guess",
        "description": "\n"
"        Guess the primary use-cases of the current blend file (scored 0-100 with certainty).\n"
"        ",
        "inputSchema": {
            "properties": {},
            "title": "get_blendfile_summary_usage_guessArguments",
            "type": "object"
        }
    },
    {
        "name": "get_blendfile_summary_usage_guess_for_cli",
        "description": "\n"
"        Guess use-cases by opening *blend_file* in background Blender.\n"
"        ",
        "inputSchema": {
            "properties": {
                "blend_file": {
                    "title": "Blend File",
                    "type": "string"
                }
            },
            "required": [
                "blend_file"
            ],
            "title": "get_blendfile_summary_usage_guess_for_cliArguments",
            "type": "object"
        }
    },
    {
        "name": "get_job_status",
        "description": "\n"
"        How a long job (a background render or bake) is doing; waits for it to finish.\n"
"\n"
"        Long jobs answer with a ``job_id`` instead of holding the call open. Call this\n"
"        with that id: it answers as soon as the job is over, or after ``wait_seconds``\n"
"        with ``state`` \"running\" and the progress so far. While it runs, call it again\n"
"        (don't poll faster than every few seconds; the wait does that for you).\n"
"\n"
"        Args:\n"
"            job_id: The ``job_id`` a long job returned.\n"
"            wait_seconds: How long to wait for the job to finish (0 = answer now, at most 240).\n"
"\n"
"        Returns:\n"
"            ``status``, ``job_id``, ``state`` (\"running\", \"done\", \"error\", \"cancelled\" or \"stuck\"),\n"
"            ``kind``, ``label``, ``elapsed_seconds``, ``progress_percent`` (when Blender\n"
"            reports it), ``result`` once done (e.g. the ``filepath`` or baked images),\n"
"            and a ``message``.  ``state`` \"stuck\" (a render with no progress for a few\n"
"            minutes): stop polling, don't start another render, tell the user.\n"
"        ",
        "inputSchema": {
            "properties": {
                "job_id": {
                    "title": "Job Id",
                    "type": "string"
                },
                "wait_seconds": {
                    "default": 25.0,
                    "title": "Wait Seconds",
                    "type": "number"
                }
            },
            "required": [
                "job_id"
            ],
            "title": "get_job_statusArguments",
            "type": "object"
        }
    },
    {
        "name": "get_node_group_interface",
        "description": "\n"
"        Return the interface of a node group loaded in the current blend file.\n"
"\n"
"        Lists the group's editor type (Geometry Nodes / Shader / Compositor)\n"
"        and every input/output socket with its socket type, default value,\n"
"        min/max range, and description. The asset-author convention is to\n"
"        name interface inputs ``Scale``, ``Seed``, ``Strength``, ``Color``\n"
"        and to put a one-line usage note in the asset description, which\n"
"        lets this tool double as the group's wiring manual.\n"
"\n"
"        Args:\n"
"            group_name: Name of the node group in ``bpy.data.node_groups``\n"
"                (e.g. the group loaded by ``load_asset_in_context``).\n"
"\n"
"        Returns:\n"
"            Status, editor type, input and output socket lists with types\n"
"            and defaults.\n"
"        ",
        "inputSchema": {
            "properties": {
                "group_name": {
                    "title": "Group Name",
                    "type": "string"
                }
            },
            "required": [
                "group_name"
            ],
            "title": "get_node_group_interfaceArguments",
            "type": "object"
        }
    },
    {
        "name": "get_node_tree_as_code",
        "description": "\n"
"        Read a whole node tree as short build code (Geometry Nodes, shader or compositor).\n"
"\n"
"        One line per node, only the settings and input values that differ from a\n"
"        new node's, links written as references, e.g.::\n"
"\n"
"            tree \"Scatter\" (GeometryNodeTree, is_modifier=True)\n"
"            input Geometry: Geometry\n"
"            input Density: Float(min_value=0.0) = 10.0\n"
"            output Geometry: Geometry\n"
"            \"Group Input\" = GroupInput()\n"
"            points = DistributePointsOnFaces(Mesh=\"Group Input\".Geometry, Density=\"Group Input\".Density)\n"
"            \"Group Output\" = GroupOutput(Geometry=points.Points)\n"
"\n"
"        Edit the text and send it to ``build_node_tree_from_code``.  The node groups the\n"
"        tree uses come first, as their own ``tree`` blocks.  Read only: a missing tree is\n"
"        reported, never created.\n"
"\n"
"        Which tree (like ``get_active_node_tree``): ``node_tree_name`` (a node group),\n"
"        ``material_name``, or ``object_name`` (+ ``modifier_name``) for its Geometry Nodes\n"
"        modifier / active material; nothing = the active object's material, then its\n"
"        Geometry Nodes modifier, then the scene's compositor.\n"
"\n"
"        Args:\n"
"            tree_type: \"geometry\", \"shader\" or \"compositor\" (or GeometryNodeTree...).\n"
"            node_tree_name: A node group's name.\n"
"            material_name: A material's name (its node tree).\n"
"            object_name: An object: its Geometry Nodes modifier's group, or its material.\n"
"            modifier_name: Which Geometry Nodes modifier of the object.\n"
"            nodes: Only these nodes (by name).\n"
"            include_groups: Also print the node groups the tree uses.\n"
"            max_chars: Size limit; whole nodes are cut and the result says how to get the rest.\n"
"            node_offset: Start at this node (to read the rest after a cut).\n"
"\n"
"        Returns:\n"
"            ``code`` (the text), ``node_count``, ``cut_nodes`` and ``next_node_offset``\n"
"            (-1 = nothing cut), the ``groups`` printed.\n"
"        ",
        "inputSchema": {
            "properties": {
                "tree_type": {
                    "default": "",
                    "title": "Tree Type",
                    "type": "string"
                },
                "node_tree_name": {
                    "default": "",
                    "title": "Node Tree Name",
                    "type": "string"
                },
                "material_name": {
                    "default": "",
                    "title": "Material Name",
                    "type": "string"
                },
                "object_name": {
                    "default": "",
                    "title": "Object Name",
                    "type": "string"
                },
                "modifier_name": {
                    "default": "",
                    "title": "Modifier Name",
                    "type": "string"
                },
                "nodes": {
                    "anyOf": [
                        {
                            "items": {
                                "type": "string"
                            },
                            "type": "array"
                        },
                        {
                            "type": "null"
                        }
                    ],
                    "default": None,
                    "title": "Nodes"
                },
                "include_groups": {
                    "default": True,
                    "title": "Include Groups",
                    "type": "boolean"
                },
                "max_chars": {
                    "default": 6000,
                    "title": "Max Chars",
                    "type": "integer"
                },
                "node_offset": {
                    "default": 0,
                    "title": "Node Offset",
                    "type": "integer"
                }
            },
            "title": "get_node_tree_as_codeArguments",
            "type": "object"
        }
    },
    {
        "name": "get_object_detail_summary",
        "description": "\n"
"        Return a structured summary of the object identified by *name*.\n"
"\n"
"        Includes type, transforms, parent, children, modifiers, constraints,\n"
"        materials, visibility, data-block name, and collections.\n"
"        ",
        "inputSchema": {
            "properties": {
                "name": {
                    "title": "Name",
                    "type": "string"
                }
            },
            "required": [
                "name"
            ],
            "title": "get_object_detail_summaryArguments",
            "type": "object"
        }
    },
    {
        "name": "get_objects_summary",
        "description": "\n"
"        Return the scene's collection hierarchy and their objects.\n"
"\n"
"        Each collection lists its objects (name, type, parent, data name,\n"
"        selection, visibility) and nested child collections.\n"
"        ",
        "inputSchema": {
            "properties": {},
            "title": "get_objects_summaryArguments",
            "type": "object"
        }
    },
    {
        "name": "get_operation_history",
        "description": "\n"
"        Read the last *count* tool operations from the history log.\n"
"\n"
"        Use this to check what operations have already been performed in the\n"
"        current session, avoiding redundant or repeated tool calls.\n"
"\n"
"        Args:\n"
"            count: Number of recent operations to return (max 50).\n"
"\n"
"        Returns:\n"
"            A formatted text summary of the last N operations, or a message\n"
"            indicating no history is available.\n"
"        ",
        "inputSchema": {
            "properties": {
                "count": {
                    "default": 10,
                    "title": "Count",
                    "type": "integer"
                }
            },
            "title": "get_operation_historyArguments",
            "type": "object"
        }
    },
    {
        "name": "get_polyhaven_status",
        "description": "\n"
"        Check whether the Poly Haven API is accessible.\n"
"\n"
"        Returns:\n"
"            A dict with ``status`` and a ``message`` describing API\n"
"            availability and asset counts (consistent with other tools).\n"
"        ",
        "inputSchema": {
            "properties": {},
            "title": "get_polyhaven_statusArguments",
            "type": "object"
        }
    },
    {
        "name": "get_python_api_docs",
        "description": "\n"
"        Return the Blender Python API docs for *identifier*, or list\n"
"        modules matching a trailing-``*`` discovery pattern.\n"
"\n"
"        *identifier* should be a fully-qualified Python name (e.g.\n"
"        ``bpy.app`` or ``bpy.types.Scene.frame_current``).\n"
"        The trailing-``*`` forms are supported as discovery entry-points:\n"
"\n"
"        - ``*`` enumerates the top-level modules (``bpy``, ``bmesh``,\n"
"          ``mathutils``, ``gpu``, ...).\n"
"        - ``X.*`` enumerates the direct-child identifiers under the\n"
"          *X* namespace (``bpy.*`` -> ``bpy.app``, ``bpy.context``, ...).\n"
"\n"
"        Both return a ``namespace`` response even when ``X.rst`` would\n"
"        otherwise resolve to ``exact``; the ``.*`` form lets an agent\n"
"        force the child listing.\n"
"\n"
"        The response always carries ``kind``, ``found``, and ``identifier``.\n"
"        The remaining keys depend on ``kind``:\n"
"\n"
"        - ``\"exact\"`` (``found=True``): ``<identifier>.rst`` was read.\n"
"          Extra keys: ``content`` (RST text), ``examples``. When the\n"
"          file exceeds 32 KB, ``content`` is replaced with a dot-point\n"
"          summary of the file's top-level definitions (prefixed by a\n"
"          header noting the truncation) and ``examples`` is empty -\n"
"          re-query individual members for their rendered blocks.\n"
"        - ``\"namespace\"`` (``found=True``):\n"
"          no ``<identifier>.rst`` but ``<identifier>.<child>.rst`` siblings exist.\n"
"          Extra key: ``submodules`` (list of child identifiers).\n"
"        - ``\"definition\"`` (``found=True``):\n"
"          *identifier* is defined inside a parent RST\n"
"          (e.g. ``bpy.props.IntProperty`` lives in ``bpy.props.rst``).\n"
"          Extra keys: ``content`` (rendered block), ``examples``.\n"
"        - ``\"partial\"`` (``found=False``):\n"
"          the parent RST was located but the trailing component isn't defined in it.\n"
"          Extra keys:\n"
"          - ``parent`` the identifier whose RST was loaded.\n"
"          - ``available`` top-level definitions in that RST.\n"
"          - ``submodules`` sibling identifiers ``<parent>.<child>`` with their own RSTs,\n"
"            filtered to those whose last component contains every character of the missing tail.\n"
"\n"
"          For a toctree landing page like ``bpy.types`` ``available`` is empty and ``submodules``\n"
"          is the near-miss list; for a self-contained module like ``bpy.props`` it's the reverse.\n"
"        - ``\"suggestions\"`` (``found=False``):\n"
"          no direct match, but *identifier* appears as a component of other files.\n"
"          Extra key: ``suggestions`` (list of full identifiers).\n"
"        - ``\"missing\"`` (``found=False``): nothing matched.\n"
"\n"
"        ``examples`` (present on the ``exact`` and ``definition`` kinds)\n"
"        is a list of ``{path, content}`` entries referenced from this documentation.\n"
"        ",
        "inputSchema": {
            "properties": {
                "identifier": {
                    "title": "Identifier",
                    "type": "string"
                }
            },
            "required": [
                "identifier"
            ],
            "title": "get_python_api_docsArguments",
            "type": "object"
        }
    },
    {
        "name": "get_scene_digest",
        "description": "\n"
"        A short summary of the scene (counts, active and selected objects, up to 40 objects)\n"
"        with a ``hash``. Call it first instead of a full listing.\n"
"\n"
"        Called again with ``since`` = the hash you got, it answers ``unchanged`` or only\n"
"        what changed (objects added, removed, changed), so re-checking the scene is cheap.\n"
"\n"
"        Args:\n"
"            since: The ``hash`` from an earlier call (empty = the whole summary).\n"
"\n"
"        Returns:\n"
"            Status and the digest: ``hash``, scene, frame, mode, active, and either\n"
"            ``unchanged``, ``changes`` or ``objects``.\n"
"        ",
        "inputSchema": {
            "properties": {
                "since": {
                    "default": "",
                    "title": "Since",
                    "type": "string"
                }
            },
            "title": "get_scene_digestArguments",
            "type": "object"
        }
    },
    {
        "name": "get_screenshot_of_area_as_image",
        "description": "\n"
"        Take a screenshot of a single Blender area and return it as a PNG image.\n"
"\n"
"        *area_ui_type* matches the area's ``ui_type``.\n"
"\n"
"        *size_limit_in_bytes* caps the image size in bytes.\n"
"        Zero (the default) uses the MCP message size limit.\n"
"        ",
        "inputSchema": {
            "properties": {
                "area_ui_type": {
                    "enum": [
                        "VIEW_3D",
                        "IMAGE_EDITOR",
                        "UV",
                        "ShaderNodeTree",
                        "CompositorNodeTree",
                        "GeometryNodeTree",
                        "TextureNodeTree",
                        "SEQUENCE_EDITOR",
                        "CLIP_EDITOR",
                        "DOPESHEET_EDITOR",
                        "GRAPH_EDITOR",
                        "NLA_EDITOR",
                        "TEXT_EDITOR",
                        "CONSOLE",
                        "INFO",
                        "TOPBAR",
                        "STATUSBAR",
                        "OUTLINER",
                        "PROPERTIES",
                        "FILE_BROWSER",
                        "SPREADSHEET",
                        "PREFERENCES"
                    ],
                    "title": "Area Ui Type",
                    "type": "string"
                },
                "size_limit_in_bytes": {
                    "default": 0,
                    "title": "Size Limit In Bytes",
                    "type": "integer"
                }
            },
            "required": [
                "area_ui_type"
            ],
            "title": "get_screenshot_of_area_as_imageArguments",
            "type": "object"
        }
    },
    {
        "name": "get_screenshot_of_window_as_image",
        "description": "\n"
"        Take a screenshot of the entire Blender window and return it as a PNG image.\n"
"\n"
"        *size_limit_in_bytes* caps the image size in bytes.\n"
"        Zero (the default) uses the MCP message size limit.\n"
"        ",
        "inputSchema": {
            "properties": {
                "size_limit_in_bytes": {
                    "default": 0,
                    "title": "Size Limit In Bytes",
                    "type": "integer"
                }
            },
            "title": "get_screenshot_of_window_as_imageArguments",
            "type": "object"
        }
    },
    {
        "name": "get_screenshot_of_window_as_json",
        "description": "\n"
"        Return a JSON description of the Blender window layout, areas, active object, and selection.\n"
"\n"
"        ``sequencer`` says where the Video Sequencer is (scene, strip count, current frame,\n"
"        active strip) when a scene has strips.\n"
"        ",
        "inputSchema": {
            "properties": {},
            "title": "get_screenshot_of_window_as_jsonArguments",
            "type": "object"
        }
    },
    {
        "name": "get_transcript_text",
        "description": "\n"
"        Read a Sequencer clip's transcript as text: what it says, as it plays on the\n"
"        timeline now (cuts already made are left out), one line per sentence with a\n"
"        line number and its time. Use it to find a part (\"the bit about pricing\") before\n"
"        apply_transcript_edit. The clip must have been transcribed (transcribe_strip).\n"
"\n"
"        Args:\n"
"            strip: The sound or movie strip (none: the active strip, or the only clip\n"
"                with a transcript).\n"
"\n"
"        Returns:\n"
"            ``lines`` ([{line, time, frame, text}]), ``text`` (the whole text, each line\n"
"            starting with its time mark), ``strip``, ``media``, ``text_block`` (the Text\n"
"            Editor text of it, if open) and ``pending_edits`` (it has edits not applied).\n"
"        ",
        "inputSchema": {
            "properties": {
                "strip": {
                    "anyOf": [
                        {
                            "type": "string"
                        },
                        {
                            "type": "null"
                        }
                    ],
                    "default": None,
                    "title": "Strip"
                }
            },
            "title": "get_transcript_textArguments",
            "type": "object"
        }
    },
    {
        "name": "get_vse_strip_detail",
        "description": "\n"
"        Everything about one Video Sequencer strip.\n"
"\n"
"        Frames and channel, content offsets, opacity and blend mode, transform and crop,\n"
"        the source file (movie, image, sound, scene), text and its style, colour, volume,\n"
"        speed (retiming keys), effect inputs, modifiers (colour balance values), proxy.\n"
"\n"
"        Args:\n"
"            strip_name: The strip's exact name (get_vse_strips_summary lists them).\n"
"            scene_name: Another scene (default: the scene the Sequencer shows).\n"
"\n"
"        Returns:\n"
"            ``status``, ``scene``, ``strip`` (the details), ``message``; when the name is\n"
"            not found, ``message`` lists the nearest names.\n"
"        ",
        "inputSchema": {
            "properties": {
                "strip_name": {
                    "title": "Strip Name",
                    "type": "string"
                },
                "scene_name": {
                    "default": "",
                    "title": "Scene Name",
                    "type": "string"
                }
            },
            "required": [
                "strip_name"
            ],
            "title": "get_vse_strip_detailArguments",
            "type": "object"
        }
    },
    {
        "name": "get_vse_strips_summary",
        "description": "\n"
"        List the Video Sequencer's strips: name, type, channel, frames, mute, lock, selection.\n"
"\n"
"        Use it to find a strip by name or to see what is on a channel (\"what's on channel 3?\").\n"
"        Frames are Blender's: ``start`` is the first frame shown, ``end`` the frame after the\n"
"        last one (a strip on frames 1-10 has start 1, end 11). Strips inside a meta strip\n"
"        name it in ``meta``.\n"
"\n"
"        Args:\n"
"            channel: Only this channel (0 = all).\n"
"            strip_type: Only this type, e.g. TEXT, COLOR, MOVIE, IMAGE, SOUND, SCENE, META.\n"
"            frame_start: Only strips still showing at or after this frame.\n"
"            frame_end: Only strips starting at or before this frame.\n"
"            name_contains: Only names containing this text (any case).\n"
"            limit: At most this many strips (default 50, at most 500).\n"
"            scene_name: Another scene (default: the scene the Sequencer shows).\n"
"\n"
"        Returns:\n"
"            ``status``, ``scene``, ``total`` / ``matched`` / ``shown``, ``more`` (strips\n"
"            left out by ``limit``), ``strips`` (rows sorted by channel and start), ``message``.\n"
"        ",
        "inputSchema": {
            "properties": {
                "channel": {
                    "default": 0,
                    "title": "Channel",
                    "type": "integer"
                },
                "strip_type": {
                    "default": "",
                    "title": "Strip Type",
                    "type": "string"
                },
                "frame_start": {
                    "anyOf": [
                        {
                            "type": "integer"
                        },
                        {
                            "type": "null"
                        }
                    ],
                    "default": None,
                    "title": "Frame Start"
                },
                "frame_end": {
                    "anyOf": [
                        {
                            "type": "integer"
                        },
                        {
                            "type": "null"
                        }
                    ],
                    "default": None,
                    "title": "Frame End"
                },
                "name_contains": {
                    "default": "",
                    "title": "Name Contains",
                    "type": "string"
                },
                "limit": {
                    "default": 50,
                    "title": "Limit",
                    "type": "integer"
                },
                "scene_name": {
                    "default": "",
                    "title": "Scene Name",
                    "type": "string"
                }
            },
            "title": "get_vse_strips_summaryArguments",
            "type": "object"
        }
    },
    {
        "name": "get_vse_timeline_overview",
        "description": "\n"
"        An overview of the Video Sequencer timeline; call it first for any editing request.\n"
"\n"
"        Frame range, fps, resolution, current frame, the channels in use (strip count, name,\n"
"        mute, lock), gaps (frames where no picture plays), overlapping strips, markers and\n"
"        the active strip.\n"
"\n"
"        Args:\n"
"            scene_name: Another scene (default: the scene the Sequencer shows).\n"
"\n"
"        Returns:\n"
"            ``status``, ``scene``, ``sequencer_shows`` (the scene the editor shows),\n"
"            ``fps``, ``resolution`` [x, y, percent], ``frame_start`` / ``frame_end`` /\n"
"            ``current_frame``, ``strip_count``, ``content_range``, ``active_strip``,\n"
"            ``channels``, ``gaps`` ([first, last] frames), ``overlaps``, ``markers``, ``message``.\n"
"        ",
        "inputSchema": {
            "properties": {
                "scene_name": {
                    "default": "",
                    "title": "Scene Name",
                    "type": "string"
                }
            },
            "title": "get_vse_timeline_overviewArguments",
            "type": "object"
        }
    },
    {
        "name": "jump_to_asset_browser",
        "description": "\n"
"        Switch to (or create) the Asset Browser editor.\n"
"\n"
"        If an Asset Browser area is already open in any workspace it is\n"
"        reused.  Otherwise, with *allow_edits* (default), the largest free area\n"
"        of the current workspace is turned into an Asset Browser.  Not taken:\n"
"        the Coworker editor, and editors whose sidebar is open (the compact\n"
"        chat may sit there).  With no free area, or while a render, a bake or\n"
"        another operation is running, nothing is changed and the answer says\n"
"        why.  No workspace is created, renamed or switched; the result's\n"
"        ``changed_area`` and message name the editor that was replaced, so it\n"
"        can be set back.\n"
"\n"
"        *library_name* optionally preselects the asset library shown in the\n"
"        browser (best-effort: if the name matches a configured library or a\n"
"        built-in reference ``\"LOCAL\"`` (Current File), ``\"ESSENTIALS\"`` or\n"
"        ``\"ALL\"`` it is applied).\n"
"        *catalog_path* optionally selects a catalog: a path such as\n"
"        ``\"Props/Chairs\"`` (looked up in the library's ``blender_assets.cats.txt``;\n"
"        for ``\"LOCAL\"`` the one next to the saved .blend) or a catalog UUID.\n"
"        An unknown path is not applied and the nearest catalog paths come back\n"
"        in ``nearest_catalogs``; anything that could not be applied is\n"
"        explained in ``message``.\n"
"\n"
"        Args:\n"
"            library_name: Asset library to select (empty = leave as-is).\n"
"            catalog_path: Catalog path or catalog UUID to select (empty = leave as-is).\n"
"            allow_edits: Allow turning an area of the current workspace into an\n"
"                Asset Browser when none is open.\n"
"\n"
"        Returns:\n"
"            Status, workspace, whether an area was converted (and which), the library and\n"
"            catalog (path and UUID) applied, and what could not be applied.\n"
"        ",
        "inputSchema": {
            "properties": {
                "library_name": {
                    "default": "",
                    "title": "Library Name",
                    "type": "string"
                },
                "catalog_path": {
                    "default": "",
                    "title": "Catalog Path",
                    "type": "string"
                },
                "allow_edits": {
                    "default": True,
                    "title": "Allow Edits",
                    "type": "boolean"
                }
            },
            "title": "jump_to_asset_browserArguments",
            "type": "object"
        }
    },
    {
        "name": "jump_to_tab_by_name",
        "description": "\n"
"        Switch the active workspace tab to *name*.\n"
"\n"
"        Use e.g. \"Main\", \"Modeling\", \"Layout\", \"UV Editing\",\n"
"        \"Geometry Nodes\" or other workspace names (see the\n"
"        ``available_workspaces`` field of the response); matching is\n"
"        case-insensitive.\n"
"        ",
        "inputSchema": {
            "properties": {
                "name": {
                    "title": "Name",
                    "type": "string"
                }
            },
            "required": [
                "name"
            ],
            "title": "jump_to_tab_by_nameArguments",
            "type": "object"
        }
    },
    {
        "name": "jump_to_tab_by_space_type",
        "description": "\n"
"        Switch to a workspace whose main area matches *space_type*.\n"
"\n"
"        If *allow_edits* is True and no matching workspace exists, a new one\n"
"        is created by duplicating the current workspace.\n"
"        ",
        "inputSchema": {
            "properties": {
                "space_type": {
                    "title": "Space Type",
                    "type": "string"
                },
                "allow_edits": {
                    "default": False,
                    "title": "Allow Edits",
                    "type": "boolean"
                }
            },
            "required": [
                "space_type"
            ],
            "title": "jump_to_tab_by_space_typeArguments",
            "type": "object"
        }
    },
    {
        "name": "jump_to_view3d_object_by_name",
        "description": "\n"
"        Move the 3D viewport to focus on an object by *name*.\n"
"\n"
"        If *allow_edits* is True the object may be un-hidden and its\n"
"        collections enabled to make it visible.\n"
"        ",
        "inputSchema": {
            "properties": {
                "name": {
                    "title": "Name",
                    "type": "string"
                },
                "allow_edits": {
                    "default": False,
                    "title": "Allow Edits",
                    "type": "boolean"
                }
            },
            "required": [
                "name"
            ],
            "title": "jump_to_view3d_object_by_nameArguments",
            "type": "object"
        }
    },
    {
        "name": "jump_to_view3d_object_data_by_name",
        "description": "\n"
"        Move the 3D viewport to the object whose data block matches *name*.\n"
"\n"
"        If *allow_edits* is True the object may be un-hidden and its\n"
"        collections enabled to make it visible.\n"
"        ",
        "inputSchema": {
            "properties": {
                "name": {
                    "title": "Name",
                    "type": "string"
                },
                "allow_edits": {
                    "default": False,
                    "title": "Allow Edits",
                    "type": "boolean"
                }
            },
            "required": [
                "name"
            ],
            "title": "jump_to_view3d_object_data_by_nameArguments",
            "type": "object"
        }
    },
    {
        "name": "list_asset_catalogs",
        "description": "\n"
"        List the catalogs of asset libraries: their paths and UUIDs.\n"
"\n"
"        Reads each library's catalog file (blender_assets.cats.txt), the same source that\n"
"        jump_to_asset_browser uses, so a path returned here can be passed to it as catalog_path.\n"
"        Folders on disk are not catalogs. A library without a catalog file answers with an\n"
"        empty list and a message saying so. When the library's metadata index is ready, each\n"
"        catalog also gets its asset count and up to 10 sample asset names, and each library the\n"
"        number of assets without a catalog (the first call starts building the index).\n"
"\n"
"        Args:\n"
"            library_name: Limit to a specific library. Empty = all libraries.\n"
"                          LOCAL = the current file's catalogs, ESSENTIALS = the built-in library.\n"
"\n"
"        Returns:\n"
"            catalogs (path, uuid, library, and assets / counts / asset_names when known),\n"
"            libraries (catalog count and assets without a catalog), asset_counts, message.\n"
"        ",
        "inputSchema": {
            "properties": {
                "library_name": {
                    "default": "",
                    "title": "Library Name",
                    "type": "string"
                }
            },
            "title": "list_asset_catalogsArguments",
            "type": "object"
        }
    },
    {
        "name": "load_asset_in_context",
        "description": "\n"
"        Load an asset from the asset browser into the current context.\n"
"\n"
"        Type-aware loading:\n"
"        - Material: Assigns to active object (replaces slot 0 or appends)\n"
"        - Geometry Node Group: Adds as modifier on active mesh object\n"
"        - Shader Node Group: Adds to active material's node tree\n"
"        - Compositor Node Group: Adds to scene compositor node tree\n"
"        - Collection: Appends/links collection to scene (optionally at a position)\n"
"        - Object: Appends/links object to scene (optionally at a position)\n"
"        - World: Sets as scene world\n"
"        - Action: Assigns to active object's animation data\n"
"\n"
"        Default is APPEND (full independent copy). Use LINK for shared\n"
"        references to source files (e.g. large collections you want to\n"
"        keep in sync).\n"
"\n"
"        Args:\n"
"            library_name: Name of the asset library to load from.\n"
"            asset_name: Name of the asset to load.\n"
"            asset_type: Optional type hint (MATERIAL, NODETREE, COLLECTION, OBJECT, WORLD, ACTION).\n"
"                        Auto-detected if omitted.\n"
"            link_mode: \"APPEND\" (default, full copy) or \"LINK\" (shared reference).\n"
"                        Used as the fallback when ``import_method=\"auto\"`` has\n"
"                        no asset metadata to consult.\n"
"            location: Optional [x, y, z] world position for COLLECTION and OBJECT assets.\n"
"            object_name: Explicit target object for MATERIAL / Geometry-Nodes /\n"
"                ACTION loads (defaults to the active object). Useful when no\n"
"                editor context exists (e.g. background mode).\n"
"            tree_name: Explicit target tree for node-group loads: a material\n"
"                or scene name (defaults to the active context). For shader\n"
"                groups this is a material name (or ShaderNodeTree name); for\n"
"                compositor groups a scene name.\n"
"            import_method: \"auto\" (default) = honour the asset's\n"
"                ``asset_data.preferred_import_method`` when metadata is\n"
"                available, else fall back to ``link_mode``. Explicit\n"
"                \"append\", \"link\", or \"pack\" overrides everything.\n"
"        ",
        "inputSchema": {
            "properties": {
                "library_name": {
                    "title": "Library Name",
                    "type": "string"
                },
                "asset_name": {
                    "title": "Asset Name",
                    "type": "string"
                },
                "asset_type": {
                    "default": "",
                    "title": "Asset Type",
                    "type": "string"
                },
                "link_mode": {
                    "default": "APPEND",
                    "title": "Link Mode",
                    "type": "string"
                },
                "location": {
                    "anyOf": [
                        {
                            "items": {
                                "type": "number"
                            },
                            "type": "array"
                        },
                        {
                            "type": "null"
                        }
                    ],
                    "default": None,
                    "title": "Location"
                },
                "object_name": {
                    "default": "",
                    "title": "Object Name",
                    "type": "string"
                },
                "tree_name": {
                    "default": "",
                    "title": "Tree Name",
                    "type": "string"
                },
                "import_method": {
                    "default": "auto",
                    "title": "Import Method",
                    "type": "string"
                }
            },
            "required": [
                "library_name",
                "asset_name"
            ],
            "title": "load_asset_in_contextArguments",
            "type": "object"
        }
    },
    {
        "name": "load_tools",
        "description": "\n"
"        Make a group of specialised tools available when the task needs them. If a tool\n"
"        you need isn't in your list (rendering, baking, materials, nodes, assets,\n"
"        animation, lighting...), call this first; the tools appear right after.\n"
"\n"
"        Domains and the tools they add:\n"
"        - rendering: render to a file, bake textures, lighting rig, compositor trees\n"
"          (render_viewport_to_path, bake_textures, three_point_lighting_rig,\n"
"          get_screenshot_of_area_as_image, get_node_tree_as_code, build_node_tree_from_code)\n"
"        - material: PBR materials, Polyhaven textures, bake textures, shader trees\n"
"          (setup_pbr_material, assign_material_to_objects, download_polyhaven_asset,\n"
"          search_polyhaven_assets, load_asset_in_context, bake_textures, render_viewport_to_path,\n"
"          get_screenshot_of_area_as_image, get_node_tree_as_code, build_node_tree_from_code)\n"
"        - lighting: lights, HDRIs (three_point_lighting_rig, download_polyhaven_asset,\n"
"          search_polyhaven_assets, render_viewport_to_path, get_screenshot_of_area_as_image)\n"
"        - animation: keyframes (batch_keyframe_insert, jump_to_view3d_object_by_name,\n"
"          jump_to_view3d_object_data_by_name, render_viewport_to_path)\n"
"        - modeling: navigate and organise (jump_to_view3d_object_by_name,\n"
"          jump_to_view3d_object_data_by_name, jump_to_tab_by_name, jump_to_tab_by_space_type,\n"
"          set_collection_color_tag, get_screenshot_of_area_as_image)\n"
"        - geometry_nodes: node trees (get_node_tree_as_code, build_node_tree_from_code,\n"
"          get_active_node_tree, get_node_group_interface, wire_node_group,\n"
"          jump_to_view3d_object_by_name, jump_to_view3d_object_data_by_name,\n"
"          get_screenshot_of_area_as_image)\n"
"        - assets: asset libraries (search_assets, get_asset_libraries, get_asset_tags,\n"
"          list_asset_catalogs, load_asset_in_context, place_asset_in_scene,\n"
"          jump_to_asset_browser, download_polyhaven_asset, search_polyhaven_assets,\n"
"          assign_material_to_objects, get_active_node_tree, get_node_group_interface,\n"
"          wire_node_group)\n"
"        - vse: Video Sequencer strips: list, read, move / trim, preview a frame, speech to text,\n"
"          remove silences, captions, edit by transcript and a vertical 9:16 version with editable\n"
"          framing (get_vse_timeline_overview, get_vse_strips_summary, get_vse_strip_detail,\n"
"          set_strip_frame_range, render_vse_preview, transcribe_strip, remove_silences,\n"
"          add_captions, get_transcript_text, apply_transcript_edit, reframe_vertical,\n"
"          jump_to_tab_by_name, jump_to_tab_by_space_type)\n"
"\n"
"        Load only what the request needs; the basic tools (scene, code, docs,\n"
"        screenshots, plan, questions, bake_textures, render_viewport_to_path,\n"
"        get_job_status) are always there. Called with no domains, it lists them.\n"
"\n"
"        Args:\n"
"            domains: One or more domain names from the list above (none: list them).\n"
"\n"
"        Returns:\n"
"            The domains loaded and the tools they added (name and what it does).\n"
"        ",
        "inputSchema": {
            "properties": {
                "domains": {
                    "anyOf": [
                        {
                            "items": {
                                "type": "string"
                            },
                            "type": "array"
                        },
                        {
                            "type": "null"
                        }
                    ],
                    "default": None,
                    "title": "Domains"
                }
            },
            "title": "load_toolsArguments",
            "type": "object"
        }
    },
    {
        "name": "place_asset_in_scene",
        "description": "\n"
"        Place a COLLECTION or OBJECT asset at an explicit world transform.\n"
"\n"
"        Use this when the user wants the asset at a specific position,\n"
"        rotation, or scale (e.g. \"add the brick wall at x=10 facing the\n"
"        camera\").  For materials, node groups, worlds, or actions use\n"
"        ``load_asset_in_context`` instead.\n"
"\n"
"        ``link_mode`` defaults to ``\"APPEND\"`` (full independent copy,\n"
"        positioned directly).  ``\"LINK\"`` keeps a shared reference: for\n"
"        collections this creates an empty + collection instance at the\n"
"        requested transform instead of moving the source objects.\n"
"\n"
"        Args:\n"
"            library_name: Name of the asset library to load from.\n"
"            asset_name: Name of the asset to place.\n"
"            asset_type: ``\"OBJECT\"`` or ``\"COLLECTION\"`` (auto-detected if omitted).\n"
"            link_mode: ``\"APPEND\"`` (default) or ``\"LINK\"``. Used as the\n"
"                fallback when ``import_method=\"auto\"`` has no asset metadata\n"
"                to consult.\n"
"            location: Optional [x, y, z] world position.\n"
"            rotation: Optional [x, y, z] Euler rotation in **degrees**.\n"
"            scale: Optional [x, y, z] scale factors.\n"
"            import_method: ``\"auto\"`` (default) = honour the asset's\n"
"                ``asset_data.preferred_import_method`` when metadata is\n"
"                available, else fall back to ``link_mode``. Explicit\n"
"                ``\"append\"``, ``\"link\"``, or ``\"pack\"`` overrides.\n"
"\n"
"        Returns:\n"
"            Status, final transform, and how many objects were affected.\n"
"        ",
        "inputSchema": {
            "properties": {
                "library_name": {
                    "title": "Library Name",
                    "type": "string"
                },
                "asset_name": {
                    "title": "Asset Name",
                    "type": "string"
                },
                "asset_type": {
                    "default": "",
                    "title": "Asset Type",
                    "type": "string"
                },
                "link_mode": {
                    "default": "APPEND",
                    "title": "Link Mode",
                    "type": "string"
                },
                "location": {
                    "anyOf": [
                        {
                            "items": {
                                "type": "number"
                            },
                            "type": "array"
                        },
                        {
                            "type": "null"
                        }
                    ],
                    "default": None,
                    "title": "Location"
                },
                "rotation": {
                    "anyOf": [
                        {
                            "items": {
                                "type": "number"
                            },
                            "type": "array"
                        },
                        {
                            "type": "null"
                        }
                    ],
                    "default": None,
                    "title": "Rotation"
                },
                "scale": {
                    "anyOf": [
                        {
                            "items": {
                                "type": "number"
                            },
                            "type": "array"
                        },
                        {
                            "type": "null"
                        }
                    ],
                    "default": None,
                    "title": "Scale"
                },
                "import_method": {
                    "default": "auto",
                    "title": "Import Method",
                    "type": "string"
                }
            },
            "required": [
                "library_name",
                "asset_name"
            ],
            "title": "place_asset_in_sceneArguments",
            "type": "object"
        }
    },
    {
        "name": "read_tool_output",
        "description": "\n"
"        Read more of a long tool result that was shortened.\n"
"\n"
"        A result longer than about 8000 characters arrives as a preview with an\n"
"        ``output_id``. Call this only when the preview isn't enough, and read\n"
"        just the part you need.\n"
"\n"
"        Args:\n"
"            output_id: The ``output_id`` from the shortened result.\n"
"            offset: Character offset to start from (0 = the beginning).\n"
"            limit: Characters to return (at most 6000).\n"
"\n"
"        Returns:\n"
"            ``text``, its ``offset``, ``next_offset`` (None at the end) and ``total_chars``.\n"
"        ",
        "inputSchema": {
            "properties": {
                "output_id": {
                    "title": "Output Id",
                    "type": "string"
                },
                "offset": {
                    "default": 0,
                    "title": "Offset",
                    "type": "integer"
                },
                "limit": {
                    "default": 6000,
                    "title": "Limit",
                    "type": "integer"
                }
            },
            "required": [
                "output_id"
            ],
            "title": "read_tool_outputArguments",
            "type": "object"
        }
    },
    {
        "name": "reframe_vertical",
        "description": "\n"
"        Make the Sequencer's edit vertical (9:16), one undo step.\n"
"\n"
"        Each movie, image and scene strip gets an editable \"Reframe\" compositor\n"
"        modifier (Offset X / Y from -1 to 1 pan inside the picture, Zoom 1 = just\n"
"        covers, Blur Fill on / off, Blur Amount, Background Dim); change a shot's\n"
"        framing afterwards through that modifier's inputs. Strips that already have\n"
"        one keep their framing unless ``reset``.\n"
"\n"
"        Args:\n"
"            strips: Strip names to reframe (empty = every visible movie / image / scene strip).\n"
"            mode: CROP (centre crop, fills the frame) or BLUR_FILL (the whole shot over a\n"
"                blurred, dimmed copy of itself).\n"
"            in_copy: Work in a copy of the scene named \"<scene> Vertical\" and show it in the\n"
"                Sequencer, leaving the 16:9 edit as it is.\n"
"            resolution: Output size, e.g. 1080x1920, 720x1280, 2160x3840.\n"
"            reset: Give strips that already have a Reframe modifier fresh framing too.\n"
"\n"
"        Returns:\n"
"            ``status``, the ``scene`` used (and ``source_scene``, ``copied``), ``mode``,\n"
"            ``resolution``, ``strips_changed``, ``strips_kept`` and a ``message``.\n"
"        ",
        "inputSchema": {
            "properties": {
                "strips": {
                    "anyOf": [
                        {
                            "items": {
                                "type": "string"
                            },
                            "type": "array"
                        },
                        {
                            "type": "null"
                        }
                    ],
                    "default": None,
                    "title": "Strips"
                },
                "mode": {
                    "default": "CROP",
                    "title": "Mode",
                    "type": "string"
                },
                "in_copy": {
                    "default": True,
                    "title": "In Copy",
                    "type": "boolean"
                },
                "resolution": {
                    "default": "1080x1920",
                    "title": "Resolution",
                    "type": "string"
                },
                "reset": {
                    "default": False,
                    "title": "Reset",
                    "type": "boolean"
                }
            },
            "title": "reframe_verticalArguments",
            "type": "object"
        }
    },
    {
        "name": "remove_silences",
        "description": "\n"
"        Cut the quiet parts out of the Sequencer's edit (Remove Silences), one undo step.\n"
"\n"
"        Listens to the named sound strips (a movie strip brings its own sound). With\n"
"        sounds from several files, only the time where all of them are quiet goes. The\n"
"        same cuts go through every strip at that level, selected or not (picture,\n"
"        voice-over, music, titles, captions), so nothing drifts; captions over a cut\n"
"        are trimmed, and one left too short to read is removed. When a locked,\n"
"        retimed, scene or meta strip is in the way nothing changes: ``refused`` says\n"
"        which strip and why. Strips cut in pieces keep their name on the first piece;\n"
"        ``new_names`` lists every piece. A second call with the same settings finds\n"
"        nothing more to cut.\n"
"\n"
"        Args:\n"
"            strips: Sound or movie strips to listen to (empty: the selected ones; nothing\n"
"                selected: every sound strip).\n"
"            threshold_db: Quieter than this (dB below full scale, -120..0) counts as silence.\n"
"            min_silence: Only quiet stretches at least this long (seconds) are cut.\n"
"            padding: Quiet time (seconds) kept before and after the sound, so words aren't clipped.\n"
"            min_keep: What's left between two cuts goes too when shorter than this (seconds).\n"
"            close_gaps: Move everything after each cut left (ripple); off leaves gaps.\n"
"            update_scene_end: When the scene ended at the end of the edit, it ends at the new end\n"
"                (and the preview range).\n"
"\n"
"        Returns:\n"
"            ``status`` (ok / refused / error), ``message``, ``cuts``, ``seconds_removed``,\n"
"            ``cut_ranges`` (frames before the cut), ``new_names`` (original strip -> its pieces;\n"
"            [] when all of it went), ``end_frame`` (the scene's end now), ``content_end``,\n"
"            ``refused`` ([{strip, reason}]), ``problems``, ``captions_trimmed``,\n"
"            ``captions_dropped``.\n"
"        ",
        "inputSchema": {
            "properties": {
                "strips": {
                    "anyOf": [
                        {
                            "items": {
                                "type": "string"
                            },
                            "type": "array"
                        },
                        {
                            "type": "null"
                        }
                    ],
                    "default": None,
                    "title": "Strips"
                },
                "threshold_db": {
                    "default": -40.0,
                    "title": "Threshold Db",
                    "type": "number"
                },
                "min_silence": {
                    "default": 0.4,
                    "title": "Min Silence",
                    "type": "number"
                },
                "padding": {
                    "default": 0.15,
                    "title": "Padding",
                    "type": "number"
                },
                "min_keep": {
                    "default": 0.1,
                    "title": "Min Keep",
                    "type": "number"
                },
                "close_gaps": {
                    "default": True,
                    "title": "Close Gaps",
                    "type": "boolean"
                },
                "update_scene_end": {
                    "default": True,
                    "title": "Update Scene End",
                    "type": "boolean"
                }
            },
            "title": "remove_silencesArguments",
            "type": "object"
        }
    },
    {
        "name": "render_thumbnail_to_path",
        "description": "\n"
"        Render a small, low-quality thumbnail to *output_path* (at most 320 px and 16 samples;\n"
"        the scene's settings are put back when the render is over).\n"
"        ",
        "inputSchema": {
            "properties": {
                "output_path": {
                    "title": "Output Path",
                    "type": "string"
                }
            },
            "required": [
                "output_path"
            ],
            "title": "render_thumbnail_to_pathArguments",
            "type": "object"
        }
    },
    {
        "name": "render_viewport_to_path",
        "description": "\n"
"        Render the scene's camera view to *output_path*.\n"
"\n"
"        ``quality=\"preview\"`` (the default) is for looking and checking your work: at\n"
"        most 1024 px, few samples, a Cycles time limit, Simplify on, no compositing,\n"
"        Sequencer, motion blur or persistent data; the scene's settings are put back\n"
"        afterwards. ``quality=\"viewport\"`` is quicker still: solid shading (OpenGL)\n"
"        from the camera, no materials or lighting. ``quality=\"final\"`` renders with the\n"
"        scene's own settings (full size and samples, can take minutes or hours): use it\n"
"        only when the user asked for the real render.\n"
"\n"
"        A render that takes longer than about 45 s answers with a ``job_id`` instead\n"
"        of the file, and carries on: wait for it with ``get_job_status(job_id)``.\n"
"        For a final render you know is long, pass ``background=True`` to get the\n"
"        ``job_id`` at once.\n"
"\n"
"        Args:\n"
"            output_path: File name for the image (it is written to Coworker's temp folder).\n"
"            background: Start the render and return a ``job_id`` right away.\n"
"            quality: ``\"preview\"`` (default), ``\"viewport\"`` or ``\"final\"``.\n"
"\n"
"        Returns:\n"
"            ``status``; ``filepath`` (where the image is, or will be, written); a\n"
"            ``job_id`` when the render goes on in the background; ``quality`` and\n"
"            ``caps`` (what the preview limited); ``message`` (also on an error: the\n"
"            quality and limits it ran with). ``status`` is ``\"stopped\"`` when the user\n"
"            stopped the render in Blender (``partial``: ``filepath`` holds the unfinished\n"
"            image); don't render again unless they ask.\n"
"        ",
        "inputSchema": {
            "properties": {
                "output_path": {
                    "title": "Output Path",
                    "type": "string"
                },
                "background": {
                    "default": False,
                    "title": "Background",
                    "type": "boolean"
                },
                "quality": {
                    "default": "preview",
                    "title": "Quality",
                    "type": "string"
                }
            },
            "required": [
                "output_path"
            ],
            "title": "render_viewport_to_pathArguments",
            "type": "object"
        }
    },
    {
        "name": "render_vse_preview",
        "description": "\n"
"        Render one frame of the Video Sequencer's output and return it as an image to look at.\n"
"\n"
"        Use it to check an edit: a title's position, what plays at a cut, a colour. The\n"
"        scene's render settings are left as they were; the image is a JPEG in Coworker's\n"
"        temp folder (``filepath``). A scene strip's 3D scene renders with few samples\n"
"        (a preview), so judge framing and timing from it, not render quality.\n"
"\n"
"        Args:\n"
"            frame: The frame to render (default: the current frame).\n"
"            max_size: Longest side of the image in pixels (64-2048, default 1024).\n"
"            scene_name: Another scene (default: the scene the Sequencer shows).\n"
"\n"
"        Returns:\n"
"            The image, and ``status``, ``scene``, ``frame``, ``filepath``, ``width`` /\n"
"            ``height``, ``message`` (a ``job_id`` instead when the render takes long:\n"
"            wait with ``get_job_status``). ``status`` is ``\"stopped\"`` when the user\n"
"            stopped the render in Blender (``partial``: the image is unfinished).\n"
"        ",
        "inputSchema": {
            "properties": {
                "frame": {
                    "anyOf": [
                        {
                            "type": "integer"
                        },
                        {
                            "type": "null"
                        }
                    ],
                    "default": None,
                    "title": "Frame"
                },
                "max_size": {
                    "default": 1024,
                    "title": "Max Size",
                    "type": "integer"
                },
                "scene_name": {
                    "default": "",
                    "title": "Scene Name",
                    "type": "string"
                }
            },
            "title": "render_vse_previewArguments",
            "type": "object"
        }
    },
    {
        "name": "search_api_docs",
        "description": "\n"
"Full-text search over the bundled Blender Python API reference.\n"
"\n"
"Returns a ranked list of hits. Each hit has:\n"
"\n"
"- ``path``: file path relative to the bundled docs.\n"
"- ``text``: the matching paragraph plus ``context``\n"
"  paragraphs on either side.\n"
"- ``breadcrumb``: the section path containing the hit\n"
"  (``Section > Sub-section > ...``).\n"
"- ``index``: the hit's position in the result list.\n"
"- ``score``: a relevance score; higher is better.\n"
"\n"
"The query is tokenised on whitespace and matched\n"
"case-insensitively. Every token must appear somewhere in\n"
"the paragraph body, the file path, or an enclosing section\n"
"title - in any order. Common English stop-words (``the``,\n"
"``a``, ``how``, ``to``, ...) are dropped, so natural\n"
"phrasings like ``\"how to bake\"`` work as expected. Regular\n"
"expressions are not supported.\n"
"\n"
"Use ``context`` to pull more surrounding paragraphs into\n"
"each hit (symmetric, default 0). Use ``index`` with the\n"
"position of a previous hit (same query) to get that hit\n"
"alone with its text widened to its enclosing section.\n"
"\n"
"Read-only; consults bundled RST files only.\n",
        "inputSchema": {
            "properties": {
                "query": {
                    "title": "Query",
                    "type": "string"
                },
                "max_results": {
                    "default": 20,
                    "title": "Max Results",
                    "type": "integer"
                },
                "context": {
                    "default": 0,
                    "title": "Context",
                    "type": "integer"
                },
                "index": {
                    "anyOf": [
                        {
                            "type": "integer"
                        },
                        {
                            "type": "null"
                        }
                    ],
                    "default": None,
                    "title": "Index"
                }
            },
            "required": [
                "query"
            ],
            "title": "search_api_docsArguments",
            "type": "object"
        }
    },
    {
        "name": "search_assets",
        "description": "\n"
"        Search across asset libraries by name/tag/type.\n"
"\n"
"        Args:\n"
"            query: Search term to match against asset names, tags, and descriptions.\n"
"            library_name: Optional library name to search within (empty = all libraries).\n"
"            asset_type: Optional asset type filter (e.g., 'MATERIAL', 'NODETREE', 'OBJECT', 'WORLD').\n"
"\n"
"        Returns top 20 matches with name, type, and source library.\n"
"        ",
        "inputSchema": {
            "properties": {
                "query": {
                    "title": "Query",
                    "type": "string"
                },
                "library_name": {
                    "default": "",
                    "title": "Library Name",
                    "type": "string"
                },
                "asset_type": {
                    "default": "",
                    "title": "Asset Type",
                    "type": "string"
                }
            },
            "required": [
                "query"
            ],
            "title": "search_assetsArguments",
            "type": "object"
        }
    },
    {
        "name": "search_manual_docs",
        "description": "\n"
"Full-text search over the bundled Blender user manual.\n"
"\n"
"Returns a ranked list of hits. Each hit has:\n"
"\n"
"- ``path``: file path relative to the bundled docs.\n"
"- ``text``: the matching paragraph plus ``context``\n"
"  paragraphs on either side.\n"
"- ``breadcrumb``: the section path containing the hit\n"
"  (``Section > Sub-section > ...``).\n"
"- ``index``: the hit's position in the result list.\n"
"- ``score``: a relevance score; higher is better.\n"
"\n"
"The query is tokenised on whitespace and matched\n"
"case-insensitively. Every token must appear somewhere in\n"
"the paragraph body, the file path, or an enclosing section\n"
"title - in any order. Common English stop-words (``the``,\n"
"``a``, ``how``, ``to``, ...) are dropped, so natural\n"
"phrasings like ``\"how to bake\"`` work as expected. Regular\n"
"expressions are not supported.\n"
"\n"
"Use ``context`` to pull more surrounding paragraphs into\n"
"each hit (symmetric, default 0). Use ``index`` with the\n"
"position of a previous hit (same query) to get that hit\n"
"alone with its text widened to its enclosing section.\n"
"\n"
"Read-only; consults bundled RST files only.\n",
        "inputSchema": {
            "properties": {
                "query": {
                    "title": "Query",
                    "type": "string"
                },
                "max_results": {
                    "default": 20,
                    "title": "Max Results",
                    "type": "integer"
                },
                "context": {
                    "default": 0,
                    "title": "Context",
                    "type": "integer"
                },
                "index": {
                    "anyOf": [
                        {
                            "type": "integer"
                        },
                        {
                            "type": "null"
                        }
                    ],
                    "default": None,
                    "title": "Index"
                }
            },
            "required": [
                "query"
            ],
            "title": "search_manual_docsArguments",
            "type": "object"
        }
    },
    {
        "name": "search_polyhaven_assets",
        "description": "\n"
"        Search Poly Haven for free assets (HDRIs, textures, or models).\n"
"\n"
"        Poly Haven provides free, high-quality CC0 assets.  No API key needed.\n"
"\n"
"        Uses smart client-side search with relevance scoring across asset\n"
"        names, tags, categories, and descriptions.\n"
"\n"
"        Args:\n"
"            category: Asset type -- ``\"hdris\"``, ``\"textures\"``, or ``\"models\"``.\n"
"            query: Search term to find matching assets (e.g. ``\"brick wall\"``,\n"
"                ``\"sunset\"``, ``\"wood floor\"``).\n"
"            tags: Comma-separated tags to filter by (e.g. ``\"brick, outdoor\"``).\n"
"                Asset must match at least one tag or category.\n"
"            sort_by: ``\"relevance\"`` (default) for best match, or ``\"popular\"``\n"
"                for most downloaded first.\n"
"\n"
"        Returns:\n"
"            A formatted list of up to 10 matching assets with IDs, names,\n"
"            tags, resolution, and download info.\n"
"        ",
        "inputSchema": {
            "properties": {
                "category": {
                    "default": "textures",
                    "title": "Category",
                    "type": "string"
                },
                "query": {
                    "default": "",
                    "title": "Query",
                    "type": "string"
                },
                "tags": {
                    "default": "",
                    "title": "Tags",
                    "type": "string"
                },
                "sort_by": {
                    "default": "relevance",
                    "title": "Sort By",
                    "type": "string"
                }
            },
            "title": "search_polyhaven_assetsArguments",
            "type": "object"
        }
    },
    {
        "name": "set_collection_color_tag",
        "description": "\n"
"        Set the color tag of a collection in the current scene.\n"
"\n"
"        Args:\n"
"            collection_name: Name of the collection to modify.\n"
"            color: Color tag to set. One of: NONE, COLOR_01, COLOR_02, COLOR_03,\n"
"                   COLOR_04, COLOR_05, COLOR_06, COLOR_07, COLOR_08.\n"
"\n"
"        Returns status and the new color tag value.\n"
"        ",
        "inputSchema": {
            "properties": {
                "collection_name": {
                    "title": "Collection Name",
                    "type": "string"
                },
                "color": {
                    "title": "Color",
                    "type": "string"
                }
            },
            "required": [
                "collection_name",
                "color"
            ],
            "title": "set_collection_color_tagArguments",
            "type": "object"
        }
    },
    {
        "name": "set_strip_frame_range",
        "description": "\n"
"        Move or trim a Video Sequencer strip, or put it on another channel (one undo step).\n"
"\n"
"        \"Move the title strip to frame 50\" is ``start=50``. Give any of start, end, channel:\n"
"        - ``start`` alone moves the whole strip so it begins there (its length is kept);\n"
"        - ``end`` sets the frame after its last one (trims or extends the end);\n"
"        - ``trim=True`` makes ``start`` move only the left handle (the content stays put);\n"
"        - ``channel`` moves it to that channel (1-128).\n"
"        A change that would overlap another strip is refused and names a free channel.\n"
"\n"
"        Args:\n"
"            strip_name: The strip's exact name (get_vse_strips_summary lists them).\n"
"            start: New first frame.\n"
"            end: New end frame (exclusive: a strip on frames 1-10 has end 11).\n"
"            channel: New channel.\n"
"            trim: Trim the start instead of moving the strip.\n"
"            scene_name: Another scene (default: the scene the Sequencer shows).\n"
"\n"
"        Returns:\n"
"            ``status``, ``scene``, ``strip``, ``before`` / ``after`` (start, end, channel),\n"
"            ``undo_step`` (one named step was pushed), ``message``.\n"
"        ",
        "inputSchema": {
            "properties": {
                "strip_name": {
                    "title": "Strip Name",
                    "type": "string"
                },
                "start": {
                    "anyOf": [
                        {
                            "type": "integer"
                        },
                        {
                            "type": "null"
                        }
                    ],
                    "default": None,
                    "title": "Start"
                },
                "end": {
                    "anyOf": [
                        {
                            "type": "integer"
                        },
                        {
                            "type": "null"
                        }
                    ],
                    "default": None,
                    "title": "End"
                },
                "channel": {
                    "anyOf": [
                        {
                            "type": "integer"
                        },
                        {
                            "type": "null"
                        }
                    ],
                    "default": None,
                    "title": "Channel"
                },
                "trim": {
                    "default": False,
                    "title": "Trim",
                    "type": "boolean"
                },
                "scene_name": {
                    "default": "",
                    "title": "Scene Name",
                    "type": "string"
                }
            },
            "required": [
                "strip_name"
            ],
            "title": "set_strip_frame_rangeArguments",
            "type": "object"
        }
    },
    {
        "name": "setup_pbr_material",
        "description": "\n"
"        Create a physically-based material with optional Polyhaven textures.\n"
"\n"
"        **Without Polyhaven** (manual mode):\n"
"        Creates a Principled BSDF material with the given base color,\n"
"        metallic, and roughness values.  Useful for quick material setup\n"
"        without external textures.\n"
"\n"
"        **With Polyhaven** (texture mode):\n"
"        Downloads the full PBR texture set for the given asset from\n"
"        Polyhaven (Diffuse, Normal, Roughness, AO, Displacement) and\n"
"        builds a complete material with all maps connected.\n"
"\n"
"        Args:\n"
"            material_name: Name for the new material datablock.\n"
"            base_color: Comma-separated RGBA string (e.g. ``\"0.9, 0.5, 0.1, 1.0\"``).\n"
"                Used as fallback when no Polyhaven diffuse texture is available.\n"
"            metallic: Metallic value (0.0 - 1.0).  Overridden by ARM texture\n"
"                when using Polyhaven textures.\n"
"            roughness: Roughness value (0.0 - 1.0).  Overridden by Polyhaven\n"
"                roughness/ARM texture when available.\n"
"            use_polyhaven_textures: Set ``True`` to download and apply\n"
"                Polyhaven textures for the given asset.\n"
"            polyhaven_asset_id: Polyhaven asset ID (e.g. ``\"concrete_floor_01\"``).\n"
"                Required when *use_polyhaven_textures* is ``True``.\n"
"            polyhaven_resolution: Download resolution -- ``\"512\"``, ``\"1k\"``,\n"
"                ``\"2k\"``, ``\"4k\"``, or ``\"8k\"``.  Typically injected from\n"
"                addon preferences.\n"
"\n"
"        Returns:\n"
"            A dict with ``status``, ``message``, and ``material_name``.\n"
"        ",
        "inputSchema": {
            "properties": {
                "material_name": {
                    "default": "PBR_Material",
                    "title": "Material Name",
                    "type": "string"
                },
                "base_color": {
                    "default": "0.8, 0.8, 0.8, 1.0",
                    "title": "Base Color",
                    "type": "string"
                },
                "metallic": {
                    "default": 0.0,
                    "title": "Metallic",
                    "type": "number"
                },
                "roughness": {
                    "default": 0.5,
                    "title": "Roughness",
                    "type": "number"
                },
                "use_polyhaven_textures": {
                    "default": False,
                    "title": "Use Polyhaven Textures",
                    "type": "boolean"
                },
                "polyhaven_asset_id": {
                    "default": "",
                    "title": "Polyhaven Asset Id",
                    "type": "string"
                },
                "polyhaven_resolution": {
                    "default": "2k",
                    "title": "Polyhaven Resolution",
                    "type": "string"
                }
            },
            "title": "setup_pbr_materialArguments",
            "type": "object"
        }
    },
    {
        "name": "three_point_lighting_rig",
        "description": "\n"
"        Create a three-point lighting rig (key, fill, rim) targeting an object.\n"
"\n"
"        *target_object* -- name of the object to light (empty string = active object).\n"
"        *distance* -- how far from the target the lights are placed.\n"
"        Colors are comma-separated RGB strings (e.g. \"1.0, 0.95, 0.9\").\n"
"        ",
        "inputSchema": {
            "properties": {
                "target_object": {
                    "default": "",
                    "title": "Target Object",
                    "type": "string"
                },
                "key_energy": {
                    "default": 1000.0,
                    "title": "Key Energy",
                    "type": "number"
                },
                "fill_energy": {
                    "default": 500.0,
                    "title": "Fill Energy",
                    "type": "number"
                },
                "rim_energy": {
                    "default": 800.0,
                    "title": "Rim Energy",
                    "type": "number"
                },
                "key_color": {
                    "default": "1.0, 0.95, 0.9",
                    "title": "Key Color",
                    "type": "string"
                },
                "fill_color": {
                    "default": "0.9, 0.95, 1.0",
                    "title": "Fill Color",
                    "type": "string"
                },
                "rim_color": {
                    "default": "1.0, 1.0, 1.0",
                    "title": "Rim Color",
                    "type": "string"
                },
                "distance": {
                    "default": 5.0,
                    "title": "Distance",
                    "type": "number"
                }
            },
            "title": "three_point_lighting_rigArguments",
            "type": "object"
        }
    },
    {
        "name": "transcribe_strip",
        "description": "\n"
"        The spoken words of a Sequencer sound or movie strip, each with its start and end.\n"
"\n"
"        Transcribes the strip's media file locally with whisper.cpp (or the Remote\n"
"        provider when the user chose that in Preferences > Coworker > Speech) and caches\n"
"        the transcript next to the .blend, so the next call answers at once. A long clip\n"
"        answers with a ``job_id`` instead: wait for it with ``get_job_status(job_id)``,\n"
"        then call this again (it is cached by then).\n"
"\n"
"        Only the words where the strip is visible are returned, a page at a time.\n"
"\n"
"        Args:\n"
"            strip: The strip's name (a SOUND or MOVIE strip).\n"
"            language: Language code such as \"en\" or \"de\", or \"auto\" (default: the Speech setting).\n"
"            limit: Words per answer (default 150, at most 2000; 0 = no word list, the plain text).\n"
"            offset: Skip this many words (use ``next_offset`` from the previous answer).\n"
"            prompt: Names and terms to spell right (e.g. \"Bforartists, Blender\").\n"
"            model: Speech model (tiny, base, small, medium, large-v3-turbo; default: the Speech setting).\n"
"\n"
"        Returns:\n"
"            ``words``: one row per word, ``[w, start, end, frame_start, frame_end]``\n"
"            (``columns``): start / end in seconds of the media file, frames on the\n"
"            timeline. Also ``word_count``, ``next_offset`` when there are more,\n"
"            ``language``, ``model``, ``cached``, ``transcript_path``, ``pieces`` (a long\n"
"            clip is transcribed in pieces of up to 55 s: ``count``, ``no_speech`` pieces\n"
"            that gave no words, ``failed`` [start, end] stretches left without words\n"
"            because whisper kept repeating itself); or ``job_id`` while a long clip is\n"
"            transcribed (``get_job_status`` shows ``progress_step``, \"piece 3 of 26\"); or a\n"
"            ``message`` saying what is missing (e.g. the speech server isn't downloaded).\n"
"        ",
        "inputSchema": {
            "properties": {
                "strip": {
                    "title": "Strip",
                    "type": "string"
                },
                "language": {
                    "default": "",
                    "title": "Language",
                    "type": "string"
                },
                "limit": {
                    "default": 150,
                    "title": "Limit",
                    "type": "integer"
                },
                "offset": {
                    "default": 0,
                    "title": "Offset",
                    "type": "integer"
                },
                "prompt": {
                    "default": "",
                    "title": "Prompt",
                    "type": "string"
                },
                "model": {
                    "default": "",
                    "title": "Model",
                    "type": "string"
                }
            },
            "required": [
                "strip"
            ],
            "title": "transcribe_stripArguments",
            "type": "object"
        }
    },
    {
        "name": "update_plan",
        "description": "\n"
"        Keep a short step plan for multi-step requests (3+ steps); the user sees it in\n"
"        Coworker's Goal & Plan panel and watches it tick off.\n"
"\n"
"        Call once with ``steps`` to set the plan, then with ``done`` (step numbers) as you\n"
"        finish steps.\n"
"\n"
"        Args:\n"
"            steps: Full ordered list of short steps (replaces the plan).\n"
"            done: 1-based numbers of steps that are now finished.\n"
"            current: 1-based number of the step you are working on now.\n"
"\n"
"        Returns:\n"
"            Status and the plan as it stands.\n"
"        ",
        "inputSchema": {
            "properties": {
                "steps": {
                    "anyOf": [
                        {
                            "items": {
                                "type": "string"
                            },
                            "type": "array"
                        },
                        {
                            "type": "null"
                        }
                    ],
                    "default": None,
                    "title": "Steps"
                },
                "done": {
                    "anyOf": [
                        {
                            "items": {
                                "type": "integer"
                            },
                            "type": "array"
                        },
                        {
                            "type": "null"
                        }
                    ],
                    "default": None,
                    "title": "Done"
                },
                "current": {
                    "anyOf": [
                        {
                            "type": "integer"
                        },
                        {
                            "type": "null"
                        }
                    ],
                    "default": None,
                    "title": "Current"
                }
            },
            "title": "update_planArguments",
            "type": "object"
        }
    },
    {
        "name": "wire_node_group",
        "description": "\n"
"        Load a node-group asset and wire it into a target node tree.\n"
"\n"
"        Unlike ``load_asset_in_context`` (which drops the group unconnected\n"
"        at top level), this tool splices the group **into the graph** with\n"
"        validated, undo-able links.\n"
"\n"
"        ``insert_mode``:\n"
"        - ``\"add_top_level\"`` - place the group unconnected near the active\n"
"          node (fallback; inspect the tree with ``get_active_node_tree``\n"
"          first for the other modes).\n"
"        - ``\"replace_active\"`` - wrap ``target_node`` (default: the active\n"
"          node): its incoming links re-route through the group's inputs and\n"
"          its outgoing links through the group's outputs, then the target\n"
"          node is removed.\n"
"        - ``\"insert_between\"`` - splice into the link between\n"
"          ``from_node``/``from_socket`` and ``to_node``/``to_socket``\n"
"          (socket names optional - matched automatically).\n"
"        - ``\"connect_to_output\"`` - attach the group to the tree's output:\n"
"          a SHADER output to Material Output *Surface*, an IMAGE output to\n"
"          Composite *Image*, or a GEOMETRY output to Group Output *Geometry*.\n"
"\n"
"        Socket matching is deterministic: exact socket name first, then\n"
"        fuzzy name, then first unused socket of a compatible type.  Any\n"
"        sockets that cannot be mapped are reported in ``unmapped`` instead\n"
"        of failing silently.  ``bpy.ops.ed.undo_push`` is called before\n"
"        mutating so a bad wire is one undo away.\n"
"\n"
"        Args:\n"
"            library_name: Asset library to load from (empty = group must\n"
"                already exist in ``bpy.data.node_groups``, e.g. loaded by\n"
"                ``load_asset_in_context``).\n"
"            asset_name: Node group asset name.\n"
"            tree_type: ``\"ShaderNodeTree\"`` / ``\"CompositorNodeTree\"`` /\n"
"                ``\"GeometryNodeTree\"`` (empty = the group's own type).\n"
"            node_tree_name: Explicit target tree name; empty = resolve from\n"
"                context (active material / compositor / GN modifier).\n"
"            insert_mode: How to wire the group (see above).\n"
"            target_node: Node name for ``replace_active`` (empty = active).\n"
"            from_node, from_socket, to_node, to_socket: Link endpoints for\n"
"                ``insert_between``.\n"
"            link_mode: ``\"APPEND\"`` (default) or ``\"LINK\"``.\n"
"            auto_map: Enable deterministic interface auto-mapping.\n"
"\n"
"        Returns:\n"
"            Status, the created node name, links created, and any\n"
"            unmapped sockets.\n"
"        ",
        "inputSchema": {
            "properties": {
                "library_name": {
                    "default": "",
                    "title": "Library Name",
                    "type": "string"
                },
                "asset_name": {
                    "default": "",
                    "title": "Asset Name",
                    "type": "string"
                },
                "tree_type": {
                    "default": "",
                    "title": "Tree Type",
                    "type": "string"
                },
                "node_tree_name": {
                    "default": "",
                    "title": "Node Tree Name",
                    "type": "string"
                },
                "insert_mode": {
                    "default": "add_top_level",
                    "title": "Insert Mode",
                    "type": "string"
                },
                "target_node": {
                    "default": "",
                    "title": "Target Node",
                    "type": "string"
                },
                "from_node": {
                    "default": "",
                    "title": "From Node",
                    "type": "string"
                },
                "from_socket": {
                    "default": "",
                    "title": "From Socket",
                    "type": "string"
                },
                "to_node": {
                    "default": "",
                    "title": "To Node",
                    "type": "string"
                },
                "to_socket": {
                    "default": "",
                    "title": "To Socket",
                    "type": "string"
                },
                "link_mode": {
                    "default": "APPEND",
                    "title": "Link Mode",
                    "type": "string"
                },
                "auto_map": {
                    "default": True,
                    "title": "Auto Map",
                    "type": "boolean"
                }
            },
            "title": "wire_node_groupArguments",
            "type": "object"
        }
    }
]
# END: EXPECTED_TOOLS


def _list_tools() -> list[dict[str, object]]:
    """
    Starts the MCP server and returns the full tool listing.
    """

    # Async is required because the MCP client SDK is async-only.
    async def _run() -> list[dict[str, object]]:
        env = os.environ.copy()
        env["PYTHONPATH"] = os.path.join(_REPO_DIR, "mcp")
        params = StdioServerParameters(
            command=sys.executable,
            args=["-m", "blmcp"],
            env=env,
        )
        async with stdio_client(params) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                result = await session.list_tools()
                return [
                    {
                        "name": t.name,
                        "description": t.description,
                        "inputSchema": t.inputSchema,
                    }
                    for t in result.tools
                ]

    return asyncio.run(_run())


class TestToolListing(unittest.TestCase):
    """
    Checks that the live tool listing matches the frozen snapshot.
    """

    _tools: list[dict[str, object]]

    @classmethod
    def setUpClass(cls) -> None:
        cls._tools = _list_tools()

    def test_tools_match_expected(self) -> None:
        """
        Checks that the live tool listing exactly matches ``EXPECTED_TOOLS``.
        """
        self.assertEqual(self._tools, EXPECTED_TOOLS)


def _update_expected_tools() -> None:
    """
    Re-generates the ``EXPECTED_TOOLS`` block from a live server query.
    """
    import json
    import subprocess

    filepath = os.path.abspath(__file__)
    with open(filepath, "r", encoding="utf-8") as fh:
        source = fh.read()
    begin = source.index("# BEGIN: EXPECTED_TOOLS\n") + len("# BEGIN: EXPECTED_TOOLS\n")
    end = source.index("# END: EXPECTED_TOOLS\n")
    formatted = json.dumps(_list_tools(), indent=4)
    formatted = (
        formatted.replace(": true", ": True")
        .replace(": false", ": False")
        .replace(": null", ": None")
    )
    formatted = formatted.replace("\\n", '\\n"\n"')
    # Also handles the `\n"` case (no trailing empty string).
    formatted = formatted.replace('\\n"\n""', '\\n"')
    formatted = "EXPECTED_TOOLS = " + formatted + "\n"
    with open(filepath, "w", encoding="utf-8") as fh:
        fh.write(source[:begin] + formatted + source[end:])
    subprocess.check_call(["autopep8", "--in-place", filepath])


if __name__ == "__main__":
    if "--update" in sys.argv:
        sys.argv.remove("--update")
        _update_expected_tools()
    else:
        unittest.main()
