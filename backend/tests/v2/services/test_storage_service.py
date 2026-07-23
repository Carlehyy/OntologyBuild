"""
StorageService 단위 테스트.
실제 MinIO 없이 unittest.mock으로 Minio 클라이언트를 모킹합니다.
"""
from __future__ import annotations

import io
from unittest.mock import MagicMock, patch, PropertyMock

import pytest
from minio.error import S3Error

from app.services.storage_service import StorageService, BUCKETS


@pytest.fixture
def mock_minio():
    with patch("app.services.storage_service.Minio") as MockMinio:
        instance = MockMinio.return_value
        instance.bucket_exists.return_value = True
        yield instance


@pytest.fixture
def storage(mock_minio, monkeypatch, tmp_path):
    monkeypatch.setattr(
        "app.shared.storage.settings.storage_local_dir", str(tmp_path))
    svc = StorageService(
        endpoint="localhost:9000",
        access_key="test",
        secret_key="test",
        secure=False,
    )
    return svc


# ── 1. 버킷 초기화 ────────────────────────────────────────────
def test_ensure_bucket_existing(storage, mock_minio):
    """이미 존재하는 버킷은 make_bucket을 호출하지 않는다."""
    mock_minio.bucket_exists.return_value = True
    storage.ensure_bucket("raw-datasets")
    mock_minio.make_bucket.assert_not_called()


def test_ensure_bucket_new(storage, mock_minio):
    """존재하지 않는 버킷은 make_bucket을 호출한다."""
    mock_minio.bucket_exists.return_value = False
    storage.ensure_bucket("new-bucket")
    mock_minio.make_bucket.assert_called_once_with("new-bucket")


def test_ensure_default_buckets_creates_4(storage, mock_minio):
    """4개 기본 버킷을 초기화할 때 각 버킷 존재 여부를 확인한다."""
    mock_minio.bucket_exists.return_value = True
    storage.ensure_default_buckets()
    assert mock_minio.bucket_exists.call_count == len(BUCKETS)


# ── 2. 업로드 ─────────────────────────────────────────────────
def test_put_object_returns_s3_uri(storage, mock_minio):
    """put_object는 s3://bucket/key 형태의 URI를 반환한다."""
    data = io.BytesIO(b"hello world")
    uri = storage.put_object("raw-datasets", "test/data.csv", data, "text/csv", 11)
    assert uri == "s3://raw-datasets/test/data.csv"
    mock_minio.put_object.assert_called_once()


def test_put_bytes_returns_s3_uri(storage, mock_minio):
    """put_bytes는 bytes를 받아 s3:// URI를 반환한다."""
    uri = storage.put_bytes("media", "file.pdf", b"%PDF", "application/pdf")
    assert uri == "s3://media/file.pdf"


# ── 3. 다운로드 ───────────────────────────────────────────────
def test_get_object_returns_bytes(storage, mock_minio):
    """get_object는 오브젝트 내용을 bytes로 반환한다."""
    mock_resp = MagicMock()
    mock_resp.read.return_value = b"content"
    mock_minio.get_object.return_value = mock_resp
    result = storage.get_object("s3://raw-datasets/test.csv")
    assert result == b"content"


# ── 4. URI 파싱 ───────────────────────────────────────────────
def test_parse_uri_valid():
    bucket, key = StorageService._parse_uri("s3://my-bucket/path/to/file.csv")
    assert bucket == "my-bucket"
    assert key == "path/to/file.csv"


def test_parse_local_uri_valid():
    bucket, key = StorageService._parse_uri("local://my-bucket/path/to/file.csv")
    assert bucket == "my-bucket"
    assert key == "path/to/file.csv"


def test_parse_uri_invalid_scheme():
    with pytest.raises(ValueError, match="Invalid storage URI"):
        StorageService._parse_uri("http://bucket/key")


def test_parse_uri_missing_key():
    with pytest.raises(ValueError, match="Invalid storage URI"):
        StorageService._parse_uri("s3://bucket/")


@pytest.mark.parametrize(
    "uri",
    [
        "local://media/../raw-datasets/x",
        r"local://media/folder\file.txt",
        "s3://media/./file.txt",
    ],
)
def test_parse_uri_rejects_unsafe_object_keys(uri):
    with pytest.raises(ValueError, match="Invalid storage object key"):
        StorageService._parse_uri(uri)


