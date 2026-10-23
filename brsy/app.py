from typing import Annotated

from fastapi import Depends, FastAPI, Header, HTTPException
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, StrictBool

from . import demo
from .bot import csv_bytes
from .config import Config, ROOT
from .grade import GradeError, InvalidToken
from .security import AuthError, validate_init_data
from .service import NotLinked, Service


class TokenBody(BaseModel):
    token: str = Field(min_length=1, max_length=256)


class SettingsBody(BaseModel):
    notifications: StrictBool | None = None
    target: int | None = Field(default=None, ge=1, le=100, strict=True)


def create_app(config: Config, service: Service | None = None) -> FastAPI:
    app = FastAPI(title="БРС рядом", docs_url=None, redoc_url=None, openapi_url=None)

    @app.middleware("http")
    async def security_headers(request, call_next):
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Cache-Control"] = "no-store"
        response.headers["Content-Security-Policy"] = "default-src 'self'; script-src 'self' https://telegram.org; style-src 'self'; img-src 'self' data:; connect-src 'self'; font-src 'self'; object-src 'none'; base-uri 'none'; form-action 'self'; frame-ancestors https://web.telegram.org https://*.telegram.org"
        return response

    def identity(authorization: Annotated[str | None, Header()] = None):
        if config.demo:
            return {"id": 1, "first_name": "Александр"}
        if not authorization or not authorization.startswith("tma "):
            raise HTTPException(401, "Открой приложение через Telegram-бота.")
        try:
            return validate_init_data(authorization[4:], config.bot_token)
        except AuthError as exc:
            raise HTTPException(401, str(exc)) from None

    User = Depends(identity)

    @app.exception_handler(GradeError)
    async def grade_error(request, exc):
        from fastapi.responses import JSONResponse
        status = 409 if isinstance(exc, NotLinked) else 401 if isinstance(exc, InvalidToken) else 502
        return JSONResponse({"detail": str(exc)}, status_code=status)

    @app.get("/healthz")
    async def health():
        return {"status": "ok", "mode": "demo" if config.demo else "live"}

    @app.get("/api/dashboard")
    async def dashboard(semester: str | None = None, user=User):
        if config.demo:
            return demo.dashboard()
        if not service.store.user(user["id"]):
            return {"linked": False, "demo": False, "name": user.get("first_name", "")}
        return await service.dashboard(user["id"], semester)

    @app.post("/api/refresh")
    async def refresh(semester: str | None = None, user=User):
        return demo.dashboard() if config.demo else await service.dashboard(user["id"], semester, force=True)

    @app.post("/api/connect")
    async def connect(body: TokenBody, user=User):
        if config.demo:
            raise HTTPException(403, "В деморежиме подключение реального токена отключено.")
        try:
            await service.link(user["id"], body.token, user.get("first_name", "Студент"))
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from None
        return {"ok": True}

    @app.patch("/api/settings")
    async def settings(body: SettingsBody, user=User):
        if config.demo:
            return {"ok": True, "demo": True}
        service.require(user["id"])
        service.store.settings(user["id"], notifications=body.notifications, target=body.target)
        return {"ok": True}

    @app.delete("/api/account")
    async def delete_account(user=User):
        if config.demo:
            raise HTTPException(403, "Это демонстрационный профиль.")
        await service.delete(user["id"])
        return {"ok": True}

    @app.get("/api/subjects/{sid}")
    async def detail(sid: str, semester: str | None = None, user=User):
        if config.demo:
            result = demo.detail(sid)
            if result is None:
                raise HTTPException(404, "Предмет не найден.")
            return result
        return await service.detail(user["id"], sid, semester)

    @app.get("/api/export")
    async def export(semester: str | None = None, user=User):
        data = demo.dashboard() if config.demo else await service.dashboard(user["id"], semester)
        return Response(csv_bytes(data["subjects"]), media_type="text/csv; charset=utf-8",
                        headers={"Content-Disposition": 'attachment; filename="brsy-grades.csv"'})

    @app.get("/")
    async def index():
        return FileResponse(ROOT / "web" / "index.html")

    app.mount("/static", StaticFiles(directory=ROOT / "web"), name="static")
    return app
