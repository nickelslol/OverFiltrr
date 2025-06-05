import unittest
from unittest.mock import patch, mock_open, MagicMock
import sys
import os
import yaml
import importlib

# --- Start of Pre-Import Patching ---
MINIMAL_MOCK_CONFIG_FOR_IMPORT = {
    'OVERSEERR_BASEURL': 'http://mock.com',
    'DRY_RUN': True,
    'API_KEYS': {'overseerr': 'mock_key'},
    'LOG_LEVEL': 'DEBUG',
    'TV_CATEGORIES': {'default': 'd', 'd': {'weight': 0, 'apply': {'root_folder': '/', 'sonarr_id': 1, 'default_profile_id': 1}}},
    'MOVIE_CATEGORIES': {'default': 'd', 'd': {'weight': 0, 'apply': {'root_folder': '/', 'radarr_id': 1, 'default_profile_id': 1}}},
    'SERVER': {'HOST': '0.0.0.0', 'PORT': 12210, 'THREADS': 1, 'CONNECTION_LIMIT': 10},
    'NOTIFIARR': {'API_KEY': 'mock_notifiarr', 'CHANNEL': '1', 'SOURCE': 'mock_source', 'TIMEOUT': 5}
}

mock_config_content = yaml.dump(MINIMAL_MOCK_CONFIG_FOR_IMPORT)
patch_builtin_open = patch('builtins.open', mock_open(read_data=mock_config_content))
patch_yaml_safe_load = patch('yaml.safe_load', return_value=MINIMAL_MOCK_CONFIG_FOR_IMPORT)
patch_sys_exit = patch('sys.exit', MagicMock())
patch_logging_dict_config = patch('logging.config.dictConfig', MagicMock())

patch_builtin_open.start()
patch_yaml_safe_load.start()
patch_sys_exit.start()
patch_logging_dict_config.start()
# --- End of Pre-Import Patching ---

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
import overfiltrr

patch_builtin_open.stop()
patch_yaml_safe_load.stop()
patch_sys_exit.stop()
patch_logging_dict_config.stop()


class TestEvaluateCondition(unittest.TestCase):
    def test_not_equal_requires_all_elements(self):
        context = {'genres': ['Action', 'Comedy']}
        condition = {'genres': {'!=': 'Action'}}
        self.assertFalse(overfiltrr.evaluate_condition(condition, context))

    def test_not_equal_passes_when_all_elements_match(self):
        context = {'genres': ['Comedy', 'Drama']}
        condition = {'genres': {'!=': 'Action'}}
        self.assertTrue(overfiltrr.evaluate_condition(condition, context))


if __name__ == '__main__':
    unittest.main()
