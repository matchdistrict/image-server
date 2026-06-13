import asyncio
import logging
from contextlib import asynccontextmanager
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.exceptions import RequestValidationError
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.middleware.sessions import SessionMiddleware

from app.database import Base, engine
from app.api.endpoints import router as api_router, templates
from app.bot.bot_instance import bot, dp
from app.bot.handlers import router as bot_router
from app.config import STORAGE_CHANNEL_ID, SECRET_KEY

logger = logging.getLogger(__name__)

# Auto-initialize database tables on startup and dynamically upgrade schema if needed
async def init_db():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        try:
            from sqlalchemy import text
            await conn.execute(text("ALTER TABLE images ADD COLUMN filename VARCHAR(255)"))
            logger.info("Database schema updated: Added 'filename' column to 'images' table.")
        except Exception as e:
            # Column already exists, or database doesn't support the ALTER statement
            logger.debug(f"Migration fallback (expected if column exists): {e}")
            pass

bot_task = None

@asynccontextmanager
async def lifespan(app: FastAPI):
    global bot_task
    # Startup Phase
    logger.info("Initializing database schema...")
    await init_db()
    
    logger.info("Registering bot routers...")
    dp.include_router(bot_router)
    
    # Verify bot credentials and STORAGE_CHANNEL_ID permissions on startup
    try:
        me = await bot.get_me()
        logger.info(f"Bot authenticated successfully as @{me.username} ({me.first_name})")
        
        # Verify STORAGE_CHANNEL_ID access
        if STORAGE_CHANNEL_ID:
            try:
                chat = await bot.get_chat(STORAGE_CHANNEL_ID)
                logger.info(f"Storage channel verified: '{chat.title}' (ID: {STORAGE_CHANNEL_ID})")
            except Exception as channel_err:
                logger.error(
                    f"CRITICAL: Bot has no access to storage channel ID {STORAGE_CHANNEL_ID}. "
                    f"Please ensure the channel ID is correct, the bot is added as an Administrator, "
                    f"and has permissions to post/edit messages. Error: {channel_err}"
                )
        else:
            logger.error("CRITICAL: STORAGE_CHANNEL_ID is not configured in the environment variables!")
    except Exception as auth_err:
        logger.error(
            f"CRITICAL: Failed to authenticate bot with BOT_TOKEN. "
            f"Please ensure the BOT_TOKEN in your .env / environment variables is valid and not expired. "
            f"Telegram API returned: {auth_err}"
        )

    logger.info("Starting Aiogram polling...")
    bot_task = asyncio.create_task(dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types()))
    
    yield
    
    # Shutdown Phase
    logger.info("Stopping Telegram Bot polling loop...")
    if bot_task:
        bot_task.cancel()
        try:
            await bot_task
        except asyncio.CancelledError:
            pass
    logger.info("Closing Telegram Bot session...")
    await bot.session.close()

app = FastAPI(
    title="PictureMania",
    description="High-performance, secure, Telegram-backed image hosting server API (PictureMania).",
    version="1.0.0",
    lifespan=lifespan
)

# Register Session Middleware
app.add_middleware(SessionMiddleware, secret_key=SECRET_KEY, session_cookie="picturemania_session")

# Mount static asset folders
app.mount("/static", StaticFiles(directory="app/static"), name="static")

# Include api views & endpoints
app.include_router(api_router)

# Custom error page templates
@app.exception_handler(StarletteHTTPException)
async def http_exception_handler(request: Request, exc: StarletteHTTPException):
    if request.url.path.startswith("/api/"):
        return JSONResponse(
            status_code=exc.status_code,
            content={"detail": exc.detail}
        )
    return templates.TemplateResponse(
        request=request,
        name="error.html",
        context={
            "error_code": str(exc.status_code),
            "message": exc.detail
        },
        status_code=exc.status_code
    )

@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    if request.url.path.startswith("/api/"):
        return JSONResponse(
            status_code=422,
            content={"detail": exc.errors()}
        )
    return templates.TemplateResponse(
        request=request,
        name="error.html",
        context={
            "error_code": "422",
            "message": f"Input validation failed: {str(exc.errors())}"
        },
        status_code=422
    )

@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    logger.error(f"Unhandled system error: {exc}", exc_info=True)
    if request.url.path.startswith("/api/"):
        return JSONResponse(
            status_code=500,
            content={"detail": "Internal Server Error"}
        )
    return templates.TemplateResponse(
        request=request,
        name="error.html",
        context={
            "error_code": "500",
            "message": "An unexpected server error occurred. Please try again later."
        },
        status_code=500
    )
