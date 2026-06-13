import io
import asyncio
import datetime
import secrets
import logging
from aiogram import Router, F
from aiogram.types import Message, BufferedInputFile, ChatJoinRequest, ChatMemberUpdated, MenuButtonWebApp, WebAppInfo, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.filters import Command
from sqlalchemy import select, func, delete
from aiogram.exceptions import TelegramBadRequest

from app.config import STORAGE_CHANNEL_ID, DOMAIN, USER_LIMIT, ADMIN_USER_IDS, SECRET_KEY
from app.database import async_session
from app.models import Image, BannedUser, UserLock
from app.services.moderation_service import moderation_service
from app.services.image_service import image_service
from app.services.cache_service import cache_service
from app.services.backup_service import backup_service
from app.services.settings_service import settings_service
from app.bot.bot_instance import bot

logger = logging.getLogger(__name__)
router = Router()

# Global media group collection cache: media_group_id -> list of Messages
media_group_cache = {}

def get_moderation_keyboard(user_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="🚫 Ban User", callback_data=f"ban:{user_id}"),
            InlineKeyboardButton(text="✅ Unban User", callback_data=f"unban:{user_id}")
        ]
    ])

async def generate_unique_slug(db) -> str:
    while True:
        # Generate 6 character alphanumeric slug
        slug = secrets.token_urlsafe(5).replace("-", "").replace("_", "")[:6].lower()
        stmt = select(Image).where(Image.slug == slug)
        res = await db.execute(stmt)
        if not res.scalar():
            return slug

async def is_user_banned(db, user_id: int) -> bool:
    stmt = select(BannedUser).where(BannedUser.telegram_id == user_id)
    res = await db.execute(stmt)
    return res.scalar() is not None

async def check_user_limit(db, user_id: int) -> bool:
    IST = datetime.timezone(datetime.timedelta(hours=5, minutes=30))
    now_ist = datetime.timezone.now(IST)
    today_midnight_ist = now_ist.replace(hour=0, minute=0, second=0, microsecond=0)
    today_midnight_utc_naive = today_midnight_ist.astimezone(datetime.timezone.utc).replace(tzinfo=None)
    
    stmt = select(func.count(Image.id)).where(Image.uploaded_by == user_id, Image.created_at >= today_midnight_utc_naive)
    count = (await db.execute(stmt)).scalar() or 0
    return count < USER_LIMIT

async def check_message_exists(chat_id: int | str, message_id: int, slug: str) -> bool:
    cache_key = f"msg_exists:{slug}"
    cached = await cache_service.get(cache_key)
    if cached == b"1":
        return True
    if cached == b"0":
        return False
        
    try:
        # Edit the message's reply markup to None (a safe, non-destructive check)
        await bot.edit_message_reply_markup(chat_id=chat_id, message_id=message_id, reply_markup=None)
        await cache_service.set(cache_key, b"1", expire=60)
        return True
    except TelegramBadRequest as e:
        err_str = str(e).lower()
        # Common message-not-found/deleted strings in Telegram Bot API errors
        if "message to edit not found" in err_str or "message not found" in err_str or "chat not found" in err_str:
            await cache_service.set(cache_key, b"0", expire=60)
            return False
        if "message is not modified" in err_str:
            await cache_service.set(cache_key, b"1", expire=60)
            return True
        logger.warning(f"Unexpected TelegramBadRequest checking message {message_id}: {e}")
        return True
    except Exception as e:
        logger.error(f"Error checking message existence for image {slug}: {e}")
        # On generic connection/other errors, assume it exists to prevent accidental deletion
        return True

