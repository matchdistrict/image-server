import io
import datetime
import secrets
import urllib.parse
import logging
import hmac
import hashlib
from typing import Optional
from fastapi import APIRouter, Depends, File, UploadFile, HTTPException, Request, Form
from fastapi.responses import HTMLResponse, Response, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import select, func, delete
from sqlalchemy.ext.asyncio import AsyncSession
from aiogram.exceptions import TelegramBadRequest
from pydantic import BaseModel

from app.config import DOMAIN, STORAGE_CHANNEL_ID, SECRET_KEY, GUEST_LIMIT, USER_LIMIT, BOT_USERNAME, BOT_TOKEN, ADMIN_USER_IDS
from app.database import get_db
from app.models import Image, Analytics, BannedUser, AdminApiKey
from app.schemas import UploadSuccessResponse, StatsItem, ImageResponse
from app.services.cache_service import cache_service
from app.services.image_service import image_service
from app.services.moderation_service import moderation_service
from app.services.stats_service import stats_service
from app.services.backup_service import backup_service
from app.services.settings_service import settings_service
from app.bot.bot_instance import bot
from aiogram.types import BufferedInputFile

logger = logging.getLogger(__name__)
router = APIRouter()
templates = Jinja2Templates(directory="app/templates")

# Register global context variables for template generation
templates.env.globals["bot_username"] = BOT_USERNAME
templates.env.globals["domain"] = DOMAIN

# WebApp Authentication Helper
def verify_telegram_webapp_data(token: str, init_data: str) -> dict | None:
    try:
        # Parse query string
        parsed = dict(urllib.parse.parse_qsl(init_data))
        if "hash" not in parsed:
            return None
            
        received_hash = parsed.pop("hash")
        
        # Sort and join fields
        data_check_string = "\n".join(f"{k}={v}" for k, v in sorted(parsed.items()))
        
        # Generate secret key: HMAC_SHA256(key="WebAppData", msg=bot_token)
        secret_key = hmac.new(b"WebAppData", token.encode("utf-8"), hashlib.sha256).digest()
        
        # Calculate hash
        calculated_hash = hmac.new(secret_key, data_check_string.encode("utf-8"), hashlib.sha256).hexdigest()
        
        if calculated_hash == received_hash:
            import json
            return json.loads(parsed.get("user", "{}"))
        return None
    except Exception as e:
        logger.error(f"Error validating Telegram WebApp signature: {e}")
        return None

# Pydantic schema for WebApp auth payload
class WebAppAuthRequest(BaseModel):
    init_data: str

# Rate limiting helper for guest IP
async def check_ip_rate_limit(ip: str, limit: int) -> bool:
    day_str = datetime.datetime.now().strftime("%Y-%m-%d")
    cache_key = f"rate_limit:ip:{ip}:{day_str}"
    
    current = await cache_service.get(cache_key)
    if current is None:
        await cache_service.set(cache_key, b"1", expire=86400)
        return True
    
    count = int(current.decode("utf-8"))
    if count >= limit:
        return False
        
    await cache_service.set(cache_key, str(count + 1).encode("utf-8"), expire=86400)
    return True

# Admin auth helper
def verify_admin_token(token: Optional[str]) -> bool:
    return token == SECRET_KEY

# Unique slug helper
async def generate_unique_slug(db: AsyncSession) -> str:
    while True:
        # Generate 6 character alphanumeric slug
        slug = secrets.token_urlsafe(5).replace("-", "").replace("_", "")[:6].lower()
        stmt = select(Image).where(Image.slug == slug)
        res = await db.execute(stmt)
        if not res.scalar():
            return slug

# Telegram message existence check helper
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

# ----------------- HTML VIEWS -----------------

