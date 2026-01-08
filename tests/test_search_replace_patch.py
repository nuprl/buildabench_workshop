# SPDX-License-Identifier: BSD-3-Clause
# Copyright (c) 2025 Arjun Guha
"""
Tests for SearchReplacePatch class.
"""

import pytest
from pathlib import Path
import tempfile
import shutil
from buildabench_workshop.search_replace_patch import SearchReplacePatch


class TestFromString:
    """Tests for SearchReplacePatch.from_string()"""
    
    def test_simple_patch(self):
        """Test parsing a simple single-file patch."""
        patch_content = """### test.py
<<<<<<< SEARCH
old code
=======
new code
>>>>>>> REPLACE
"""
        patch = SearchReplacePatch.from_string(patch_content)
        assert patch is not None
        assert patch.patches == {"test.py": [("old code\n", "new code\n")]}
    
    def test_multiple_patches_same_file(self):
        """Test parsing multiple patches for the same file."""
        patch_content = """### test.py
<<<<<<< SEARCH
first old
=======
first new
>>>>>>> REPLACE

### test.py
<<<<<<< SEARCH
second old
=======
second new
>>>>>>> REPLACE
"""
        patch = SearchReplacePatch.from_string(patch_content)
        assert patch is not None
        assert patch.patches == {
            "test.py": [
                ("first old\n", "first new\n"),
                ("second old\n", "second new\n")
            ]
        }
    
    def test_multiple_files(self):
        """Test parsing patches for multiple files."""
        patch_content = """### file1.py
<<<<<<< SEARCH
code1
=======
code1_new
>>>>>>> REPLACE

### file2.py
<<<<<<< SEARCH
code2
=======
code2_new
>>>>>>> REPLACE
"""
        patch = SearchReplacePatch.from_string(patch_content)
        assert patch is not None
        assert "file1.py" in patch.patches
        assert "file2.py" in patch.patches
        assert patch.patches["file1.py"] == [("code1\n", "code1_new\n")]
        assert patch.patches["file2.py"] == [("code2\n", "code2_new\n")]
    
    def test_noop_patch_filtered(self):
        """Test that no-op patches (old == new) are filtered out."""
        patch_content = """### test.py
<<<<<<< SEARCH
same code
=======
same code
>>>>>>> REPLACE
"""
        patch = SearchReplacePatch.from_string(patch_content)
        assert patch is None
    
    def test_noop_patch_with_valid_patch(self):
        """Test that no-op patches are filtered but valid patches remain."""
        patch_content = """### test.py
<<<<<<< SEARCH
same code
=======
same code
>>>>>>> REPLACE

### test.py
<<<<<<< SEARCH
old code
=======
new code
>>>>>>> REPLACE
"""
        patch = SearchReplacePatch.from_string(patch_content)
        assert patch is not None
        assert patch.patches == {"test.py": [("old code\n", "new code\n")]}
    
    def test_empty_patch(self):
        """Test parsing empty patch content."""
        patch = SearchReplacePatch.from_string("")
        assert patch is None
    
    def test_no_valid_patches(self):
        """Test parsing content with no valid patches."""
        patch = SearchReplacePatch.from_string("some random text")
        assert patch is None
    
    def test_missing_file_path(self):
        """Test patch with SEARCH marker but no file path."""
        patch_content = """<<<<<<< SEARCH
code
=======
new code
>>>>>>> REPLACE
"""
        patch = SearchReplacePatch.from_string(patch_content)
        assert patch is None
    
    def test_missing_divider(self):
        """Test patch with SEARCH but no divider."""
        patch_content = """### test.py
<<<<<<< SEARCH
code
"""
        patch = SearchReplacePatch.from_string(patch_content)
        assert patch is None
    
    def test_missing_replace_marker(self):
        """Test patch with divider but no REPLACE marker."""
        patch_content = """### test.py
<<<<<<< SEARCH
code
=======
new code
"""
        patch = SearchReplacePatch.from_string(patch_content)
        assert patch is None
    
    def test_preserves_newlines(self):
        """Test that newlines are preserved in patch content."""
        patch_content = """### test.py
<<<<<<< SEARCH
line1
line2
=======
new_line1
new_line2
>>>>>>> REPLACE
"""
        patch = SearchReplacePatch.from_string(patch_content)
        assert patch is not None
        old_text, new_text = patch.patches["test.py"][0]
        assert old_text == "line1\nline2\n"
        assert new_text == "new_line1\nnew_line2\n"
    
    def test_preserves_whitespace(self):
        """Test that whitespace is preserved."""
        patch_content = """### test.py
<<<<<<< SEARCH
    indented code
=======
    new indented code
>>>>>>> REPLACE
"""
        patch = SearchReplacePatch.from_string(patch_content)
        assert patch is not None
        old_text, new_text = patch.patches["test.py"][0]
        assert old_text == "    indented code\n"
        assert new_text == "    new indented code\n"
    
    def test_file_path_with_spaces(self):
        """Test file path with spaces."""
        patch_content = """### path/to/file.py
<<<<<<< SEARCH
code
=======
new code
>>>>>>> REPLACE
"""
        patch = SearchReplacePatch.from_string(patch_content)
        assert patch is not None
        assert "path/to/file.py" in patch.patches


