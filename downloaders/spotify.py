# downloaders/spotify.py

import re
import time
import asyncio
import subprocess
from functools import partial
from pathlib import Path
from typing import List, Tuple, Dict, Any, Optional

import mimetypes
import requests
import spotipy
from spotipy.oauth2 import SpotifyClientCredentials


def sanitize_filename(name: str) -> str:
    """Replace filesystem-unsafe characters with underscores."""
    return re.sub(r'[\\/:"*?<>|]+', "_", name)


# Type aliases
SuccessItem = Tuple[Dict[str, Any], Path]
FailItem    = Tuple[str, str]
WarnItem    = Tuple[str, str]


class SpotifyDownloader:
    """
    Download Spotify tracks/albums/playlists via the spotDL CLI.

    spotDL uses Spotify for metadata but actually fetches audio from providers
    such as YouTube / YouTube Music via yt-dlp. Supplying an authenticated
    cookies.txt (Netscape format) with `--cookie-file` lets spotDL/yt-dlp
    access age-restricted or account-gated content and, when using a YouTube
    Music Premium account + appropriate format (e.g., M4A/OPUS + `--bitrate disable`),
    can yield higher-bitrate sources.
    """

    MAX_RETRIES   = 3
    INITIAL_DELAY = 5  # seconds

    def __init__(
        self,
        download_dir: Path,
        creds: Dict[str, str],
        output_template: Optional[str] = None,
        cookie_file: Optional[str | Path] = None,
    ):
        """
        :param download_dir: Root download directory.
        :param creds: {'client_id': ..., 'client_secret': ...}
        :param output_template: Optional spotDL --output template string.
        :param cookie_file: Path to Netscape cookies.txt for account auth / higher quality sources.
        """
        self.download_dir    = download_dir
        self.client_id       = creds["client_id"]
        self.client_secret   = creds["client_secret"]
        self.output_template = output_template
        self.cookie_file     = Path(cookie_file) if cookie_file else None

        # Spotipy client for metadata
        auth = SpotifyClientCredentials(
            client_id=self.client_id,
            client_secret=self.client_secret,
        )
        self.sp = spotipy.Spotify(auth_manager=auth)

        # Per-download warnings
        self.warnings: List[WarnItem] = []

    # ------------------------------------------------------------------ #
    # Internal helpers
    # ------------------------------------------------------------------ #
    def _run_spotdl(self, cmd: List[str], context: str):
        """
        Run spotDL CLI with retries on failure. Record warnings
        on each failed attempt before final failure.
        """
        delay = self.INITIAL_DELAY
        for attempt in range(1, self.MAX_RETRIES + 1):
            try:
                subprocess.run(
                    cmd,
                    check=True,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                )
                return
            except subprocess.CalledProcessError as e:
                err_txt = e.stderr.strip() if e.stderr else str(e)
                self.warnings.append((context, f"spotDL failed on attempt {attempt}: {err_txt}"))
                if attempt == self.MAX_RETRIES:
                    raise RuntimeError(
                        f"spotDL CLI failed after {self.MAX_RETRIES} attempts: {err_txt}"
                    )
                time.sleep(delay)
                delay *= 2

    def _fetch_album_metadata(
        self,
        album_url: str,
    ) -> Tuple[str, str, List[Dict[str, Any]], str]:
        """
        Fetch full album info and return:
            album_name, cover_url, track_objs, primary_artist
        """
        m = re.search(r"album/([0-9A-Za-z]+)", album_url)
        if not m:
            raise RuntimeError("Invalid Spotify album URL")
        album_id = m.group(1)

        alb = self.sp.album(album_id)
        album_name     = alb["name"]
        cover_url      = alb["images"][0]["url"] if alb["images"] else None
        primary_artist = alb["artists"][0]["name"] if alb.get("artists") else "Unknown Artist"

        # Spotify album() returns up to 50 tracks; page if needed
        tracks = list(alb["tracks"]["items"])
        while alb["tracks"]["next"]:
            alb["tracks"] = self.sp.next(alb["tracks"])
            tracks.extend(alb["tracks"]["items"])

        # Order by disc_number + track_number
        tracks.sort(key=lambda t: (t.get("disc_number", 1), t.get("track_number", 0)))
        return album_name, cover_url, tracks, primary_artist

    def _fetch_playlist_metadata(
        self,
        playlist_url: str,
    ) -> Tuple[str, str, List[Dict[str, Any]], str]:
        """
        Fetch playlist info and return:
            playlist_name, cover_url, track_objs, primary_owner
        """
        m = re.search(r"playlist/([0-9A-Za-z]+)", playlist_url)
        if not m:
            raise RuntimeError("Invalid Spotify playlist URL")
        playlist_id = m.group(1)

        pl = self.sp.playlist(playlist_id)
        playlist_name = pl["name"]
        cover_url     = pl["images"][0]["url"] if pl["images"] else None
        primary_owner = pl["owner"]["display_name"] or "Playlist"

        # Paginated items
        tracks: List[Dict[str, Any]] = []
        results = pl["tracks"]
        while True:
            for item in results["items"]:
                tr = item.get("track")
                if tr:
                    tracks.append(tr)
            if results["next"]:
                results = self.sp.next(results)
            else:
                break

        return playlist_name, cover_url, tracks, primary_owner

    @staticmethod
    def _maybe_download_cover(cover_url: Optional[str], out_dir: Path) -> None:
        """
        Скачивает обложку альбома в out_dir как cover.*,
        если нет файлов cover.*, folder.* или front.*.
        """
        if not cover_url:
            return

        # Проверяем существующие файлы (CoverArtPriority)
        for pattern in ("cover.*", "folder.*", "front.*"):
            if any(out_dir.glob(pattern)):
                return

        try:
            resp = requests.get(cover_url, timeout=10)
            resp.raise_for_status()

            # Выясняем расширение из Content-Type или URL
            mime = resp.headers.get("Content-Type", "").split(";")[0].strip()
            ext = mimetypes.guess_extension(mime) or Path(cover_url).suffix or ".jpg"
            if ext.lower() in {".jpeg", ".jpe"}:
                ext = ".jpg"

            file_path = out_dir / f"cover{ext}"
            with open(file_path, "wb") as fh:
                fh.write(resp.content)
        except Exception as e:
            # Записываем предупреждение, если не удалось скачать обложку
            # (обработка происходит через self.warnings)
            # Здесь контекст — имя папки
            warn_msg = f"cover download failed: {e}"
            SpotifyDownloader._record_warning_static(out_dir.name, warn_msg)

    @staticmethod
    def _record_warning_static(context: str, message: str) -> None:
        """
        Вспомогательный метод для записи предупреждений из статического контекста.
        Реальную реализацию можно перенести в _run_spotdl или другой центр.
        """
        # Реализуйте логику, например, ставьте в глобальный лог
        pass

    # ------------------------------------------------------------------ #
    # Sync worker
    # ------------------------------------------------------------------ #
    def _sync_download(self, url: str, link_type: str) -> List[SuccessItem]:
        if link_type == "track":
            return self._download_track(url)

        if link_type == "album":
            name, cover_url, tracks, primary = self._fetch_album_metadata(url)
        else:
            name, cover_url, tracks, primary = self._fetch_playlist_metadata(url)

        dir_name = sanitize_filename(f"{primary} - {name}")
        out_dir = self.download_dir / dir_name
        out_dir.mkdir(parents=True, exist_ok=True)

        # Скачиваем обложку альбома/плейлиста
        self._maybe_download_cover(cover_url, out_dir)

        context = dir_name
        out_template = (
            self.output_template
            or str(out_dir / "{track-number} - {artists} - {title}.{output-ext}")
        )

        cmd = [
            "spotdl",
            url,
            "--output",
            out_template,
            "--format",
            "m4a",
            "--overwrite",
            "skip",
        ]
        if self.cookie_file:
            cmd.extend(["--cookie-file", str(self.cookie_file)])

        self._run_spotdl(cmd, context)

        m4a_files = sorted(out_dir.glob("*.m4a"))
        results: List[SuccessItem] = []
        for data, file_path in zip(tracks, m4a_files):
            meta = self._track_meta_from_spotify_obj(data)
            results.append((meta, file_path))

        return results

    def _download_track(self, url: str) -> List[SuccessItem]:
        context = url
        out_template = (
            self.output_template
            or str(self.download_dir / "{artists} - {title}.{output-ext}")
        )
        cmd = [
            "spotdl",
            url,
            "--output",
            out_template,
            "--format",
            "m4a",
            "--overwrite",
            "skip",
        ]
        if self.cookie_file:
            cmd.extend(["--cookie-file", str(self.cookie_file)])

        self._run_spotdl(cmd, context)

        files = sorted(self.download_dir.glob("*.m4a"), key=lambda p: p.stat().st_mtime)
        if not files:
            raise RuntimeError("No file downloaded for track")

        tr_id = re.search(r"track/([0-9A-Za-z]+)", url)
        data = self.sp.track(tr_id.group(1)) if tr_id else {}
        meta = self._track_meta_from_spotify_obj(data) if data else {}
        return [(meta, files[-1])]

    @staticmethod
    def _track_meta_from_spotify_obj(data: Dict[str, Any]) -> Dict[str, Any]:
        if not data:
            return {}
        album = data.get("album", {})
        return {
            "title":        data.get("name"),
            "artist":       ", ".join(a["name"] for a in data.get("artists", [])),
            "album":        album.get("name"),
            "album_artist": ", ".join(a["name"] for a in album.get("artists", [])),
            "track_number": data.get("track_number"),
            "disc_number":  data.get("disc_number"),
            "release_date": album.get("release_date"),
            "genre":        None,
            "duration":     (data.get("duration_ms") or 0) // 1000,
            "isrc":         (data.get("external_ids") or {}).get("isrc"),
            "popularity":   data.get("popularity"),
            "cover_url":    (album.get("images") or [{}])[0].get("url"),
            "cover_bytes":  None,
            "url":          (data.get("external_urls") or {}).get("spotify"),
        }

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
        func = partial(self._sync_download, url, link_type)

        try:
            successes = await loop.run_in_executor(None, func)
            return successes, [], self.warnings
        except Exception as e:
            return [], [(url, str(e))], self.warnings
