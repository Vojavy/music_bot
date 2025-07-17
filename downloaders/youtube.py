# downloaders/youtube.py

import re
import time
import asyncio
from functools import partial
from pathlib import Path
from typing import List, Tuple, Dict, Any, Optional

import yt_dlp

# Optional imports: YouTube Music and Spotify metadata enrichment
try:
    from ytmusicapi import YTMusic  # type: ignore
except ImportError:  # pragma: no cover
    YTMusic = None  # We'll check at runtime

try:
    import spotipy  # type: ignore
    from spotipy.oauth2 import SpotifyClientCredentials  # type: ignore
except ImportError:  # pragma: no cover
    spotipy = None
    SpotifyClientCredentials = None

# Return types
SuccessItem = Tuple[Dict[str, Any], Path]
WarnItem    = Tuple[str, str]
FailItem    = Tuple[str, str]

MAX_RETRIES   = 3
INITIAL_DELAY = 5  # seconds


def sanitize_filename(name: str) -> str:
    """Replace filesystem-unsafe characters with underscores."""
    return re.sub(r'[\\/:"*?<>|]+', "_", name)


# ---------------------- #
# Normalization helpers  #
# ---------------------- #
_NUM_RE   = re.compile(r"^\s*(\d+)")
_DATE_RE  = re.compile(r"^(\d{4})")
_SPLIT_RE = re.compile(r"\s*[-–]\s*")

def _coerce_int(v: Any) -> Optional[int]:
    if v is None:
        return None
    if isinstance(v, int):
        return v
    if isinstance(v, (list, tuple)) and v:
        return _coerce_int(v[0])
    s = str(v)
    m = _NUM_RE.match(s)
    if m:
        try:
            return int(m.group(1))
        except ValueError:
            return None
    try:
        return int(s)
    except Exception:
        return None

def _year_from(v: Any) -> Optional[str]:
    if not v:
        return None
    if isinstance(v, (list, tuple)) and v:
        return _year_from(v[0])
    s = str(v)
    m = _DATE_RE.match(s)
    return m.group(1) if m else None

def _split_artist_title(text: Optional[str]) -> Tuple[Optional[str], Optional[str]]:
    """
    Simple heuristic: 'Artist - Title'.
    """
    if not text:
        return None, None
    parts = _SPLIT_RE.split(text, maxsplit=1)
    if len(parts) != 2:
        return None, None
    return parts[0].strip() or None, parts[1].strip() or None

def _normalize_meta_for_export(base: Dict[str, Any], *, platform: str) -> Dict[str, Any]:
    """
    Produce a shallow copy of base meta, coercing values into simple
    scalar forms that downstream embedders (Mutagen) accept cleanly.
    We also add a couple of source hints for later TagLookup merging.
    """
    m = dict(base)  # shallow copy

    # Guarantee artist/album_artist consistency
    if not m.get("artist") and m.get("album_artist"):
        m["artist"] = m["album_artist"]
    if not m.get("album_artist") and m.get("artist"):
        m["album_artist"] = m["artist"]

    # Track / disc numbers
    tn = _coerce_int(m.get("track_number"))
    if tn is not None:
        m["track_number"] = tn
    else:
        m.pop("track_number", None)

    dn = _coerce_int(m.get("disc_number"))
    if dn is not None:
        m["disc_number"] = dn
    else:
        m.pop("disc_number", None)

    # Year (for ID3/MP4 'date' convenience)
    yr = _year_from(m.get("release_date"))
    if yr:
        m["date"] = yr

    # Remove huge / non-embeddable fields (downstream embedder may ignore anyway)
    for k in ("description", "tags", "view_count", "like_count", "popularity"):
        m.pop(k, None)

    # Source hints (for TagLookup merge logic)
    m.setdefault("source_platform", platform)
    if m.get("url"):
        m.setdefault("source_url", m["url"])

    return m


