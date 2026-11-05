import pytest
from app.main import create_app
from fastapi.testclient import TestClient


@pytest.fixture
def client(tmp_path):
    with TestClient(create_app(f"sqlite:///{tmp_path}/test.db")) as test_client:
        yield test_client
