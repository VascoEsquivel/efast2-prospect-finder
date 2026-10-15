import pytest
import requests

import data_fetcher


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    """Tests never touch the network: any request fails like a dead API would."""
    def blocked(*args, **kwargs):
        raise requests.ConnectionError("network disabled in tests")
    monkeypatch.setattr(requests, "get", blocked)
    monkeypatch.setattr(requests.Session, "request", blocked)


@pytest.fixture
def tmp_data(tmp_path, monkeypatch):
    """Point raw/cache/upload dirs at a temp folder."""
    monkeypatch.setattr(data_fetcher, "RAW_DIR", tmp_path / "raw")
    monkeypatch.setattr(data_fetcher, "CACHE_DIR", tmp_path / "cache")
    monkeypatch.setattr(data_fetcher, "UPLOAD_DIR", tmp_path / "raw" / "uploads")
    return tmp_path
