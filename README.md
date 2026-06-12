# PictureMania

PictureMania is a premium, high-performance, secure image hosting platform and Telegram Mini App (WebApp) built with FastAPI, SQLAlchemy, and Aiogram 3. It utilizes a private Telegram channel as an infinite storage system and features a gorgeous glassmorphic dashboard for personalized image galleries, usage stats, and direct file management.

---

## 🚀 Key Features

### 📱 Telegram Mini App (WebApp) Integration
* **Auto-Login:** When opened inside Telegram, users are securely authenticated in the background via the Telegram WebApp SDK using their `initData` token.
* **WebApp Menu Button:** A persistent "Open App" menu button is dynamically registered next to the user's keyboard inside the chat area.
* **Personal Dashboard:** Logged-in users are redirected to a premium glassmorphic dashboard featuring:
  * **My Gallery:** A responsive photo grid showing all images uploaded by the user, view counts, and quick copyable direct/preview links.
  * **Upload Center:** Drag-and-drop file uploader supporting multi-image uploads (up to 10 images at once) that dynamically refreshes the gallery tab.
  * **Telemetry:** Real-time usage indicators showing standard user quotas, remaining upload capacity, and lifetime hostings.

### 🤖 Telegram Bot Features
* **Media Group Uploads:** Group-processes albums (up to 10 files) and responds with consolidated links to prevent chat spam.
* **Personalized Statistics:** Custom `/stats` command details standard roles, daily counters, and remaining upload limits.
* **Inline Deletion:** Every upload receives a unique deletion token allowing users to permanently remove files via web link commands.
* **Channel Protection (Kick-All):** An administrative defense system that automatically declines group join requests or kicks/bans unrecognized users attempting to enter the group.

### 🛡️ Administrative Console
* Secure access via the `{DOMAIN}/admin?token={SECRET_KEY}` URL or the bot `/admin` command.
* Full-text search and management (banning users, deleting images).
* **Admin API Keys:** Access token generator for external integrations (e.g., custom upload scripts, programmatic CLI uploads).
* **System Telemetry:** Live counters detailing total storage sizes, uploads, views, and system performance.
* **SQL Backup & Restore:** Downloads a complete database backup dump as a `.sql` attachment and supports restoration via direct web file upload.

### ⚡ Performance & Storage Architecture
* **Infinite Storage:** All uploads are stored securely on a Telegram storage channel.
* **Caching Layer:** Redis/In-memory cache scales raw assets and thumbnails dynamically while enforcing custom ETag and client Cache-Control rules.
* **Image Optimization:** Automated PIL processing optimizes size and format while removing metadata, and dynamically generates `300px` image thumbnails.

---

## 🛠️ Technology Stack

* **Backend:** FastAPI, Python 3.14+, SQLAlchemy (Async), Starlette Session Middleware
* **Telegram Bot:** Aiogram 3 (polling loop under FastAPI lifespan hooks)
* **Database:** SQLite (local fallback) / PostgreSQL (production-ready)
* **Cache:** Redis / Local Memory fallback
* **Frontend:** Tailwind CSS, FontAwesome 6, Custom Vanilla CSS Glassmorphism
* **Containerization:** Docker & Docker Compose

---

## ⚙️ Configuration (.env)

Configure the following variables in a `.env` file at the root of the project:

```env
# Server Settings
DOMAIN=https://your-domain.com
PORT=8000
SECRET_KEY=your-session-cookie-secret-key

# Telegram Bot Credentials
BOT_TOKEN=123456789:ABCDefGhIJKlmNoPQRsTUVwxyZ
BOT_USERNAME=picturemaniabot
STORAGE_CHANNEL_ID=-100XXXXXXXXXX

# Limits & Quotas
GUEST_LIMIT=20
USER_LIMIT=500

# Cache & DB Settings (optional)
DATABASE_URL=postgresql+asyncpg://user:password@host:port/database
REDIS_URL=redis://localhost:6379

# Administrators list (comma-separated Telegram IDs)
ADMIN_USER_IDS=123456789,987654321
```

---

## 💻 Local Setup & Installation

### 1. Prerequisites
Ensure you have **Python 3.10+** installed on your system.

### 2. Install Dependencies
```bash
python -m venv venv
# On Windows:
.\venv\Scripts\activate
# On macOS/Linux:
source venv/bin/activate

pip install -r requirements.txt
```

### 3. Running the Server
The application automatically spins up the Telegram bot polling task inside the lifespan of the FastAPI webserver:

```bash
uvicorn app.main:app --reload --port 8000
```

---

## 🐳 Docker Deployment

To spin up the service along with PostgreSQL and Redis containers:

```bash
docker-compose up --build -d
```

---

## 🧪 Running Tests

The test suite validates mock upload responses, rate limit overrides, and database operations. Run the tests using:

```bash
pytest
```
