"""Tests for the authenticated cross-repo dedup request in weekly_pipeline."""

from __future__ import annotations

import os
import unittest
from unittest import mock

from workflows.weekly_pipeline import _github_api_request


class GithubApiRequestTests(unittest.TestCase):
    URL = "https://api.github.com/repos/example/repo/contents/articles"

    def test_sends_bearer_token_when_github_token_set(self):
        with mock.patch.dict(os.environ, {"GITHUB_TOKEN": "test-token-123"}):
            request = _github_api_request(self.URL)
        self.assertEqual(request.get_header("Authorization"), "Bearer test-token-123")

    def test_omits_auth_header_without_token(self):
        env = {k: v for k, v in os.environ.items() if k != "GITHUB_TOKEN"}
        with mock.patch.dict(os.environ, env, clear=True):
            request = _github_api_request(self.URL)
        self.assertIsNone(request.get_header("Authorization"))

    def test_keeps_api_headers(self):
        with mock.patch.dict(os.environ, {"GITHUB_TOKEN": "t"}):
            request = _github_api_request(self.URL)
        self.assertEqual(request.get_header("Accept"), "application/vnd.github.v3+json")
        self.assertEqual(request.get_header("User-agent"), "ios-dev-ai-writer")


if __name__ == "__main__":
    unittest.main()
