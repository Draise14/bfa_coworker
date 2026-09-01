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
"            resolution: Download resolution \u2014 ``\"512\"``, ``\"1k\"``, ``\"2k\"``,\n"
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
"        The code runs in Blender's Python environment with full access to ``bpy``.\n"
"        To return data, assign a JSON-serialisable dict to a variable named ``result``.\n"
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
"        - ``\"CompositorNodeTree\"`` - the scene's compositor tree\n"
"          (enables ``use_nodes`` if needed).\n"
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
"            Dict with tags, editor_type (for node groups), color_tag, and other metadata.\n"
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
"            A status message indicating API availability and asset counts.\n"
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
"        ",
        "inputSchema": {
            "properties": {},
            "title": "get_screenshot_of_window_as_jsonArguments",
            "type": "object"
        }
    },
    {
        "name": "jump_to_asset_browser",
        "description": "\n"
"        Switch to (or create) the Asset Browser editor.\n"
"\n"
"        If an Asset Browser area is already open in any workspace it is\n"
"        reused.  Otherwise, with *allow_edits* (default), a new workspace\n"
"        is created by duplicating the current one and converting its main\n"
"        area into the Asset Browser - so the user's current workspace is\n"
"        left untouched.\n"
"\n"
"        *library_name* optionally preselects the asset library shown in the\n"
"        browser (best-effort: if the name matches a configured library or a\n"
"        built-in reference such as ``\"LOCAL\"`` / ``\"USER\"`` it is applied).\n"
"        *catalog_path* optionally selects a catalog/folder, best-effort.\n"
"\n"
"        Args:\n"
"            library_name: Asset library to select (empty = leave as-is).\n"
"            catalog_path: Catalog path or catalog UUID to select (empty = leave as-is).\n"
"            allow_edits: Allow creating a new workspace/area when no Asset\n"
"                Browser is open.\n"
"\n"
"        Returns:\n"
"            Status, workspace/area created or reused, and library/catalog applied.\n"
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
"        List the catalog/directory structure of asset libraries.\n"
"\n"
"        Shows how assets are organized into folders within each library,\n"
"        with counts of materials, node groups, objects, worlds, and actions\n"
"        in each directory.\n"
"\n"
"        Args:\n"
"            library_name: Limit to a specific library. Empty = all libraries.\n"
"\n"
"        Returns:\n"
"            Catalogs with paths and asset counts per directory.\n"
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
        "name": "render_thumbnail_to_path",
        "description": "\n"
"        Render a small, low-quality thumbnail to *output_path* (temporarily overrides settings).\n"
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
"        Render the current scene to *output_path* using current render settings.\n"
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
            "title": "render_viewport_to_pathArguments",
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
"            category: Asset type \u2014 ``\"hdris\"``, ``\"textures\"``, or ``\"models\"``.\n"
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
"            polyhaven_resolution: Download resolution \u2014 ``\"512\"``, ``\"1k\"``,\n"
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
"        *target_object* \u2014 name of the object to light (empty string = active object).\n"
"        *distance* \u2014 how far from the target the lights are placed.\n"
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
