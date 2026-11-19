import os
import tempfile

_TMP = tempfile.mkdtemp(prefix="media-platform-tests-")
os.environ["DATA_DIR"] = _TMP
os.environ["SECRET_KEY"] = "test-secret-key"
os.environ["TURNSTILE_ENABLED"] = "false"

import pytest  # noqa: E402
from sqlmodel import Session, delete  # noqa: E402

from backend.db import engine, init_db  # noqa: E402
from backend.models import (  # noqa: E402
    AgentDevice,
    AgentPair,
    AuthSession,
    DesktopJob,
    Job,
    User,
)
from backend.ratelimit import SlidingWindowLimiter  # noqa: E402


def _wipe() -> None:
    with Session(engine) as session:
        session.exec(delete(DesktopJob))
        session.exec(delete(Job))
        session.exec(delete(AgentPair))
        session.exec(delete(AgentDevice))
        session.exec(delete(AuthSession))
        session.exec(delete(User))
        session.commit()


def _reset_limiters() -> None:
    from backend.api import agent as agent_api
    from backend.api import auth as auth_api
    from backend.api import jobs as jobs_api
    from backend.config import settings

    jobs_api.anon_limiter = SlidingWindowLimiter(settings.anon_jobs_per_ip_hour, 3600)
    jobs_api.user_limiter = SlidingWindowLimiter(
        max(settings.pro_jobs_per_user_hour, settings.free_jobs_per_user_hour), 3600
    )
    auth_api.register_limiter = SlidingWindowLimiter(10, 900)
    auth_api.login_limiter = SlidingWindowLimiter(20, 900)
    agent_api.pair_limiter = SlidingWindowLimiter(10, 3600)
    agent_api.claim_limiter = SlidingWindowLimiter(30, 900)


@pytest.fixture(autouse=True)
def clean_db():
    init_db()
    _wipe()
    _reset_limiters()
    yield
    _wipe()


@pytest.fixture
def client():
    from fastapi.testclient import TestClient

    from backend.api.main import app

    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture
def register(client):
    def _register(email: str = "user@example.com", password: str = "password123") -> dict:
        response = client.post("/v1/auth/register", json={"email": email, "password": password})
        assert response.status_code == 201, response.text
        return response.json()

    return _register