# ── 5. 삭제 ──────────────────────────────────────────────────
def test_delete_object_calls_remove(storage, mock_minio):
    storage.delete_object("s3://raw-datasets/old.csv")
    mock_minio.remove_object.assert_called_once_with("raw-datasets", "old.csv")


# ── 6. 목록 ──────────────────────────────────────────────────
def test_list_prefix_returns_uris(storage, mock_minio):
    obj1 = MagicMock()
    obj1.object_name = "prefix/file1.csv"
    obj2 = MagicMock()
    obj2.object_name = "prefix/file2.csv"
    mock_minio.list_objects.return_value = [obj1, obj2]
    uris = storage.list_prefix("raw-datasets", "prefix/")
    assert uris == ["s3://raw-datasets/prefix/file1.csv", "s3://raw-datasets/prefix/file2.csv"]


# ── 7. Presigned URL ─────────────────────────────────────────
def test_presigned_get_returns_url(storage, mock_minio):
    mock_minio.presigned_get_object.return_value = "http://minio/bucket/key?sig=xxx"
    url = storage.presigned_get("s3://media/report.pdf", 1800)
    assert url.startswith("http://")
    mock_minio.presigned_get_object.assert_called_once()


# ── 8. 바이너리 roundtrip ─────────────────────────────────────
def test_binary_roundtrip(storage, mock_minio):
    """put_bytes 후 get_object가 동일 bytes를 반환하는 흐름 검증"""
    content = b"\x89PNG\r\n\x1a\n"  # PNG magic bytes
    mock_resp = MagicMock()
    mock_resp.read.return_value = content
    mock_minio.get_object.return_value = mock_resp

    uri = storage.put_bytes("media", "img.png", content, "image/png")
    retrieved = storage.get_object(uri)
    assert retrieved == content


def _local_only_storage(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "app.shared.storage.settings.storage_local_dir", str(tmp_path))
    svc = StorageService.__new__(StorageService)
    svc._available = False
    svc._client = None
    svc._allow_local_fallback = True
    svc._connection_options = None
    svc._is_default = False
    svc._next_reconnect_at = float("inf")
    return svc


def test_local_fallback_returns_explicit_uri_and_roundtrips(tmp_path, monkeypatch):
    storage = _local_only_storage(tmp_path, monkeypatch)

    uri = storage.put_bytes("media", "pipeline/report.pdf", b"%PDF")

    assert uri == "local://media/pipeline/report.pdf"
    assert storage.get_object(uri) == b"%PDF"
    assert storage.object_exists(uri) is True
    assert storage.list_prefix("media", "pipeline/") == [uri]


def test_cross_bucket_local_key_is_rejected_without_touching_target(
    tmp_path, monkeypatch,
):
    storage = _local_only_storage(tmp_path, monkeypatch)
    target = tmp_path / "raw-datasets" / "x"
    target.parent.mkdir(parents=True)
    target.write_bytes(b"sentinel")
    unsafe_uri = "local://media/../raw-datasets/x"

    operations = [
        lambda: storage.put_bytes("media", "../raw-datasets/x", b"overwrite"),
        lambda: storage.get_object(unsafe_uri),
        lambda: storage.get_stream(unsafe_uri),
        lambda: storage.delete_object(unsafe_uri),
        lambda: storage.object_exists(unsafe_uri),
    ]
    for operation in operations:
        with pytest.raises(ValueError, match="Invalid storage object key"):
            operation()

    assert target.read_bytes() == b"sentinel"
    assert not (tmp_path / "media").exists()


def test_symlinked_bucket_cannot_escape_local_storage_root(tmp_path, monkeypatch):
    base = tmp_path / "storage"
    outside = tmp_path / "outside"
    base.mkdir()
    outside.mkdir()
    (base / "media").symlink_to(outside, target_is_directory=True)
    storage = _local_only_storage(base, monkeypatch)

    operations = [
        lambda: storage.ensure_bucket("media"),
        lambda: storage.put_bytes("media", "escaped.txt", b"must stay inside"),
        lambda: storage.list_prefix("media", ""),
    ]
    for operation in operations:
        with pytest.raises(ValueError, match="escapes bucket root"):
            operation()

    assert not (outside / "escaped.txt").exists()