async def process_media_group(media_group_id: str):
    # Wait for all media group messages to arrive in the polling buffer
    await asyncio.sleep(0.8)
    messages = media_group_cache.pop(media_group_id, [])
    if not messages:
        return
        
    # Sort messages by message_id to process them chronologically
    messages.sort(key=lambda m: m.message_id)
    
    first_msg = messages[0]
    chat_id = first_msg.chat.id
    user_id = first_msg.from_user.id
    
    # Filter for image attachments
    valid_media_messages = []
    for msg in messages:
        if msg.photo:
            valid_media_messages.append(msg)
        elif msg.document and msg.document.mime_type and msg.document.mime_type.startswith("image/"):
            valid_media_messages.append(msg)
            
    if not valid_media_messages:
        return
        
    # Enforce media group cap (max 10 images at a time)
    if len(valid_media_messages) > 10:
        await first_msg.reply("⚠️ Maximum 10 images can be uploaded in an album at a time. Only the first 10 will be processed.")
        valid_media_messages = valid_media_messages[:10]
        
    status_msg = await first_msg.reply(f"⏳ <i>Processing album of {len(valid_media_messages)} images...</i>")
    
    async with async_session() as db:
        # 1. Ban Check
        if await is_user_banned(db, user_id):
            await status_msg.edit_text("❌ You are banned from using this service.")
            return
  
        # 2. Admin verification
        is_admin = user_id in ADMIN_USER_IDS

        # 3. Accumulated Limit Check (bypassed for admins)
        if not is_admin:
            IST = datetime.timezone(datetime.timedelta(hours=5, minutes=30))
            now_ist = datetime.timezone.now(IST)
            today_midnight_ist = now_ist.replace(hour=0, minute=0, second=0, microsecond=0)
            today_midnight_utc_naive = today_midnight_ist.astimezone(datetime.timezone.utc).replace(tzinfo=None)
            
            stmt = select(func.count(Image.id)).where(Image.uploaded_by == user_id, Image.created_at >= today_midnight_utc_naive)
            count = (await db.execute(stmt)).scalar() or 0
            if count + len(valid_media_messages) > USER_LIMIT:
                await status_msg.edit_text(f"❌ Uploading this album would exceed your daily limit of {USER_LIMIT} images. Remaining capacity: {max(0, USER_LIMIT - count)}.")
                return

        results = []
        for idx, msg in enumerate(valid_media_messages, 1):
            file_id = None
            file_unique_id = None
            mime_type = "image/jpeg"
            file_size = 0
            file_name = f"image_{idx}.jpg"
            
            if msg.photo:
                photo = msg.photo[-1]
                file_id = photo.file_id
                file_unique_id = photo.file_unique_id
                file_size = photo.file_size
                mime_type = "image/jpeg"
            elif msg.document:
                doc = msg.document
                file_id = doc.file_id
                file_unique_id = doc.file_unique_id
                file_size = doc.file_size
                mime_type = doc.mime_type
                file_name = doc.file_name or f"image_{idx}.jpg"
                
            # Skip if file ID is not parsed
            if not file_id:
                results.append((file_name, "Missing file payload.", False))
                continue
                
            # Enforce 10MB size limit (20MB for admins - maximum download size for Telegram Bot API files)
            max_size = 20 * 1024 * 1024 if is_admin else 10 * 1024 * 1024
            if file_size > max_size:
                results.append((file_name, f"Exceeds limit ({max_size // (1024*1024)}MB).", False))
                continue

            # Duplicate Upload Check
            stmt = select(Image).where(Image.file_unique_id == file_unique_id)
            res = await db.execute(stmt)
            existing_image = res.scalar()
            if existing_image:
                if await check_message_exists(STORAGE_CHANNEL_ID, existing_image.message_id, existing_image.slug):
                    results.append((file_name, existing_image.slug, True))
                    continue
                else:
                    await db.delete(existing_image)
                    await db.commit()
                    await cache_service.delete(f"image_cache:{existing_image.slug}:full")
                    await cache_service.delete(f"image_cache:{existing_image.slug}:thumb")

            # Process media
            try:
                tg_file = await bot.get_file(file_id)
                if not tg_file.file_path:
                    results.append((file_name, "Telegram file path unavailable.", False))
                    continue
                    
                buffer = io.BytesIO()
                await bot.download_file(tg_file.file_path, destination=buffer)
                file_bytes = buffer.getvalue()
                    
                optimized_bytes = image_service.validate_and_optimize(file_bytes)
                file_size = len(optimized_bytes)
                
                is_nsfw = moderation_service.check_nsfw(optimized_bytes)
                
                stored_file_id = file_id
                uploader_name = first_msg.from_user.username or first_msg.from_user.first_name
                caption_text = f"👤 Uploaded by: {uploader_name} (ID: {user_id})"
                mod_markup = get_moderation_keyboard(user_id)
                
                try:
                    channel_msg = await bot.copy_message(
                        chat_id=STORAGE_CHANNEL_ID,
                        from_chat_id=chat_id,
                        message_id=msg.message_id,
                        caption=caption_text,
                        reply_markup=mod_markup
                    )
                except Exception as e:
                    input_file = BufferedInputFile(optimized_bytes, filename=file_name)
                    channel_msg = await bot.send_document(
                        chat_id=STORAGE_CHANNEL_ID,
                        document=input_file,
                        caption=caption_text,
                        reply_markup=mod_markup
                    )
                    if channel_msg.document:
                        stored_file_id = channel_msg.document.file_id
                    elif channel_msg.photo:
                        stored_file_id = channel_msg.photo[-1].file_id

                slug = await generate_unique_slug(db)
                delete_token = secrets.token_hex(16)
                
                new_image = Image(
                    slug=slug,
                    file_id=stored_file_id,
                    file_unique_id=file_unique_id,
                    message_id=channel_msg.message_id,
                    mime_type=mime_type,
                    file_size=file_size,
                    uploaded_by=user_id,
                    delete_token=delete_token,
                    is_nsfw=is_nsfw,
                    nsfw_checked=True
                )
                db.add(new_image)
                await db.commit()
                
                await cache_service.set(f"msg_exists:{slug}", b"1", expire=60)
                results.append((file_name, slug, True))
            except Exception as e:
                logger.error(f"Error processing item in media group: {e}")
                results.append((file_name, f"Upload error: {str(e)}", False))
                
        # Send consolidated URLs response
        success_count = sum(1 for r in results if r[2])
        response_text = f"✅ <b>Album Upload Complete! ({success_count}/{len(results)} success)</b>\n\n"
        for idx, (name, val, success) in enumerate(results, 1):
            if success:
                view_url = f"{DOMAIN}/i/{val}"
                raw_url = f"{DOMAIN}/raw/{val}"
                response_text += f"📷 <b>{name}</b>:\n👁️ View: {view_url}\n🔗 Direct: {raw_url}\n\n"
            else:
                response_text += f"❌ <b>{name}</b>: {val}\n\n"
                
        await status_msg.edit_text(response_text, disable_web_page_preview=True)

