# config.py
"""
Centralised YAML → dataclass loader for MusicBot.

•  Читает все обязательные ключи и сохраняет «неизвестные» поля,
   чтобы при расширении config.yaml код бота не падал.
•  Поддерживает новые секции:
      - cookies
      - file_upload
      - metadata_lookup
   (Если в YAML эти разделы отсутствуют, будут установлены пустые dict.)
•  Путь download_dir сразу приводится к pathlib.Path.
"""

from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml


class Config:
    """
    Lightweight container for bot settings.
    """

    # ----------------------------- #
    # Required fields
    # ----------------------------- #
    telegram_token: str
    allowed_users: List[int]
    download_dir: Path

    # ----------------------------- #
    # Optional structured sections
    # ----------------------------- #
    spotify: Dict[str, Any]
    yandex: Dict[str, Any]
    cookies: Dict[str, Any]
    file_upload: Dict[str, Any]
    metadata_lookup: Dict[str, Any]

    # ----------------------------- #
    # Constructor
    # ----------------------------- #
    def __init__(
        self,
        telegram_token: str,
        allowed_users: List[int],
        download_dir: str | Path,
        *,
        spotify: Optional[Dict[str, Any]] = None,
        yandex: Optional[Dict[str, Any]] = None,
        cookies: Optional[Dict[str, Any]] = None,
        file_upload: Optional[Dict[str, Any]] = None,
        metadata_lookup: Optional[Dict[str, Any]] = None,
        **unknown: Any,  # future-proofing: capture unexpected keys
    ) -> None:
        # required
        self.telegram_token = telegram_token
        self.allowed_users = allowed_users
        self.download_dir = Path(download_dir)

        # optional sections
        self.spotify = spotify or {}
        self.yandex = yandex or {}
        self.cookies = cookies or {}
        self.file_upload = file_upload or {}
        self.metadata_lookup = metadata_lookup or {}

        # preserve unknown keys (won’t be used directly but keep for debugging)
        for k, v in unknown.items():
            setattr(self, k, v)

    # ----------------------------- #
    # YAML loader
    # ----------------------------- #
    @staticmethod
    def load(path: str | Path) -> "Config":
        """
        Parse YAML into Config instance.

        Any top-level keys not explicitly listed in __init__ will still be
        attached to the resulting object (forward compatibility).
        """
        path = Path(path)
        with path.open("r", encoding="utf-8") as fh:
            data = yaml.safe_load(fh) or {}

        # Expand `download_dir` tilde/user variables if present
        if "download_dir" in data:
            data["download_dir"] = str(Path(data["download_dir"]).expanduser())

        return Config(**data)  # type: ignore[arg-type]