class TestRender:
    """Tests for SearchReplacePatch.render()"""
    
    def test_simple_render(self):
        """Test rendering a simple patch."""
        patch = SearchReplacePatch({"test.py": [("old\n", "new\n")]})
        rendered = patch.render()
        expected = """### test.py
<<<<<<< SEARCH
old
=======
new
>>>>>>> REPLACE

"""
        assert rendered == expected


class TestApply:
    """Tests for SearchReplacePatch.apply()"""
    
    def test_simple_apply(self):
        """Test applying a simple patch successfully."""
        with tempfile.TemporaryDirectory() as tmpdir:
            repo_dir = Path(tmpdir)
            test_file = repo_dir / "test.py"
            test_file.write_text("old code\n")
            
            patch = SearchReplacePatch({"test.py": [("old code\n", "new code\n")]})
            success = patch.apply(repo_dir, dry_run=False)
            
            assert success is True
            assert test_file.read_text() == "new code\n"
    
    def test_dry_run(self):
        """Test that dry_run doesn't modify files."""
        with tempfile.TemporaryDirectory() as tmpdir:
            repo_dir = Path(tmpdir)
            test_file = repo_dir / "test.py"
            test_file.write_text("old code\n")
            
            patch = SearchReplacePatch({"test.py": [("old code\n", "new code\n")]})
            success = patch.apply(repo_dir, dry_run=True)
            
            assert success is True
            # File should be unchanged
            assert test_file.read_text() == "old code\n"
    
    def test_multiple_patches_same_file(self):
        """Test applying multiple patches to the same file sequentially."""
        with tempfile.TemporaryDirectory() as tmpdir:
            repo_dir = Path(tmpdir)
            test_file = repo_dir / "test.py"
            test_file.write_text("first\nsecond\n")
            
            patch = SearchReplacePatch({
                "test.py": [
                    ("first\n", "FIRST\n"),
                    ("second\n", "SECOND\n")
                ]
            })
            success = patch.apply(repo_dir, dry_run=False)
            
            assert success is True
            assert test_file.read_text() == "FIRST\nSECOND\n"
    
  
    
    def test_file_not_found(self):
        """Test applying patch when file doesn't exist."""
        with tempfile.TemporaryDirectory() as tmpdir:
            repo_dir = Path(tmpdir)
            
            patch = SearchReplacePatch({"nonexistent.py": [("old\n", "new\n")]})
            success = patch.apply(repo_dir, dry_run=False)
            
            assert success is False
    
    def test_search_text_not_found(self):
        """Test applying patch when search text is not in file."""
        with tempfile.TemporaryDirectory() as tmpdir:
            repo_dir = Path(tmpdir)
            test_file = repo_dir / "test.py"
            test_file.write_text("different code\n")
            
            patch = SearchReplacePatch({"test.py": [("old code\n", "new code\n")]})
            success = patch.apply(repo_dir, dry_run=False)
            
            assert success is False
            # File should be unchanged
            assert test_file.read_text() == "different code\n"
  
    
    def test_replace_only_first_occurrence(self):
        """Test that only the first occurrence is replaced."""
        with tempfile.TemporaryDirectory() as tmpdir:
            repo_dir = Path(tmpdir)
            test_file = repo_dir / "test.py"
            test_file.write_text("old\nold\nold\n")
            
            patch = SearchReplacePatch({"test.py": [("old\n", "new\n")]})
            success = patch.apply(repo_dir, dry_run=False)
            
            assert success is True
            assert test_file.read_text() == "new\nold\nold\n"
    

    def test_empty_search_string_filtered(self):
        """Test that patches with empty search strings are filtered out during parsing."""
        patch_content = "### test.py\n<<<<<<< SEARCH\n=======\nnew content\n>>>>>>> REPLACE"
        patch = SearchReplacePatch.from_string(patch_content)
        assert patch is None or patch.patches == {}
    
    def test_file_read_error(self):
        """Test handling file read errors gracefully."""
        with tempfile.TemporaryDirectory() as tmpdir:
            repo_dir = Path(tmpdir)
            test_file = repo_dir / "test.py"
            test_file.write_text("old code\n")
            # Make file unreadable (on Unix)
            test_file.chmod(0o000)
            
            try:
                patch = SearchReplacePatch({"test.py": [("old code\n", "new code\n")]})
                success = patch.apply(repo_dir, dry_run=False)
                assert success is False
            finally:
                # Restore permissions for cleanup
                test_file.chmod(0o644)
    
    def test_file_write_error(self):
        """Test handling file write errors gracefully."""
        with tempfile.TemporaryDirectory() as tmpdir:
            repo_dir = Path(tmpdir)
            test_file = repo_dir / "test.py"
            test_file.write_text("old code\n")
            
            patch = SearchReplacePatch({"test.py": [("old code\n", "new code\n")]})
            # Make file read-only to cause write failure
            test_file.chmod(0o444)
            
            try:
                success = patch.apply(repo_dir, dry_run=False)
                assert success is False
            finally:
                # Restore permissions for cleanup
                test_file.chmod(0o644)
