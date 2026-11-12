import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from bootstrap import install


class ScheduleTests(unittest.TestCase):
    def config(self, **maintenance):
        return SimpleNamespace(maintenance={'enabled': True, **maintenance},
                               raw={'notifications': {'target': 'test-chat'}},
                               path=Path('/tmp/config.yaml'))

    @patch('bootstrap.cli', return_value={'jobs': []})
    def test_fixed_interval_preserves_daily_and_overrides_legacy(self, cli):
        result = install(self.config(refresh_every='5h', refresh_schedule='*/10 * * * *'))
        self.assertEqual(result[0]['schedule'], '0 3 * * *')
        self.assertEqual(result[1]['schedule'], {'every': '5h'})

    @patch('bootstrap.cli', return_value={'jobs': []})
    def test_legacy_schedule_is_preserved(self, cli):
        result = install(self.config(refresh_schedule='*/20 * * * *'))
        self.assertEqual(result[1]['schedule'], '*/20 * * * *')


if __name__ == '__main__':
    unittest.main()
