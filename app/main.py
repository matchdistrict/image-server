import asyncio
import logging
from contextlib import asynccontextmanager
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.exceptions import RequestValidationError
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.database import Base, engine
from app.api.endpoints import router as api_router, templates
from app.bot.bot_instance import bot, dp
from app.bot.handlers import router as bot_router

logger = logging.getLogger(__name__)

# Auto-initialize database tables on startup
async def init_db():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

bot_task = None

@asynccontextmanager
async def lifespan(app: FastAPI):
    global bot_task
    # Startup Phase
    logger.info("Initializing database schema...")
    await init_db()
    
    logger.info("Registering bot routers and starting Aiogram polling...")
    dp.include_router(bot_router)
    bot_task = asyncio.create_task(dp.start_polling(bot))
    
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
    title="TGCloud",
    description="High-performance, secure, Telegram-backed image hosting server API.",
    version="1.0.0",
    lifespan=lifespan
)

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