@router.get("/", response_class=HTMLResponse)
async def homepage_view(request: Request, db: AsyncSession = Depends(get_db)):
    user_id = request.session.get("user_id")
    if user_id:
        # Check if the user is banned
        stmt_ban = select(BannedUser).where(BannedUser.telegram_id == user_id)
        res_ban = await db.execute(stmt_ban)
        if res_ban.scalar():
            request.session.clear()
            return templates.TemplateResponse(request=request, name="index.html")

        # Query all images uploaded by this user
        stmt = select(Image).where(Image.uploaded_by == user_id).order_by(Image.created_at.desc())
        res = await db.execute(stmt)
        images = res.scalars().all()
        
        # Query lifetime uploads
        stmt_lifetime = select(func.count(Image.id)).where(Image.uploaded_by == user_id)
        lifetime_uploads = (await db.execute(stmt_lifetime)).scalar() or 0
        
        # Query daily uploads
        day_ago = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=1)
        stmt_daily = select(func.count(Image.id)).where(Image.uploaded_by == user_id, Image.created_at >= day_ago)
        daily_count = (await db.execute(stmt_daily)).scalar() or 0
        
        is_admin = user_id in ADMIN_USER_IDS
        user_stats = {
            "lifetime_uploads": lifetime_uploads,
            "daily_uploads": daily_count,
            "limit": "Unlimited" if is_admin else str(USER_LIMIT),
            "remaining": "Unlimited" if is_admin else str(max(0, USER_LIMIT - daily_count)),
            "role": "👑 Administrator" if is_admin else "👤 Standard User"
        }
        
        return templates.TemplateResponse(
            request=request,
            name="dashboard.html",
            context={
                "images": images,
                "user_stats": user_stats,
                "username": request.session.get("username", "User"),
                "token": SECRET_KEY
            }
        )
    return templates.TemplateResponse(request=request, name="index.html")

@router.get("/stats", response_class=HTMLResponse)
async def system_stats_view(request: Request, db: AsyncSession = Depends(get_db)):
    stats = await stats_service.get_stats(db)
    return templates.TemplateResponse(request=request, name="stats.html", context={"stats": stats})

@router.get("/i/{slug}", response_class=HTMLResponse)
async def image_preview_view(slug: str, request: Request, db: AsyncSession = Depends(get_db)):
    stmt = select(Image).where(Image.slug == slug)
    res = await db.execute(stmt)
    image = res.scalar()
    if not image:
        raise HTTPException(status_code=404, detail="Image not found")
        
    # Check if Telegram message is deleted
    if not await check_message_exists(STORAGE_CHANNEL_ID, image.message_id, slug):
        await db.delete(image)
        await db.commit()
        await cache_service.delete(f"image_cache:{slug}:full")
        await cache_service.delete(f"image_cache:{slug}:thumb")
        raise HTTPException(status_code=404, detail="Image not found")
        
    # Record view async
    await stats_service.record_view(
        db, 
        image, 
        ip=request.client.host if request.client else "127.0.0.1",
        referrer=request.headers.get("referer"),
        user_agent=request.headers.get("user-agent")
    )
    
    return templates.TemplateResponse(
        request=request,
        name="image.html",
        context={
            "image": image,
            "domain": DOMAIN
        }
    )

@router.get("/delete/{slug}/{delete_token}", response_class=HTMLResponse)
async def delete_image_view(slug: str, delete_token: str, request: Request, db: AsyncSession = Depends(get_db)):
    stmt = select(Image).where(Image.slug == slug, Image.delete_token == delete_token)
    res = await db.execute(stmt)
    image = res.scalar()
    if not image:
        raise HTTPException(status_code=404, detail="Invalid deletion credentials")
        
    # Delete from DB
    await db.delete(image)
    await db.commit()
    
    # Delete from Cache
    await cache_service.delete(f"image_cache:{slug}:full")
    await cache_service.delete(f"image_cache:{slug}:thumb")
    await cache_service.delete(f"msg_exists:{slug}")
    
    return templates.TemplateResponse(
        request=request,
        name="error.html",
        context={
            "error_code": "Deleted",
            "message": f"Image with slug '{slug}' has been permanently deleted from storage."
        }
    )

@router.get("/admin", response_class=HTMLResponse)
async def admin_dashboard_view(
    request: Request,
    token: Optional[str] = None,
    search: Optional[str] = None,
    db: AsyncSession = Depends(get_db)
):
    if not verify_admin_token(token):
        return templates.TemplateResponse(
            request=request,
            name="error.html",
            context={
                "error_code": "401",
                "message": "Unauthorized access. Invalid or missing administrator session token."
            },
            status_code=401
        )
        
    # Get stats
    stats = await stats_service.get_stats(db)
    
    # Get images list
    if search:
        stmt = select(Image).where(
            (Image.slug.ilike(f"%{search}%")) |
            (func.cast(Image.uploaded_by, func.String).ilike(f"%{search}%"))
        ).order_by(Image.created_at.desc())
    else:
        stmt = select(Image).order_by(Image.created_at.desc()).limit(50)
        
    res = await db.execute(stmt)
    images = res.scalars().all()
    
    # Get active Admin API key
    key_stmt = select(AdminApiKey)
    key_res = await db.execute(key_stmt)
    key_record = key_res.scalar()
    api_key = key_record.key if key_record else None
    
    # Get kick_all setting
    kick_all_enabled = await settings_service.is_kick_all_enabled(db)
    
    return templates.TemplateResponse(
        request=request,
        name="admin.html",
        context={
            "stats": stats,
            "images": images,
            "token": token,
            "search": search,
            "api_key": api_key,
            "kick_all_enabled": kick_all_enabled
        }
    )

