# config.py
import yaml
from pathlib import Path
from typing import Dict, Any

class Config:
    """
    Configuration loader for the MusicBot.
    """
    def __init__(
        self,
        telegram_token: str,
        allowed_users: list[int],
        download_dir: str,
        spotify: Dict[str, Any],
        yandex: Dict[str, Any]
    ):
        self.telegram_token = telegram_token
        self.allowed_users = allowed_users
        self.download_dir = Path(download_dir)
        self.spotify = spotify
        self.yandex = yandex

    @staticmethod
    def load(path: str) -> "Config":
        """
        Load and parse the YAML config from the given file path.
        """
        with open(path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f)
        return Config(
            telegram_token = data["telegram_token"],
            allowed_users  = data["allowed_users"],
            download_dir   = data["download_dir"],
            spotify        = data.get("spotify", {}),
            yandex         = data.get("yandex", {})
        )
