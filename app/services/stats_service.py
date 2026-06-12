import datetime
from typing import Optional
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from app.models import Image, Analytics

class StatsService:
    @staticmethod
    async def record_view(
        db: AsyncSession,
        image: Image,
        ip: str,
        referrer: Optional[str] = None,
        user_agent: Optional[str] = None
    ) -> None:
        # Increment view count
        image.views += 1
        
        # Add visitor analytic entry
        log_entry = Analytics(
            image_id=image.id,
            ip_address=ip,
            referrer=referrer,
            user_agent=user_agent
        )
        db.add(log_entry)
        await db.commit()

    @staticmethod
    async def get_stats(db: AsyncSession) -> dict:
        # Total uploads
        uploads_res = await db.execute(select(func.count(Image.id)))
        total_uploads = uploads_res.scalar() or 0

        # Total views
        views_res = await db.execute(select(func.sum(Image.views)))
        total_views = views_res.scalar() or 0

        # Total storage size
        storage_res = await db.execute(select(func.sum(Image.file_size)))
        total_bytes = storage_res.scalar() or 0
        total_storage_mb = round(total_bytes / (1024 * 1024), 2)

        # Daily uploads
        day_ago = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=1)
        daily_res = await db.execute(select(func.count(Image.id)).where(Image.created_at >= day_ago))
        daily_uploads = daily_res.scalar() or 0

        # Most viewed images
        most_viewed_res = await db.execute(
            select(Image).order_by(Image.views.desc()).limit(5)
        )
        most_viewed = list(most_viewed_res.scalars().all())

        return {
            "total_uploads": total_uploads,
            "total_views": total_views,
            "total_storage_mb": total_storage_mb,
            "daily_uploads": daily_uploads,
            "most_viewed": most_viewed
        }

stats_service = StatsService()