@router.post("/admin/settings/toggle-kick-all")
async def admin_toggle_kick_all(
    token: str = Form(...),
    db: AsyncSession = Depends(get_db)
):
    if not verify_admin_token(token):
        raise HTTPException(status_code=401, detail="Unauthorized")
        
    current = await settings_service.is_kick_all_enabled(db)
    new_val = "false" if current else "true"
    await settings_service.set_setting(db, "kick_all", new_val)
    
    return RedirectResponse(url=f"/admin?token={token}", status_code=303)

@router.post("/admin/delete/{slug}")
async def admin_delete_image(
    slug: str,
    token: str = Form(...),
    db: AsyncSession = Depends(get_db)
):
    if not verify_admin_token(token):
        raise HTTPException(status_code=401, detail="Unauthorized")
        
    stmt = select(Image).where(Image.slug == slug)
    res = await db.execute(stmt)
    image = res.scalar()
    if not image:
        raise HTTPException(status_code=404, detail="Image not found")
        
    await db.delete(image)
    await db.commit()
    
    await cache_service.delete(f"image_cache:{slug}:full")
    await cache_service.delete(f"image_cache:{slug}:thumb")
    await cache_service.delete(f"msg_exists:{slug}")
    
    return RedirectResponse(url=f"/admin?token={token}", status_code=303)

@router.post("/admin/ban/{telegram_id}")
async def admin_ban_user(
    telegram_id: int,
    token: str = Form(...),
    reason: Optional[str] = Form(None),
    db: AsyncSession = Depends(get_db)
):
    if not verify_admin_token(token):
        raise HTTPException(status_code=401, detail="Unauthorized")
        
    ban_entry = BannedUser(telegram_id=telegram_id, reason=reason)
    db.add(ban_entry)
    await db.commit()
    
    return RedirectResponse(url=f"/admin?token={token}", status_code=303)

@router.post("/admin/api-key/generate")
async def admin_generate_api_key(
    token: str = Form(...),
    db: AsyncSession = Depends(get_db)
):
    if not verify_admin_token(token):
        raise HTTPException(status_code=401, detail="Unauthorized")
        
    # Delete existing API keys to enforce only one key at a time
    await db.execute(delete(AdminApiKey))
    
    new_key = f"PictureMania_{secrets.token_urlsafe(32)}"
    api_key_entry = AdminApiKey(key=new_key)
    db.add(api_key_entry)
    await db.commit()
    
    return RedirectResponse(url=f"/admin?token={token}", status_code=303)

@router.post("/admin/api-key/revoke")
async def admin_revoke_api_key(
    token: str = Form(...),
    db: AsyncSession = Depends(get_db)
):
    if not verify_admin_token(token):
        raise HTTPException(status_code=401, detail="Unauthorized")
        
    await db.execute(delete(AdminApiKey))
    await db.commit()
    
    return RedirectResponse(url=f"/admin?token={token}", status_code=303)

@router.get("/admin/backup/download")
async def admin_download_backup(
    token: str,
    db: AsyncSession = Depends(get_db)
):
    if not verify_admin_token(token):
        raise HTTPException(status_code=401, detail="Unauthorized")
        
    try:
        sql_dump = await backup_service.generate_sql_backup(db)
        return Response(
            content=sql_dump,
            media_type="application/sql",
            headers={
                "Content-Disposition": "attachment; filename=picturemania_backup.sql"
            }
        )
    except Exception as e:
        logger.error(f"Failed to generate backup SQL script: {e}")
        raise HTTPException(status_code=500, detail=f"Backup generation failed: {e}")

