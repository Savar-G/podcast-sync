"""Optional features. Each module here defines register(service) and is loaded
automatically, so a new feature never edits a shared list.

Inside register(), a feature may:
  service.handlers["name"] = fn      # POST /name, fn(body: dict) -> dict (raise BadRequest on bad input)
  service.status_extras.append(fn)   # fn() -> dict, merged into GET /status
  service.offset_extras.append(fn)   # fn(collection_id) -> seconds, added to a show's offset
  service.position_mappers.append(m) # m.to_podcast / m.to_youtube(video_id, track_id, t) -> seconds or None
  service.body_limits["name"] = n    # POST /name may send up to n bytes (default server.MAX_BODY)
"""
import importlib
import pkgutil


def load_all(service) -> None:
    for mod in sorted(pkgutil.iter_modules(__path__), key=lambda m: m.name):
        importlib.import_module(f"{__name__}.{mod.name}").register(service)
