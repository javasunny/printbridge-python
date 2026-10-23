import os
import uvicorn

if __name__ == "__main__":
    uvicorn.run("printbridge.app:create_app", factory=True,
                host=os.getenv("PRINT_HOST", "127.0.0.1"),
                port=int(os.getenv("PRINT_PORT", "8765")), workers=1,
                access_log=False, proxy_headers=False, timeout_graceful_shutdown=150)