@router.post("/admin/backup/restore")
async def admin_restore_backup(
    token: str = Form(...),
    file: UploadFile = File(...),
    db: AsyncSession = Depends(get_db)
):
    if not verify_admin_token(token):
        raise HTTPException(status_code=401, detail="Unauthorized")
        
    if not file.filename.endswith(".sql"):
        raise HTTPException(status_code=400, detail="Only SQL backup files (.sql) are supported.")
        
    try:
        content_bytes = await file.read()
        sql_content = content_bytes.decode("utf-8")
        
        # Run restore transactions
        await backup_service.restore_sql_backup(db, sql_content)
        
        # Invalidate memory/redis caches
        if cache_service.redis:
            await cache_service.redis.flushall()
        else:
            cache_service._local_cache.clear()
    except Exception as e:
        logger.error(f"Failed to execute SQL database restore: {e}")
        raise HTTPException(status_code=500, detail=f"Database restore failed: {e}")
        
    return RedirectResponse(url=f"/admin?token={token}", status_code=303)

# ----------------- PROXY RAW SERVING -----------------

@router.get("/raw/{slug}")
async def raw_image_endpoint(slug: str, request: Request, db: AsyncSession = Depends(get_db)):
    cache_key = f"image_cache:{slug}:full"
    
    stmt = select(Image).where(Image.slug == slug)
    res = await db.execute(stmt)
    image = res.scalar()
    if not image:
        raise HTTPException(status_code=404, detail="Image not found")
        
    # Check if Telegram message is deleted
    if not await check_message_exists(STORAGE_CHANNEL_ID, image.message_id, slug):
        await db.delete(image)
        await db.commit()
        await cache_service.delete(cache_key)
        await cache_service.delete(f"image_cache:{slug}:thumb")
        raise HTTPException(status_code=404, detail="Image not found")
        
    # 1. Check Cache
    cached_data = await cache_service.get(cache_key)
    
    if cached_data:
        # Cache hit
        etag = f'W/"{slug}-full"'
        if request.headers.get("if-none-match") == etag:
            return Response(status_code=304)
        return Response(
            content=cached_data, 
            media_type=image.mime_type,
            headers={
                "Cache-Control": "public, max-age=86400",
                "ETag": etag
            }
        )
        
    # 2. Cache Miss - Fetch from Telegram Bot API securely
    try:
        tg_file = await bot.get_file(image.file_id)
        if not tg_file.file_path:
            raise ValueError("File path not available")
            
        buffer = io.BytesIO()
        await bot.download_file(tg_file.file_path, destination=buffer)
        image_bytes = buffer.getvalue()
        if not image_bytes:
            raise ValueError("Failed to retrieve file contents")
        
        # Optimize image with Pillow
        optimized_bytes = image_service.validate_and_optimize(image_bytes)
        
        # Save to Cache
        await cache_service.set(cache_key, optimized_bytes, expire=86400)
        
        return Response(
            content=optimized_bytes,
            media_type=image.mime_type,
            headers={
                "Cache-Control": "public, max-age=86400",
                "ETag": f'W/"{slug}-full"'
            }
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Proxy error loading storage asset: {e}")

@router.get("/thumb/{slug}")
async def thumbnail_image_endpoint(slug: str, request: Request, db: AsyncSession = Depends(get_db)):
    cache_key = f"image_cache:{slug}:thumb"
    
    stmt = select(Image).where(Image.slug == slug)
    res = await db.execute(stmt)
    image = res.scalar()
    if not image:
        raise HTTPException(status_code=404, detail="Image not found")
        
    # Check if Telegram message is deleted
    if not await check_message_exists(STORAGE_CHANNEL_ID, image.message_id, slug):
        await db.delete(image)
        await db.commit()
        await cache_service.delete(f"image_cache:{slug}:full")
        await cache_service.delete(cache_key)
        raise HTTPException(status_code=404, detail="Image not found")
        
    # Check Cache
    cached_data = await cache_service.get(cache_key)
    
    if cached_data:
        etag = f'W/"{slug}-thumb"'
        if request.headers.get("if-none-match") == etag:
            return Response(status_code=304)
        return Response(
            content=cached_data,
            media_type=image.mime_type,
            headers={
                "Cache-Control": "public, max-age=86400",
                "ETag": etag
            }
        )
        
    try:
        # Fetch original from Telegram
        tg_file = await bot.get_file(image.file_id)
        buffer = io.BytesIO()
        await bot.download_file(tg_file.file_path, destination=buffer)
        original_bytes = buffer.getvalue()
        if not original_bytes:
            raise ValueError("Failed to download file content")
            
        # Generate 300px thumbnail
        thumb_bytes = image_service.generate_thumbnail(original_bytes)
        
        # Save to Cache
        await cache_service.set(cache_key, thumb_bytes, expire=86400)
        
        return Response(
            content=thumb_bytes,
            media_type=image.mime_type,
            headers={
                "Cache-Control": "public, max-age=86400",
                "ETag": f'W/"{slug}-thumb"'
            }
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Proxy error creating thumbnail: {e}")

# ----------------- JSON REST API -----------------

@router.post("/api/auth/webapp")
async def api_auth_webapp(
    request: Request,
    payload: WebAppAuthRequest,
    db: AsyncSession = Depends(get_db)
):
    user_info = verify_telegram_webapp_data(BOT_TOKEN, payload.init_data)
    if not user_info or "id" not in user_info:
        raise HTTPException(status_code=401, detail="Invalid Telegram authentication payload.")
        
    user_id = user_info["id"]
    username = user_info.get("username") or user_info.get("first_name", "User")
    
    # Verify if user is banned
    stmt = select(BannedUser).where(BannedUser.telegram_id == user_id)
    res = await db.execute(stmt)
    if res.scalar():
        raise HTTPException(status_code=403, detail="You are banned from using this service.")
        
    # Store session values
    request.session["user_id"] = user_id
    request.session["username"] = username
    
    return {"success": True, "user_id": user_id, "username": username}

@router.post("/api/auth/logout")
async def api_auth_logout(request: Request):
    request.session.clear()
    return {"success": True}

@router.post("/api/user/delete/{slug}")
async def api_user_delete_image(
    slug: str,
    request: Request,
    db: AsyncSession = Depends(get_db)
):
    user_id = request.session.get("user_id")
    if not user_id:
        raise HTTPException(status_code=401, detail="Unauthorized")
        
    stmt = select(Image).where(Image.slug == slug)
    res = await db.execute(stmt)
    image = res.scalar()
    if not image:
        raise HTTPException(status_code=404, detail="Image not found")
        
    # Verify that the image belongs to the logged-in user OR the user is an admin
    is_admin = user_id in ADMIN_USER_IDS
    if image.uploaded_by != user_id and not is_admin:
        raise HTTPException(status_code=403, detail="Forbidden. You do not own this image.")
        
    await db.delete(image)
    await db.commit()
    
    await cache_service.delete(f"image_cache:{slug}:full")
    await cache_service.delete(f"image_cache:{slug}:thumb")
    await cache_service.delete(f"msg_exists:{slug}")
    
    return {"success": True}

@router.post("/api/upload", response_model=UploadSuccessResponse)
async def api_upload_image(
    request: Request,
    file: UploadFile = File(...),
    db: AsyncSession = Depends(get_db)
):
    ip = request.client.host if request.client else "127.0.0.1"
    
    # 1. API Key Check
    api_key = request.headers.get("X-API-Key") or request.query_params.get("api_key")
    is_admin_api = False
    if api_key:
        stmt = select(AdminApiKey).where(AdminApiKey.key == api_key)
        res = await db.execute(stmt)
        if res.scalar():
            is_admin_api = True
            
    # Check WebApp session authentication
    session_user_id = request.session.get("user_id")
    is_admin_session = session_user_id in ADMIN_USER_IDS if session_user_id else False
    
    # 2. Rate Limiting Check for Guest IP (bypassed for Admin API and WebApp session)
    if not is_admin_api and not session_user_id:
        if not await check_ip_rate_limit(ip, GUEST_LIMIT):
            raise HTTPException(status_code=429, detail="Daily rate limit exceeded for guest upload.")
            
    # Check User daily limit if authenticated via session
    if session_user_id and not is_admin_session:
        day_ago = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=1)
        stmt = select(func.count(Image.id)).where(Image.uploaded_by == session_user_id, Image.created_at >= day_ago)
        daily_count = (await db.execute(stmt)).scalar() or 0
        if daily_count >= USER_LIMIT:
            raise HTTPException(status_code=429, detail=f"Daily limit of {USER_LIMIT} uploads reached.")
        
    # 3. File Verification
    if not file.content_type or not file.content_type.startswith("image/"):
        raise HTTPException(status_code=400, detail="Only image files are permitted.")
        
    try:
        file_bytes = await file.read()
        
        # Enforce size limit: 10MB for guest / standard users, 20MB for Admin API / Admin WebApp session
        is_admin = is_admin_api or is_admin_session
        max_allowed_size = 20 * 1024 * 1024 if is_admin else 10 * 1024 * 1024
        if len(file_bytes) > max_allowed_size:
            limit_mb = max_allowed_size // (1024 * 1024)
            raise ValueError(f"File size exceeds the {limit_mb}MB limit.")
            
        # Validate structure with Pillow
        optimized_bytes = image_service.validate_and_optimize(file_bytes)
        file_size = len(optimized_bytes)
    except ValueError as val_err:
        raise HTTPException(status_code=400, detail=str(val_err) or "Invalid image structure or corrupt content.")
        
    # 4. Content Moderation
    is_nsfw = moderation_service.check_nsfw(optimized_bytes)
    
    # 5. Upload to storage channel
    try:
        input_file = BufferedInputFile(optimized_bytes, filename=file.filename or "image.jpg")
        sent_msg = await bot.send_document(
            chat_id=STORAGE_CHANNEL_ID,
            document=input_file
        )
        file_id = sent_msg.document.file_id if sent_msg.document else sent_msg.photo[-1].file_id
        file_unique_id = sent_msg.document.file_unique_id if sent_msg.document else sent_msg.photo[-1].file_unique_id
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Storage upload failed: {e}")
        
    # Check if the image already exists in the database
    stmt = select(Image).where(Image.file_unique_id == file_unique_id)
    res = await db.execute(stmt)
    existing_image = res.scalar()
    if existing_image:
        return UploadSuccessResponse(
            message="Image uploaded successfully.",
            slug=existing_image.slug,
            view_url=f"{DOMAIN}/i/{existing_image.slug}",
            raw_url=f"{DOMAIN}/raw/{existing_image.slug}",
            delete_url=f"{DOMAIN}/delete/{existing_image.slug}/{existing_image.delete_token}"
        )
        
    # 6. Save DB entry
    slug = await generate_unique_slug(db)
    delete_token = secrets.token_hex(16)
    
    new_image = Image(
        slug=slug,
        file_id=file_id,
        file_unique_id=file_unique_id,
        message_id=sent_msg.message_id,
        mime_type=file.content_type,
        file_size=file_size,
        uploaded_by=session_user_id, # Link it to their Telegram account!
        delete_token=delete_token,
        is_nsfw=is_nsfw,
        nsfw_checked=True
    )
    db.add(new_image)
    await db.commit()
    
    # Cache message as existing
    await cache_service.set(f"msg_exists:{slug}", b"1", expire=60)
    
    return UploadSuccessResponse(
        message="Image uploaded successfully.",
        slug=slug,
        view_url=f"{DOMAIN}/i/{slug}",
        raw_url=f"{DOMAIN}/raw/{slug}",
        delete_url=f"{DOMAIN}/delete/{slug}/{delete_token}"
    )

@router.get("/api/image/{slug}", response_model=ImageResponse)
async def api_get_image_metadata(slug: str, db: AsyncSession = Depends(get_db)):
    stmt = select(Image).where(Image.slug == slug)
    res = await db.execute(stmt)
    image = res.scalar()
    if not image:
        raise HTTPException(status_code=404, detail="Image not found")
        
    # Check if Telegram message is deleted
    if not await check_message_exists(STORAGE_CHANNEL_ID, image.message_id, slug):
        await db.delete(image)
        await db.commit()
        await cache_service.delete(f"image_cache:{slug}:full")
        await cache_service.delete(f"image_cache:{slug}:thumb")
        raise HTTPException(status_code=404, detail="Image not found")
        
    return image

@router.delete("/api/image/{slug}")
async def api_delete_image(
    slug: str,
    delete_token: str,
    db: AsyncSession = Depends(get_db)
):
    stmt = select(Image).where(Image.slug == slug, Image.delete_token == delete_token)
    res = await db.execute(stmt)
    image = res.scalar()
    if not image:
        raise HTTPException(status_code=404, detail="Invalid delete token or slug")
        
    await db.delete(image)
    await db.commit()
    
    await cache_service.delete(f"image_cache:{slug}:full")
    await cache_service.delete(f"image_cache:{slug}:thumb")
    await cache_service.delete(f"msg_exists:{slug}")
    
    return {"message": f"Image '{slug}' has been successfully deleted."}

@router.get("/api/stats", response_model=StatsItem)
async def api_get_system_stats(db: AsyncSession = Depends(get_db)):
    stats = await stats_service.get_stats(db)
    return stats