def test_local_uri_stays_local_after_minio_recovers(
    tmp_path, monkeypatch,
):
    storage = _local_only_storage(tmp_path, monkeypatch)
    uri = storage.put_bytes("media", "attachment.txt", b"local copy")
    recovered_minio = MagicMock()
    storage._client = recovered_minio
    storage._available = True

    assert storage.get_object(uri) == b"local copy"
    stream = storage.get_stream(uri)
    try:
        assert stream.read() == b"local copy"
    finally:
        stream.close()
    assert storage.list_prefix("media", "") == [uri]
    recovered_minio.get_object.assert_not_called()
    recovered_minio.stat_object.assert_not_called()


def test_historical_s3_label_can_read_local_object_after_recovery(
    tmp_path, monkeypatch,
):
    storage = _local_only_storage(tmp_path, monkeypatch)
    local_uri = storage.put_bytes("media", "legacy.txt", b"legacy local")
    recovered_minio = MagicMock()
    recovered_minio.get_object.side_effect = RuntimeError("not in MinIO")
    recovered_minio.stat_object.side_effect = RuntimeError("not in MinIO")
    storage._client = recovered_minio
    storage._available = True

    historical_uri = local_uri.replace("local://", "s3://", 1)
    assert storage.get_object(historical_uri) == b"legacy local"
    assert storage.object_exists(historical_uri) is True


def test_reconnect_switches_new_writes_back_to_minio(tmp_path, monkeypatch):
    storage = _local_only_storage(tmp_path, monkeypatch)
    storage._connection_options = {
        "endpoint": "minio:9000",
        "access_key": "test",
        "secret_key": "test",
        "secure": False,
        "region": None,
    }
    storage._next_reconnect_at = 0.0
    recovered_minio = MagicMock()
    recovered_minio.bucket_exists.return_value = True

    with patch("app.shared.storage.Minio", return_value=recovered_minio):
        uri = storage.put_bytes("media", "after-recovery.txt", b"MinIO")

    assert uri == "s3://media/after-recovery.txt"
    recovered_minio.list_buckets.assert_called_once()
    recovered_minio.put_object.assert_called_once()


def test_seekable_stream_rewinds_when_minio_upload_fails(
    storage, mock_minio, tmp_path,
):
    def consume_then_fail(_bucket, _key, source, **_kwargs):
        assert source.read() == b"seekable"
        raise RuntimeError("MinIO outage")

    mock_minio.put_object.side_effect = consume_then_fail

    uri = storage.put_object(
        "media", "fallback/seekable.bin", io.BytesIO(b"seekable"), length=8)

    assert uri == "local://media/fallback/seekable.bin"
    assert (tmp_path / "media" / "fallback" / "seekable.bin").read_bytes() == b"seekable"


class _NonSeekableStream:
    def __init__(self, content: bytes):
        self._buffer = io.BytesIO(content)

    def read(self, size: int = -1) -> bytes:
        return self._buffer.read(size)

    def tell(self):
        raise OSError("not seekable")

    def seek(self, *_args):
        raise OSError("not seekable")


def test_non_seekable_stream_is_staged_for_successful_minio_upload(
    storage, mock_minio,
):
    uploaded = []

    def consume(_bucket, _key, source, **_kwargs):
        uploaded.append(source.read())

    mock_minio.put_object.side_effect = consume
    uri = storage.put_object(
        "media", "stream/success.bin", _NonSeekableStream(b"streamed"), length=8)

    assert uri == "s3://media/stream/success.bin"
    assert uploaded == [b"streamed"]


def test_non_seekable_stream_is_staged_for_failed_minio_upload(
    storage, mock_minio, tmp_path,
):
    def consume_then_fail(_bucket, _key, source, **_kwargs):
        assert source.read() == b"streamed"
        raise RuntimeError("MinIO outage")

    mock_minio.put_object.side_effect = consume_then_fail
    uri = storage.put_object(
        "media", "stream/fallback.bin", _NonSeekableStream(b"streamed"), length=8)

    assert uri == "local://media/stream/fallback.bin"
    assert (tmp_path / "media" / "stream" / "fallback.bin").read_bytes() == b"streamed"


