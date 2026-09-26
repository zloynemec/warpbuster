"""Same-origin HTTP surface with public reports and owner-only FIT streaming."""

import asyncio
import hashlib
import hmac
import sqlite3
import time
import uuid
from contextlib import asynccontextmanager
from urllib.parse import urlsplit

from python_multipart.exceptions import MultipartParseError
from starlette.applications import Starlette
from starlette.datastructures import UploadFile
from starlette.exceptions import HTTPException
from starlette.formparsers import MultiPartException, MultiPartParser
from starlette.middleware import Middleware
from starlette.middleware.trustedhost import TrustedHostMiddleware
from starlette.requests import Request
from starlette.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse
from starlette.routing import Mount, Route
from starlette.staticfiles import StaticFiles

from .config import WebConfig
from .metadata import HOME_DESCRIPTION, HOME_TITLE, render_page, result_metadata
from .store import CapacityError, Store, valid_token
from .worker import Worker

# Yandex's documented collector hosts; no broad wildcard script permissions.
METRIKA_HOSTS = [
    "mc.yandex." + suffix
    for suffix in (
        "ru",
        "az",
        "by",
        "co.il",
        "com",
        "com.am",
        "com.ge",
        "com.tr",
        "ee",
        "fr",
        "kg",
        "kz",
        "lt",
        "lv",
        "md",
        "tj",
        "tm",
        "uz",
    )
] + ["mc.webvisor.com", "mc.webvisor.org"]
METRIKA_HTTPS = " ".join("https://" + host for host in METRIKA_HOSTS)
METRIKA_CONNECT = METRIKA_HTTPS + " " + " ".join("wss://" + host for host in METRIKA_HOSTS)
CONTENT_SECURITY_POLICY = (
    "default-src 'self'; script-src 'self' https://mc.yandex.ru https://yastatic.net; "
    "style-src 'self'; img-src 'self' data: https://tile.openstreetmap.org "
    + METRIKA_HTTPS
    + "; connect-src 'self' "
    + METRIKA_CONNECT
    + "; frame-src blob: "
    + METRIKA_HTTPS
    + "; child-src blob: "
    + METRIKA_HTTPS
    + "; object-src 'none'; base-uri 'none'; frame-ancestors 'none'"
)


class CanonicalHostMiddleware:
    """Redirect only the configured www alias, preserving escaped path and query."""

    def __init__(self, app, config: WebConfig):
        self.app = app
        self.origin = config.public_origin
        self.alias = "www." + urlsplit(self.origin).hostname

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http" and Request(scope).url.hostname == self.alias:
            path = scope.get("raw_path", scope["path"].encode()).decode("ascii")
            query = scope.get("query_string", b"").decode("ascii")
            response = RedirectResponse(
                self.origin + path + ("?" + query if query else ""), status_code=301
            )
            return await response(scope, receive, send)
        return await self.app(scope, receive, send)


ERROR_MESSAGES = {
    "invalid_fit": "Не удалось прочитать FIT. Экспортируйте оригинальную запись ещё раз.",
    "invalid_gpx": "Не удалось прочитать маршрут GPX. Проверьте файл маршрута.",
    "empty_activity": "В FIT нет записей активности.",
    "too_many_records": "В файлах слишком много точек для этой версии сервиса.",
    "repair_refused": "Не удалось безопасно записать исправленный FIT. Файл не опубликован.",
    "processing_failed": "Обработка не завершилась. Попробуйте загрузить файлы ещё раз.",
    "timeout": "Обработка заняла слишком много времени. Попробуйте другой файл.",
    "interrupted": "Сервис перезапустился во время обработки. Загрузите файлы ещё раз.",
}


@asynccontextmanager
async def multipart_form(request: Request):
    if (
        request.headers.get("content-type", "").split(";")[0].strip().lower()
        != "multipart/form-data"
    ):
        raise HTTPException(400, "Нужны FIT и необязательный GPX в формате multipart/form-data.")
    parser = MultiPartParser(
        request.headers, request.stream(), max_files=2, max_fields=0, max_part_size=1024
    )
    try:
        yield await parser.parse()
    except MultiPartException as error:
        raise HTTPException(400, error.message) from None
    except MultipartParseError:
        raise HTTPException(400, "Не удалось прочитать загрузку.") from None
    finally:
        # Starlette 0.52 closes these on MultiPartException only. Also close on malformed
        # multipart, disconnect and cancellation, before a timeout exits the upload scope.
        # Version is bounded in pyproject; regression tests exercise this cleanup contract.
        for stream in parser._files_to_close_on_error:
            stream.close()


