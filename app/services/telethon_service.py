import io
import logging
from telethon import TelegramClient
from telethon.sessions import StringSession
from telethon.tl.types import DocumentAttributeFilename

from app.config import (
    TELEGRAM_API_ID,
    TELEGRAM_API_HASH,
    TELETHON_SESSION_STRING,
    BOT_TOKEN,
    STORAGE_CHANNEL_ID,
)

logger = logging.getLogger(__name__)


class TelethonService:
    """
    MTProto client wrapper using Telethon.

    Authenticates using the bot token so no user account or interactive
    phone-verification step is required. Uploads and downloads are performed
    over the MTProto protocol directly — NOT through the HTTP Bot API — which
    means the 50 MB upload ceiling and 20 MB download ceiling of the Bot API
    do not apply here.

    Bot accounts authenticated via MTProto can upload up to 2 GB and download
    files of any size that exist in channels the bot has access to.
    """

    def __init__(self):
        self.client: TelegramClient | None = None
        self._session_string: str = TELETHON_SESSION_STRING or ""

    async def connect(self):
        """Initialise and connect the Telethon MTProto client."""
        if not TELEGRAM_API_ID or not TELEGRAM_API_HASH:
            logger.warning(
                "TELEGRAM_API_ID / TELEGRAM_API_HASH not configured — "
                "Telethon MTProto service will be unavailable."
            )
            return

        try:
            self.client = TelegramClient(
                StringSession(self._session_string),
                TELEGRAM_API_ID,
                TELEGRAM_API_HASH,
            )
            # Authenticate as the bot (no phone/code prompt needed)
            await self.client.start(bot_token=BOT_TOKEN)

            # Persist the session string so it survives restarts
            self._session_string = self.client.session.save()
            logger.info("Telethon MTProto client connected and authenticated.")
        except Exception as e:
            logger.error(f"Failed to connect Telethon client: {e}")
            self.client = None

    async def disconnect(self):
        """Gracefully disconnect the Telethon client."""
        if self.client and self.client.is_connected():
            await self.client.disconnect()
            logger.info("Telethon MTProto client disconnected.")

    @property
    def is_available(self) -> bool:
        return self.client is not None and self.client.is_connected()

    # ------------------------------------------------------------------
    # Upload
    # ------------------------------------------------------------------

    async def upload_file(
        self,
        file_bytes: bytes,
        filename: str,
        caption: str = "",
        uploader_id: int | None = None,
    ) -> tuple[str, str, int]:
        """
        Upload a file to the Telegram storage channel via MTProto.

        Returns (file_id, file_unique_id, message_id) where:
          - file_id       → "tl_msg_{message_id}"  (internal Telethon marker)
          - file_unique_id → Telegram document access hash as hex string
          - message_id    → actual Telegram message ID in the channel
        """
        if not self.is_available:
            raise RuntimeError("Telethon MTProto client is not connected.")

        buffer = io.BytesIO(file_bytes)
        buffer.name = filename  # Telethon uses .name for the filename hint

        # Upload the file bytes to Telegram's DC (chunked automatically)
        tl_file = await self.client.upload_file(buffer, file_name=filename)

        # Build the inline keyboard markup string for captions
        # (Telethon sends the caption directly; aiogram moderation buttons
        #  are set separately via bot.edit_message_reply_markup after upload)
        sent = await self.client.send_file(
            entity=STORAGE_CHANNEL_ID,
            file=tl_file,
            caption=caption,
            force_document=True,       # always send as document, never auto-compressed
            attributes=[DocumentAttributeFilename(file_name=filename)],
        )

        doc = sent.document
        if doc is None:
            raise ValueError("Telegram returned a message with no document attachment.")

        message_id = sent.id
        # Use a prefixed marker so the /raw/ endpoint knows to fetch via Telethon
        file_id = f"tl_msg_{message_id}"
        file_unique_id = hex(doc.access_hash & 0xFFFFFFFFFFFFFFFF)

        logger.info(
            f"Telethon upload complete — message_id={message_id}, "
            f"size={doc.size} bytes, file={filename}"
        )
        return file_id, file_unique_id, message_id

    # ------------------------------------------------------------------
    # Download / Stream
    # ------------------------------------------------------------------

    async def download_file(self, message_id: int) -> bytes:
        """
        Download any file from the storage channel by its message ID.
        Works for files of any size (no 20 MB Bot-API cap).
        """
        if not self.is_available:
            raise RuntimeError("Telethon MTProto client is not connected.")

        message = await self.client.get_messages(STORAGE_CHANNEL_ID, ids=message_id)
        if message is None or message.document is None:
            raise ValueError(f"No document found in message {message_id}.")

        buffer = io.BytesIO()
        await self.client.download_media(message, file=buffer)
        return buffer.getvalue()

    async def stream_file(self, message_id: int, chunk_size: int = 1024 * 1024):
        """
        Async generator that streams a file from the channel in chunks.
        Use this for large files to avoid holding the whole payload in memory.

        Yields: bytes chunks
        """
        if not self.is_available:
            raise RuntimeError("Telethon MTProto client is not connected.")

        message = await self.client.get_messages(STORAGE_CHANNEL_ID, ids=message_id)
        if message is None or message.document is None:
            raise ValueError(f"No document found in message {message_id}.")

        async for chunk in self.client.iter_download(message.document, chunk_size=chunk_size):
            yield chunk


# Singleton instance — imported everywhere in the app
telethon_service = TelethonService()
