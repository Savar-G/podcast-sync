import json
import sqlite3
from contextlib import closing
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "helper"))

from podsync import config  # noqa: E402
from podsync.features import continue_watching as cw  # noqa: E402
from podsync.podcasts_db import APPLE_EPOCH, Episode, JsonLibrary, PodcastsDB  # noqa: E402
from podsync.service import BadRequest, SyncService  # noqa: E402
from podsync.state import State  # noqa: E402
from podsync.youtube import VideoMeta  # noqa: E402

# Invented shows, episodes, channels and positions.
CHANNEL = "UCaaaaaaaaaaaaaaaaaaaaaa"
OTHER_CHANNEL = "UCbbbbbbbbbbbbbbbbbbbbbb"
SHOW_A, SHOW_B = 111, 222
NOW = time.time()
DAY = 86400


def ep(track_id, **kw):
    base = dict(
        track_id=track_id,
        collection_id=SHOW_A,
        show="Example Show",
        title=f"Episode {track_id} with Jane Example",
        duration=3600.0,
        pub_date=NOW - 5 * DAY,
        playhead=600.0,
        last_played=NOW - 3600,
    )
    base.update(kw)
    return Episode(**base)


class FakeDB:
    def __init__(self, episodes):
        self.rows = {e.track_id: e for e in episodes}

    def episodes(self, ids):
        return [e for e in self.rows.values() if e.collection_id in ids]

    def episode(self, track_id):
        return self.rows.get(track_id)

    def episodes_near(self, t, days=10):
        return []

    def recently_played(self, since, min_playhead=60, end_margin=60, limit=5):
        lib = JsonLibrary.__new__(JsonLibrary)
        lib.rows = self.rows
        return lib.recently_played(since, min_playhead, end_margin, limit)


class FakeApp:
    def __init__(self, db, on_refresh=None):
        self.db, self.refreshed, self.on_refresh = db, [], on_refresh

    def refresh(self, track_id):
        self.refreshed.append(track_id)
        if self.on_refresh:
            self.on_refresh()
        return self.db.episode(track_id)


def feed_xml(entries):
    items = "".join(
        f"""<entry><yt:videoId>{vid}</yt:videoId><title>{title}</title>
        <link rel="alternate" href="https://www.youtube.com/{'shorts/' if short else 'watch?v='}{vid}"/>
        <published>{published}</published></entry>"""
        for vid, title, published, short in entries
    )
    return (
        '<?xml version="1.0"?><feed xmlns:yt="http://www.youtube.com/xml/schemas/2015" '
        f'xmlns="http://www.w3.org/2005/Atom">{items}</feed>'
    ).encode()


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.cfg = config.Config()
        self.cfg.state_dir = self.tmp / "state"
        self.cfg.handoff_dir = self.tmp / "handoff"
        self.db = FakeDB([ep(1)])
        self.app = FakeApp(self.db)
        self.metas = {}  # video_id -> VideoMeta
        self.fetched = []
        self.feeds = {}  # channel -> uploads
        self.feed_calls = []
        self.clock = [NOW]
        self.svc = SyncService(
            self.cfg, self.db, self.app, State(self.cfg.state_dir), fetch_meta=self.fetch_meta, lookup=lambda ids: []
        )
        self.feature = cw.ContinueWatching(self.svc, fetch_feed=self.fetch_feed, clock=lambda: self.clock[0], budget=2.0)

    def fetch_meta(self, video_id):
        self.fetched.append(video_id)
        return self.metas.get(video_id)

    def fetch_feed(self, channel_id):
        self.feed_calls.append(channel_id)
        return self.feeds.get(channel_id, [])

    def video(self, vid, track, title=None, published=None, duration=3500.0):
        """A video that the matcher pairs with episode `track`."""
        e = self.db.rows[track]
        self.metas[vid] = VideoMeta(vid, title or e.title, CHANNEL, "Example", duration, published or e.pub_date)

    def recent(self, **kw):
        return self.feature.recent(**kw)["episodes"]


