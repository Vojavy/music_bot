# main.py
import asyncio
from aiogram import Bot, Dispatcher, types, F
from config import Config
from detector import URLDetector
from downloaders.youtube import YouTubeDownloader
from downloaders.spotify import SpotifyDownloader
# from downloaders.yandex import YandexDownloader
from metadata import MetadataEmbedder
from utils import setup_logging

class MusicBot:
    def __init__(self):
        # Logging setup
        setup_logging()

        # Load configuration
        cfg = Config.load("config.yaml")
        self.bot = Bot(token=cfg.telegram_token)
        self.dp = Dispatcher()
        self.allowed = set(cfg.allowed_users)
        self.download_dir = cfg.download_dir

        # Initialize components
        self.detector = URLDetector()
        self.yt = YouTubeDownloader(self.download_dir)
        self.sp = SpotifyDownloader(self.download_dir, cfg.spotify)
        # self.ym = YandexDownloader(self.download_dir, cfg.yandex)
        self.embedder = MetadataEmbedder()

        # Register handler
        self.dp.message.register(self.handle_message, F.text)

    async def handle_message(self, msg: types.Message):
        user_id = msg.from_user.id
        if user_id not in self.allowed:
            return await msg.reply("❌ You are not in the list of authorized users.")

        url = msg.text.strip()
        platform, link_type = self.detector.detect(url)
        if not platform:
            return await msg.reply(
                "❓ Please send a valid link to a track, album, or playlist "
                "(YouTube, Spotify, Yandex.Music)."
            )

        await msg.reply(f"🔄 Detected {link_type} on {platform}, starting download...")

        try:
            if platform == "youtube":
                results = await self.yt.download(url, link_type)
            elif platform == "spotify":
                results = await self.sp.download(url, link_type)
            else:  # yandex
                results = await self.ym.download(url, link_type)

            # Embed metadata
            for meta, path in results:
                try:
                    self.embedder.embed(path, meta, meta.get("cover_bytes"))
                except Exception as e:
                    # If embedding fails, notify but continue
                    await msg.reply(f"⚠️ Metadata embedding error for {path.name}: {e}")

        except Exception as e:
            # For any other exception, return the exception message
            return await msg.reply(f"❗ An error occurred: {e}")

        files = "\n".join(p.name for _, p in results)
        await msg.reply(f"✅ Done! Saved:\n{files}")

    def run(self):
        self.dp.run_polling(self.bot)

if __name__ == "__main__":
    MusicBot().run()