@pytest.mark.parametrize(
    "operation",
    ["ensure", "put", "get", "get_stream", "list", "exists"],
)
def test_runtime_minio_failure_opens_circuit_breaker(
    operation, storage, mock_minio,
):
    failure = RuntimeError(f"{operation} connection timeout")

    if operation == "ensure":
        mock_minio.bucket_exists.side_effect = failure
        storage.ensure_bucket("media")
    elif operation == "put":
        mock_minio.put_object.side_effect = failure
        assert storage.put_bytes("media", "breaker/put.bin", b"data").startswith(
            "local://")
    elif operation == "get":
        mock_minio.get_object.side_effect = failure
        with pytest.raises(RuntimeError, match="connection timeout"):
            storage.get_object("s3://media/breaker/get.bin")
    elif operation == "get_stream":
        mock_minio.get_object.side_effect = failure
        with pytest.raises(RuntimeError, match="connection timeout"):
            storage.get_stream("s3://media/breaker/stream.bin")
    elif operation == "list":
        mock_minio.list_objects.side_effect = failure
        assert storage.list_prefix("media", "breaker/") == []
    else:
        mock_minio.stat_object.side_effect = failure
        assert storage.object_exists("s3://media/breaker/exists.bin") is False

    assert storage._available is False
    assert storage._client is None
    assert storage._next_reconnect_at > 0


def test_runtime_failure_clears_shared_client_and_throttles_followup(
    storage, mock_minio, monkeypatch,
):
    monkeypatch.setattr(storage, "_is_default", True)
    monkeypatch.setattr(StorageService, "_shared_client", mock_minio)
    monkeypatch.setattr(StorageService, "_shared_unavailable_until", 0.0)
    mock_minio.put_object.side_effect = RuntimeError("service unavailable")

    with patch("app.shared.storage.Minio") as reconnect:
        first = storage.put_bytes("media", "breaker/first.bin", b"first")
        second = storage.put_bytes("media", "breaker/second.bin", b"second")

    assert first.startswith("local://")
    assert second.startswith("local://")
    assert mock_minio.put_object.call_count == 1
    reconnect.assert_not_called()
    assert StorageService._shared_client is None
    assert StorageService._shared_unavailable_until == storage._next_reconnect_at


def test_failed_reconnect_probe_runs_once_per_backoff_window(
    tmp_path, monkeypatch,
):
    storage = _local_only_storage(tmp_path, monkeypatch)
    storage._connection_options = {
        "endpoint": "minio:9000",
        "access_key": "test",
        "secret_key": "test",
        "secure": False,
        "region": None,
    }
    storage._next_reconnect_at = 0.0
    unavailable_client = MagicMock()
    unavailable_client.list_buckets.side_effect = RuntimeError("still down")

    with patch(
        "app.shared.storage.Minio",
        return_value=unavailable_client,
    ) as reconnect:
        first = storage.put_bytes("media", "retry/first.bin", b"first")
        second = storage.put_bytes("media", "retry/second.bin", b"second")

    assert first.startswith("local://")
    assert second.startswith("local://")
    reconnect.assert_called_once()
    unavailable_client.list_buckets.assert_called_once()


def test_runtime_breaker_recovers_and_new_writes_return_to_minio(
    storage, mock_minio,
):
    mock_minio.put_object.side_effect = RuntimeError("temporary outage")
    assert storage.put_bytes("media", "recover/local.bin", b"local").startswith(
        "local://")

    recovered_minio = MagicMock()
    recovered_minio.bucket_exists.return_value = True
    storage._next_reconnect_at = 0.0
    with patch(
        "app.shared.storage.Minio",
        return_value=recovered_minio,
    ) as reconnect:
        uri = storage.put_bytes("media", "recover/minio.bin", b"minio")

    assert uri == "s3://media/recover/minio.bin"
    reconnect.assert_called_once()
    recovered_minio.list_buckets.assert_called_once()
    recovered_minio.put_object.assert_called_once()


def _not_found_error(code: str = "NoSuchKey") -> S3Error:
    return S3Error(
        code,
        "not found",
        "/media/missing.bin",
        "request-id",
        "host-id",
        MagicMock(),
    )


def test_explicit_s3_not_found_does_not_open_circuit_breaker(
    storage, mock_minio,
):
    response = MagicMock()
    response.read.return_value = b"recovered"
    mock_minio.get_object.side_effect = [_not_found_error(), response]
    original_client = storage._client

    with pytest.raises(S3Error):
        storage.get_object("s3://media/missing.bin")

    assert storage._available is True
    assert storage._client is original_client
    assert storage._next_reconnect_at == 0.0
    assert storage.get_object("s3://media/available.bin") == b"recovered"
    assert mock_minio.get_object.call_count == 2