class QueryTest(unittest.TestCase):
    def test_only_started_unfinished_recent_episodes_newest_first(self):
        rows = [
            ep(1, last_played=NOW - 2 * DAY),
            ep(2, last_played=NOW - 60),
            ep(3, playhead=30.0),  # barely started
            ep(4, playhead=3570.0),  # within a minute of the end
            ep(5, last_played=NOW - 20 * DAY),  # too long ago
            ep(6, last_played=None),  # never played
            ep(7, duration=None, playhead=99999.0, last_played=NOW - 10),  # unknown length: keep
        ]
        lib = JsonLibrary.__new__(JsonLibrary)
        lib.rows = {e.track_id: e for e in rows}
        got = [e.track_id for e in lib.recently_played(NOW - 14 * DAY)]
        self.assertEqual(got, [7, 2, 1])
        self.assertEqual(len(lib.recently_played(NOW - 14 * DAY, limit=2)), 2)

    def test_sqlite_query_is_read_only_and_reads_artwork(self):
        path = Path(tempfile.mkdtemp()) / "MTLibrary.sqlite"
        with closing(sqlite3.connect(path)) as c, c:
            c.executescript(
                """CREATE TABLE ZMTPODCAST (Z_PK INTEGER PRIMARY KEY, ZSTORECOLLECTIONID INTEGER, ZTITLE TEXT,
                       ZSUBSCRIBED INTEGER, ZARTWORKTEMPLATEURL TEXT);
                   CREATE TABLE ZMTEPISODE (Z_PK INTEGER PRIMARY KEY, ZPODCAST INTEGER, ZSTORETRACKID INTEGER,
                       ZTITLE TEXT, ZDURATION FLOAT, ZENTITLEDDURATION FLOAT, ZFREEDURATION FLOAT, ZPUBDATE FLOAT,
                       ZPLAYHEAD FLOAT, ZLASTDATEPLAYED FLOAT, ZARTWORKTEMPLATEURL TEXT);"""
            )
            art = "https://is1-ssl.mzstatic.com/image/thumb/x/y.jpg/{w}x{h}bb.{f}"
            c.execute("INSERT INTO ZMTPODCAST VALUES (1, 111, 'Example Show', 1, ?)", (art,))
            played = NOW - APPLE_EPOCH - 3600
            for pk, track, playhead, last in (
                (1, 501, 600, played),
                (2, 502, 10, played),
                (3, 503, 3590, played),
                (4, 504, 900, played + 60),
                (5, 0, 900, played),
            ):
                c.execute(
                    "INSERT INTO ZMTEPISODE VALUES (?, 1, ?, 'Ep', 3600, 0, 0, ?, ?, ?, NULL)",
                    (pk, track, played - 86400, playhead, last),
                )
        db = PodcastsDB(path)
        got = db.recently_played(NOW - 14 * DAY)
        self.assertEqual([e.track_id for e in got], [504, 501])
        self.assertEqual(got[0].artwork, art)
        self.assertEqual(got[0].show, "Example Show")
        with closing(db._connect()) as c, self.assertRaises(sqlite3.OperationalError):  # opened with mode=ro
            c.execute("DELETE FROM ZMTEPISODE")

    def test_sqlite_query_works_without_artwork_columns(self):
        path = Path(tempfile.mkdtemp()) / "old.sqlite"
        with closing(sqlite3.connect(path)) as c, c:
            c.executescript(
                """CREATE TABLE ZMTPODCAST (Z_PK INTEGER PRIMARY KEY, ZSTORECOLLECTIONID INTEGER, ZTITLE TEXT);
                   CREATE TABLE ZMTEPISODE (Z_PK INTEGER PRIMARY KEY, ZPODCAST INTEGER, ZSTORETRACKID INTEGER,
                       ZTITLE TEXT, ZDURATION FLOAT, ZENTITLEDDURATION FLOAT, ZFREEDURATION FLOAT, ZPUBDATE FLOAT,
                       ZPLAYHEAD FLOAT, ZLASTDATEPLAYED FLOAT);
                   INSERT INTO ZMTPODCAST VALUES (1, 111, 'Example Show');"""
            )
            c.execute("INSERT INTO ZMTEPISODE VALUES (1, 1, 501, 'Ep', 3600, 0, 0, 0, 600, ?)", (NOW - APPLE_EPOCH - 60,))
        got = PodcastsDB(path).recently_played(NOW - 14 * DAY)
        self.assertEqual([(e.track_id, e.artwork) for e in got], [(501, None)])