@router.message(Command("start"))
async def start_command(message: Message):
    try:
        await bot.set_chat_menu_button(
            chat_id=message.chat.id,
            menu_button=MenuButtonWebApp(text="Open App", web_app=WebAppInfo(url=DOMAIN))
        )
    except Exception as e:
        logger.error(f"Failed to set WebApp menu button in start: {e}")
        
    welcome_text = (
        "📸 <b>Welcome to the Premium Image Hosting Bot!</b>\n\n"
        "Send me any image (as a photo or document), and I will host it securely on our Telegram-backed database storage!\n\n"
        "<b>Commands:</b>\n"
        "/help - How to use the bot\n"
        "/stats - Quick bot storage metrics\n"
        "/myuploads - View your hosted uploads\n"
        "/lock &lt;PIN&gt; - Lock your web gallery with a 4-digit PIN"
    )
    await message.reply(welcome_text)

@router.message(Command("help"))
async def help_command(message: Message):
    try:
        await bot.set_chat_menu_button(
            chat_id=message.chat.id,
            menu_button=MenuButtonWebApp(text="Open App", web_app=WebAppInfo(url=DOMAIN))
        )
    except Exception as e:
        logger.error(f"Failed to set WebApp menu button in help: {e}")
        
    help_text = (
        "📖 <b>How to use the Image Hosting Bot:</b>\n\n"
        "1. Send any photo or image file directly to the bot.\n"
        "2. The bot will validate the file type (JPEG, PNG, WEBP, GIF supported).\n"
        "3. It checks the content for safety and stores it in our secure channel storage.\n"
        "4. You can send multiple images at a time (albums up to 10 files).\n"
        "5. You will receive a direct preview link, a raw image URL, and a deletion token.\n\n"
        "<b>Security Options:</b>\n"
        "• <code>/lock &lt;4-digit PIN&gt;</code> - Secure your web gallery dashboard with a numeric 4-digit PIN.\n\n"
        "<b>Upload Limits:</b>\n"
        f"- Up to {USER_LIMIT} uploads per day (resets daily at 12:00 AM IST)."
    )
    await message.reply(help_text)

