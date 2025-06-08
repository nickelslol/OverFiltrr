import importlib
import os
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
import overfiltrr


class TestEvaluateCondition(unittest.TestCase):
    def test_not_equal_requires_all_elements(self):
        context = {"genres": ["Action", "Comedy"]}
        condition = {"genres": {"!=": "Action"}}
        self.assertFalse(overfiltrr.evaluate_condition(condition, context))

    def test_not_equal_passes_when_all_elements_match(self):
        context = {"genres": ["Comedy", "Drama"]}
        condition = {"genres": {"!=": "Action"}}
        self.assertTrue(overfiltrr.evaluate_condition(condition, context))


if __name__ == "__main__":
    unittest.main()
