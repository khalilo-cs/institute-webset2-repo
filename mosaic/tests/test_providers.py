import os
import unittest
from unittest.mock import patch

from mosaic.providers import ProviderUnavailable, chat_completion


class ProviderRouterTests(unittest.TestCase):
    @patch("mosaic.providers._request", return_value="local answer")
    def test_local_provider_is_preferred(self, request):
        with patch.dict(os.environ, {
            "MOSAIC_LOCAL_BASE_URL": "http://127.0.0.1:11434/v1",
            "MOSAIC_LOCAL_MODEL": "test-local",
            "MOSAIC_CLOUD_BASE_URL": "https://cloud.example/v1",
            "MOSAIC_CLOUD_API_KEY": "not-a-real-key",
            "MOSAIC_CLOUD_MODEL": "test-cloud",
        }):
            answer, provider = chat_completion([{"role": "user", "content": "hello"}])
        self.assertEqual(answer, "local answer")
        self.assertEqual(provider, "local:test-local")
        self.assertEqual(request.call_count, 1)

    @patch("mosaic.providers._request", return_value="cloud answer")
    def test_private_chat_never_uses_cloud(self, request):
        with patch.dict(os.environ, {
            "MOSAIC_LOCAL_BASE_URL": "",
            "MOSAIC_CLOUD_BASE_URL": "https://cloud.example/v1",
            "MOSAIC_CLOUD_API_KEY": "not-a-real-key",
            "MOSAIC_CLOUD_MODEL": "test-cloud",
        }):
            with self.assertRaises(ProviderUnavailable):
                chat_completion([{"role": "user", "content": "secret"}], private=True)
        request.assert_not_called()


if __name__ == "__main__":
    unittest.main()
