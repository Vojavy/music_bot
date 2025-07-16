# downloaders/spotify.py

from spotdl import Spotdl
from pathlib import Path
from typing import List, Tuple, Dict, Any

class SpotifyDownloader:
    """
    Downloads Spotify tracks, albums or playlists via spotdl.
    """
    def __init__(self, download_dir: Path, creds: Dict[str, str]):
        self.download_dir = download_dir
        self.client_id = creds.get("client_id")
        self.client_secret = creds.get("client_secret")

    async def download(self, url: str, link_type: str) -> List[Tuple[Dict[str, Any], Path]]:
        try:
            spot = Spotdl(
                save_format="mp3",
                output=str(self.download_dir),
                quality="320",
                quiet=True,
                client_id=self.client_id,
                client_secret=self.client_secret
            )
            paths = spot.download_track([url])
        except Exception as e:
            raise RuntimeError(f"Spotify download failed ({link_type}): {e}")

        results: List[Tuple[Dict[str, Any], Path]] = []
        for p in paths:
            meta: Dict[str, Any] = {
                "title": None,
                "artist": None,
                "album": None,
                "track_number": None,
                "disc_number": None,
                "release_date": None,
                "genre": None,
                "isrc": None,
                "duration": None,
                "popularity": None,
                "cover_bytes": None
            }
            results.append((meta, Path(p)))
        return results
