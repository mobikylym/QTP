import uvicorn

from src.app.core.config import settings

if __name__ == '__main__':
    uvicorn.run(
        'src.app.main:app',
        host=settings.APP_HOST,
        port=settings.APP_PORT,
        reload=True,
    )
