# main.py
"""
MusicBot Telegram bot: download & tag audio from YouTube / Spotify (+ direct file uploads).

Per-user storage
----------------
All incoming downloads/uploads are now stored under:
    <download_dir>/<user_slug>/

Where `user_slug` is the Telegram @username sanitized to safe filesystem
characters. If a user has no username, we fall back to `id<telegram_id>`.

Features
--------
- URL ingest (YouTube / Spotify) with optional authenticated cookie file.
- Direct uploads: users can send audio/document files; bot validates extension
  and saves under per-user subfolder.
- Unified summary (successes / warnings / errors) for all operations.
- Automatic metadata enrichment via TagLookup (Mutagen + AcoustID + MusicBrainz + extras),
  then sanitized & embedded into files so Navidrome can organize library correctly.

Navidrome relies primarily on embedded tags (Title, Artist, Album, Album Artist, Track No;
plus Genre/Year/Disc strongly recommended) to group and display your library; folder layout
is secondary.  # See Navidrome docs / community guides.
"""

import asyncio
import logging
import re
from pathlib import Path
from typing import Iterable, List, Tuple, Dict, Any, Optional

from aiogram import Bot, Dispatcher, types, F

from config import Config
from detector import URLDetector
from downloaders.youtube import YouTubeDownloader
from downloaders.spotify import SpotifyDownloader
from downloaders.file import FileDownloader
# from downloaders.yandex import YandexDownloader
from metadata import MetadataEmbedder
from utils import setup_logging

from taglookup import TagLookup, LookupConfig


# --------------------------------------------------------------------------- #
# Markdown helpers
# --------------------------------------------------------------------------- #
_MD_ESCAPE_CHARS = ("\\", "`", "*", "_", "{", "}", "[", "]", "(", ")", "#", "+", "-", ".", "!")

def md_escape(text: str) -> str:
    if not text:
        return text
    for ch in _MD_ESCAPE_CHARS:
        text = text.replace(ch, f"\\{ch}")
    return text


def build_summary_md(
    successes: List[Tuple[dict, Path]],
    warnings: List[Tuple[str, str]],
    failures: List[Tuple[str, str]],
) -> str:
    if successes:
        downloaded_files = "\n".join(f"- {md_escape(p.name)}" for _, p in successes)
    else:
        downloaded_files = "None"

    if warnings:
        warning_list = "\n".join(f"- {md_escape(item)}: {md_escape(msg)}" for item, msg in warnings)
    else:
        warning_list = "None"

    if failures:
        error_list = "\n".join(f"- {md_escape(item)}: {md_escape(err)}" for item, err in failures)
    else:
        error_list = "None"

    return (
        "✅ *Download Summary*\n\n"
        f"*Successfully downloaded:*\n{downloaded_files}\n\n"
        f"*Warnings:*\n{warning_list}\n\n"
        f"*Errors:*\n{error_list}"
    )


async def send_chunked(
    message: types.Message,
    text: str,
    *,
    parse_mode: str = "Markdown",
    chunk_size: int = 4000,
):
    if len(text) <= chunk_size:
        await message.reply(text, parse_mode=parse_mode)
        return
    start = 0
    while start < len(text):
        end = min(len(text), start + chunk_size)
        await message.reply(text[start:end], parse_mode=parse_mode)
        start = end


# --------------------------------------------------------------------------- #
# Metadata sanitization helper
# --------------------------------------------------------------------------- #
_NUM_RE = re.compile(r"^\s*(\d+)")
_DATE_RE = re.compile(r"^(\d{4})")

def _first_int(val: Any) -> Optional[int]:
    """
    Coerce common tag representations ('05', '5/12', ['5'], None) to int.
    """
    if val is None:
        return None
    if isinstance(val, int):
        return val
    if isinstance(val, (list, tuple)) and val:
        return _first_int(val[0])
    if isinstance(val, str):
        m = _NUM_RE.match(val)
        if m:
            try:
                return int(m.group(1))
            except ValueError:
                return None
    try:
        return int(val)
    except Exception:
        return None

def _year_from_date(val: Any) -> Optional[str]:
    if not val:
        return None
    if isinstance(val, (list, tuple)) and val:
        return _year_from_date(val[0])
    s = str(val)
    m = _DATE_RE.match(s)
    return m.group(1) if m else None

