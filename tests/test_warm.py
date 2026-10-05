import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "helper"))
sys.path.insert(0, str(ROOT / "tests"))

from podsync import config  # noqa: E402
from podsync.features import warm  # noqa: E402
from podsync.service import SyncService  # noqa: E402
from podsync.state import State  # noqa: E402
from test_service import COLLECTION, SENRA, FakeApp, FakeDB, episode  # noqa: E402


class WarmApp(FakeApp):
    def __init__(self, db):
        super().__init__(db)
        self.warms = 0

    def warm(self):
        self.warms += 1
        return True


class WarmFeatureTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.cfg = config.Config(shows=[config.Show("David Senra", [SENRA], [COLLECTION])])
        self.cfg.handoff_dir = self.tmp / "handoff"
        self.cfg.state_dir = self.tmp / "state"
        self.db = FakeDB([episode()])
        self.app = WarmApp(self.db)
        self.svc = self._service(self.app, self.cfg.state_dir)
        self._spawn, warm._spawn = warm._spawn, lambda fn: fn()  # run the warm-up inline

    def tearDown(self):
        warm._spawn = self._spawn

    def _service(self, app, state_dir):
        return SyncService(self.cfg, self.db, app, State(state_dir), fetch_meta=lambda v: None, lookup=lambda i: [])

    def test_warm_starts_podcasts_once_per_interval(self):
        self.assertEqual(self.svc.handlers["warm"]({}), {"warming": True})
        self.assertEqual(self.svc.handlers["warm"]({})["reason"], "recent")
        self.assertEqual(self.app.warms, 1)

    def test_no_launch_before_any_show_is_known(self):
        self.cfg.shows = []
        self.assertEqual(self.svc.handlers["warm"]({})["reason"], "no_shows_yet")
        self.svc.state.learn_channel(SENRA, COLLECTION)
        self.assertTrue(self.svc.handlers["warm"]({})["warming"])
        self.assertEqual(self.app.warms, 1)

    def test_no_launch_when_the_library_is_not_readable(self):
        self.db.access = False
        self.assertEqual(self.svc.handlers["warm"]({})["reason"], "library_not_readable")
        self.assertEqual(self.app.warms, 0)

    def test_apps_without_warm_are_left_alone(self):
        svc = self._service(FakeApp(self.db), self.tmp / "s2")
        self.assertEqual(svc.handlers["warm"]({})["reason"], "unsupported")

    def test_a_failing_warm_up_is_contained(self):
        def boom():
            raise RuntimeError("open failed")

        self.app.warm = boom
        with self.assertLogs("podsync.warm", "ERROR"):
            self.assertTrue(self.svc.handlers["warm"]({})["warming"])


if __name__ == "__main__":
    unittest.main()