@router.message(Command("stats"))
async def stats_command(message: Message):
    user_id = message.from_user.id
    is_admin = user_id in ADMIN_USER_IDS
    
    async with async_session() as db:
        # Lifetime Uploads
        stmt_lifetime = select(func.count(Image.id)).where(Image.uploaded_by == user_id)
        lifetime_uploads = (await db.execute(stmt_lifetime)).scalar() or 0
        
        # Daily Uploads (since 12:00 AM IST)
        IST = datetime.timezone(datetime.timedelta(hours=5, minutes=30))
        now_ist = datetime.timezone.now(IST)
        today_midnight_ist = now_ist.replace(hour=0, minute=0, second=0, microsecond=0)
        today_midnight_utc_naive = today_midnight_ist.astimezone(datetime.timezone.utc).replace(tzinfo=None)
        
        stmt_daily = select(func.count(Image.id)).where(Image.uploaded_by == user_id, Image.created_at >= today_midnight_utc_naive)
        daily_count = (await db.execute(stmt_daily)).scalar() or 0
        
    role_text = "👑 Administrator" if is_admin else "👤 Standard User"
    limit_text = "Unlimited" if is_admin else str(USER_LIMIT)
    remaining_text = "Unlimited" if is_admin else str(max(0, USER_LIMIT - daily_count))
    
    stats_text = (
        "👤 <b>Your Account Statistics</b>\n\n"
        f"• <b>User ID:</b> <code>{user_id}</code>\n"
        f"• <b>Role:</b> {role_text}\n"
        f"• <b>Lifetime Uploads:</b> {lifetime_uploads} images\n\n"
        f"📊 <b>Daily Usage (Resets at 12:00 AM IST)</b>\n"
        f"• <b>Uploaded Today:</b> {daily_count} / {limit_text} images\n"
        f"• <b>Remaining Capacity:</b> {remaining_text} images"
    )
    await message.reply(stats_text)

@router.message(Command("myuploads"))
async def myuploads_command(message: Message):
    user_id = message.from_user.id
    async with async_session() as db:
        stmt = select(Image).where(Image.uploaded_by == user_id).order_by(Image.created_at.desc()).limit(10)
        res = await db.execute(stmt)
        images = res.scalars().all()

    if not images:
        await message.reply("📂 You haven't uploaded any images yet!")
        return

    uploads_text = "📂 <b>Your Last 10 Uploads:</b>\n\n"
    for idx, img in enumerate(images, 1):
        uploads_text += f"{idx}. <code>{img.slug}</code> - <a href='{DOMAIN}/i/{img.slug}'>View Image</a> ({img.views} views)\n"

    await message.reply(uploads_text, disable_web_page_preview=True)

@router.message(Command("backup"))
async def backup_command(message: Message):
    user_id = message.from_user.id
    if user_id not in ADMIN_USER_IDS:
        await message.reply("❌ Unauthorized access. Only bot administrators can perform database backups.")
        return
        
    status_msg = await message.reply("⏳ <i>Generating database SQL backup...</i>")
    
    try:
        async with async_session() as db:
            sql_dump = await backup_service.generate_sql_backup(db)
            
        input_file = BufferedInputFile(sql_dump.encode("utf-8"), filename="tgcloud_backup.sql")
        await message.reply_document(
            document=input_file,
            caption="📦 <b>TGCloud Database Backup SQL Dump</b>\n\n"
                    "You can restore this backup via the website Admin Dashboard."
        )
        await status_msg.delete()
    except Exception as e:
        logger.error(f"Failed to generate bot database backup: {e}")
        await status_msg.edit_text("❌ Failed to generate database backup. Please check logs.")

