# Copyright (C) 2025-2026 Retio AI
# SPDX-License-Identifier: AGPL-3.0-only

"""Tests for S9 SPA framework detection."""

from __future__ import annotations

from pagemap.diagnostics import SpaFramework
from pagemap.diagnostics.spa_loader import parse_spa_signals


class TestFrameworkDetection:
    def test_react(self):
        signals = {
            "react": True,
            "nextjs": False,
            "vue": False,
            "nuxt": False,
            "angular": False,
            "svelte": False,
            "skeletonCount": 0,
            "contentLength": 5000,
        }
        result = parse_spa_signals(signals)
        assert result is not None
        assert result.framework == SpaFramework.REACT
        assert result.hydrated is True

    def test_nextjs(self):
        signals = {
            "react": True,
            "nextjs": True,
            "vue": False,
            "nuxt": False,
            "angular": False,
            "svelte": False,
            "skeletonCount": 0,
            "contentLength": 3000,
        }
        result = parse_spa_signals(signals)
        assert result is not None
        assert result.framework == SpaFramework.NEXTJS  # NEXTJS takes priority over REACT
        assert result.confidence >= 0.80

    def test_vue(self):
        signals = {
            "react": False,
            "nextjs": False,
            "vue": True,
            "nuxt": False,
            "angular": False,
            "svelte": False,
            "skeletonCount": 0,
            "contentLength": 2000,
        }
        result = parse_spa_signals(signals)
        assert result is not None
        assert result.framework == SpaFramework.VUE

    def test_angular(self):
        signals = {
            "react": False,
            "nextjs": False,
            "vue": False,
            "nuxt": False,
            "angular": True,
            "svelte": False,
            "skeletonCount": 0,
            "contentLength": 1500,
        }
        result = parse_spa_signals(signals)
        assert result is not None
        assert result.framework == SpaFramework.ANGULAR

    def test_svelte(self):
        signals = {
            "react": False,
            "nextjs": False,
            "vue": False,
            "nuxt": False,
            "angular": False,
            "svelte": True,
            "skeletonCount": 0,
            "contentLength": 1000,
        }
        result = parse_spa_signals(signals)
        assert result is not None
        assert result.framework == SpaFramework.SVELTE


class TestHydration:
    def test_not_hydrated(self):
        """Skeleton present + very short content = not hydrated."""
        signals = {
            "react": True,
            "nextjs": False,
            "vue": False,
            "nuxt": False,
            "angular": False,
            "svelte": False,
            "skeletonCount": 3,
            "contentLength": 50,
        }
        result = parse_spa_signals(signals)
        assert result is not None
        assert result.hydrated is False
        assert result.has_skeleton is True

    def test_hydrated_with_skeleton(self):
        """Skeleton present but content is long = hydrated."""
        signals = {
            "react": True,
            "nextjs": False,
            "vue": False,
            "nuxt": False,
            "angular": False,
            "svelte": False,
            "skeletonCount": 2,
            "contentLength": 5000,
        }
        result = parse_spa_signals(signals)
        assert result is not None
        assert result.hydrated is True
        assert result.has_skeleton is True


class TestNoDetection:
    def test_no_spa(self):
        signals = {
            "react": False,
            "nextjs": False,
            "vue": False,
            "nuxt": False,
            "angular": False,
            "svelte": False,
            "skeletonCount": 0,
            "contentLength": 5000,
        }
        result = parse_spa_signals(signals)
        assert result is None

    def test_none_input(self):
        result = parse_spa_signals(None)
        assert result is None

    def test_empty_dict(self):
        result = parse_spa_signals({})
        assert result is None
