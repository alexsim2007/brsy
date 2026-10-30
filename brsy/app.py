from hashlib import sha256
from datetime import date
from typing import Annotated, Literal

from fastapi import Depends, FastAPI, Header, HTTPException, Query
from fastapi.responses import HTMLResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, StrictBool
from starlette.middleware.gzip import GZipMiddleware

from . import demo
from .bot import csv_bytes
from .config import Config, ROOT
from .grade import GradeError, InvalidToken
from .security import AuthError, validate_init_data
from .service import NotLinked, Service
from .schedule import PROVIDERS, ScheduleClient, ScheduleError, calendar_bytes


class TokenBody(BaseModel):
    token: str = Field(min_length=1, max_length=256)


class SettingsBody(BaseModel):
    notifications: StrictBool | None = None
    target: int | None = Field(default=None, ge=1, le=100, strict=True)


class ScheduleBody(BaseModel):
    provider: Literal["mmcs", "ictis"]
    group_id: str = Field(min_length=1, max_length=64, pattern=r"^[a-zA-Z0-9-]+$")
    group_name: str = Field(default="", max_length=120)
    course: int | None = Field(default=None, ge=1, le=100, strict=True)


def create_app(config: Config, service: Service | None = None, schedule: ScheduleClient | None = None) -> FastAPI:
    app = FastAPI(title="БРС рядом", docs_url=None, redoc_url=None, openapi_url=None)
    if schedule is None and config.demo:
        schedule = ScheduleClient(None, demo=True)
    demo_preference = None
    app.add_middleware(GZipMiddleware, minimum_size=1000, compresslevel=4)
    # Hash once at startup. A deployment changes URLs and safely bypasses old caches.
    assets = {f"/static/{name}": sha256((ROOT / "web" / name).read_bytes()).hexdigest()[:16]
              for name in ("theme.js", "style.css", "app.js", "brand-icon.png")}
    index_html = (ROOT / "web" / "index.html").read_text(encoding="utf-8")
    for path, version in assets.items():
        index_html = index_html.replace(path, f"{path}?v={version}")

    @app.middleware("http")
    async def security_headers(request, call_next):
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        version = assets.get(request.url.path)
        if version and response.status_code in (200, 304):
            response.headers["Cache-Control"] = (
                "public, max-age=31536000, immutable" if request.query_params.get("v") == version
                else "public, max-age=0, must-revalidate"
            )
        else:
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

    def timetable():
        if schedule is None:
            raise HTTPException(503, "Расписание не подключено на сервере.")
        return schedule

    def preference(uid):
        return demo_preference if config.demo else service.store.schedule_preference(uid)

    @app.exception_handler(ScheduleError)
    async def schedule_error(request, exc):
        from fastapi.responses import JSONResponse
        return JSONResponse({"detail": str(exc)}, status_code=exc.status)

    @app.get("/api/schedule/catalog")
    async def schedule_catalog(provider: Literal["mmcs", "ictis"] = "mmcs", course: int | None = None,
                               q: str = Query(default="", max_length=80), user=User):
        return await timetable().catalogue(provider, course, q)

    @app.patch("/api/schedule/preference")
    async def schedule_preference(body: ScheduleBody, user=User):
        nonlocal demo_preference
        choice = await timetable().validate_group(body.provider, body.group_id, body.course, body.group_name)
        if config.demo:
            demo_preference = choice
        else:
            service.store.save_schedule_preference(user["id"], choice)
        return {"ok": True, "preference": choice}

    @app.get("/api/schedule")
    async def get_schedule(week: date | None = None, user=User):
        choice = preference(user["id"])
        if choice is None:
            return {"configured": False, "providers": PROVIDERS, "demo": config.demo}
        return await timetable().week(choice, week)

    @app.get("/api/schedule/calendar")
    async def schedule_calendar(week: date | None = None, user=User):
        choice = preference(user["id"])
        if choice is None:
            raise HTTPException(409, "Сначала выбери свою группу.")
        data = await timetable().week(choice, week)
        return Response(calendar_bytes(data), media_type="text/calendar; charset=utf-8",
                        headers={"Content-Disposition": f'attachment; filename="brsy-schedule-{data["week_start"]}.ics"'})

    @app.exception_handler(GradeError)
    async def grade_error(request, exc):
        from fastapi.responses import JSONResponse
        status = 409 if isinstance(exc, NotLinked) else 401 if isinstance(exc, InvalidToken) else 502
        return JSONResponse({"detail": str(exc)}, status_code=status)

    @app.get("/health")
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
        return HTMLResponse(index_html)

    app.mount("/static", StaticFiles(directory=ROOT / "web"), name="static")
    return app