@router.message(Command("admin"))
async def admin_command(message: Message):
    user_id = message.from_user.id
    if user_id not in ADMIN_USER_IDS:
        await message.reply("❌ Unauthorized access.")
        return
        
    admin_link = f"{DOMAIN}/admin?token={SECRET_KEY}"
    response_text = (
        "🛠️ <b>TGCloud Admin Control Panel</b>\n\n"
        "Here are your administrator-only commands:\n\n"
        "• /admin - Display this admin control panel and web link.\n"
        "• /backup - Generate and download a database SQL dump.\n"
        "• /dblink - Retrieve invite link to database channel.\n"
        "• /protection [on|off] - Enable/disable the \"Kick All\" channel member protection.\n"
        "• /kickall [on|off] - Shortcut command for channel protection.\n\n"
        "🔗 <b>Web Admin Dashboard:</b>\n"
        f"{admin_link}"
    )
    await message.reply(response_text, disable_web_page_preview=True)

@router.message(Command("dblink"))
async def dblink_command(message: Message):
    user_id = message.from_user.id
    if user_id not in ADMIN_USER_IDS:
        await message.reply("❌ Unauthorized access.")
        return
        
    try:
        # Get storage channel details
        chat = await bot.get_chat(chat_id=STORAGE_CHANNEL_ID)
        
        # Check if the channel is public or has a custom invite link
        if chat.username:
            channel_link = f"https://t.me/{chat.username}"
        else:
            try:
                invite_link = chat.invite_link
                if not invite_link:
                    invite_link = await bot.export_chat_invite_link(chat_id=STORAGE_CHANNEL_ID)
                channel_link = invite_link
            except Exception as invite_err:
                logger.warning(f"Could not export invite link: {invite_err}")
                # Fallback to direct client link format
                channel_link = f"https://t.me/c/{str(STORAGE_CHANNEL_ID).replace('-100', '')}"
                
        await message.reply(
            f"📁 <b>Database Storage Channel Link:</b>\n\n"
            f"• <b>Channel Name:</b> {chat.title or 'Storage Channel'}\n"
            f"• <b>Channel ID:</b> <code>{STORAGE_CHANNEL_ID}</code>\n"
            f"• <b>Link:</b> {channel_link}",
            disable_web_page_preview=True
        )
    except Exception as e:
        logger.error(f"Failed to retrieve database link: {e}")
        fallback_link = f"https://t.me/c/{str(STORAGE_CHANNEL_ID).replace('-100', '')}"
        await message.reply(
            f"📁 <b>Database Storage Channel Details:</b>\n\n"
            f"• <b>Channel ID:</b> <code>{STORAGE_CHANNEL_ID}</code>\n"
            f"• <b>Fallback Link (Private format):</b> {fallback_link}\n\n"
            f"⚠️ <i>Failed to fetch live info: {str(e)}</i>",
            disable_web_page_preview=True
        )

@router.message(Command("protection", "kickall"))
async def protection_command(message: Message):
    user_id = message.from_user.id
    if user_id not in ADMIN_USER_IDS:
        await message.reply("❌ Unauthorized access.")
        return
        
    parts = message.text.strip().split()
    async with async_session() as db:
        if len(parts) > 1:
            arg = parts[1].lower()
            if arg in ["on", "enable", "true", "yes"]:
                await settings_service.set_setting(db, "kick_all", "true")
                await message.reply("🔒 <b>Channel Protection (Kick All) Enabled.</b>\nAll new members attempting to join will be immediately kicked/declined.")
            elif arg in ["off", "disable", "false", "no"]:
                await settings_service.set_setting(db, "kick_all", "false")
                await message.reply("🔓 <b>Channel Protection (Kick All) Disabled.</b>")
            else:
                await message.reply("❓ Invalid argument. Use <code>/protection on</code> or <code>/protection off</code>.")
        else:
            is_enabled = await settings_service.is_kick_all_enabled(db)
            status_text = "🔒 <b>Enabled</b>" if is_enabled else "🔓 <b>Disabled</b>"
            await message.reply(
                f"🛡️ <b>Channel Protection (Kick All) Status:</b> {status_text}\n\n"
                f"To toggle, run:\n"
                f"• <code>/protection on</code>\n"
                f"• <code>/protection off</code>"
            )