def _sanitize_for_embed(meta: Dict[str, Any]) -> Dict[str, Any]:
    """
    Produce a shallow copy of meta with values coerced to simple types so Mutagen
    won't reject them (e.g., MultiSpec errors when giving complex lists/None).
    """
    m2 = dict(meta)  # shallow copy

    # ensure required keys exist
    if not m2.get("artist") and m2.get("album_artist"):
        m2["artist"] = m2["album_artist"]
    if not m2.get("album_artist") and m2.get("artist"):
        m2["album_artist"] = m2["artist"]

    # track / disc numbers -> ints
    tn = _first_int(m2.get("track_number"))
    if tn is not None:
        m2["track_number"] = tn
    else:
        m2.pop("track_number", None)

    dn = _first_int(m2.get("disc_number"))
    if dn is not None:
        m2["disc_number"] = dn
    else:
        m2.pop("disc_number", None)

    # date/year normalization
    yr = _year_from_date(m2.get("release_date") or m2.get("date"))
    if yr:
        m2["date"] = yr

    # drop unembeddable large objects (like cover_url) – embedder gets cover_bytes separately
    for drop_key in ("cover_url", "url", "tags", "description", "popularity"):
        if drop_key in m2 and m2[drop_key] is None:
            del m2[drop_key]

    return m2


# --------------------------------------------------------------------------- #
# Username → safe filesystem slug
# --------------------------------------------------------------------------- #
# Allow only Telegram-legal characters plus dash as local convenience;
# strip leading @ if present; fallback to "id<user_id>" if empty.
# Telegram usernames: 5–32 chars, a-z 0-9 underscore. We'll downcase for path.  # noqa: E501
_USER_SAFE_RE = re.compile(r"[^A-Za-z0-9_-]+")

def _user_slug(user: types.User) -> str:
    name = user.username or ""
    if name.startswith("@"):
        name = name[1:]
    name = name.strip()
    if not name:
        return f"id{user.id}"
    # sanitize: replace disallowed chars with underscore
    slug = _USER_SAFE_RE.sub("_", name)
    slug = slug.strip("._-") or f"id{user.id}"
    return slug.lower()


