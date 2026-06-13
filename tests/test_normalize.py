"""Unit tests for normalize_folder_path (REQ-002, REQ-063, REQ-064, REQ-066)."""
import sys
import os
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))
from functions import normalize_folder_path


class TestNormalizeFolderPath:
    def test_unix_path(self):
        assert normalize_folder_path('/home/user/projects/myapp') == 'home/user/projects/myapp'

    def test_windows_path(self):
        result = normalize_folder_path('C:\\Users\\foo\\projects\\myapp')
        assert '\\' not in result
        assert 'Users' in result or 'users' in result.lower()

    def test_trailing_slash_stripped(self):
        result = normalize_folder_path('/home/user/projects/')
        assert not result.endswith('/')

    def test_leading_slash_stripped(self):
        result = normalize_folder_path('/home/user/projects')
        assert not result.startswith('/')

    def test_double_slashes_collapsed(self):
        result = normalize_folder_path('/home//user///projects')
        assert '//' not in result

    def test_empty_string_raises(self):
        with pytest.raises(ValueError):
            normalize_folder_path('')

    def test_path_traversal_raises(self):
        with pytest.raises(ValueError):
            normalize_folder_path('/home/user/../../etc/passwd')

    def test_length_cap(self):
        long_path = 'a/' * 300
        result = normalize_folder_path(long_path)
        assert len(result) <= 512

    def test_idempotent(self):
        path = '/home/user/projects/myapp'
        once = normalize_folder_path(path)
        twice = normalize_folder_path(once)
        assert once == twice

    def test_relative_path(self):
        result = normalize_folder_path('projects/myapp/src')
        assert result == 'projects/myapp/src'

    def test_windows_forward_slashes(self):
        result = normalize_folder_path('C:/Users/foo/projects')
        assert '\\' not in result

    def test_single_segment(self):
        result = normalize_folder_path('/workspace')
        assert result == 'workspace'