@router.message(Command("lock"))
async def lock_command(message: Message):
    parts = message.text.strip().split(maxsplit=1)
    if len(parts) < 2:
        await message.reply("🔑 <b>How to lock your gallery:</b>\nUse <code>/lock &lt;4-digit PIN&gt;</code> to set a PIN (e.g., <code>/lock 1456</code>).")
        return
        
    password = parts[1].strip()
    if not password.isdigit() or len(password) != 4:
        await message.reply("❌ PIN/Password must be a 4-digit number (e.g., <code>/lock 1456</code>).")
        return
        
    user_id = message.from_user.id
    async with async_session() as db:
        # Check if user already has a lock entry
        stmt = select(UserLock).where(UserLock.telegram_id == user_id)
        res = await db.execute(stmt)
        lock_entry = res.scalar()
        
        if lock_entry:
            lock_entry.password = password
        else:
            lock_entry = UserLock(telegram_id=user_id, password=password)
            db.add(lock_entry)
            
        await db.commit()
        
    await message.reply(f"🔒 <b>Gallery locked successfully!</b>\nYour PIN has been set. You will be asked for this PIN when opening the gallery web page.")

@router.chat_join_request()
async def handle_chat_join_request(event: ChatJoinRequest):
    async with async_session() as db:
        if await settings_service.is_kick_all_enabled(db):
            try:
                await event.decline()
                logger.info(f"Declined join request from user {event.from_user.id} in chat {event.chat.id} (Channel Protection active)")
            except Exception as e:
                logger.error(f"Failed to decline join request: {e}")

@router.chat_member()
async def handle_chat_member_updated(event: ChatMemberUpdated):
    # Only act if a new member joins (not left, not updated admin rights, etc.)
    if event.new_chat_member.status != "member":
        return
        
    user_id = event.new_chat_member.user.id
    
    # Do not kick bot admins or the bot itself
    if user_id in ADMIN_USER_IDS:
        return
        
    try:
        bot_info = await bot.get_me()
        if user_id == bot_info.id:
            return
    except Exception as bot_err:
        logger.error(f"Error checking bot self ID during member update check: {bot_err}")
        
    async with async_session() as db:
        if await settings_service.is_kick_all_enabled(db):
            try:
                await bot.ban_chat_member(chat_id=event.chat.id, user_id=user_id)
                await bot.unban_chat_member(chat_id=event.chat.id, user_id=user_id)
                logger.info(f"Kicked user {user_id} from chat {event.chat.id} due to active Channel Protection")
            except Exception as e:
                logger.error(f"Failed to kick user {user_id} from chat {event.chat.id}: {e}")

