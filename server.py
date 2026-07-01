from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from dotenv import load_dotenv
import uvicorn

load_dotenv()

import db
from routes.settings import router as settings_router
from routes.history import router as history_router
from routes.pipeline import router as pipeline_router
from routes.upload import router as upload_router


@asynccontextmanager
async def lifespan(app: FastAPI):
    db.init_db()
    yield


app = FastAPI(title="QA Pipeline Manager", lifespan=lifespan)

app.include_router(settings_router, prefix="/api/settings", tags=["settings"])
app.include_router(history_router, prefix="/api/history", tags=["history"])
app.include_router(pipeline_router, prefix="/api/pipeline", tags=["pipeline"])
app.include_router(upload_router, prefix="/api", tags=["upload"])

app.mount("/static", StaticFiles(directory="static"), name="static")


@app.get("/")
async def root():
    return FileResponse("static/index.html")


if __name__ == "__main__":
    uvicorn.run("server:app", host="127.0.0.1", port=8000, reload=True, reload_dirs=["routes", "core", "static"])
