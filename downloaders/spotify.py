# downloaders/spotify.py

import re
import time
import requests
import asyncio
import subprocess
from functools import partial
from pathlib import Path
from typing import List, Tuple, Dict, Any

import spotipy
from spotipy.oauth2 import SpotifyClientCredentials
from mutagen.easyid3 import EasyID3

def sanitize_filename(name: str) -> str:
    """Replace filesystem‐unsafe characters with underscores."""
    return re.sub(r'[\\/:"*?<>|]+', '_', name)

class SpotifyDownloader:
    """
    Downloads Spotify tracks/albums/playlists via the spotdl CLI,
    with retry logic and sorting by ID3 tracknumber.
    """
    MAX_RETRIES = 3
    INITIAL_DELAY = 5  # seconds

    def __init__(self, download_dir: Path, creds: Dict[str, str]):
        self.download_dir  = download_dir
        self.client_id     = creds["client_id"]
        self.client_secret = creds["client_secret"]

        # Spotipy client for metadata only
        auth = SpotifyClientCredentials(
            client_id=self.client_id,
            client_secret=self.client_secret
        )
        self.sp = spotipy.Spotify(auth_manager=auth)

    def _run_spotdl(self, cmd: List[str]):
        """
        Run spotdl CLI with retries on failure.
        """
        delay = self.INITIAL_DELAY
        for attempt in range(self.MAX_RETRIES):
            try:
                subprocess.run(cmd, check=True)
                return
            except subprocess.CalledProcessError as e:
                if attempt == self.MAX_RETRIES - 1:
                    raise RuntimeError(
                        f"spotdl CLI failed after {self.MAX_RETRIES} attempts: {e}"
                    )
                time.sleep(delay)
                delay *= 2

    def _fetch_album_metadata(self, album_url: str) -> Tuple[str, str, List[str]]:
        """
        Return (album_name, cover_url, track_urls) for the given album URL.
        """
        m = re.search(r"album/([0-9A-Za-z]+)", album_url)
        if not m:
            raise RuntimeError("Invalid Spotify album URL")
        album_id = m.group(1)

        alb = self.sp.album(album_id)
        album_name = alb["name"]
        cover_url = alb["images"][0]["url"] if alb["images"] else None

        track_urls = [
            f"https://open.spotify.com/track/{t['id']}"
            for t in alb["tracks"]["items"]
        ]
        return album_name, cover_url, track_urls

    def _sync_download(
        self,
        url: str,
        link_type: str
    ) -> List[Tuple[Dict[str, Any], Path]]:
        """
        Blocking download worker, run in ThreadPoolExecutor.
        """
        # Single‐track download
        if link_type == "track":
            out_template = str(self.download_dir / "%(title)s.%(ext)s")
            cmd = [
                "spotdl", url,
                "--output", out_template,
                "--format", "mp3",
                "--overwrite", "skip"
            ]
            self._run_spotdl(cmd)
            files = sorted(self.download_dir.glob("*.mp3"))
            if not files:
                raise RuntimeError("No file downloaded for track")
            return [({}, files[-1])]

        # Album or playlist
        album_name, cover_url, track_urls = self._fetch_album_metadata(url)
        safe_album = sanitize_filename(album_name)
        album_dir = self.download_dir / safe_album
        album_dir.mkdir(parents=True, exist_ok=True)

        # download with numeric prefixes
        out_template = str(album_dir)
        cmd = [
            "spotdl", url,
            "--output", out_template,
            "--format", "m4a",
            "--overwrite", "skip"
        ]
        self._run_spotdl(cmd)

        # collect and sort by ID3 tracknumber
        mp3_files = list(album_dir.glob("*.mp3"))
        def get_track_number(path: Path) -> int:
            try:
                tags = EasyID3(str(path))
                tn = tags.get("tracknumber", ["0"])[0]
                return int(tn.split("/")[0])
            except Exception:
                return 0
        mp3_files.sort(key=get_track_number)

        # build metadata list via Spotipy
        results: List[Tuple[Dict[str, Any], Path]] = []
        for idx, file_path in enumerate(mp3_files):
            data = self.sp.track(track_urls[idx])
            meta: Dict[str, Any] = {
                "title":        data["name"],
                "artist":       ", ".join(a["name"] for a in data["artists"]),
                "album":        album_name,
                "track_number": data.get("track_number"),
                "disc_number":  data.get("disc_number"),
                "release_date": data["album"]["release_date"],
                "genre":        None,
                "duration":     data["duration_ms"] // 1000,
                "isrc":         data["external_ids"].get("isrc"),
                "popularity":   data.get("popularity"),
                "cover_bytes":  None,
            }
            results.append((meta, file_path))

        return results

    async def download(
        self,
        url: str,
        link_type: str
    ) -> List[Tuple[Dict[str, Any], Path]]:
        """
        Async entrypoint: off‑load blocking work into a thread.
        """
        loop = asyncio.get_running_loop()
        func = partial(self._sync_download, url, link_type)
        try:
            return await loop.run_in_executor(None, func)
        except Exception as e:
            raise RuntimeError(f"Spotify download failed ({link_type}): {e}")
