from unittest.mock import AsyncMock, MagicMock
from fastapi.testclient import TestClient
from app.main import app

client = TestClient(app)

def test_api_stats_endpoint():
    response = client.get("/api/stats")
    assert response.status_code == 200
    data = response.json()
    assert "total_uploads" in data
    assert "total_views" in data
    assert "total_storage_mb" in data

def test_api_nonexistent_image():
    response = client.get("/api/image/nonexistent_slug")
    assert response.status_code == 404

def test_api_duplicate_upload(monkeypatch):
    # Mock image_service and moderation_service
    from app.api.endpoints import image_service, moderation_service, bot
    
    monkeypatch.setattr(image_service, "validate_and_optimize", MagicMock(return_value=b"fake_optimized_bytes"))
    monkeypatch.setattr(moderation_service, "check_nsfw", MagicMock(return_value=False))
    
    # Mock bot.send_document
    mock_send_document = AsyncMock()
    mock_msg = MagicMock()
    mock_msg.message_id = 9999
    mock_msg.document = MagicMock()
    mock_msg.document.file_id = "mocked_file_id_123"
    mock_msg.document.file_unique_id = "mocked_unique_id_123"
    mock_send_document.return_value = mock_msg
    monkeypatch.setattr(bot, "send_document", mock_send_document)
    
    # First upload
    payload = {"file": ("test.png", b"fake_png_data", "image/png")}
    response1 = client.post("/api/upload", files=payload)
    assert response1.status_code == 200
    data1 = response1.json()
    assert "slug" in data1
    slug1 = data1["slug"]
    
    # Second upload (same file unique ID)
    response2 = client.post("/api/upload", files=payload)
    assert response2.status_code == 200
    data2 = response2.json()
    assert data2["slug"] == slug1

def test_api_deleted_from_telegram(monkeypatch):
    from app.api.endpoints import image_service, moderation_service, bot
    from aiogram.exceptions import TelegramBadRequest
    
    monkeypatch.setattr(image_service, "validate_and_optimize", MagicMock(return_value=b"fake_optimized_bytes"))
    monkeypatch.setattr(moderation_service, "check_nsfw", MagicMock(return_value=False))
    
    # Mock bot.send_document
    mock_send_document = AsyncMock()
    mock_msg = MagicMock()
    mock_msg.message_id = 8888
    mock_msg.document = MagicMock()
    mock_msg.document.file_id = "delete_test_file_id"
    mock_msg.document.file_unique_id = "delete_test_unique_id"
    mock_send_document.return_value = mock_msg
    monkeypatch.setattr(bot, "send_document", mock_send_document)
    
    # Upload image
    payload = {"file": ("test.png", b"fake_png_data", "image/png")}
    response = client.post("/api/upload", files=payload)
    assert response.status_code == 200
    slug = response.json()["slug"]
    
    # Clear the msg_exists cache so the next request actually queries Telegram
    from app.services.cache_service import cache_service
    import asyncio
    cache_service._local_cache.pop(f"msg_exists:{slug}", None)
    try:
        loop = asyncio.get_running_loop()
        loop.create_task(cache_service.delete(f"msg_exists:{slug}"))
    except RuntimeError:
        asyncio.run(cache_service.delete(f"msg_exists:{slug}"))
    
    # Mock bot.edit_message_reply_markup to raise TelegramBadRequest (simulating deleted message)
    mock_edit_markup = AsyncMock()
    # E400 bad request simulating Telegram error
    mock_edit_markup.side_effect = TelegramBadRequest(
        message="Bad Request: message to edit not found",
        method=MagicMock()
    )
    monkeypatch.setattr(bot, "edit_message_reply_markup", mock_edit_markup)
    
    # Access the image - it should detect the deletion, remove the image from the DB, and return 404
    response_access = client.get(f"/api/image/{slug}")
    assert response_access.status_code == 404
    
    # Try accessing again (should be completely gone from DB now)
    response_access2 = client.get(f"/api/image/{slug}")
    assert response_access2.status_code == 404