@router.message(F.photo | F.document)
async def media_upload_handler(message: Message):
    user_id = message.from_user.id
    
    # 1. Media Group / Album Check
    if message.media_group_id:
        if message.media_group_id not in media_group_cache:
            media_group_cache[message.media_group_id] = [message]
            asyncio.create_task(process_media_group(message.media_group_id))
        else:
            media_group_cache[message.media_group_id].append(message)
        return
        
    # 2. Single image processing flow
    async with async_session() as db:
        # Ban Check
        if await is_user_banned(db, user_id):
            await message.reply("❌ You are banned from using this service.")
            return

        # Limit Check (bypassed for Admins)
        is_admin = user_id in ADMIN_USER_IDS
        if not is_admin and not await check_user_limit(db, user_id):
            await message.reply("❌ Daily upload limit reached. Try again in 24 hours.")
            return

        # Identify Media & Extract Info
        file_id = None
        file_unique_id = None
        mime_type = "image/jpeg"
        file_size = 0
        file_name = "image.jpg"

        if message.photo:
            photo = message.photo[-1]
            file_id = photo.file_id
            file_unique_id = photo.file_unique_id
            file_size = photo.file_size
            mime_type = "image/jpeg"
        elif message.document:
            doc = message.document
            # Validate mime type
            if not doc.mime_type or not doc.mime_type.startswith("image/"):
                await message.reply("❌ Only image files (JPEG, PNG, WEBP, GIF) are allowed!")
                return
            file_id = doc.file_id
            file_unique_id = doc.file_unique_id
            file_size = doc.file_size
            mime_type = doc.mime_type
            file_name = doc.file_name or "image.jpg"

        if not file_id:
            await message.reply("❌ Failed to process upload attachment.")
            return

        # Enforce size limit (20MB for admins, 10MB for others)
        max_size = 20 * 1024 * 1024 if is_admin else 10 * 1024 * 1024
        if file_size > max_size:
            await message.reply(f"❌ File size exceeds the limit of {max_size // (1024 * 1024)}MB.")
            return

        # Check if the image already exists in the database
        stmt = select(Image).where(Image.file_unique_id == file_unique_id)
        res = await db.execute(stmt)
        existing_image = res.scalar()
        if existing_image:
            # Check if message exists in Telegram channel
            if await check_message_exists(STORAGE_CHANNEL_ID, existing_image.message_id, existing_image.slug):
                view_url = f"{DOMAIN}/i/{existing_image.slug}"
                raw_url = f"{DOMAIN}/raw/{existing_image.slug}"
                delete_url = f"{DOMAIN}/delete/{existing_image.slug}/{existing_image.delete_token}"

                response_text = (
                    "✅ <b>Image already hosted!</b>\n\n"
                    f"👁️ <b>View:</b> {view_url}\n"
                    f"🔗 <b>Direct link:</b> {raw_url}\n"
                    f"🗑️ <b>Delete link:</b> {delete_url}\n"
                )
                await message.reply(response_text, disable_web_page_preview=True)
                return
            else:
                # The message was deleted from the channel, so delete it from DB to allow re-upload
                await db.delete(existing_image)
                await db.commit()
                await cache_service.delete(f"image_cache:{existing_image.slug}:full")
                await cache_service.delete(f"image_cache:{existing_image.slug}:thumb")

        status_msg = await message.reply("⏳ <i>Processing image and database entry...</i>")

        try:
            # Download and validate content
            tg_file = await bot.get_file(file_id)
            if not tg_file.file_path:
                raise ValueError("Telegram file path missing")

            buffer = io.BytesIO()
            await bot.download_file(tg_file.file_path, destination=buffer)
            file_bytes = buffer.getvalue()

            try:
                optimized_bytes = image_service.validate_and_optimize(file_bytes)
                file_size = len(optimized_bytes)
            except ValueError:
                await status_msg.edit_text("❌ Upload validation failed. File is not a valid or supported image format.")
                return

            # Content Moderation / NSFW Check
            is_nsfw = moderation_service.check_nsfw(optimized_bytes)

            # Copy message or fallback
            stored_file_id = file_id
            uploader_name = message.from_user.username or message.from_user.first_name
            caption_text = f"👤 Uploaded by: {uploader_name} (ID: {user_id})"
            mod_markup = get_moderation_keyboard(user_id)

            try:
                channel_msg = await bot.copy_message(
                    chat_id=STORAGE_CHANNEL_ID,
                    from_chat_id=message.chat.id,
                    message_id=message.message_id,
                    caption=caption_text,
                    reply_markup=mod_markup
                )
            except Exception as copy_err:
                logger.warning(f"copy_message failed: {copy_err}. Falling back to uploading optimized bytes directly.")
                try:
                    input_file = BufferedInputFile(optimized_bytes, filename=file_name)
                    channel_msg = await bot.send_document(
                        chat_id=STORAGE_CHANNEL_ID,
                        document=input_file,
                        caption=caption_text,
                        reply_markup=mod_markup
                    )
                    if channel_msg.document:
                        stored_file_id = channel_msg.document.file_id
                    elif channel_msg.photo:
                        stored_file_id = channel_msg.photo[-1].file_id
                except Exception as upload_err:
                    logger.error(f"Both copy_message and fallback send_document failed. Copy error: {copy_err}, Upload error: {upload_err}")
                    raise upload_err

            # Write Database Record
            slug = await generate_unique_slug(db)
            delete_token = secrets.token_hex(16)

            new_image = Image(
                slug=slug,
                file_id=stored_file_id,
                file_unique_id=file_unique_id,
                message_id=channel_msg.message_id,
                mime_type=mime_type,
                file_size=file_size,
                uploaded_by=user_id,
                delete_token=delete_token,
                is_nsfw=is_nsfw,
                nsfw_checked=True
            )
            db.add(new_image)
            await db.commit()

            # Cache message as existing
            await cache_service.set(f"msg_exists:{slug}", b"1", expire=60)

            # Send URLs Response
            view_url = f"{DOMAIN}/i/{slug}"
            raw_url = f"{DOMAIN}/raw/{slug}"
            delete_url = f"{DOMAIN}/delete/{slug}/{delete_token}"

            response_text = (
                "✅ <b>Image uploaded successfully!</b>\n\n"
                f"👁️ <b>View:</b> {view_url}\n"
                f"🔗 <b>Direct link:</b> {raw_url}\n"
                f"🗑️ <b>Delete link:</b> {delete_url}\n"
            )
            if is_nsfw:
                response_text += "\n⚠️ <i>Note: This upload has been flagged for NSFW review.</i>"

            await status_msg.edit_text(response_text, disable_web_page_preview=True)

        except Exception as e:
            logger.error(f"Error handling media upload: {e}", exc_info=True)
            error_message = f"❌ <b>System error processing your upload:</b>\n<code>{str(e)}</code>\n\nPlease check your bot configuration (e.g., bot permissions in the storage channel, database connection, etc.)."
            await status_msg.edit_text(error_message)