class HelpersTest(unittest.TestCase):
    def test_parse_feed_skips_shorts_and_bad_ids(self):
        xml = feed_xml(
            [
                ("vidAAAAAAA1", "Full episode", "2026-09-30T12:00:00+00:00", False),
                ("vidAAAAAAA2", "A clip", "2026-09-30T13:00:00+00:00", True),
                ("bad id", "Broken", "2026-09-30T13:00:00+00:00", False),
            ]
        )
        got = cw.parse_feed(xml)
        self.assertEqual([(v, t) for v, t, _ in got], [("vidAAAAAAA1", "Full episode")])
        self.assertAlmostEqual(got[0][2], 1790769600.0)

    def test_artwork_url(self):
        t = "https://is1-ssl.mzstatic.com/image/thumb/a/b.jpg/{w}x{h}bb.{f}"
        self.assertEqual(cw.artwork_url(t), "https://is1-ssl.mzstatic.com/image/thumb/a/b.jpg/120x120bb.jpg")
        for bad in (None, "", "http://is1-ssl.mzstatic.com/x/{w}x{h}bb.{f}", "https://evil.example/{w}x{h}bb.{f}", "javascript:alert(1)"):
            self.assertIsNone(cw.artwork_url(bad), bad)

    def test_search_url_is_encoded(self):
        self.assertEqual(
            cw.search_url("Show & Co", "Ep #3: Hi?"),
            "https://www.youtube.com/results?search_query=Show+%26+Co+Ep+%233%3A+Hi%3F",
        )

    def test_rank_uploads_prefers_shared_words_then_date_and_drops_far_dates(self):
        e = ep(1, title="Jane Example on building things", pub_date=NOW)
        uploads = [
            ("far00000000", "Jane Example on building things", NOW - 30 * DAY),
            ("near0000000", "Unrelated clip", NOW - 1 * DAY),
            ("nearer00000", "Another clip", NOW - 0.5 * DAY),
            ("guest000000", "Why Jane Example builds", NOW - 3 * DAY),
        ]
        self.assertEqual(cw.rank_uploads(e, uploads), ["guest000000", "nearer00000", "near0000000"])


class RowTest(Base):
    def test_row_fields_and_search_fallback(self):
        self.db.rows[1] = ep(1, artwork="https://is1-ssl.mzstatic.com/a/{w}x{h}bb.{f}", playhead=2430.0, title="Ep & more")
        (row,) = self.recent()
        self.assertEqual(row["videoId"], None)
        self.assertEqual(row["url"], "https://www.youtube.com/results?search_query=Example+Show+Ep+%26+more")
        self.assertEqual((row["label"], row["youtubeTime"], row["playhead"]), ("40:30", 2430.0, 2430.0))
        self.assertEqual((row["show"], row["episode"], row["duration"]), ("Example Show", "Ep & more", 3600.0))
        self.assertEqual(row["artwork"], "https://is1-ssl.mzstatic.com/a/120x120bb.jpg")
        self.assertEqual(row["trackId"], 1)
        json.dumps(row)  # the handler's answer must be JSON

    def test_offset_moves_the_youtube_time_and_is_capped_by_video_length(self):
        self.cfg.shows = [config.Show("Example Show", [CHANNEL], [SHOW_A], offset_seconds=90)]
        self.db.rows[1] = ep(1, playhead=2430.0)
        self.svc.state.set_video("vidAAAAAAA1", {"track_id": 1, "at": NOW, "video": {"duration": 4000.0}})
        (row,) = self.recent()
        self.assertEqual((row["youtubeTime"], row["label"]), (2520.0, "42:00"))
        self.assertEqual(row["url"], "https://www.youtube.com/watch?v=vidAAAAAAA1&t=2520s")
        self.svc.state.set_video("vidAAAAAAA1", {"track_id": 1, "at": NOW, "video": {"duration": 2500.0}})
        self.assertEqual(self.recent()[0]["youtubeTime"], 2495.0)
        self.cfg.shows[0].offset_seconds = -9999
        self.assertEqual(self.recent()[0]["youtubeTime"], 0.0)

    def test_handler_validates_body(self):
        self.assertEqual(self.svc.handlers["recent"]({})["episodes"][0]["trackId"], 1)
        with self.assertRaises(BadRequest):
            self.svc.handlers["recent"]({"refresh": "yes"})

    def test_hidden_episodes_leave_room_for_the_next_ones(self):
        for i in range(2, 8):  # 7 episodes, newest first: 1, 2, ... 7
            self.db.rows[i] = ep(i, last_played=NOW - 3600 * i)
        self.assertEqual([r["trackId"] for r in self.recent()], [1, 2, 3, 4, 5])
        hidden = {1: NOW - 3600, 3: NOW - 3 * 3600}
        self.assertEqual([r["trackId"] for r in self.recent(hidden=hidden)], [2, 4, 5, 6, 7])

    def test_hidden_episode_comes_back_when_played_again(self):
        hidden = {1: NOW - 3600}
        self.assertEqual(self.recent(hidden=hidden), [])
        self.db.rows[1] = ep(1, last_played=NOW - 60)  # played again on the iPhone
        self.assertEqual([r["trackId"] for r in self.recent(hidden=hidden)], [1])

    def test_hidden_is_validated(self):
        handle = self.svc.handlers["recent"]
        self.assertEqual(handle({"hidden": {"1": NOW - 3600}})["episodes"], [])
        self.assertEqual(len(handle({"hidden": {}})["episodes"]), 1)
        for bad in (["1"], {"x": 1}, {"1": "soon"}, {"1": True}, {"1": float("nan")}, {str(i): 1 for i in range(201)}):
            with self.assertRaises(BadRequest, msg=repr(bad)[:40]):
                handle({"hidden": bad})

    def test_unreadable_library(self):
        def boom(*a, **k):
            raise sqlite3.OperationalError("authorization denied")

        self.db.recently_played = boom
        self.assertEqual(self.feature.recent(), {"episodes": [], "error": "library_unreadable"})

    def test_refresh_runs_once_for_the_newest_episode_and_rereads(self):
        self.db.rows[2] = ep(2, last_played=NOW - 7200)

        def iphone_listened():
            self.db.rows[3] = ep(3, last_played=NOW - 5)

        self.app.on_refresh = iphone_listened
        self.assertEqual([r["trackId"] for r in self.recent()], [1, 2])
        self.assertEqual(self.app.refreshed, [])
        out = self.feature.recent(refresh=True)
        self.assertEqual(self.app.refreshed, [1])
        self.assertEqual([r["trackId"] for r in out["episodes"]], [3, 1, 2])
        self.assertTrue(out["refreshed"])


