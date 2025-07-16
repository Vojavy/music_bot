# detector.py

import re
from typing import Optional, Tuple

class URLDetector:
    """
    Detects platform (youtube/spotify/yandex) and link type (track/album/playlist)
    from a given URL.
    """
    YT_TRACK    = re.compile(r'(?:youtu\.be/|youtube\.com/watch\?v=)[\w-]+')
    YT_PLAYLIST = re.compile(r'(?:youtube\.com/(?:playlist\?list=|watch.*?&list=))[\w-]+')

    SP_TRACK    = re.compile(r'open\.spotify\.com/track/[\w]+')
    SP_ALBUM    = re.compile(r'open\.spotify\.com/album/[\w]+')
    SP_PLAYLIST = re.compile(r'open\.spotify\.com/playlist/[\w]+')

    YA_TRACK    = re.compile(r'music\.yandex\.ru/track/\d+')
    YA_ALBUM    = re.compile(r'music\.yandex\.ru/album/\d+')
    YA_PLAYLIST = re.compile(r'music\.yandex\.ru/users/[^/]+/playlists/\d+')

    def detect(self, url: str) -> Tuple[Optional[str], Optional[str]]:
        """
        Returns (platform, link_type) or (None, None) if no match.
        link_type is one of: "track", "album", "playlist".
        """
        if self.YT_TRACK.search(url):
            return "youtube", "track"
        if self.YT_PLAYLIST.search(url):
            return "youtube", "playlist"

        if self.SP_TRACK.search(url):
            return "spotify", "track"
        if self.SP_ALBUM.search(url):
            return "spotify", "album"
        if self.SP_PLAYLIST.search(url):
            return "spotify", "playlist"

        if self.YA_TRACK.search(url):
            return "yandex", "track"
        if self.YA_ALBUM.search(url):
            return "yandex", "album"
        if self.YA_PLAYLIST.search(url):
            return "yandex", "playlist"

        return None, None
