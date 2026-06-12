import uvicorn
from app.config import WEB_PORT

if __name__ == "__main__":
    print(f"Launching TGCloud Unified Platform on port {WEB_PORT}...")
    uvicorn.run("app.main:app", host="0.0.0.0", port=WEB_PORT, reload=True)
