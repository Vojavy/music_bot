# downloaders/yandex.py

import re
from yandex_music import Client as YaClient
from pathlib import Path
from typing import List, Tuple, Dict, Any

class YandexDownloader:
    """
    Downloads Yandex.Music tracks, albums or playlists via yandex-music-api.
    """
    def __init__(self, download_dir: Path, creds: Dict[str, Any]):
        self.download_dir = download_dir
        self.client = YaClient()
        if creds.get("username") and creds.get("password"):
            self.client.init(creds["username"], creds["password"])

    async def download(self, url: str, link_type: str) -> List[Tuple[Dict[str, Any], Path]]:
        try:
            if link_type == "track":
                return [self._download_track(url)]
            if link_type == "album":
                return self._download_album(url)
            return self._download_playlist(url)
        except Exception as e:
            raise RuntimeError(f"Yandex.Music download failed ({link_type}): {e}")

    def _download_track(self, url: str) -> Tuple[Dict[str, Any], Path]:
        track = self.client.track(url)
        filename = f"{track.artists[0].name} – {track.title}.mp3"
        dest = self.download_dir / filename
        audio = track.fetch_audio()
        dest.write_bytes(audio)
        cover = track.fetch_covers()[0] if track.fetch_covers() else None

        meta: Dict[str, Any] = {
            "title": track.title,
            "artist": track.artists[0].name,
            "album": "",
            "track_number": None,
            "disc_number": None,
            "release_date": None,
            "genre": None,
            "duration": track.duration_ms,
            "cover_bytes": cover
        }
        return meta, dest

    def _download_album(self, url: str) -> List[Tuple[Dict[str, Any], Path]]:
        album_id = int(re.search(r"/album/(\d+)", url).group(1))
        album = self.client.albums_with_tracks(album_id)
        results: List[Tuple[Dict[str, Any], Path]] = []
        for volume in album.volumes:
            for short in volume:
                full = short.fetch_track()
                filename = f"{short.artists[0].name} – {short.title}.mp3"
                dest = self.download_dir / filename
                dest.write_bytes(full.fetch_audio())
                cover = full.fetch_covers()[0] if full.fetch_covers() else None

                meta: Dict[str, Any] = {
                    "title": short.title,
                    "artist": ", ".join(a.name for a in short.artists),
                    "album": album.title,
                    "track_number": short.track_no,
                    "disc_number": short.disc_no,
                    "release_date": album.year,
                    "genre": None,
                    "duration": short.duration_ms,
                    "cover_bytes": cover
                }
                results.append((meta, dest))
        return results

    def _download_playlist(self, url: str) -> List[Tuple[Dict[str, Any], Path]]:
        m = re.search(r"/users/([^/]+)/playlists/(\d+)", url)
        user, pid = m.group(1), int(m.group(2))
        playlist = self.client.users_playlists(user, pid)
        shorts = playlist.fetch_tracks()
        results: List[Tuple[Dict[str, Any], Path]] = []
        for short in shorts:
            full = short.fetch_track()
            filename = f"{short.artists[0].name} – {short.title}.mp3"
            dest = self.download_dir / filename
            dest.write_bytes(full.fetch_audio())
            cover = full.fetch_covers()[0] if full.fetch_covers() else None

            meta: Dict[str, Any] = {
                "title": short.title,
                "artist": ", ".join(a.name for a in short.artists),
                "album": playlist.title,
                "track_number": short.track_no,
                "disc_number": short.disc_no,
                "release_date": playlist.created,
                "genre": None,
                "duration": short.duration_ms,
                "cover_bytes": cover
            }
            results.append((meta, dest))
        return results
