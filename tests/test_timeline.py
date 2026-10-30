"""The broadcast timeline the page shows: which aired events become rows."""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from radio import config, store, timeline


class TimelineRowsTests(unittest.TestCase):
    def test_station_ids_are_not_rows(self):
        tmp = tempfile.TemporaryDirectory(prefix='radio-timeline-'); self.addCleanup(tmp.cleanup)
        for name, value in [('DATA_DIR', Path(tmp.name)), ('RUN_DIR', Path(tmp.name) / 'run')]:
            p = patch.object(config, name, value); p.start(); self.addCleanup(p.stop)
        (Path(tmp.name) / 'db').mkdir()
        db = store.connect(); self.addCleanup(db.close)
        store.log_event(db, 1, 'SESSION_STARTED')
        store.log_event(db, 1, 'STATION_ID', 'assets/stinger_out.wav')
        store.log_event(db, 1, 'GAP', '', 'Loading gap')
        self.assertEqual([r['type'] for r in timeline._build(db, 12, 10)], ['GAP', 'SESSION_STARTED'])


if __name__ == '__main__':
    unittest.main()