class YouTubeDownloader:
    """
    Download audio from YouTube (video or playlist) as M4A, embed thumbnail,
    and optionally enrich metadata from YouTube Music and/or Spotify.

    NOTE ON OUTPUT TEMPLATE:
      Use comma-separated fallback fields, e.g. '%(uploader,channel)s - %(title)s.%(ext)s'.
      Nested format fields inside the '&' conditional operator are not supported by yt-dlp's
      outtmpl parser. See yt-dlp output template documentation for details.  # noqa: E501
    """

    def __init__(
        self,
        download_dir: Path,
        *,
        cookie_file: Optional[str | Path] = None,
        enrich_from_ytmusic: bool = False,
        enrich_from_spotify: bool = False,
        spotify_creds: Optional[Dict[str, str]] = None,
        # Output template override. If None, sensible defaults are used per link_type.
        output_template_track: Optional[str] = None,
        output_template_playlist: Optional[str] = None,
    ):
        self.download_dir = download_dir

        # Normalize cookie path (allow str)
        self.cookie_file: Optional[Path] = Path(cookie_file) if cookie_file else None

        # enrichment flags
        self.enrich_from_ytmusic  = enrich_from_ytmusic and YTMusic is not None
        self.enrich_from_spotify  = enrich_from_spotify and spotipy is not None

        # init YTMusic client if requested
        self.ytmusic = None
        if self.enrich_from_ytmusic:
            try:
                self.ytmusic = YTMusic()  # anonymous unless auth headers provided
            except Exception as e:  # degrade gracefully
                self.enrich_from_ytmusic = False
                print(f"[YouTubeDownloader] YTMusic init failed: {e}")

        # init Spotify client if requested
        self.sp = None
        if self.enrich_from_spotify and spotify_creds and SpotifyClientCredentials:
            try:
                auth = SpotifyClientCredentials(
                    client_id=spotify_creds["client_id"],
                    client_secret=spotify_creds["client_secret"],
                )
                self.sp = spotipy.Spotify(auth_manager=auth)
            except Exception as e:
                self.enrich_from_spotify = False
                print(f"[YouTubeDownloader] Spotify client init failed: {e}")

        # custom output templates (yt-dlp style)
        self.output_template_track    = output_template_track
        self.output_template_playlist = output_template_playlist

        # per-download warnings
        self.warnings: List[WarnItem] = []

    # ------------------------------------------------------------------ #
    # Public async API
    # ------------------------------------------------------------------ #
    async def download(
        self,
        url: str,
        link_type: str,
    ) -> Tuple[List[SuccessItem], List[FailItem], List[WarnItem]]:
        """
        Async entrypoint: off-load blocking work into a thread.
        """
        loop = asyncio.get_running_loop()
        self.warnings = []
        fn = partial(self._sync_download, url, link_type)
        try:
            successes = await loop.run_in_executor(None, fn)
            return successes, [], self.warnings
        except Exception as e:
            return [], [(url, str(e))], self.warnings

    # ------------------------------------------------------------------ #
    # Internal sync worker dispatch
    # ------------------------------------------------------------------ #
    def _sync_download(self, url: str, link_type: str) -> List[SuccessItem]:
        if link_type == "track":
            return self._download_single(url)
        else:
            # treat everything else as playlist (channel uploads, watch later, etc.)
            return self._download_playlist(url)

    # ------------------------------------------------------------------ #
    # yt-dlp runner with retries
    # ------------------------------------------------------------------ #
    def _run_ytdlp(self, opts: Dict[str, Any], context: str) -> Tuple[yt_dlp.YoutubeDL, dict]:
        """
        Invoke yt-dlp with retries, return (ydl, info_dict).
        """
        delay = INITIAL_DELAY
        last_exc: Optional[Exception] = None
        for attempt in range(1, MAX_RETRIES + 1):
            try:
                ydl = yt_dlp.YoutubeDL(opts)
                info = ydl.extract_info(opts["url"], download=True)
                return ydl, info
            except Exception as e:  # broad; yt-dlp throws many
                last_exc = e
                self.warnings.append((context, f"yt-dlp failed on attempt {attempt}: {e}"))
                if attempt == MAX_RETRIES:
                    raise RuntimeError(f"YouTube download failed after {MAX_RETRIES} attempts: {e}")
                time.sleep(delay)
                delay *= 2
        raise RuntimeError(f"yt-dlp failed: {last_exc}")

    # ------------------------------------------------------------------ #
    # Helper: build common yt-dlp options block
    # ------------------------------------------------------------------ #
    def _base_opts(self) -> Dict[str, Any]:
        """
        Build a baseline set of yt-dlp options we reuse.
        """
        opts: Dict[str, Any] = {
            "format":         "bestaudio[ext=m4a]/bestaudio/best",
            "quiet":          True,
            "writethumbnail": True,
            "postprocessors": [
                {"key": "EmbedThumbnail"},
                {"key": "FFmpegMetadata"},  # ask yt-dlp/ffmpeg to write basic tags
            ],
        }
        if self.cookie_file:
            opts["cookiefile"] = str(self.cookie_file)
        return opts

    # ------------------------------------------------------------------ #
    # Single video download
    # ------------------------------------------------------------------ #
    def _download_single(self, url: str) -> List[SuccessItem]:
        outtmpl = (
            self.output_template_track
            or str(self.download_dir / "%(uploader,channel)s - %(title)s.%(ext)s")
        )
        opts = self._base_opts()
        opts.update({"url": url, "outtmpl": outtmpl})

        ydl, info = self._run_ytdlp(opts, context=url)

        raw_path = Path(ydl.prepare_filename(info))
        path = raw_path.with_suffix(".m4a")  # expected audio format

        meta = self._meta_from_info_dict(info, playlist_title=None, playlist_index=None)
        meta.update(self._enrich_metadata(meta))
        meta = _normalize_meta_for_export(meta, platform="youtube")

        return [(meta, path)]

    # ------------------------------------------------------------------ #
    # Playlist download
    # ------------------------------------------------------------------ #
    def _download_playlist(self, url: str) -> List[SuccessItem]:
        probe_title = "(playlist)"
        probe_uploader = ""
        try:
            probe_opts = {"url": url, "quiet": True, "skip_download": True}
            if self.cookie_file:
                probe_opts["cookiefile"] = str(self.cookie_file)
            with yt_dlp.YoutubeDL(probe_opts) as probe:
                info = probe.extract_info(url, download=False)
                probe_title = info.get("title", probe_title)
                probe_uploader = info.get("uploader") or info.get("channel") or ""
        except Exception as e:
            self.warnings.append((url, f"yt-dlp probe failed: {e}"))

        folder = sanitize_filename(f"{probe_uploader} - {probe_title}" if probe_uploader else probe_title)
        pl_dir = self.download_dir / folder
        pl_dir.mkdir(parents=True, exist_ok=True)

        outtmpl = (
            self.output_template_playlist
            or str(pl_dir / "%(playlist_index)03d - %(title)s.%(ext)s")
        )
        opts = self._base_opts()
        opts.update({"url": url, "outtmpl": outtmpl})
        self._run_ytdlp(opts, context=folder)

        files = sorted(pl_dir.glob("*.m4a"))

        # Re-probe entries
        entries: List[dict] = []
        try:
            probe_opts = {"url": url, "quiet": True, "skip_download": True}
            if self.cookie_file:
                probe_opts["cookiefile"] = str(self.cookie_file)
            with yt_dlp.YoutubeDL(probe_opts) as probe:
                info = probe.extract_info(url, download=False)
                entries = info.get("entries", []) or []
        except Exception as e:
            self.warnings.append((url, f"yt-dlp re-probe failed: {e}"))

        results: List[SuccessItem] = []
        for idx, p in enumerate(files):
            entry = entries[idx] if idx < len(entries) else {}
            playlist_index = entry.get("playlist_index") or (idx + 1)
            meta = self._meta_from_info_dict(
                entry,
                playlist_title=probe_title,
                playlist_index=playlist_index,
            )
            meta.update(self._enrich_metadata(meta))
            meta = _normalize_meta_for_export(meta, platform="youtube")
            results.append((meta, p))

        return results

    # ------------------------------------------------------------------ #
    # Base metadata extraction from yt-dlp info dict
    # ------------------------------------------------------------------ #
    def _meta_from_info_dict(
        self,
        info: Dict[str, Any],
        *,
        playlist_title: Optional[str],
        playlist_index: Optional[int],
    ) -> Dict[str, Any]:
        title   = info.get("title")
        artist  = info.get("artist") or info.get("uploader") or info.get("channel")
        album   = info.get("album") or playlist_title or ""
        track   = info.get("track") or title
        track_number = info.get("track_number") or playlist_index
        release_date = info.get("release_date") or info.get("upload_date")

        meta: Dict[str, Any] = {
            "title":        track or title,
            "artist":       artist,
            "album":        album,
            "album_artist": artist,
            "track_number": track_number,
            "disc_number":  None,
            "release_date": release_date,
            "genre":        None,
            "duration":     info.get("duration"),
            "view_count":   info.get("view_count"),
            "like_count":   info.get("like_count"),
            "tags":         info.get("tags", []),
            "description":  info.get("description", ""),
            "cover_bytes":  None,  # rely on EmbedThumbnail; TagLookup may fetch better art
            "url":          info.get("webpage_url") or info.get("original_url"),
        }
        return meta

    # ------------------------------------------------------------------ #
    # Metadata enrichment pipeline
    # ------------------------------------------------------------------ #
    def _enrich_metadata(self, meta: Dict[str, Any]) -> Dict[str, Any]:
        parsed_artist, parsed_title = _split_artist_title(meta["title"])
        updates: Dict[str, Any] = {}
        if not meta.get("artist") and parsed_artist:
            updates["artist"] = parsed_artist
        if parsed_title and parsed_title != meta["title"]:
            updates["title"] = parsed_title

        # YT Music first
        if self.enrich_from_ytmusic and self.ytmusic:
            yt_upd = self._enrich_from_ytmusic(
                artist=updates.get("artist") or meta.get("artist"),
                title=updates.get("title") or meta.get("title"),
            )
            updates.update({k: v for k, v in yt_upd.items() if v is not None})

        # Spotify fallback for album/track no.
        need_album = not (updates.get("album") or meta.get("album"))
        need_trkno = updates.get("track_number") is None and meta.get("track_number") is None
        if self.enrich_from_spotify and self.sp and (need_album or need_trkno):
            sp_upd = self._enrich_from_spotify(
                artist=updates.get("artist") or meta.get("artist"),
                title=updates.get("title") or meta.get("title"),
            )
            updates.update({k: v for k, v in sp_upd.items() if v is not None})

        return updates

    # ------------------------------------------------------------------ #
    # YT Music enrichment
    # ------------------------------------------------------------------ #
    def _enrich_from_ytmusic(self, artist: Optional[str], title: Optional[str]) -> Dict[str, Any]:
        if not (artist or title) or not self.ytmusic:
            return {}
        query = " ".join(x for x in [artist, title] if x)
        try:
            search = self.ytmusic.search(query, filter="songs", limit=1)
        except Exception as e:
            self.warnings.append(("ytmusic", f"YTMusic search failed: {e}"))
            return {}
        if not search:
            return {}
        song = search[0]
        upd: Dict[str, Any] = {
            "title":        song.get("title") or title,
            "artist":       ", ".join(a["name"] for a in song.get("artists", [])) or artist,
            "album":        (song.get("album") or {}).get("name"),
            "album_artist": (song.get("album") or {}).get("name"),
            "duration":     song.get("duration_seconds"),
        }
        thumbs = song.get("thumbnails") or []
        if thumbs:
            upd["cover_url"] = thumbs[-1].get("url")
        return upd

    # ------------------------------------------------------------------ #
    # Spotify enrichment
    # ------------------------------------------------------------------ #
    def _enrich_from_spotify(self, artist: Optional[str], title: Optional[str]) -> Dict[str, Any]:
        if not self.sp or not (artist or title):
            return {}
        q_parts = []
        if title:
            q_parts.append(f'track:"{title}"')
        if artist:
            q_parts.append(f'artist:"{artist}"')
        q = " ".join(q_parts) if q_parts else title or artist
        try:
            resp = self.sp.search(q=q, type="track", limit=1)
        except Exception as e:
            self.warnings.append(("spotify", f"Spotify search failed: {e}"))
            return {}
        items = resp.get("tracks", {}).get("items", [])
        if not items:
            return {}
        tr = items[0]
        alb = tr.get("album", {})
        upd: Dict[str, Any] = {
            "title":        tr.get("name") or title,
            "artist":       ", ".join(a["name"] for a in tr.get("artists", [])) or artist,
            "album":        alb.get("name"),
            "album_artist": ", ".join(a["name"] for a in alb.get("artists", [])),
            "track_number": tr.get("track_number"),
            "disc_number":  tr.get("disc_number"),
            "release_date": alb.get("release_date"),
            "duration":     (tr.get("duration_ms") or 0) // 1000,
            "isrc":         (tr.get("external_ids") or {}).get("isrc"),
            "popularity":   tr.get("popularity"),
            "cover_url":    (alb.get("images") or [{}])[0].get("url"),
        }
        return upd
