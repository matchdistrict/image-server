import uuid
from sqlalchemy import Column, String, Integer, BigInteger, DateTime, Boolean, ForeignKey, func
from sqlalchemy.orm import relationship
from app.database import Base

class Image(Base):
    __tablename__ = "images"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    slug = Column(String(50), unique=True, index=True, nullable=False)
    file_id = Column(String(255), nullable=False)
    file_unique_id = Column(String(100), unique=True, index=True, nullable=False)
    message_id = Column(Integer, nullable=False)
    mime_type = Column(String(100), nullable=False)
    file_size = Column(Integer, nullable=False)
    uploaded_by = Column(BigInteger, index=True, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), index=True, nullable=False)
    views = Column(Integer, default=0, nullable=False)
    delete_token = Column(String(100), unique=True, nullable=False)
    is_nsfw = Column(Boolean, default=False, nullable=False)
    nsfw_checked = Column(Boolean, default=False, nullable=False)

    analytics = relationship("Analytics", back_populates="image", cascade="all, delete-orphan")


class Analytics(Base):
    __tablename__ = "analytics"

    id = Column(Integer, primary_key=True, autoincrement=True)
    image_id = Column(String(36), ForeignKey("images.id", ondelete="CASCADE"), nullable=False, index=True)
    ip_address = Column(String(100), nullable=False)
    referrer = Column(String(255), nullable=True)
    user_agent = Column(String(255), nullable=True)
    viewed_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    image = relationship("Image", back_populates="analytics")


class BannedUser(Base):
    __tablename__ = "banned_users"

    telegram_id = Column(BigInteger, primary_key=True)
    banned_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    reason = Column(String(255), nullable=True)


class AdminApiKey(Base):
    __tablename__ = "admin_api_keys"

    key = Column(String(255), primary_key=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