# --------------------------------------------------------------------------- #
# Main bot class
# --------------------------------------------------------------------------- #
class MusicBot:
    def __init__(self):
        # Logging setup
        setup_logging()
        self.log = logging.getLogger("MusicBot")

        # Load configuration
        cfg = Config.load("config.yaml")
        self.bot = Bot(token=cfg.telegram_token)
        self.dp = Dispatcher()
        self.allowed = set(cfg.allowed_users)
        self.base_download_dir = cfg.download_dir  # keep original root

        # Metadata lookup configuration
        ml_cfg_raw = getattr(cfg, "metadata_lookup", {}) or {}
        ml_cfg = LookupConfig(
            enable=ml_cfg_raw.get("enable", True),
            min_confidence=ml_cfg_raw.get("min_confidence", 0.5),
            acoustid_api_key=ml_cfg_raw.get("acoustid_api_key"),
            musicbrainz_useragent=ml_cfg_raw.get("musicbrainz_useragent", "MusicBot/0.1 (contact@example.com)"),
            lastfm_api_key=ml_cfg_raw.get("lastfm_api_key"),
            discogs_user_agent=(ml_cfg_raw.get("discogs") or {}).get("user_agent"),
            discogs_token=(ml_cfg_raw.get("discogs") or {}).get("token"),
            prefer_existing=ml_cfg_raw.get("prefer_existing_tags", True),
            fetch_cover_art=ml_cfg_raw.get("fetch_cover_art", True),
        )
        self.tag_lookup = TagLookup(ml_cfg)

        # Resolve cookie file paths (optional)
        self.yt_cookie: Optional[Path] = None
        try:
            yt_cookie_cfg = getattr(cfg, "cookies", {}).get("youtube")
            if yt_cookie_cfg:
                p = Path(yt_cookie_cfg).expanduser()
                if p.is_file():
                    self.yt_cookie = p
                else:
                    self.log.warning("YouTube cookie file not found at %s", p)
        except Exception as e:  # defensive
            self.log.warning("Cookie config parse error: %s", e)

        # File-upload settings
        fu_cfg = getattr(cfg, "file_upload", {}) or {}
        self.fu_subdir = fu_cfg.get("subdir")  # may be None
        self.fu_allowed_exts = fu_cfg.get("allowed_exts") or []

        # Store Spotify creds for per-user instantiation
        self.spotify_creds = cfg.spotify

        # Yandex downloader (placeholder)
        # self.yandex_creds = cfg.yandex

        self.embedder = MetadataEmbedder()
        self.detector = URLDetector()

        # Register handlers: file first (so docs/audios don't fall through to text detector)
        self.dp.message.register(self.handle_audio_message, F.audio)
        self.dp.message.register(self.handle_document_message, F.document)
        # Then text messages (URLs)
        self.dp.message.register(self.handle_text_message, F.text)

        self.log.info(
            "MusicBot initialized. Base download dir=%s cookie=%s",
            self.base_download_dir,
            self.yt_cookie,
        )

    # ------------------------------------------------------------------ #
    def _user_root(self, user: types.User) -> Path:
        """
        Return/create per-user root directory.
        """
        slug = _user_slug(user)
        root = self.base_download_dir / slug
        root.mkdir(parents=True, exist_ok=True)
        return root

    # ------------------------------------------------------------------ #
    async def _authorized(self, msg: types.Message) -> bool:
        user_id = msg.from_user.id
        if user_id not in self.allowed:
            await msg.reply("❌ You are not in the list of authorized users.")
            return False
        return True

    # ------------------------------------------------------------------ #
    async def handle_audio_message(self, msg: types.Message):
        """
        Handle audio uploads (Telegram 'music' type). Telegram sends performer/title/duration;
        we enrich + embed to produce proper library tags.
        """
        if not await self._authorized(msg):
            return

        user_root = self._user_root(msg.from_user)

        # One-off FileDownloader rooted at this user's folder
        file_dl = FileDownloader(
            user_root,
            subdir=self.fu_subdir,
            allowed_exts=self.fu_allowed_exts,
        )

        await msg.reply("🔄 Received audio file, saving...")

        successes, failures, warnings = await file_dl.download_message(self.bot, msg)

        # Lookup + embed for each file
        new_successes = []
        for meta, path in successes:
            enriched, w = await self.tag_lookup.lookup(path, hints=meta)
            warnings.extend([(path.name, m) for _, m in w])
            try:
                self.embedder.embed(path, _sanitize_for_embed(enriched), enriched.get("cover_bytes"))
            except Exception as e:
                warnings.append((path.name, f"Metadata embed error: {e}"))
            new_successes.append((enriched, path))
        successes = new_successes

        summary = build_summary_md(successes, warnings, failures)
        await send_chunked(msg, summary, parse_mode="Markdown")

    # ------------------------------------------------------------------ #
    async def handle_document_message(self, msg: types.Message):
        """
        Handle generic file uploads (Telegram Document). Uses aiogram download flow.
        """
        if not await self._authorized(msg):
            return

        user_root = self._user_root(msg.from_user)
        file_dl = FileDownloader(
            user_root,
            subdir=self.fu_subdir,
            allowed_exts=self.fu_allowed_exts,
        )

        await msg.reply("🔄 Received file, checking and saving...")

        successes, failures, warnings = await file_dl.download_message(self.bot, msg)

        new_successes = []
        for meta, path in successes:
            enriched, w = await self.tag_lookup.lookup(path, hints=meta)
            warnings.extend([(path.name, m) for _, m in w])
            try:
                self.embedder.embed(path, _sanitize_for_embed(enriched), enriched.get("cover_bytes"))
            except Exception as e:
                warnings.append((path.name, f"Metadata embed error: {e}"))
            new_successes.append((enriched, path))
        successes = new_successes

        summary = build_summary_md(successes, warnings, failures)
        await send_chunked(msg, summary, parse_mode="Markdown")

    # ------------------------------------------------------------------ #
    async def handle_text_message(self, msg: types.Message):
        """
        Handle URL messages (YouTube/Spotify). We tag downloads so Navidrome can group albums.
        """
        if not await self._authorized(msg):
            return

        url = msg.text.strip()
        platform, link_type = self.detector.detect(url)
        if not platform:
            return await msg.reply(
                "❓ Please send a valid link to a track, album, or playlist "
                "(YouTube, Spotify)."
            )

        await msg.reply(f"🔄 Detected {link_type} on {platform}, starting download...")

        # Per-user base
        user_root = self._user_root(msg.from_user)

        # Create one-off downloaders rooted at user_root
        yt_cookie_str = str(self.yt_cookie) if self.yt_cookie else None
        yt_dl = YouTubeDownloader(
            user_root,
            cookie_file=yt_cookie_str,
            enrich_from_ytmusic=False,
            enrich_from_spotify=False,
        )
        sp_dl = SpotifyDownloader(
            user_root,
            self.spotify_creds,
            cookie_file=yt_cookie_str,
        )
        # ym_dl = YandexDownloader(user_root, self.yandex_creds)  # if enabled

        try:
            if platform == "youtube":
                successes, failures, warnings = await yt_dl.download(url, link_type)
            elif platform == "spotify":
                successes, failures, warnings = await sp_dl.download(url, link_type)
            else:
                successes, failures, warnings = [], [(url, "Yandex downloader not enabled.")], []

            # Enrich + embed
            new_successes = []
            for meta, path in successes:
                enriched, w = await self.tag_lookup.lookup(path, hints=meta)
                warnings.extend([(path.name, m) for _, m in w])
                try:
                    self.embedder.embed(path, _sanitize_for_embed(enriched), enriched.get("cover_bytes"))
                except Exception as e:
                    warnings.append((path.name, f"Metadata embed error: {e}"))
                new_successes.append((enriched, path))
            successes = new_successes

        except Exception as e:
            return await msg.reply(f"❗ An unexpected error occurred: {e}")

        summary = build_summary_md(successes, warnings, failures)
        await send_chunked(msg, summary, parse_mode="Markdown")

    # ------------------------------------------------------------------ #
    def run(self):
        self.dp.run_polling(self.bot)


if __name__ == "__main__":
    MusicBot().run()