class PrivacyMiddleware:
    def __init__(self, app, config: WebConfig):
        self.app = app
        self.config = config

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        received = 0
        upload_deadline = time.monotonic() + self.config.upload_timeout_seconds

        async def bounded_receive():
            nonlocal received
            if scope["path"] == "/api/jobs":
                try:
                    message = await asyncio.wait_for(
                        receive(), max(0, upload_deadline - time.monotonic())
                    )
                except TimeoutError:
                    raise MultiPartException("upload_timeout") from None
            else:
                message = await receive()
            if scope["path"] == "/api/jobs" and message["type"] == "http.request":
                received += len(message.get("body", b""))
                if received > self.config.body_limit_bytes:
                    # Starlette's multipart parser closes spooled files for this exception.
                    raise MultiPartException("upload_limit_exceeded")
            return message

        async def private_send(message):
            if message["type"] == "http.response.start":
                headers = list(message.get("headers", []))
                headers.extend(
                    [
                        (b"referrer-policy", b"no-referrer"),
                        (b"x-content-type-options", b"nosniff"),
                        (
                            b"content-security-policy",
                            CONTENT_SECURITY_POLICY.encode("ascii"),
                        ),
                    ]
                )
                if scope["path"].startswith(("/api/", "/res/")):
                    headers.extend(
                        [
                            (b"cache-control", b"private, no-store"),
                            (b"x-robots-tag", b"noindex, nofollow, noarchive"),
                        ]
                    )
                message = {**message, "headers": headers}
            await send(message)

        await self.app(scope, bounded_receive, private_send)


