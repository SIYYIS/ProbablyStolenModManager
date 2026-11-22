"""Read opt-in cover filenames at the root of an installed mod."""

from pathlib import Path

from .file_ops import is_link

_MAX_BYTES = 2 * 1024 * 1024
_FORMATS = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".webp": "image/webp"}
_NAMES = ("preview", "cover", "thumbnail", "icon", "workshop_preview")


def cover_candidates(root: Path | None, folder: Path):
    try:
        if root is None or folder.parent != root or is_link(root) or is_link(folder):
            return
    except OSError:
        return
    for name in _NAMES:
        for suffix, media_type in _FORMATS.items():
            path = folder / (name + suffix)
            try:
                if not is_link(path) and path.is_file() and 0 < path.stat().st_size <= _MAX_BYTES:
                    yield path, media_type
            except OSError:
                continue


def read_cover(root: Path | None, folder: Path) -> tuple[bytes, str] | None:
    for path, media_type in cover_candidates(root, folder):
        try:
            with path.open("rb") as stream:
                data = stream.read(_MAX_BYTES + 1)
        except OSError:
            continue
        if len(data) > _MAX_BYTES:
            continue
        if (media_type == "image/png" and data.startswith(b"\x89PNG\r\n\x1a\n") or
                media_type == "image/jpeg" and data.startswith(b"\xff\xd8\xff") or
                media_type == "image/webp" and data.startswith(b"RIFF") and data[8:12] == b"WEBP"):
            return data, media_type
    return None
