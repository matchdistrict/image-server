import datetime
import logging
from sqlalchemy import select, text
from app.models import Image, Analytics, BannedUser, AdminApiKey

logger = logging.getLogger(__name__)

class BackupService:
    @staticmethod
    async def generate_sql_backup(db) -> str:
        """
        Generates a complete, database-agnostic SQL script containing all current image records,
        banned users, and view analytics.
        """
        sql_lines = []
        sql_lines.append("-- TGCloud Database Backup SQL Dump")
        sql_lines.append(f"-- Generated: {datetime.datetime.now(datetime.timezone.utc).isoformat()}")
        sql_lines.append("PRAGMA foreign_keys = OFF;")
        sql_lines.append("BEGIN TRANSACTION;")
        
        # 1. Banned Users Table
        stmt = select(BannedUser)
        res = await db.execute(stmt)
        banned_users = res.scalars().all()
        sql_lines.append("\n-- Table: banned_users")
        sql_lines.append("DELETE FROM banned_users;")
        for u in banned_users:
            reason_val = f"'{u.reason.replace("'", "''")}'" if u.reason else "NULL"
            sql_lines.append(
                f"INSERT INTO banned_users (telegram_id, banned_at, reason) "
                f"VALUES ({u.telegram_id}, '{u.banned_at.isoformat()}', {reason_val});"
            )
            
        # 2. Images Table
        stmt = select(Image)
        res = await db.execute(stmt)
        images = res.scalars().all()
        sql_lines.append("\n-- Table: images")
        sql_lines.append("DELETE FROM images;")
        for img in images:
            uploaded_by_val = str(img.uploaded_by) if img.uploaded_by is not None else "NULL"
            is_nsfw_val = "1" if img.is_nsfw else "0"
            nsfw_checked_val = "1" if img.nsfw_checked else "0"
            sql_lines.append(
                f"INSERT INTO images (id, slug, file_id, file_unique_id, message_id, mime_type, file_size, uploaded_by, created_at, views, delete_token, is_nsfw, nsfw_checked) "
                f"VALUES ('{img.id}', '{img.slug}', '{img.file_id}', '{img.file_unique_id}', {img.message_id}, '{img.mime_type}', {img.file_size}, {uploaded_by_val}, '{img.created_at.isoformat()}', {img.views}, '{img.delete_token}', {is_nsfw_val}, {nsfw_checked_val});"
            )
            
        # 3. Analytics Table
        stmt = select(Analytics)
        res = await db.execute(stmt)
        analytics = res.scalars().all()
        sql_lines.append("\n-- Table: analytics")
        sql_lines.append("DELETE FROM analytics;")
        for a in analytics:
            referrer_val = f"'{a.referrer.replace("'", "''")}'" if a.referrer else "NULL"
            user_agent_val = f"'{a.user_agent.replace("'", "''")}'" if a.user_agent else "NULL"
            sql_lines.append(
                f"INSERT INTO analytics (id, image_id, ip_address, referrer, user_agent, viewed_at) "
                f"VALUES ({a.id}, '{a.image_id}', '{a.ip_address}', {referrer_val}, {user_agent_val}, '{a.viewed_at.isoformat()}');"
            )
            
        sql_lines.append("\nCOMMIT;")
        sql_lines.append("PRAGMA foreign_keys = ON;")
        return "\n".join(sql_lines)

    @staticmethod
    async def restore_sql_backup(db, sql_content: str) -> None:
        """
        Parses and executes a SQL backup script to restore all database rows.
        """
        # Split sql commands by semicolons while filtering out comment logs
        statements = []
        current_statement = []
        for line in sql_content.splitlines():
            line_strip = line.strip()
            if not line_strip or line_strip.startswith("--"):
                continue
            
            # Check if there is an inline comment to ignore for the semicolon check
            check_line = line_strip
            if "--" in line_strip:
                parts = line_strip.split("--")
                # Count single quotes in the first part to ensure the "--" is not inside a string literal
                if parts[0].count("'") % 2 == 0:
                    check_line = parts[0].strip()

            current_statement.append(line)
            if check_line.endswith(";"):
                statements.append("\n".join(current_statement))
                current_statement = []
                
        # Execute each statement
        for stmt in statements:
            if stmt.strip():
                await db.execute(text(stmt))
        await db.commit()

backup_service = BackupService()
