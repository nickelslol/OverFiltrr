import importlib
import os
import sys
import unittest
from unittest.mock import MagicMock, mock_open, patch

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
import overfiltrr


class TestCLIArgs(unittest.TestCase):
    def test_parse_cli_args_defaults(self):
        args = overfiltrr.parse_cli_args([])
        self.assertIsNone(args.log_level)
        self.assertIsNone(args.log_file)

    @patch("builtins.open", new_callable=mock_open)
    @patch("yaml.safe_load")
    @patch("logging.config.dictConfig", MagicMock())
    @patch("overfiltrr.serve", MagicMock())
    def test_main_cli_overrides(self, mock_yaml_safe_load, mock_file_open):
        config_dict = {
            "OVERSEERR_BASEURL": "http://test.com",
            "DRY_RUN": False,
            "API_KEYS": {"overseerr": "key"},
            "TV_CATEGORIES": {
                "default": "test",
                "test": {
                    "weight": 1,
                    "apply": {
                        "root_folder": "/",
                        "sonarr_id": 1,
                        "default_profile_id": 1,
                    },
                },
            },
            "MOVIE_CATEGORIES": {
                "default": "test",
                "test": {
                    "weight": 1,
                    "apply": {
                        "root_folder": "/",
                        "radarr_id": 1,
                        "default_profile_id": 1,
                    },
                },
            },
            "SERVER": {},
        }
        mock_yaml_safe_load.return_value = config_dict
        mock_file_open.return_value.read.return_value = ""

        importlib.reload(overfiltrr)
        overfiltrr.main(["--log-level", "DEBUG", "--log-file", "custom.log"])

        self.assertEqual(overfiltrr.LOGGING_CONFIG["root"]["level"], "DEBUG")
        self.assertEqual(
            overfiltrr.LOGGING_CONFIG["handlers"]["file"]["filename"], "custom.log"
        )


if __name__ == "__main__":
    unittest.main()
