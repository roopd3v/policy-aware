"""ASGI API. No raw evaluator, credential administration or payment endpoint."""

import asyncio
from contextlib import asynccontextmanager
import json
import logging
import os
from pathlib import Path
import sqlite3
import time
import uuid

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from starlette.concurrency import run_in_threadpool
from starlette.middleware.trustedhost import TrustedHostMiddleware

from bemix_pilot.engine import ConfigurationError, EvidenceError
from bemix_pilot.serialization import InputError, _pairs, _reject_number
from . import __version__
from .service import DecisionService, ServiceError, fail
from .storage import IntegrityFailure, Store


MAX_BODY = 1_048_576
LOG = logging.getLogger("bemix_service")


def read_key(path):
    path = Path(path)
    if os.name == "posix" and path.stat().st_mode & 0o077:
        raise ValueError("Audit key file must have mode 0600 or stricter.")
    key = path.read_bytes()
    if len(key) != 32:
        raise ValueError("Audit key file must contain exactly 32 random bytes.")
    return key


def bearer(request):
    values = request.headers.getlist("authorization")
    if len(values) != 1:
        fail(401, "unauthorized", "One Authorization: Bearer header is required.")
    pieces = values[0].split()
    if len(pieces) != 2 or pieces[0].lower() != "bearer":
        fail(401, "unauthorized", "Bearer credentials are required.")
    return pieces[1]


async def read_body(request):
    if request.headers.get("content-type", "").split(";", 1)[0].lower() != "application/json":
        fail(415, "unsupported_media_type", "Content-Type must be application/json.")
    if request.headers.get("content-encoding", "identity").lower() != "identity":
        fail(415, "unsupported_encoding", "Compressed request bodies are not accepted.")
    data = bytearray()
    try:
        async with asyncio.timeout(10):
            async for chunk in request.stream():
                if len(data) + len(chunk) > MAX_BODY:
                    fail(413, "request_too_large", "Request body exceeds 1 MiB.")
                data.extend(chunk)
    except TimeoutError:
        fail(408, "request_timeout", "Request body was not received within ten seconds.")
    try:
        result = json.loads(data, object_pairs_hook=_pairs, parse_float=_reject_number, parse_constant=_reject_number)
    except (ValueError, UnicodeDecodeError, RecursionError) as error:
        raise ServiceError(422, "invalid_json", "JSON must use unique keys and exact numeric strings.") from error
    if not isinstance(result, dict):
        fail(422, "invalid_json", "The request must be a JSON object.")
    return result