def create_app(config: WebConfig | None = None, *, start_worker: bool = True):
    config = config or WebConfig.from_environment()
    store = Store(config)
    worker = Worker(store)

    @asynccontextmanager
    async def lifespan(app):
        if start_worker:
            worker.start()
        try:
            yield
        finally:
            if start_worker:
                await asyncio.to_thread(worker.stop)

    def require_same_origin(request: Request):
        if request.headers.get("x-warpbuster-request") != "1":
            raise HTTPException(403, "Запрос должен быть отправлен со страницы WarpBuster.")
        if request.headers.get("origin", config.public_origin) != config.public_origin:
            raise HTTPException(403, "Недопустимый источник запроса.")

    def job_for(request: Request):
        job = store.get(request.path_params["uid"])
        if not job:
            raise HTTPException(404, "Результат не найден. Проверьте ссылку.")
        if job["expires"] <= time.time() or job["status"] == "expired":
            raise HTTPException(410, "Срок хранения результата истёк. Загрузите файлы заново.")
        return job

    def owns(request: Request, job) -> bool:
        owner = store.owner(request.cookies.get(config.cookie_name))
        return bool(owner and hmac.compare_digest(owner, job["owner"]))

    async def session(request):
        require_same_origin(request)
        token = request.cookies.get(config.cookie_name)
        response = JSONResponse(
            {
                "file_limit_bytes": config.file_limit_bytes,
                "retention_days": config.retention_seconds / 86400,
            }
        )
        if not store.owner(token):
            token = store.new_session()
            response.set_cookie(
                config.cookie_name,
                token,
                max_age=config.session_seconds,
                secure=config.secure_cookie,
                httponly=True,
                samesite="lax",
                path="/",
            )
        return response

    async def health(request):
        database_ok = False
        try:
            with store.connect() as database:
                database_ok = database.execute("SELECT 1").fetchone()[0] == 1
        except OSError, sqlite3.Error:
            pass
        worker_ok = not start_worker or bool(worker.thread and worker.thread.is_alive())
        available = database_ok and worker_ok
        return JSONResponse(
            {"status": "ok" if available else "unavailable"},
            status_code=200 if available else 503,
            headers={"Cache-Control": "no-store"},
        )

    async def submit(request: Request):
        require_same_origin(request)
        owner = store.owner(request.cookies.get(config.cookie_name))
        if not owner:
            raise HTTPException(401, "Сессия не найдена. Разрешите cookie и обновите страницу.")
        length = request.headers.get("content-length")
        if length:
            try:
                if int(length) < 0 or int(length) > config.body_limit_bytes:
                    raise HTTPException(413, "Файлы превышают допустимый размер.")
            except ValueError:
                raise HTTPException(400, "Некорректная длина запроса.") from None
        submission_key = request.headers.get("x-warpbuster-upload-id", "")
        try:
            if str(uuid.UUID(submission_key)) != submission_key:
                raise ValueError
        except ValueError:
            raise HTTPException(400, "Обновите страницу перед отправкой файлов.") from None
        uid, created = store.reserve(owner, submission_key)
        if not created:
            existing = store.get(uid)
            store.events.write("upload_reused", uid, status=existing["status"])
            if existing["expires"] <= time.time():
                raise HTTPException(
                    410, "Срок хранения этой загрузки истёк. Выберите файлы заново."
                )
            if existing["status"] == "uploading":
                raise HTTPException(409, "Эта загрузка ещё идёт. Подождите и повторите запрос.")
            return JSONResponse({"uid": uid, "url": f"/res/{uid}"}, status_code=202)
        directory = config.data_dir / uid
        inputs = store.uploads_dir / uid
        committed = False
        failure_reason = "interrupted"
        try:
            store.events.write("upload_started", uid)
            directory.mkdir(mode=0o700)
            inputs.mkdir(mode=0o700)
            async with asyncio.timeout(config.upload_timeout_seconds):
                async with multipart_form(request) as form:
                    parts = form.multi_items()
                    keys = [key for key, _ in parts]
                    if (
                        len(keys) != len(set(keys))
                        or "activity" not in keys
                        or not set(keys) <= {"activity", "course"}
                    ):
                        raise HTTPException(
                            400, "Нужны FIT-запись и, при наличии, один GPX маршрута."
                        )
                    has_course = "course" in keys
                    for key, filename, extension in (
                        ("activity", "original.fit", ".fit"),
                        ("course", "course.gpx", ".gpx"),
                    ):
                        if key == "course" and not has_course:
                            continue
                        upload = form[key]
                        if not isinstance(upload, UploadFile) or not (
                            upload.filename or ""
                        ).lower().endswith(extension):
                            raise HTTPException(
                                400, "Проверьте форматы: запись .fit и маршрут .gpx."
                            )
                        if not upload.size:
                            raise HTTPException(400, "Один из файлов пустой.")
                        if upload.size > config.file_limit_bytes:
                            raise HTTPException(413, "Файл превышает допустимый размер.")
                        destination = inputs / filename
                        digest = hashlib.sha256()
                        with destination.open("xb") as stream:
                            destination.chmod(0o600)
                            while chunk := await upload.read(64 * 1024):
                                stream.write(chunk)
                                digest.update(chunk)
                        store.events.write(
                            "file_uploaded",
                            uid,
                            role=key,
                            file=filename,
                            size_bytes=upload.size,
                            sha256=digest.hexdigest(),
                        )
            store.events.write("upload_completed", uid)
            store.state(uid, "queued", has_course=has_course)
            committed = True
            return JSONResponse({"uid": uid, "url": f"/res/{uid}"}, status_code=202)
        except TimeoutError:
            failure_reason = "timeout"
            raise HTTPException(
                408, "Загрузка заняла слишком много времени. Повторите попытку."
            ) from None
        except HTTPException as error:
            failure_reason = (
                "timeout" if error.detail == "upload_timeout" else f"http_{error.status_code}"
            )
            if error.detail == "upload_timeout":
                raise HTTPException(
                    408, "Загрузка заняла слишком много времени. Повторите попытку."
                ) from None
            if error.detail == "upload_limit_exceeded":
                raise HTTPException(413, "Файлы превышают допустимый размер.") from None
            if error.status_code == 400 and not str(error.detail).startswith(
                ("Нужны", "Проверьте", "Один")
            ):
                raise HTTPException(
                    400, "Не удалось прочитать загрузку. Выберите FIT и необязательный GPX ещё раз."
                ) from None
            raise
        finally:
            if not committed:
                store.discard(uid)
                store.events.write("upload_failed", uid, reason=failure_reason)

    async def status(request):
        job = job_for(request)
        return JSONResponse(
            {
                "status": job["status"],
                "is_owner": owns(request, job),
                "can_download": job["status"] == "ready"
                and bool(job["has_fit"])
                and owns(request, job),
                "expires_at": job["expires"],
                "message": ERROR_MESSAGES.get(job["error"], "")
                if job["status"] == "failed"
                else "",
            }
        )

    async def result(request):
        job = job_for(request)
        if job["status"] != "ready":
            raise HTTPException(409, "Результат ещё не готов.")
        return FileResponse(
            config.data_dir / job["uid"] / "result.json", media_type="application/json"
        )

    async def download(request):
        job = job_for(request)
        if not owns(request, job):
            raise HTTPException(
                403, "Скачать FIT может только загрузивший файлы — в исходном браузере."
            )
        if job["status"] != "ready" or not job["has_fit"]:
            raise HTTPException(409, "Исправленный FIT недоступен для этого результата.")
        return FileResponse(
            config.data_dir / job["uid"] / "corrected.fit",
            media_type="application/octet-stream",
            filename="warpbuster-corrected.fit",
        )

    async def favicon(request):
        return FileResponse(config.static_dir / "favicon.png", media_type="image/png")

    async def robots(request):
        return FileResponse(config.static_dir / "robots.txt", media_type="text/plain")

    def page_response(page: str, *, status_code: int = 200, headers=None):
        counter_id = config.yandex_metrika_id
        analytics = ""
        if counter_id:
            analytics = (
                f'<script src="/assets/metrika.js" data-counter-id="{counter_id}" defer></script>'
                '<noscript><div><img class="metrika-pixel" role="presentation" '
                f'src="https://mc.yandex.ru/watch/{counter_id}" alt=""></div></noscript>'
            )
        return HTMLResponse(
            page.replace("<!-- analytics -->", analytics), status_code=status_code, headers=headers
        )

    async def home(request):
        return page_response(
            render_page(
                config.static_dir / "index.html",
                origin=config.public_origin,
                route="/",
                title=HOME_TITLE,
                description=HOME_DESCRIPTION,
            )
        )

    async def fix(request):
        return page_response((config.static_dir / "fix" / "index.html").read_text(encoding="utf-8"))

    async def faq(request):
        return page_response(
            render_page(
                config.static_dir / "faq" / "index.html",
                origin=config.public_origin,
                route="/faq",
                title="FAQ — как работает WarpBuster",
                description=(
                    "Как WarpBuster восстанавливает GPS-трек, сохраняет исходные данные "
                    "и защищает файлы. Этапы обработки и доступ по публичной ссылке."
                ),
            )
        )

    async def result_page(request):
        uid = request.path_params["uid"]
        if not valid_token(uid):
            raise HTTPException(404, "Результат не найден.")
        job = store.get(uid)
        code = 200
        if not job:
            code = 404
            title, description = (
                "Результат не найден — WarpBuster",
                "Проверьте ссылку на результат.",
            )
        elif job["expires"] <= time.time() or job["status"] == "expired":
            code = 410
            title = "Срок хранения результата истёк — WarpBuster"
            description = "Этот результат больше недоступен."
        elif job["status"] == "ready":
            title, description = await asyncio.to_thread(
                result_metadata, config.data_dir / uid / "result.json"
            )
        elif job["status"] == "failed":
            title = "Обработка не завершилась — WarpBuster"
            description = "Не удалось подготовить результат обработки трека."
        else:
            title = "Трек обрабатывается — WarpBuster"
            description = (
                "После обработки здесь появятся карта, показатели забега и сравнение треков."
            )
        return page_response(
            render_page(
                config.static_dir / "res" / "index.html",
                origin=config.public_origin,
                route=f"/res/{uid}",
                title=title,
                description=description,
            ),
            status_code=code,
        )

    async def http_error(request, error):
        if error.status_code == 404 and not request.url.path.startswith("/api/"):
            return page_response(
                (config.static_dir / "404" / "index.html").read_text(encoding="utf-8"),
                status_code=404,
                headers={"X-Robots-Tag": "noindex, nofollow"},
            )
        return JSONResponse({"detail": error.detail}, status_code=error.status_code)

    async def capacity_error(request, error):
        return JSONResponse(
            {"detail": "Сервис занят или достигнут лимит результатов. Попробуйте позже."},
            status_code=429,
            headers={"Retry-After": "60"},
        )

    async def internal_error(request, error):
        # No exception text or debug traceback is ever returned to a public visitor.
        return JSONResponse(
            {"detail": "Сервис временно недоступен. Попробуйте позже."},
            status_code=503,
            headers={"Cache-Control": "private, no-store"},
        )

    app = Starlette(
        lifespan=lifespan,
        routes=[
            Route("/", home),
            Route("/robots.txt", robots),
            Route("/favicon.png", favicon),
            Route("/health", health),
            Route("/fix", fix),
            Route("/faq", faq),
            Route("/res/{uid}", result_page),
            Route("/api/session", session),
            Route("/api/jobs", submit, methods=["POST"]),
            Route("/api/results/{uid}/status", status),
            Route("/api/results/{uid}", result),
            Route("/api/results/{uid}/download", download),
            Mount("/assets", StaticFiles(directory=config.static_dir / "assets")),
        ],
        middleware=[
            Middleware(PrivacyMiddleware, config=config),
            Middleware(CanonicalHostMiddleware, config=config),
            Middleware(
                TrustedHostMiddleware, allowed_hosts=[urlsplit(config.public_origin).hostname]
            ),
        ],
        exception_handlers={
            HTTPException: http_error,
            CapacityError: capacity_error,
            Exception: internal_error,
        },
    )
    app.state.store = store
    app.state.worker = worker
    app.state.config = config
    return app
