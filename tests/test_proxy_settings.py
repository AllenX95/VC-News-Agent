from __future__ import annotations

import os
import unittest
from unittest.mock import patch

from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from ai_agent.api_v1 import update_settings
from ai_agent.config import apply_network_proxy_settings
from ai_agent.database import Base
from ai_agent.services import configured_proxy_settings


class ProxySettingsTests(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(self.engine)
        self.db = Session(self.engine)

    def tearDown(self) -> None:
        self.db.close()
        self.engine.dispose()

    def test_proxy_defaults_to_off_and_clears_proxy_environment(self) -> None:
        with patch.dict(
            os.environ,
            {
                "HTTP_PROXY": "http://127.0.0.1:7890",
                "HTTPS_PROXY": "http://127.0.0.1:7890",
                "ALL_PROXY": "http://127.0.0.1:7890",
                "NO_PROXY": "localhost",
            },
            clear=True,
        ):
            info = apply_network_proxy_settings("off")

            self.assertEqual(info["mode"], "off")
            self.assertEqual(info["source"], "disabled")
            self.assertNotIn("HTTP_PROXY", os.environ)
            self.assertNotIn("HTTPS_PROXY", os.environ)
            self.assertNotIn("ALL_PROXY", os.environ)
            self.assertNotIn("NO_PROXY", os.environ)

    def test_custom_proxy_sets_requests_environment(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            info = apply_network_proxy_settings("custom", "127.0.0.1:7890", "example.com")

            self.assertEqual(info["mode"], "custom")
            self.assertEqual(info["source"], "custom")
            self.assertEqual(os.environ["HTTP_PROXY"], "http://127.0.0.1:7890")
            self.assertEqual(os.environ["HTTPS_PROXY"], "http://127.0.0.1:7890")
            self.assertIn("example.com", os.environ["NO_PROXY"])

    def test_settings_api_persists_and_applies_custom_proxy(self) -> None:
        with patch.dict(os.environ, {}, clear=True), patch("ai_agent.services.app_scheduler.refresh"):
            result = update_settings(
                {
                    "network_proxy_mode": "custom",
                    "network_proxy_url": "127.0.0.1:7890",
                    "network_proxy_no_proxy": "example.com",
                },
                self.db,
            )

            saved = configured_proxy_settings(self.db)
            self.assertEqual(saved["mode"], "custom")
            self.assertEqual(saved["url"], "127.0.0.1:7890")
            self.assertEqual(result["proxy_info"]["source"], "custom")
            self.assertEqual(os.environ["HTTP_PROXY"], "http://127.0.0.1:7890")

    def test_custom_proxy_requires_url(self) -> None:
        with patch("ai_agent.services.app_scheduler.refresh"):
            with self.assertRaises(HTTPException) as context:
                update_settings({"network_proxy_mode": "custom", "network_proxy_url": ""}, self.db)

            self.assertEqual(context.exception.status_code, 400)


if __name__ == "__main__":
    unittest.main()
