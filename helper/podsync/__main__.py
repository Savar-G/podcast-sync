from __future__ import annotations

import logging
import os
import sys
import threading
from pathlib import Path

from . import config
from .podcasts_app import PodcastsApp, StaticApp
from .podcasts_db import JsonLibrary, PodcastsDB
from .server import serve
from .service import SyncService
from .state import State


def _check_library(db: PodcastsDB) -> None:
    done = threading.Event()

    def nag():
        if not done.wait(5):
            logging.warning(
                "waiting to open the Podcasts library: macOS is probably asking to let "
                "\"Podcast Sync Helper\" access data from other apps. Click Allow."
            )

    threading.Thread(target=nag, daemon=True).start()
    ok = db.readable()
    done.set()
    if ok:
        logging.info("Podcasts library is readable")
    else:
        logging.error("cannot read the Podcasts library at %s (%s)", db.path, getattr(db, "last_error", "?"))


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s", stream=sys.stderr)
    cfg = config.load()
    fake = os.environ.get("PODSYNC_FAKE_LIBRARY")
    if fake:  # end-to-end tests: a JSON library, and never touch the Podcasts app
        db = JsonLibrary(Path(fake))
        app = StaticApp(db)
        logging.warning("using fake library %s", fake)
    else:
        db = PodcastsDB(cfg.db_path)
        # The first open of another app's data can block on a macOS privacy prompt, so
        # check in the background and serve right away.
        threading.Thread(target=_check_library, args=(db,), daemon=True).start()
        app = PodcastsApp(db, cfg.podcasts_idle_quit_seconds)
        app.start_idle_watcher()
    service = SyncService(cfg, db, app, State(cfg.state_dir))
    httpd = serve(service, cfg.port, cfg.extension_ids)
    logging.info("podsync listening on http://127.0.0.1:%d", cfg.port)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
