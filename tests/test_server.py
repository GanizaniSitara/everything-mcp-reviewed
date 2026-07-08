"""Tests for everything_mcp.server tool functions and helpers."""

from __future__ import annotations

import json

import pytest

from everything_mcp.backend import SearchResult
from everything_mcp.server import (
    _format_search_results,
    _get_file_details_sync,
)

# ── _format_search_results ────────────────────────────────────────────────


class TestFormatSearchResults:
    def test_empty_results(self):
        result = _format_search_results([], "*.py", max_results=50)
        assert "No results found" in result
        assert "*.py" in result

    def test_with_results(self, sample_results):
        result = _format_search_results(sample_results, "*.py", max_results=50)
        assert "Found 3 results" in result
        assert r"C:\Projects\app\main.py" in result
        assert r"C:\Projects\app\utils.py" in result
        assert "[FILE]" in result
        assert "[DIR]" in result

    def test_file_metadata_shown(self, sample_results):
        result = _format_search_results(sample_results, "*.py", max_results=50)
        assert "2.0 KB" in result
        assert "2026-01-15" in result

    def test_offset_shown(self, sample_results):
        result = _format_search_results(sample_results, "*.py", max_results=50, offset=100)
        assert "offset: 100" in result

    def test_pagination_hint_when_at_limit(self, sample_results):
        # When results == max_results, show pagination hint
        result = _format_search_results(sample_results, "*.py", max_results=3)
        assert "offset" in result.lower()

    def test_no_pagination_hint_when_under_limit(self, sample_results):
        result = _format_search_results(sample_results, "*.py", max_results=100)
        assert "Showing first" not in result

    def test_folder_no_size(self, sample_results):
        result = _format_search_results(sample_results, "*", max_results=50)
        # The directory "src" line should not have a size
        lines = result.split("\n")
        src_line = [line for line in lines if "src" in line and "[DIR]" in line]
        assert len(src_line) == 1
        # Should not show "unknown" or negative size
        assert "unknown" not in src_line[0]


# ── Sort validation ────────────────────────────────────────────────────────


class TestSortValidation:
    """Test that invalid sort options are rejected."""

    def test_valid_sort_accepted(self):
        from everything_mcp.server import SearchInput

        params = SearchInput(query="*.py", sort="date-modified-desc")
        assert params.sort == "date-modified-desc"

    def test_invalid_sort_rejected(self):
        from pydantic import ValidationError

        from everything_mcp.server import SearchInput

        with pytest.raises(ValidationError, match="Invalid sort option"):
            SearchInput(query="*.py", sort="invalid-sort")

    def test_all_sort_options_valid(self):
        from everything_mcp.backend import SORT_MAP
        from everything_mcp.server import SearchInput

        for sort_key in SORT_MAP:
            params = SearchInput(query="test", sort=sort_key)
            assert params.sort == sort_key


# ── Tool error handling ───────────────────────────────────────────────────


class TestToolSuccessPaths:
    def test_file_details_does_not_preview_file_content(self, tmp_path):
        f = tmp_path / "secret.txt"
        f.write_text("do-not-return-this\n")

        result = _get_file_details_sync([str(f)], preview_lines=10)
        data = json.loads(result)

        assert data["type"] == "file"
        assert "preview" not in data
        assert data["preview_note"] == "Content preview disabled in this reviewed local fork"
        assert "do-not-return-this" not in result

    def test_file_details_directory_summary(self, tmp_path):
        (tmp_path / "sub").mkdir()
        (tmp_path / "a.txt").write_text("alpha")
        (tmp_path / "b.py").write_text("print('ok')\n")

        result = _get_file_details_sync([str(tmp_path)], preview_lines=0)
        data = json.loads(result)

        assert data["type"] == "folder"
        assert data["item_count"] >= 3
        assert isinstance(data["subdirectories"], list)
        assert isinstance(data["files_sample"], list)

    @pytest.mark.asyncio
    async def test_count_stats_breakdown_excludes_directories(self):
        from everything_mcp import server
        from everything_mcp.config import EverythingConfig
        from everything_mcp.server import CountStatsInput, everything_count_stats

        class FakeBackend:
            async def count(self, query: str) -> int:
                return 3

            async def get_total_size(self, query: str) -> int:
                return 1300

            async def search(self, query: str, max_results: int, sort: str):
                return [
                    SearchResult(path=r"C:\repo\a.py", name="a.py", size=1200, extension="py"),
                    SearchResult(path=r"C:\repo\README", name="README", size=100, extension=""),
                    SearchResult(path=r"C:\repo\src", name="src", is_dir=True),
                ]

        old_backend = server._backend
        old_config = server._config
        try:
            server._backend = FakeBackend()
            server._config = EverythingConfig(es_path=r"C:\Program Files\Everything\es.exe")

            result = await everything_count_stats(
                CountStatsInput(query="path:C:\\repo", breakdown_by_extension=True)
            )
            data = json.loads(result)

            assert data["total_count"] == 3
            assert data["total_size"] == 1300
            assert "py" in data["extension_breakdown"]
            assert "(no extension)" in data["extension_breakdown"]
            assert "directories excluded" in data["breakdown_note"]
        finally:
            server._backend = old_backend
            server._config = old_config


class TestToolErrorHandling:
    """Verify that tools return error strings rather than raising."""

    @pytest.mark.asyncio
    async def test_search_returns_error_string(self):
        """When backend is unavailable, search returns an error string."""
        from everything_mcp import server

        # Temporarily set invalid state
        old_backend = server._backend
        old_config = server._config
        try:
            server._backend = None
            server._config = None

            from everything_mcp.server import SearchInput, everything_search

            params = SearchInput(query="*.py")
            result = await everything_search(params)
            assert isinstance(result, str)
            assert "Error" in result
        finally:
            server._backend = old_backend
            server._config = old_config

    @pytest.mark.asyncio
    async def test_count_stats_returns_error_string(self):
        from everything_mcp import server

        old_backend = server._backend
        old_config = server._config
        try:
            server._backend = None
            server._config = None

            from everything_mcp.server import CountStatsInput, everything_count_stats

            params = CountStatsInput(query="*.py")
            result = await everything_count_stats(params)
            assert isinstance(result, str)
            assert "Error" in result
        finally:
            server._backend = old_backend
            server._config = old_config
