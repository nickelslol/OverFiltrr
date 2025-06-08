import importlib
import os
import sys
import unittest
from unittest.mock import MagicMock, mock_open, patch

import yaml

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
import overfiltrr


class TestConfigValidation(unittest.TestCase):

    # --- Tests for validate_categories ---
    def test_validate_categories_valid_config(self):
        """Test validate_categories with a minimal valid configuration."""
        valid_tv_categories = {
            "default": "standard_tv",
            "anime_tv": {
                "weight": 1,
                "apply": {"root_folder": "/tv/Anime/", "sonarr_id": 1, "default_profile_id": 5},
                "filters": {"genres": ["Animation"]},
            },
            "standard_tv": {
                "weight": 0,
                "quality_profile_rules": [
                    {"priority": 1, "profile_id": 1, "condition": {"release_year": {">=": 2020}}}
                ],
                "apply": {"root_folder": "/tv/Standard/", "sonarr_id": 2, "default_profile_id": 6},
            },
        }
        self.assertTrue(overfiltrr.validate_categories(valid_tv_categories, "tv"))

        valid_movie_categories = {
            "default": "standard_movies",
            "action_movies": {
                "weight": 1,
                "quality_profile_rules": [
                    {"priority": 1, "profile_id": 1, "condition": {"genres": {"in": ["Action"]}}}
                ],
                "apply": {
                    "root_folder": "/movies/Action/",
                    "radarr_id": 1,
                    "default_profile_id": 2,
                },
            },
            "standard_movies": {
                "weight": 0,
                "apply": {
                    "root_folder": "/movies/Standard/",
                    "radarr_id": 2,
                    "default_profile_id": 3,
                },
            },
        }
        self.assertTrue(overfiltrr.validate_categories(valid_movie_categories, "movie"))

    @patch("logging.error")
    def test_validate_categories_missing_default_id_no_rules(self, mock_logging_error):
        """Test missing default_profile_id when quality_profile_rules are absent."""
        categories = {
            "default": "broken_tv",
            "broken_tv": {"weight": 1, "apply": {"root_folder": "/tv/Broken/", "sonarr_id": 1}},
        }
        self.assertFalse(overfiltrr.validate_categories(categories, "tv"))
        mock_logging_error.assert_any_call(
            "Category 'broken_tv' must have 'default_profile_id' in 'apply' when 'quality_profile_rules' are missing or empty."
        )

    @patch("logging.error")
    def test_validate_categories_missing_default_id_empty_rules(self, mock_logging_error):
        """Test missing default_profile_id when quality_profile_rules is an empty list."""
        categories = {
            "default": "broken_tv",
            "broken_tv": {
                "weight": 1,
                "quality_profile_rules": [],
                "apply": {"root_folder": "/tv/Broken/", "sonarr_id": 1},
            },
        }
        self.assertFalse(overfiltrr.validate_categories(categories, "tv"))
        mock_logging_error.assert_any_call(
            "Category 'broken_tv' must have 'default_profile_id' in 'apply' when 'quality_profile_rules' are missing or empty."
        )

    def test_validate_categories_present_default_id_with_rules(self):
        """Test valid when default_profile_id is present WITH quality_profile_rules."""
        categories = {
            "default": "valid_tv",
            "valid_tv": {
                "weight": 1,
                "quality_profile_rules": [
                    {"priority": 1, "profile_id": 1, "condition": {"release_year": {">=": 2020}}}
                ],
                "apply": {"root_folder": "/tv/Valid/", "sonarr_id": 1, "default_profile_id": 5},
            },
        }
        self.assertTrue(overfiltrr.validate_categories(categories, "tv"))

    def test_validate_categories_present_default_id_no_rules(self):
        """Test valid when default_profile_id is present and no quality_profile_rules."""
        categories = {
            "default": "valid_tv",
            "valid_tv": {
                "weight": 1,
                "apply": {"root_folder": "/tv/Valid/", "sonarr_id": 1, "default_profile_id": 5},
            },
        }
        self.assertTrue(overfiltrr.validate_categories(categories, "tv"))

    @patch("sys.exit")
    @patch("logging.critical")
    # We need to ensure that validate_configuration uses the categories we provide,
    # not the ones loaded by the initial (mocked) import of overfiltrr.
    # So, we patch the global TV_CATEGORIES and MOVIE_CATEGORIES in overfiltrr for this test.
    @patch("overfiltrr.TV_CATEGORIES", new_callable=dict)
    @patch("overfiltrr.MOVIE_CATEGORIES", new_callable=dict)
    # No need to mock load_config or setup_logging if we are directly setting the category globals
    # and testing validate_configuration's logic based on those.
    def test_validate_configuration_exits_on_invalid_category(
        self, mock_movie_cat, mock_tv_cat, mock_log_critical, mock_sys_exit
    ):
        """Test that validate_configuration calls sys.exit for invalid categories."""
        invalid_tv_config = {
            "default": "broken_tv",
            "broken_tv": {
                "weight": 1,
                "apply": {"root_folder": "/tv/Broken/", "sonarr_id": 1},
            },  # Invalid
        }
        valid_movie_config = {
            "default": "std_mov",
            "std_mov": {
                "weight": 0,
                "apply": {"root_folder": "/", "radarr_id": 1, "default_profile_id": 1},
            },
        }

        # Update the patched global dictionaries directly
        mock_tv_cat.update(invalid_tv_config)
        mock_movie_cat.update(valid_movie_config)

        overfiltrr.validate_configuration()

        mock_log_critical.assert_called_with(
            "Configuration validation failed. Please fix the errors and restart the script."
        )
        mock_sys_exit.assert_called_with(1)

    # --- Tests for Notifiarr Timeout (via reloading module with patched file I/O) ---
    # These tests will reload the overfiltrr module. The pre-import patches are stopped,
    # so each of these tests needs to set up its own environment for the reload.

    @patch("builtins.open", new_callable=mock_open)
    @patch("yaml.safe_load")
    @patch("logging.config.dictConfig", MagicMock())
    @patch("overfiltrr.serve", MagicMock())
    def test_notifiarr_timeout_specified(self, mock_yaml_safe_load, mock_file_open):
        """Test Notifiarr timeout when specified in config by reloading module."""
        config_dict = {
            "OVERSEERR_BASEURL": "http://test.com",
            "DRY_RUN": False,
            "API_KEYS": {"overseerr": "key"},
            "LOG_LEVEL": "INFO",
            "TV_CATEGORIES": {
                "default": "test",
                "test": {
                    "weight": 1,
                    "apply": {"root_folder": "/", "sonarr_id": 1, "default_profile_id": 1},
                },
            },
            "MOVIE_CATEGORIES": {
                "default": "test",
                "test": {
                    "weight": 1,
                    "apply": {"root_folder": "/", "radarr_id": 1, "default_profile_id": 1},
                },
            },
            "NOTIFIARR": {"API_KEY": "key", "CHANNEL": "chan", "SOURCE": "src", "TIMEOUT": 15},
            "SERVER": {},
        }
        mock_yaml_safe_load.return_value = config_dict
        # Configure mock_file_open if load_config actually reads the file content in the test
        # For these tests, yaml.safe_load is directly returning the dict, so read_data isn't strictly needed for it.
        # However, good practice if load_config was more complex:
        mock_file_open.return_value.read.return_value = yaml.dump(config_dict)

        importlib.reload(overfiltrr)
        overfiltrr.main()

        self.assertEqual(overfiltrr.NOTIFIARR_TIMEOUT, 15)

    @patch("builtins.open", new_callable=mock_open)
    @patch("yaml.safe_load")
    @patch("logging.config.dictConfig", MagicMock())
    @patch("overfiltrr.serve", MagicMock())
    def test_notifiarr_timeout_not_specified(self, mock_yaml_safe_load, mock_file_open):
        """Test Notifiarr timeout defaults to 10 when not specified."""
        config_dict = {
            "OVERSEERR_BASEURL": "http://test.com",
            "DRY_RUN": False,
            "API_KEYS": {"overseerr": "key"},
            "LOG_LEVEL": "INFO",
            "TV_CATEGORIES": {
                "default": "test",
                "test": {
                    "weight": 1,
                    "apply": {"root_folder": "/", "sonarr_id": 1, "default_profile_id": 1},
                },
            },
            "MOVIE_CATEGORIES": {
                "default": "test",
                "test": {
                    "weight": 1,
                    "apply": {"root_folder": "/", "radarr_id": 1, "default_profile_id": 1},
                },
            },
            "NOTIFIARR": {"API_KEY": "key", "CHANNEL": "chan", "SOURCE": "src"},
            "SERVER": {},
        }
        mock_yaml_safe_load.return_value = config_dict
        mock_file_open.return_value.read.return_value = yaml.dump(config_dict)

        importlib.reload(overfiltrr)
        overfiltrr.main()

        self.assertEqual(overfiltrr.NOTIFIARR_TIMEOUT, 10)

    @patch("builtins.open", new_callable=mock_open)
    @patch("yaml.safe_load")
    @patch("logging.config.dictConfig", MagicMock())
    @patch("overfiltrr.serve", MagicMock())
    def test_notifiarr_timeout_section_missing(self, mock_yaml_safe_load, mock_file_open):
        """Test Notifiarr timeout defaults to 10 when NOTIFIARR section is missing."""
        config_dict = {
            "OVERSEERR_BASEURL": "http://test.com",
            "DRY_RUN": False,
            "API_KEYS": {"overseerr": "key"},
            "LOG_LEVEL": "INFO",
            "TV_CATEGORIES": {
                "default": "test",
                "test": {
                    "weight": 1,
                    "apply": {"root_folder": "/", "sonarr_id": 1, "default_profile_id": 1},
                },
            },
            "MOVIE_CATEGORIES": {
                "default": "test",
                "test": {
                    "weight": 1,
                    "apply": {"root_folder": "/", "radarr_id": 1, "default_profile_id": 1},
                },
            },
            "SERVER": {},
        }
        mock_yaml_safe_load.return_value = config_dict
        mock_file_open.return_value.read.return_value = yaml.dump(config_dict)

        importlib.reload(overfiltrr)
        overfiltrr.main()

        self.assertEqual(overfiltrr.NOTIFIARR_TIMEOUT, 10)
        self.assertIsNone(overfiltrr.NOTIFIARR_APIKEY)
        self.assertIsNone(overfiltrr.NOTIFIARR_CHANNEL)
        self.assertIsNone(overfiltrr.NOTIFIARR_SOURCE)


if __name__ == "__main__":
    unittest.main()
