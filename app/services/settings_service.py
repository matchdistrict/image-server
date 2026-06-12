from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from app.models import SystemSetting

class SettingsService:
    @staticmethod
    async def get_setting(db: AsyncSession, key: str, default: str = "") -> str:
        try:
            stmt = select(SystemSetting).where(SystemSetting.key == key)
            res = await db.execute(stmt)
            setting = res.scalar()
            if setting:
                return setting.value
            return default
        except Exception:
            return default

    @staticmethod
    async def set_setting(db: AsyncSession, key: str, value: str):
        stmt = select(SystemSetting).where(SystemSetting.key == key)
        res = await db.execute(stmt)
        setting = res.scalar()
        if setting:
            setting.value = value
        else:
            setting = SystemSetting(key=key, value=value)
            db.add(setting)
        await db.commit()

    @staticmethod
    async def is_kick_all_enabled(db: AsyncSession) -> bool:
        val = await SettingsService.get_setting(db, "kick_all", "false")
        return val.lower() == "true"

settings_service = SettingsService()
