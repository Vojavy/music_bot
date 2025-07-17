# taglookup.py
"""
Tag lookup / enrichment pipeline.

Steps:
 1. Read existing tags via Mutagen. (Supports MP3, FLAC, MP4/M4A, Ogg/Opus, AIFF, etc.)  # :contentReference[oaicite:14]{index=14}
 2. Parse filename / user hints.
 3. Acoustic fingerprint via Chromaprint + AcoustID -> MusicBrainz IDs.  # :contentReference[oaicite:15]{index=15}
 4. Fetch structured metadata from MusicBrainz Web API (artist/album/track, track numbers, dates).  # :contentReference[oaicite:16]{index=16}
 5. Fetch cover art from Cover Art Archive by Release MBID.  # :contentReference[oaicite:17]{index=17}
 6. Optional extras: Last.fm (genre/toptags); Discogs (label/year/genre).  # :contentReference[oaicite:18]{index=18}
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import aiohttp
import mutagen  # :contentReference[oaicite:19]{index=19}

try:
    import acoustid  # pyacoustid wrapper  # :contentReference[oaicite:20]{index=20}
except ImportError:  # pragma: no cover
    acoustid = None

# ------------------------------------------------------------------ #
@dataclass
class LookupConfig:
    enable: bool = True
    min_confidence: float = 0.5
    acoustid_api_key: Optional[str] = None
    musicbrainz_useragent: str = "MusicBot/0.1 (contact@example.com)"
    lastfm_api_key: Optional[str] = None
    discogs_user_agent: Optional[str] = None
    discogs_token: Optional[str] = None
    prefer_existing: bool = True
    fetch_cover_art: bool = True


class TagLookup:
    def __init__(self, cfg: LookupConfig):
        self.cfg = cfg
        self.log = logging.getLogger("TagLookup")

    # ------------------------------------------------------------------ #
    async def lookup(self, path: Path, hints: Optional[Dict[str, Any]] = None) -> Tuple[Dict[str, Any], List[Tuple[str, str]]]:
        """
        Return (meta, warnings). Never raise (unless catastrophic).
        """
        warnings: List[Tuple[str, str]] = []
        hints = hints or {}
        meta = {}

        # 1) Existing tags
        try:
            file_tags = self._read_existing_tags(path)
            meta.update(file_tags)
        except Exception as e:
            warnings.append((path.name, f"Tag read failed: {e}"))

        # 2) Merge hints (Telegram performer/title etc.)
        for k, v in hints.items():
            if v and (not self.cfg.prefer_existing or not meta.get(k)):
                meta[k] = v

        # If disabled -> return what we have
        if not self.cfg.enable:
            return meta, warnings

        # Collect candidate artist/title for lookups
        artist_hint = meta.get("artist") or hints.get("artist")
        title_hint  = meta.get("title")  or hints.get("title")

        # 3) Acoustic fingerprint -> MBIDs
        recording_mbid = None
        release_mbid = None
        if acoustid and self.cfg.acoustid_api_key:
            try:
                recording_mbid, release_mbid = await self._fp_lookup(path, artist_hint, title_hint, warnings)
            except Exception as e:
                warnings.append((path.name, f"AcoustID lookup failed: {e}"))

        # 4) MusicBrainz metadata
        if recording_mbid or release_mbid or artist_hint or title_hint:
            try:
                mb_meta = await self._musicbrainz_metadata(
                    recording_mbid=recording_mbid,
                    release_mbid=release_mbid,
                    artist=artist_hint,
                    title=title_hint,
                )
                self._merge(meta, mb_meta)
            except Exception as e:
                warnings.append((path.name, f"MusicBrainz fetch failed: {e}"))

        # 5) Cover art
        if self.cfg.fetch_cover_art and not meta.get("cover_bytes"):
            mbid_for_art = release_mbid or (mb_meta.get("release_mbid") if 'mb_meta' in locals() else None)
            if mbid_for_art:
                try:
                    art_bytes = await self._cover_art_fetch(mbid_for_art)
                    if art_bytes:
                        meta["cover_bytes"] = art_bytes
                except Exception as e:
                    warnings.append((path.name, f"Cover art fetch failed: {e}"))

        # 6) Last.fm enrichment (genres, corrected names)
        if self.cfg.lastfm_api_key and (artist_hint or title_hint):
            try:
                lf_meta = await self._lastfm_enrich(artist_hint, title_hint, recording_mbid)
                self._merge(meta, lf_meta)
            except Exception as e:
                warnings.append((path.name, f"Last.fm enrich failed: {e}"))

        # 7) Discogs enrichment (label/year/genres)
        if self.cfg.discogs_user_agent and (artist_hint or meta.get("album")):
            try:
                dc_meta = await self._discogs_enrich(
                    artist=artist_hint,
                    album=meta.get("album"),
                )
                self._merge(meta, dc_meta)
            except Exception as e:
                warnings.append((path.name, f"Discogs enrich failed: {e}"))

        return meta, warnings

    # ------------------------------------------------------------------ #
    def _read_existing_tags(self, path: Path) -> Dict[str, Any]:
        """
        Using mutagen to read tags from many audio formats.  # :contentReference[oaicite:21]{index=21}
        """
        f = mutagen.File(path, easy=True)
        if not f:
            return {}
        get = lambda key: (f.tags.get(key)[0] if key in f.tags else None)
        return {
            "title": get("title"),
            "artist": get("artist"),
            "album": get("album"),
            "album_artist": get("albumartist") or get("album artist"),
            "track_number": get("tracknumber"),
            "disc_number": get("discnumber"),
            "genre": get("genre"),
            "date": get("date"),
        }

    # ------------------------------------------------------------------ #
    async def _fp_lookup(self, path: Path, artist: Optional[str], title: Optional[str], warnings: List[Tuple[str,str]]):
        """
        Chromaprint/AcoustID fingerprint -> candidate MBIDs.  # :contentReference[oaicite:22]{index=22}
        """
        loop = asyncio.get_running_loop()

        def _blocking():
            # returns (duration, fingerprint)
            import acoustid  # local import safety
            return acoustid.fingerprint_file(str(path))

        duration, fp = await loop.run_in_executor(None, _blocking)

        def _lookup():
            import acoustid
            return acoustid.lookup(self.cfg.acoustid_api_key, fp, duration)

        resp = await loop.run_in_executor(None, _lookup)
        # resp: (score, recording_ids, etc.)
        # See pyacoustid readme; we parse top result
        best_rec = None
        best_score = 0.0
        for score, rid, title_cand, artist_cand in resp["results"]:
            if score > best_score:
                best_score = score
                best_rec = rid
                if not artist:
                    artist = artist_cand
                if not title:
                    title = title_cand
        if best_score < self.cfg.min_confidence:
            warnings.append((path.name, f"Low AcoustID score {best_score:.2f}"))
            return None, None

        # pyacoustid can also return 'recordings' objects w/ MBIDs
        recording_mbid = None
        release_mbid = None
        for res in resp["results"]:
            # pattern differs across API versions; defensive
            recs = res.get("recordings") or []
            if recs:
                recording_mbid = recs[0].get("id")
                rels = recs[0].get("releasegroups") or []
                if rels:
                    release_mbid = rels[0].get("id")
                break

        return recording_mbid, release_mbid

    # ------------------------------------------------------------------ #
    async def _musicbrainz_metadata(
        self,
        *,
        recording_mbid: Optional[str],
        release_mbid: Optional[str],
        artist: Optional[str],
        title: Optional[str],
    ) -> Dict[str, Any]:
        """
        Query MusicBrainz Web API for recording / release info.  # :contentReference[oaicite:23]{index=23}
        """
        headers = {"User-Agent": self.cfg.musicbrainz_useragent, "Accept": "application/json"}
        base = "https://musicbrainz.org/ws/2"
        async with aiohttp.ClientSession(headers=headers) as sess:
            # Prefer direct MBID lookup
            if recording_mbid:
                url = f"{base}/recording/{recording_mbid}?inc=artists+releases&fmt=json"
                async with sess.get(url) as r:
                    data = await r.json()
                    return self._meta_from_mb_recording(data)
            if release_mbid:
                url = f"{base}/release/{release_mbid}?inc=artists+recordings&fmt=json"
                async with sess.get(url) as r:
                    data = await r.json()
                    return self._meta_from_mb_release(data)
            # Fallback search
            q_parts = []
            if artist:
                q_parts.append(f'artist:"{artist}"')
            if title:
                q_parts.append(f'track:"{title}"')
            query = " AND ".join(q_parts) or title or artist
            url = f"{base}/recording/?query={aiohttp.helpers.quote(query)}&limit=1&fmt=json"
            async with sess.get(url) as r:
                data = await r.json()
            recs = data.get("recordings") or []
            if not recs:
                return {}
            return self._meta_from_mb_recording(recs[0])

    def _meta_from_mb_recording(self, data: Dict[str, Any]) -> Dict[str, Any]:
        # Very defensive parse; MB schema is rich.  # :contentReference[oaicite:24]{index=24}
        title = data.get("title")
        # first artist credit
        credits = data.get("artist-credit") or []
        artist_names = []
        for c in credits:
            if isinstance(c, dict) and "name" in c:
                artist_names.append(c["name"])
            elif isinstance(c, str):
                artist_names.append(c)
        album = None
        track_number = None
        release_mbid = None
        if data.get("releases"):
            rel = data["releases"][0]
            album = rel.get("title")
            release_mbid = rel.get("id")
            # Some releases embed track + position
            if rel.get("track-count"):
                track_number = rel.get("track-count")
        return {
            "title": title,
            "artist": ", ".join(artist_names) or None,
            "album": album,
            "track_number": track_number,
            "release_mbid": release_mbid,
        }

    def _meta_from_mb_release(self, data: Dict[str, Any]) -> Dict[str, Any]:
        album = data.get("title")
        release_mbid = data.get("id")
        # artists
        credits = data.get("artist-credit") or []
        artist_names = []
        for c in credits:
            if isinstance(c, dict) and "name" in c:
                artist_names.append(c["name"])
            elif isinstance(c, str):
                artist_names.append(c)
        date = data.get("date")
        # track parsing omitted for brevity
        return {
            "album": album,
            "album_artist": ", ".join(artist_names) or None,
            "release_date": date,
            "release_mbid": release_mbid,
        }

    # ------------------------------------------------------------------ #
    async def _cover_art_fetch(self, release_mbid: str) -> Optional[bytes]:
        """
        Fetch "front" image bytes from Cover Art Archive.  # :contentReference[oaicite:25]{index=25}
        """
        url = f"https://coverartarchive.org/release/{release_mbid}/front-500"  # 500px thumb
        async with aiohttp.ClientSession() as sess:
            async with sess.get(url) as r:
                if r.status == 200:
                    return await r.read()
        return None

    # ------------------------------------------------------------------ #
    async def _lastfm_enrich(self, artist: Optional[str], title: Optional[str], mbid: Optional[str]) -> Dict[str, Any]:
        """
        Use Last.fm track.getInfo for genre tags, corrected names, duration.  # :contentReference[oaicite:26]{index=26}
        """
        if not self.cfg.lastfm_api_key:
            return {}
        params = {
            "method": "track.getInfo",
            "api_key": self.cfg.lastfm_api_key,
            "format": "json",
        }
        if mbid:
            params["mbid"] = mbid
        else:
            if artist:
                params["artist"] = artist
            if title:
                params["track"] = title
            params["autocorrect"] = "1"
        async with aiohttp.ClientSession() as sess:
            async with sess.get("https://ws.audioscrobbler.com/2.0/", params=params) as r:
                data = await r.json()
        tr = data.get("track") or {}
        upd = {}
        if "name" in tr:
            upd["title"] = tr["name"]
        if "artist" in tr and isinstance(tr["artist"], dict):
            upd["artist"] = tr["artist"].get("name")
        if "album" in tr and isinstance(tr["album"], dict):
            upd["album"] = tr["album"].get("title")
        if "duration" in tr:
            try:
                upd["duration"] = int(tr["duration"]) // 1000
            except Exception:
                pass
        # top tags -> genre join
        tags = tr.get("toptags", {}).get("tag", [])
        if tags:
            upd["genre"] = ", ".join(t["name"] for t in tags if "name" in t)
        return upd

    # ------------------------------------------------------------------ #
    async def _discogs_enrich(self, artist: Optional[str], album: Optional[str]) -> Dict[str, Any]:
        """
        Minimal Discogs lookup: first search release by artist+album.  # :contentReference[oaicite:27]{index=27}
        """
        if not self.cfg.discogs_user_agent:
            return {}
        headers = {"User-Agent": self.cfg.discogs_user_agent}
        params = {"type": "release"}
        if artist:
            params["artist"] = artist
        if album:
            params["release_title"] = album
        if self.cfg.discogs_token:
            params["token"] = self.cfg.discogs_token
        async with aiohttp.ClientSession(headers=headers) as sess:
            async with sess.get("https://api.discogs.com/database/search", params=params) as r:
                data = await r.json()
        results = data.get("results") or []
        if not results:
            return {}
        rel = results[0]
        genres = rel.get("genre") or []
        year = rel.get("year")
        cover_url = rel.get("cover_image")
        return {
            "genre": ", ".join(genres) if genres else None,
            "release_date": str(year) if year else None,
            "cover_url": cover_url,
        }

    # ------------------------------------------------------------------ #
    @staticmethod
    def _merge(dst: Dict[str, Any], src: Dict[str, Any]):
        """
        Merge in src where dst lacks value.
        """
        for k, v in src.items():
            if v is None:
                continue
            if not dst.get(k):
                dst[k] = v
