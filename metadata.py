# metadata.py

from mutagen.id3 import (
    ID3, TIT2, TPE1, TALB, TPE2, TRCK, TPOS, TDRC, TCON,
    TCOM, TPUB, TSRC, TBPM, USLT, COMM, TCOP, TENC, WXXX,
    APIC, TXXX
)
from mutagen.mp3 import MP3, HeaderNotFoundError
from pathlib import Path
from typing import Dict, Any

class MetadataEmbedder:
    """
    Embeds a full set of ID3 tags into an MP3 file.
    If tags already exist, updates them in-place without error.
    """
    def embed(self, filepath: Path, meta: Dict[str, Any], cover_bytes: bytes = None):
        """
        Embed ID3 tags into the MP3 at filepath.

        :param filepath: Path to the MP3 file.
        :param meta: Dictionary of metadata fields.
        :param cover_bytes: Raw bytes of the cover image, if available.
        
        Supported meta keys:
          - title, artist, album, album_artist
          - track_number, disc_number
          - release_date
          - genre, composer, publisher
          - isrc, bpm
          - lyrics, comment, copyright
          - encoder, url
          - popularity, mood, scene
        """
        # Step 1: load the file and ensure an ID3 tag container exists
        try:
            audio = MP3(str(filepath), ID3=ID3)
        except HeaderNotFoundError:
            raise RuntimeError(f"File at {filepath} is not a valid MP3.")
        except Exception as e:
            raise RuntimeError(f"Cannot open MP3 for tagging: {e}")

        # If no tags, add an empty tag set
        if audio.tags is None:
            try:
                audio.add_tags()
            except Exception:
                # If tags already exist or cannot be added, continue anyway
                pass

        # Step 2: set or update frames
        try:
            # Basic frames
            audio["TIT2"] = TIT2(encoding=3, text=meta.get("title", ""))
            audio["TPE1"] = TPE1(encoding=3, text=meta.get("artist", ""))
            audio["TALB"] = TALB(encoding=3, text=meta.get("album", ""))

            # Album artist
            if meta.get("album_artist"):
                audio["TPE2"] = TPE2(encoding=3, text=meta["album_artist"])
            # Track and disc numbers
            if meta.get("track_number") is not None:
                audio["TRCK"] = TRCK(encoding=3, text=str(meta["track_number"]))
            if meta.get("disc_number") is not None:
                audio["TPOS"] = TPOS(encoding=3, text=str(meta["disc_number"]))
            # Release date
            if meta.get("release_date"):
                audio["TDRC"] = TDRC(encoding=3, text=str(meta["release_date"]))
            # Genre, composer, publisher
            if meta.get("genre"):
                audio["TCON"] = TCON(encoding=3, text=meta["genre"])
            if meta.get("composer"):
                audio["TCOM"] = TCOM(encoding=3, text=meta["composer"])
            if meta.get("publisher"):
                audio["TPUB"] = TPUB(encoding=3, text=meta["publisher"])
            # ISRC and BPM
            if meta.get("isrc"):
                audio["TSRC"] = TSRC(encoding=3, text=meta["isrc"])
            if meta.get("bpm"):
                audio["TBPM"] = TBPM(encoding=3, text=str(meta["bpm"]))

            # Lyrics and comments
            if meta.get("lyrics"):
                audio["USLT"] = USLT(encoding=3, desc="Lyrics", text=meta["lyrics"])
            if meta.get("comment"):
                audio["COMM"] = COMM(
                    encoding=3, lang="eng", desc="Comment", text=meta["comment"]
                )
            if meta.get("copyright"):
                audio["TCOP"] = TCOP(encoding=3, text=meta["copyright"])

            # Encoder and URL
            if meta.get("encoder"):
                audio["TENC"] = TENC(encoding=3, text=meta["encoder"])
            if meta.get("url"):
                audio["WXXX"] = WXXX(encoding=3, desc="Original URL", url=meta["url"])

            # Custom text frames
            for key in ("popularity", "mood", "scene"):
                if meta.get(key) is not None:
                    frame_id = f"TXXX:{key.upper()}"
                    audio[frame_id] = TXXX(
                        encoding=3, desc=key, text=str(meta[key])
                    )

            # Album art
            if cover_bytes:
                audio["APIC"] = APIC(
                    encoding=3,
                    mime="image/jpeg",
                    type=3,
                    desc="Cover",
                    data=cover_bytes
                )

            # Finally, save all tags back to the file
            audio.save()
        except Exception as e:
            raise RuntimeError(f"Metadata embedding failed: {e}")