def create_app(service=None, allowed_hosts=None):
    if service is None:
        db = os.environ.get("BEMIX_DB_PATH")
        key_file = os.environ.get("BEMIX_AUDIT_KEY_FILE")
        if not db or not key_file:
            raise ValueError("BEMIX_DB_PATH and BEMIX_AUDIT_KEY_FILE are required; no insecure defaults exist.")
        service = DecisionService(Store(db, read_key(key_file)))
    if allowed_hosts is None:
        allowed_hosts = [v.strip() for v in os.environ.get("BEMIX_ALLOWED_HOSTS", "").split(",") if v.strip()]
    if not allowed_hosts or any("*" in v for v in allowed_hosts):
        raise ValueError("Explicit BEMIX_ALLOWED_HOSTS are required; wildcards are not accepted.")

    @asynccontextmanager
    async def lifespan(app):
        await run_in_threadpool(service.store.verify)
        yield

    app = FastAPI(title="BEMIX Decision Service", version=__version__, docs_url=None,
                  redoc_url=None, openapi_url=None, lifespan=lifespan)
    app.state.service = service
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=allowed_hosts)

    @app.middleware("http")
    async def response_headers(request, call_next):
        request.state.request_id = str(uuid.uuid4())
        started = time.monotonic()
        response = await call_next(request)
        response.headers["X-Request-ID"] = request.state.request_id
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        logging.getLogger("uvicorn.error").info("request=%s method=%s route=%s status=%s elapsed_ms=%.1f",
            request.state.request_id, request.method, getattr(request.scope.get("route"), "name", "unmatched"),
            response.status_code, (time.monotonic() - started) * 1000)
        return response

    @app.exception_handler(ServiceError)
    async def application_error(request, error):
        headers = {"WWW-Authenticate": "Bearer"} if error.status == 401 else {}
        if error.status == 429:
            headers["Retry-After"] = "60"
        return JSONResponse({"error": error.code, "message": error.message,
                             "request_id": getattr(request.state, "request_id", None)}, status_code=error.status, headers=headers)

    @app.exception_handler(ConfigurationError)
    @app.exception_handler(EvidenceError)
    @app.exception_handler(InputError)
    async def validation_error(request, error):
        return JSONResponse({"error": "invalid_input", "message": str(error)}, status_code=422)

    @app.exception_handler(sqlite3.IntegrityError)
    async def duplicate_error(request, error):
        return JSONResponse({"error": "conflict", "message": "An immutable record or unique version already exists."}, status_code=409)

    @app.exception_handler(sqlite3.OperationalError)
    @app.exception_handler(IntegrityFailure)
    async def persistence_error(request, error):
        LOG.error("Persistence/integrity failure request=%s type=%s", getattr(request.state, "request_id", ""), type(error).__name__)
        return JSONResponse({"error": "service_unavailable", "message": "The decision store requires operator attention."}, status_code=503)

    @app.exception_handler(Exception)
    async def unexpected_error(request, error):
        LOG.error("Unexpected failure request=%s type=%s", getattr(request.state, "request_id", ""), type(error).__name__)
        return JSONResponse({"error": "internal_error", "message": "An internal error prevented this operation.",
                             "request_id": getattr(request.state, "request_id", None)}, status_code=500)

    @app.get("/healthz")
    async def health():
        return {"status": "ok"}

    @app.get("/readyz")
    async def ready():
        def check():
            with service.store.connection() as conn:
                conn.execute("SELECT 1").fetchone()
        await run_in_threadpool(check)
        return {"status": "ready"}

    async def write(request, program, operation, resource=""):
        token = bearer(request)
        await run_in_threadpool(service.preflight, token, program)
        keys = request.headers.getlist("idempotency-key")
        if len(keys) != 1:
            fail(422, "idempotency_required", "Exactly one Idempotency-Key is required.")
        body = await read_body(request)
        return await run_in_threadpool(service.command, token, program, operation, resource, body, keys[0])

    @app.post("/v1/programs/{program}/policies")
    async def draft(program: str, request: Request):
        return await write(request, program, "draft_policy")

    @app.post("/v1/programs/{program}/policies/{policy_id}/publish")
    async def publish(program: str, policy_id: str, request: Request):
        return await write(request, program, "publish_policy", policy_id)

    @app.get("/v1/programs/{program}/policies/{policy_id}")
    async def policy(program: str, policy_id: str, request: Request):
        return await run_in_threadpool(service.read_policy, bearer(request), program, policy_id)

    @app.get("/v1/programs/{program}/active-policy")
    async def active_policy(program: str, asset: str, request: Request):
        return await run_in_threadpool(service.read_active_policy, bearer(request), program, asset)

    @app.get("/v1/programs/{program}/platform-guardrails")
    async def platform_guardrails(program: str, asset: str, request: Request):
        return await run_in_threadpool(service.read_guardrails, bearer(request), program, asset)

    @app.get("/v1/programs/{program}/submission-options")
    async def options(program: str, asset: str, request: Request):
        return await run_in_threadpool(service.read_options, bearer(request), program, asset)

    @app.post("/v1/programs/{program}/submissions")
    async def submit(program: str, request: Request):
        return await write(request, program, "submit")

    @app.post("/v1/programs/{program}/estimates")
    async def estimate(program: str, request: Request):
        token = bearer(request)
        await run_in_threadpool(service.preflight, token, program)
        return await run_in_threadpool(service.preview_estimate, token, program, await read_body(request))

    @app.get("/v1/programs/{program}/submissions/{submission_id}")
    async def submission(program: str, submission_id: str, request: Request):
        return await run_in_threadpool(service.read_submission, bearer(request), program, submission_id)

    @app.post("/v1/programs/{program}/submissions/{submission_id}/claims")
    async def claim_revision(program: str, submission_id: str, request: Request):
        return await write(request, program, "change_claims", submission_id)

    @app.get("/v1/programs/{program}/submissions/{submission_id}/claims/{revision}")
    async def claim_history(program: str, submission_id: str, revision: int, request: Request):
        return await run_in_threadpool(service.read_submission_version, bearer(request), program, submission_id, revision)

    @app.post("/v1/programs/{program}/cases")
    async def case_create(program: str, request: Request):
        return await write(request, program, "create_case")

    @app.get("/v1/programs/{program}/cases/{case_id}")
    async def case_read(program: str, case_id: str, request: Request, offset: int = 0, limit: int = 20):
        return await run_in_threadpool(service.read_case, bearer(request), program, case_id, offset, limit)

    @app.post("/v1/programs/{program}/cases/{case_id}/assessments")
    async def initial(program: str, case_id: str, request: Request):
        return await write(request, program, "initial", case_id)

    @app.get("/v1/programs/{program}/cases/{case_id}/records/{record_id}")
    async def case_record(program: str, case_id: str, record_id: str, request: Request):
        return await run_in_threadpool(service.read_case_record, bearer(request), program, case_id, record_id)

    @app.post("/v1/programs/{program}/cases/{case_id}/revisions")
    async def revision(program: str, case_id: str, request: Request):
        return await write(request, program, "revise", case_id)

    @app.post("/v1/programs/{program}/cases/{case_id}/handoff")
    async def handoff(program: str, case_id: str, request: Request):
        return await write(request, program, "handoff", case_id)

    @app.post("/v1/programs/{program}/cases/{case_id}/award")
    async def award(program: str, case_id: str, request: Request):
        return await write(request, program, "award", case_id)

    @app.post("/v1/programs/{program}/cases/{case_id}/appeals")
    async def appeal(program: str, case_id: str, request: Request):
        return await write(request, program, "appeal", case_id)

    @app.post("/v1/programs/{program}/appeals/{appeal_id}/resolve")
    async def appeal_resolution(program: str, appeal_id: str, request: Request):
        return await write(request, program, "resolve_appeal", appeal_id)

    return app
