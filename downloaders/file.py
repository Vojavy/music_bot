# downloaders/file.py

import asyncio
import logging
import mimetypes
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

from aiogram import Bot, types

SuccessItem = Tuple[Dict[str, Any], Path]
WarnItem    = Tuple[str, str]
FailItem    = Tuple[str, str]


def sanitize_filename(name: str) -> str:
    """
    Very small sanitizer: strip path separators and control chars.
    """
    bad = ['\\', '/', ':', '*', '?', '"', '<', '>', '|']
    for ch in bad:
        name = name.replace(ch, "_")
    return name.strip() or "untitled"


class FileDownloader:
    """
    Save audio files that users upload directly to the bot.

    - Checks extension against an allowed set (Navidrome/FFmpeg-friendly).
    - Stores files under download_root / subdir.
    - Builds minimal metadata from Telegram message: performer/title/duration. (Telegram Audio object fields. :contentReference[oaicite:6]{index=6})
    - Uses aiogram's Bot.download() convenience method to fetch the file. :contentReference[oaicite:7]{index=7}
    """

    def __init__(
        self,
        download_root: Path,
        *,
        subdir: str = "Telegram Uploads",
        allowed_exts: Optional[Iterable[str]] = None,
    ):
        self.download_root = download_root
        self.subdir = subdir
        self.allowed_exts = {e.lower() for e in (allowed_exts or [])}
        self.log = logging.getLogger("FileDownloader")

        # Per-download state
        self.warnings: List[WarnItem] = []

    # ------------------------------------------------------------------ #
    async def download_message(
        self,
        bot: Bot,
        msg: types.Message,
    ) -> Tuple[List[SuccessItem], List[FailItem], List[WarnItem]]:
        """
        Inspect the message, pick the downloadable payload, save to disk.
        Returns (successes, failures, warnings).
        """

        self.warnings = []
        successes: List[SuccessItem] = []
        failures: List[FailItem] = []

        # Determine which field we got
        downloadable = None
        filename: Optional[str] = None
        mime_type: Optional[str] = None
        duration: Optional[int] = None
        performer: Optional[str] = None
        title: Optional[str] = None

        if msg.audio:
            a = msg.audio
            downloadable = a
            filename = a.file_name
            mime_type = a.mime_type
            duration = a.duration
            performer = a.performer
            title = a.title
        elif msg.document:
            d = msg.document
            downloadable = d
            filename = d.file_name
            mime_type = d.mime_type
        else:
            failures.append(("message", "No downloadable audio/document payload found"))
            return successes, failures, self.warnings

        # Infer extension
        ext = ""
        if filename and "." in filename:
            ext = "." + filename.rsplit(".", 1)[1]
        elif mime_type:
            ext = mimetypes.guess_extension(mime_type) or ""
        ext = ext.lower()

        # Trim querystrings, Telegram sometimes sends odd names
        if "?" in ext:
            ext = ext.split("?", 1)[0]

        # Validate against whitelist if provided
        if self.allowed_exts and ext and ext not in self.allowed_exts:
            # Hard reject -> failure
            failures.append((filename or "file", f"Extension {ext} not allowed"))
            return successes, failures, self.warnings

        # Build destination path
        dest_dir = self.download_root / self.subdir
        dest_dir.mkdir(parents=True, exist_ok=True)

        safe_name = sanitize_filename(filename or downloadable.file_unique_id + (ext or ""))
        if not safe_name.lower().endswith(ext) and ext:
            safe_name += ext
        dest_path = dest_dir / safe_name

        # Download
        try:
            await bot.download(downloadable, destination=dest_path)  # aiogram convenience. :contentReference[oaicite:8]{index=8}
        except Exception as e:
            failures.append((safe_name, f"Download failed: {e}"))
            return successes, failures, self.warnings

        # Basic metadata
        meta: Dict[str, Any] = {
            "title":        title or filename or safe_name,
            "artist":       performer,
            "album":        None,   # We'll rely on embedded tags; Navidrome reads tags, not path. :contentReference[oaicite:9]{index=9}
            "album_artist": performer,
            "track_number": None,
            "disc_number":  None,
            "release_date": None,
            "genre":        None,
            "duration":     duration,
            "cover_bytes":  None,
            "url":          None,
        }

        successes.append((meta, dest_path))
        return successes, failures, self.warnings
