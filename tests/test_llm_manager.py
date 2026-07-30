# SPDX-FileCopyrightText: 2026 Blender Authors
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Unit tests for local LLM manager helper functions."""

import importlib.util
import tempfile
import unittest
from pathlib import Path


def load_llm_manager_module() -> object:
    repo_root = Path(__file__).resolve().parents[1]
    module_path = repo_root / "addon" / "bfa_coworker" / "llm_manager.py"
    spec = importlib.util.spec_from_file_location("llm_manager", module_path)
    module = importlib.util.module_from_spec(spec)
    assert spec is not None and spec.loader is not None
    spec.loader.exec_module(module)
    return module


class TestLLMManager(unittest.TestCase):
    def setUp(self) -> None:
        self.llm_manager = load_llm_manager_module()
        self._original_config = self.llm_manager.get_config()

    def tearDown(self) -> None:
        self.llm_manager.set_config(self._original_config)

    def test_get_models_dir_creates_custom_dir_if_missing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            custom_dir = Path(tmp_dir) / "my_models"
            cfg = self.llm_manager.get_config()
            cfg.downloaded_models_dir = str(custom_dir)
            self.llm_manager.set_config(cfg)

            result = self.llm_manager._get_models_dir()

            self.assertEqual(result, custom_dir)
            self.assertTrue(custom_dir.is_dir())
