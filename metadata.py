# metadata.py

from pathlib import Path
from typing import Dict, Any, Optional

# MP3 / ID3 imports
from mutagen.id3 import (
    ID3, TIT2, TPE1, TALB, TPE2, TRCK, TPOS, TDRC, TCON,
    TCOM, TPUB, TSRC, TBPM, USLT, COMM, TCOP, TENC, WXXX,
    APIC, TXXX,
)
from mutagen.mp3 import MP3, HeaderNotFoundError

# MP4 / M4A imports
from mutagen.mp4 import MP4, MP4Cover


class MetadataEmbedder:
    """
    Embed tags into audio files. Detects container by filename extension.

    - MP3: Writes ID3 frames (v2) with Mutagen MP3/ID3 APIs.
    - M4A/MP4 (AAC in MP4 container): Writes MP4 atoms using Mutagen MP4 APIs.
    """

    def embed(self, filepath: Path, meta: Dict[str, Any], cover_bytes: Optional[bytes] = None):
        suffix = filepath.suffix.lower()
        if suffix == ".mp3":
            self._embed_mp3(filepath, meta, cover_bytes)
        elif suffix in (".m4a", ".mp4"):
            self._embed_m4a(filepath, meta, cover_bytes)
        else:
            # Unknown format: silently ignore or raise? choose warn-style exception so caller can record warning
            raise RuntimeError(f"Unsupported tagging format: {suffix}")

    # ------------------------------------------------------------------ #
    # MP3 tagging
    # ------------------------------------------------------------------ #
    def _embed_mp3(self, filepath: Path, meta: Dict[str, Any], cover_bytes: Optional[bytes]):
        """
        Embed ID3 tags into an MP3 file.
        """
        try:
            audio = MP3(str(filepath), ID3=ID3)
        except HeaderNotFoundError:
            raise RuntimeError(f"File at {filepath} is not a valid MP3.")
        except Exception as e:
            raise RuntimeError(f"Cannot open MP3 for tagging: {e}")

        if audio.tags is None:
            try:
                audio.add_tags()
            except Exception:
                pass

        try:
            audio["TIT2"] = TIT2(encoding=3, text=meta.get("title", ""))
            audio["TPE1"] = TPE1(encoding=3, text=meta.get("artist", ""))
            audio["TALB"] = TALB(encoding=3, text=meta.get("album", ""))

            if meta.get("album_artist"):
                audio["TPE2"] = TPE2(encoding=3, text=meta["album_artist"])
            if meta.get("track_number") is not None:
                audio["TRCK"] = TRCK(encoding=3, text=str(meta["track_number"]))
            if meta.get("disc_number") is not None:
                audio["TPOS"] = TPOS(encoding=3, text=str(meta["disc_number"]))
            if meta.get("release_date"):
                audio["TDRC"] = TDRC(encoding=3, text=str(meta["release_date"]))
            if meta.get("genre"):
                audio["TCON"] = TCON(encoding=3, text=meta["genre"])
            if meta.get("composer"):
                audio["TCOM"] = TCOM(encoding=3, text=meta["composer"])
            if meta.get("publisher"):
                audio["TPUB"] = TPUB(encoding=3, text=meta["publisher"])
            if meta.get("isrc"):
                audio["TSRC"] = TSRC(encoding=3, text=meta["isrc"])
            if meta.get("bpm"):
                audio["TBPM"] = TBPM(encoding=3, text=str(meta["bpm"]))
            if meta.get("lyrics"):
                audio["USLT"] = USLT(encoding=3, desc="Lyrics", text=meta["lyrics"])
            if meta.get("comment"):
                audio["COMM"] = COMM(encoding=3, lang="eng", desc="Comment", text=meta["comment"])
            if meta.get("copyright"):
                audio["TCOP"] = TCOP(encoding=3, text=meta["copyright"])
            if meta.get("encoder"):
                audio["TENC"] = TENC(encoding=3, text=meta["encoder"])
            if meta.get("url"):
                audio["WXXX"] = WXXX(encoding=3, desc="Original URL", url=meta["url"])
            for key in ("popularity", "mood", "scene"):
                if meta.get(key) is not None:
                    frame_id = f"TXXX:{key.upper()}"
                    audio[frame_id] = TXXX(encoding=3, desc=key, text=str(meta[key]))

            if cover_bytes:
                audio["APIC"] = APIC(
                    encoding=3,
                    mime="image/jpeg",
                    type=3,
                    desc="Cover",
                    data=cover_bytes,
                )

            audio.save()
        except Exception as e:
            raise RuntimeError(f"Metadata embedding failed: {e}")

    # ------------------------------------------------------------------ #
    # M4A tagging
    # ------------------------------------------------------------------ #
    def _embed_m4a(self, filepath: Path, meta: Dict[str, Any], cover_bytes: Optional[bytes]):
        """
        Embed MP4-style tags into an M4A/MP4 audio file.
        Uses common iTunes-compatible atoms.
        """
        try:
            mp4 = MP4(str(filepath))
        except Exception as e:
            raise RuntimeError(f"Cannot open M4A for tagging: {e}")

        tags = mp4.tags or {}

        # Basic atoms
        tags["\xa9nam"] = meta.get("title", "") or ""
        tags["\xa9ART"] = meta.get("artist", "") or ""
        tags["\xa9alb"] = meta.get("album", "") or ""
        if meta.get("album_artist"):
            tags["aART"] = meta["album_artist"]

        # Track & disc numbers: mutagen expects [(track, total)] tuples; if total unknown use 0
        trk = meta.get("track_number")
        trk_total = 0  # we don't currently know total; could fetch album total_tracks if desired
        if trk is not None:
            tags["trkn"] = [(int(trk), int(trk_total))]

        dsk = meta.get("disc_number")
        dsk_total = 0
        if dsk is not None:
            tags["disk"] = [(int(dsk), int(dsk_total))]

        # Year / date
        if meta.get("release_date"):
            tags["\xa9day"] = str(meta["release_date"])

        if meta.get("genre"):
            tags["\xa9gen"] = meta["genre"]

        # ISRC: MP4 freeform atom; common mean/name pair used by iTunes
        if meta.get("isrc"):
            tags["----:com.apple.iTunes:ISRC"] = [meta["isrc"].encode("utf-8")]

        # URL (custom freeform)
        if meta.get("url"):
            tags["----:com.apple.iTunes:URL"] = [meta["url"].encode("utf-8")]

        # Popularity (custom)
        if meta.get("popularity") is not None:
            tags["----:com.apple.iTunes:POPM"] = [str(meta["popularity"]).encode("utf-8")]

        # Cover art
        if cover_bytes:
            # Assume JPEG; caller can decide
            tags["covr"] = [MP4Cover(cover_bytes, imageformat=MP4Cover.FORMAT_JPEG)]

        mp4.tags = tags
        try:
            mp4.save()
        except Exception as e:
            raise RuntimeError(f"M4A metadata embedding failed: {e}")
