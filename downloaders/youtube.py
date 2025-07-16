# downloaders/youtube.py

import yt_dlp
from pathlib import Path
from typing import List, Tuple, Dict, Any

class YouTubeDownloader:
    """
    Downloads YouTube tracks or playlists as MP3 + thumbnail embedding.
    """
    def __init__(self, download_dir: Path):
        self.download_dir = download_dir

    async def download(self, url: str, link_type: str) -> List[Tuple[Dict[str, Any], Path]]:
        if link_type == "track":
            return [await self._download_video(url)]
        else:
            return await self._download_playlist(url)

    async def _download_video(self, url: str) -> Tuple[Dict[str, Any], Path]:
        opts = {
                "format": "bestaudio[ext=m4a]/bestaudio/best",
                "outtmpl": str(self.download_dir / "%(title)s.%(ext)s"),
                "quiet": True,
                "writethumbnail": True,
                "postprocessors": [
                    {"key": "EmbedThumbnail"},
                ],
            }
        try:
            with yt_dlp.YoutubeDL(opts) as ydl:
                info = ydl.extract_info(url, download=True)
        except Exception as e:
            raise RuntimeError(f"YouTube video download failed: {e}")

        # final MP3 path
        filename = ydl.prepare_filename(info)
        path = Path(filename).with_suffix(".mp3")

        meta: Dict[str, Any] = {
            "title": info.get("title"),
            "artist": info.get("uploader"),
            "album": info.get("playlist") or "",
            "release_date": info.get("upload_date"),  # YYYYMMDD
            "duration": info.get("duration"),
            "view_count": info.get("view_count"),
            "like_count": info.get("like_count"),
            "tags": info.get("tags", []),
            "description": info.get("description", ""),
            "cover_bytes": None
        }

        return meta, path

    async def _download_playlist(self, url: str) -> List[Tuple[Dict[str, Any], Path]]:
        opts = {
            "format": "bestaudio/best",
            "outtmpl": str(self.download_dir / "%(playlist)s" / "%(playlist_index)s - %(title)s.%(ext)s"),
            "quiet": True,
            "writethumbnail": True,
            "postprocessors": [
                {"key": "FFmpegExtractAudio", "preferredcodec": "mp3", "preferredquality": "320"},
                {"key": "EmbedThumbnail"},
            ],
        }
        try:
            with yt_dlp.YoutubeDL(opts) as ydl:
                info = ydl.extract_info(url, download=True)
        except Exception as e:
            raise RuntimeError(f"YouTube playlist download failed: {e}")

        results = []
        for entry in info.get("entries", []):
            if not entry:
                continue
            filename = ydl.prepare_filename(entry)
            path = Path(filename).with_suffix(".mp3")
            meta = {
                "title": entry.get("title"),
                "artist": entry.get("uploader"),
                "album": info.get("title"),
                "release_date": entry.get("upload_date"),
                "duration": entry.get("duration"),
                "view_count": entry.get("view_count"),
                "tags": entry.get("tags", []),
                "description": entry.get("description", ""),
                "cover_bytes": None
            }
            results.append((meta, path))
        return results