class LookupOrderTest(Base):
    def setUp(self):
        super().setUp()
        self.svc.state.learn_channel(CHANNEL, SHOW_A)

    def test_1_cached_match_wins_without_network(self):
        self.svc.state.set_video("oldAAAAAAA1", {"track_id": 1, "at": NOW - 100})
        self.svc.state.set_video("newAAAAAAA1", {"track_id": 1, "at": NOW - 10})
        self.svc.state.set_video("othAAAAAAA1", {"track_id": 99, "at": NOW})
        self.assertEqual(self.recent()[0]["videoId"], "newAAAAAAA1")
        self.assertEqual((self.feed_calls, self.fetched), ([], []))

    def test_1_the_video_you_last_watched_wins(self):
        self.svc.state.set_video("oldAAAAAAA1", {"track_id": 1, "at": NOW - 100})
        self.svc.state.set_video("newAAAAAAA1", {"track_id": 1, "at": NOW - 10})
        self.svc.state.set_progress(1, {"time": 5, "at": NOW, "video_id": "oldAAAAAAA1"})
        self.assertEqual(self.recent()[0]["videoId"], "oldAAAAAAA1")

    def test_2_feed_candidate_confirmed_by_resolve(self):
        e = self.db.rows[1]
        self.feeds[CHANNEL] = [
            ("clipAAAAAA1", "Short clip", e.pub_date + 3600, ),
            ("fullAAAAAA1", e.title, e.pub_date),
        ]
        self.video("fullAAAAAA1", 1)
        self.metas["clipAAAAAA1"] = VideoMeta("clipAAAAAA1", "Short clip", CHANNEL, "Example", 60.0, e.pub_date)
        (row,) = self.recent()
        self.assertEqual(row["videoId"], "fullAAAAAA1")
        self.assertEqual(row["url"], "https://www.youtube.com/watch?v=fullAAAAAA1&t=600s")
        self.assertEqual(self.feed_calls, [CHANNEL])
        # resolve() cached the match, so the next call needs no network at all.
        self.feed_calls.clear()
        self.fetched.clear()
        self.assertEqual(self.recent()[0]["videoId"], "fullAAAAAA1")
        self.assertEqual((self.feed_calls, self.fetched), ([], []))

    def test_2_config_channels_count_too(self):
        self.svc.state.data["channels"].clear()
        self.cfg.shows = [config.Show("Example Show", [OTHER_CHANNEL], [SHOW_A])]
        e = self.db.rows[1]
        self.feeds[OTHER_CHANNEL] = [("fullAAAAAA1", e.title, e.pub_date)]
        self.metas["fullAAAAAA1"] = VideoMeta("fullAAAAAA1", e.title, OTHER_CHANNEL, "Example", 3500.0, e.pub_date)
        self.assertEqual(self.recent()[0]["videoId"], "fullAAAAAA1")
        self.assertEqual(self.feed_calls, [OTHER_CHANNEL])

    def test_2_feed_is_cached(self):
        self.recent()
        self.feature._not_found.clear()
        self.recent()
        self.assertEqual(self.feed_calls, [CHANNEL])
        self.clock[0] += cw.FEED_TTL + 1
        self.feature._not_found.clear()
        self.recent()
        self.assertEqual(self.feed_calls, [CHANNEL, CHANNEL])

    def test_3_no_known_channel_gives_search_without_network(self):
        self.svc.state.data["channels"].clear()
        row = self.recent()[0]
        self.assertIsNone(row["videoId"])
        self.assertIn("results?search_query=", row["url"])
        self.assertEqual((self.feed_calls, self.fetched), ([], []))

    def test_3_not_found_is_remembered_for_a_while(self):
        e = self.db.rows[1]
        self.feeds[CHANNEL] = [("otherAAAAA1", "Something else", e.pub_date)]
        self.metas["otherAAAAA1"] = VideoMeta("otherAAAAA1", "Cooking pasta", CHANNEL, "Example", 900.0, e.pub_date)
        self.assertIsNone(self.recent()[0]["videoId"])
        self.assertEqual(self.fetched, ["otherAAAAA1"])
        self.clock[0] += cw.FEED_TTL + 1  # feed cache expired, but the episode is still marked not found
        self.feature._feeds.clear()
        self.assertIsNone(self.recent()[0]["videoId"])
        self.assertEqual(self.feed_calls, [CHANNEL])
        self.clock[0] += cw.NOT_FOUND_TTL
        self.recent()
        self.assertEqual(self.feed_calls, [CHANNEL, CHANNEL])

    def test_feed_error_gives_search_and_is_not_remembered_as_not_found(self):
        def broken(ch):
            self.feed_calls.append(ch)
            raise OSError("offline")

        self.feature.fetch_feed = broken
        self.assertIsNone(self.recent()[0]["videoId"])
        self.assertNotIn(1, self.feature._not_found)

    def test_slow_lookup_respects_the_budget_and_finishes_in_background(self):
        e = self.db.rows[1]
        self.feeds[CHANNEL] = [("fullAAAAAA1", e.title, e.pub_date)]
        self.video("fullAAAAAA1", 1)
        gate = threading.Event()
        fast = self.fetch_meta

        def slow(vid):
            gate.wait(5)
            return fast(vid)

        self.svc.fetch_meta = slow
        self.feature.budget = 0.2
        started = time.monotonic()
        row = self.recent()[0]
        self.assertLess(time.monotonic() - started, 2)
        self.assertIsNone(row["videoId"])
        self.assertNotIn(1, self.feature._not_found)  # unfinished work is not a "no"
        gate.set()
        for _ in range(100):  # the background resolve caches the match
            if self.feature.cached_video(1):
                break
            time.sleep(0.02)
        self.assertEqual(self.recent()[0]["videoId"], "fullAAAAAA1")

    def test_concurrent_requests_share_work(self):
        e = self.db.rows[1]
        self.feeds[CHANNEL] = [("fullAAAAAA1", e.title, e.pub_date)]
        self.video("fullAAAAAA1", 1)
        gate = threading.Event()
        fast = self.fetch_feed

        def slow_feed(ch):
            gate.wait(5)
            return fast(ch)

        self.feature.fetch_feed = slow_feed
        out = []
        threads = [threading.Thread(target=lambda: out.append(self.recent())) for _ in range(3)]
        for t in threads:
            t.start()
        time.sleep(0.1)
        gate.set()
        for t in threads:
            t.join(5)
        self.assertEqual([r[0]["videoId"] for r in out], ["fullAAAAAA1"] * 3)
        self.assertEqual(self.feed_calls, [CHANNEL])
        self.assertEqual(self.fetched, ["fullAAAAAA1"])


if __name__ == "__main__":
    unittest.main()