@router.callback_query(F.data.startswith("ban:"))
async def handle_callback_ban(callback: CallbackQuery):
    # Check if the clicker is admin
    if callback.from_user.id not in ADMIN_USER_IDS:
        await callback.answer("❌ You are not authorized to perform this action.", show_alert=True)
        return
        
    user_id = int(callback.data.split(":")[1])
    async with async_session() as db:
        # Check if already banned
        stmt = select(BannedUser).where(BannedUser.telegram_id == user_id)
        res = await db.execute(stmt)
        if res.scalar():
            await callback.answer("User is already banned.", show_alert=True)
            return
            
        ban_entry = BannedUser(telegram_id=user_id, reason="Banned via channel moderation button")
        db.add(ban_entry)
        await db.commit()
        
    await callback.answer("🚫 User has been successfully banned.", show_alert=True)
    try:
        new_caption = f"{callback.message.caption}\n\n⚠️ STATUS: BANNED"
        await callback.message.edit_caption(caption=new_caption, reply_markup=get_moderation_keyboard(user_id))
    except Exception as e:
        logger.error(f"Failed to edit caption: {e}")

@router.callback_query(F.data.startswith("unban:"))
async def handle_callback_unban(callback: CallbackQuery):
    if callback.from_user.id not in ADMIN_USER_IDS:
        await callback.answer("❌ You are not authorized to perform this action.", show_alert=True)
        return
        
    user_id = int(callback.data.split(":")[1])
    async with async_session() as db:
        await db.execute(delete(BannedUser).where(BannedUser.telegram_id == user_id))
        await db.commit()
        
    await callback.answer("✅ User has been successfully unbanned.", show_alert=True)
    try:
        # Clean status line
        caption_lines = callback.message.caption.split("\n\n⚠️ STATUS:")[0]
        await callback.message.edit_caption(caption=caption_lines, reply_markup=get_moderation_keyboard(user_id))
    except Exception as e:
        logger.error(f"Failed to edit caption: {e}")
