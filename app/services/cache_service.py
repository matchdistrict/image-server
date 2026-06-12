import logging
import time
from typing import Optional, Union
import redis.asyncio as aioredis
from app.config import REDIS_URL

logger = logging.getLogger(__name__)

class CacheService:
    def __init__(self):
        self.redis = None
        if REDIS_URL:
            try:
                self.redis = aioredis.from_url(REDIS_URL)
                logger.info("Successfully initialized Redis cache connection.")
            except Exception as e:
                logger.error(f"Failed to connect to Redis URL: {e}. Using in-memory fallback cache.")
        else:
            logger.info("REDIS_URL not configured. Using in-memory fallback cache.")
        
        # In-memory storage fallback with TTL tracking
        self._local_cache = {}

    async def get(self, key: str) -> Optional[bytes]:
        if self.redis:
            try:
                return await self.redis.get(key)
            except Exception as e:
                logger.error(f"Redis get error: {e}")
        
        # In-memory fallback lookup
        if key in self._local_cache:
            val, expiry = self._local_cache[key]
            if expiry is None or expiry > time.time():
                return val
            else:
                del self._local_cache[key]
        return None

    async def set(self, key: str, value: bytes, expire: int = 86400) -> None:
        if self.redis:
            try:
                await self.redis.set(key, value, ex=expire)
                return
            except Exception as e:
                logger.error(f"Redis set error: {e}")
        
        # In-memory fallback insert
        expiry = time.time() + expire if expire else None
        self._local_cache[key] = (value, expiry)

    async def delete(self, key: str) -> None:
        if self.redis:
            try:
                await self.redis.delete(key)
                return
            except Exception as e:
                logger.error(f"Redis delete error: {e}")
        
        if key in self._local_cache:
            del self._local_cache[key]

cache_service = CacheService()
