"""minio_client 测试：SigV4 黄金值锁定 + MockTransport 全链路 + 本地回落。

覆盖 Stage 10 任务 4「输出到 MinIO，返回带签名的下载 URL」：
- 预签名 GET URL 的签名/结构（黄金值防签名算法漂移）；
- PUT 走请求头签名，负载哈希上链；
- 上传/下载经 MockTransport 跑通，失败转 StorageError；
- 本地回落是真实文件后端（不是 mock_），并防目录穿越。

异步测试统一用 asyncio.run 包裹：版本表未锁定 pytest-asyncio（铁律 5），
与既有 tests/test_acceptance_stage9.py 的写法保持一致。
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from urllib.parse import parse_qs, urlparse

import httpx
import pytest

from app.clients.minio_client import (
    DOCX_CONTENT_TYPE,
    LocalStorageClient,
    MinioClient,
    MinioSettings,
    StorageError,
    artifact_object_key,
    build_storage,
    presigned_url,
    resolve_source_from_local,
    signed_put_headers,
    source_candidates,
    source_object_key,
)

_FIXED = datetime(2026, 10, 24, 13, 5, 0, tzinfo=UTC)
_KEY = "artifacts/contract-7f3a/v1/annotated.docx"


def _settings(**overrides: object) -> MinioSettings:
    base = {
        "endpoint": "localhost:9000",
        "access_key": "minioadmin",
        "secret_key": "minioadmin",
        "bucket": "clauseguard",
        "region": "us-east-1",
        "secure": False,
    }
    base.update(overrides)  # type: ignore[arg-type]
    return MinioSettings(**base)  # type: ignore[arg-type]


# ---- SigV4 纯函数：黄金值 + 结构 ----


def test_presigned_url_golden_signature() -> None:
    """固定时间戳/密钥下的签名必须逐字符稳定（算法漂移即回归）。"""
    url = presigned_url(
        base_url="http://localhost:9000",
        bucket="clauseguard",
        key=_KEY,
        region="us-east-1",
        access_key="minioadmin",
        secret_key="minioadmin",
        expiry_s=3600,
        now=_FIXED,
    )
    parsed = urlparse(url)
    query = parse_qs(parsed.query)
    assert parsed.path == "/clauseguard/artifacts/contract-7f3a/v1/annotated.docx"
    assert query["X-Amz-Algorithm"] == ["AWS4-HMAC-SHA256"]
    assert query["X-Amz-Credential"] == ["minioadmin/20261024/us-east-1/s3/aws4_request"]
    assert query["X-Amz-Date"] == ["20261024T130500Z"]
    assert query["X-Amz-Expires"] == ["3600"]
    assert query["X-Amz-SignedHeaders"] == ["host"]
    assert query["X-Amz-Signature"] == [
        "c96453f5cb23c00cbedbc78e05721f442232b17264ffd3778003db524c965da3"
    ]


def test_signed_put_headers_golden() -> None:
    """PUT 请求头签名黄金值：Authorization 与负载哈希都锁定。"""
    headers = signed_put_headers(
        base_url="http://localhost:9000",
        bucket="clauseguard",
        key=_KEY,
        region="us-east-1",
        access_key="minioadmin",
        secret_key="minioadmin",
        payload=b"hello-docx",
        now=_FIXED,
    )
    assert headers["x-amz-date"] == "20261024T130500Z"
    assert headers["x-amz-content-sha256"] == (
        "1b5975ef24a0992e5561942d873c034a72591085b44d26a08de5de0adebc82b5"
    )
    assert headers["Authorization"] == (
        "AWS4-HMAC-SHA256 Credential=minioadmin/20261024/us-east-1/s3/aws4_request, "
        "SignedHeaders=host;x-amz-content-sha256;x-amz-date, "
        "Signature=30146ac501c2ae5dd6ee5723aa3f81ba428c76c917c98d79595b78c0a69d3039"
    )


def test_key_encoding_uses_rfc3986_not_plus() -> None:
    """对象键含空格/中文时按 RFC3986 编码（%20 / UTF-8 百分号），S3 才认。"""
    url = presigned_url(
        base_url="http://localhost:9000",
        bucket="clauseguard",
        key="artifacts/合同 7f3a/v1/annotated.docx",
        region="us-east-1",
        access_key="ak",
        secret_key="sk",
        expiry_s=60,
        now=_FIXED,
    )
    parsed = urlparse(url)
    assert parsed.path == (
        "/clauseguard/artifacts/%E5%90%88%E5%90%8C%207f3a/v1/annotated.docx"
    )
    assert "+" not in parsed.path


def test_different_secret_produces_different_signature() -> None:
    """换密钥必须换签名（防「签名与密钥无关」的假实现）。"""
    common = {
        "base_url": "http://localhost:9000",
        "bucket": "clauseguard",
        "key": _KEY,
        "region": "us-east-1",
        "access_key": "ak",
        "expiry_s": 60,
        "now": _FIXED,
    }
    first = presigned_url(secret_key="sk-one", **common)  # type: ignore[arg-type]
    second = presigned_url(secret_key="sk-two", **common)  # type: ignore[arg-type]
    assert parse_qs(urlparse(first).query)["X-Amz-Signature"] != parse_qs(
        urlparse(second).query
    )["X-Amz-Signature"]


# ---- 客户端：MockTransport 全链路 ----


def _recording_transport(
    requests: list[httpx.Request], status: int = 200, body: bytes = b"stored"
) -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(status, content=body)

    return httpx.MockTransport(handler)


def test_upload_sends_signed_put_and_returns_presigned_url() -> None:
    """上传：PUT + 签名头 + 正确 Content-Type；返回带签名的下载 URL。"""

    async def _run() -> None:
        requests: list[httpx.Request] = []
        client = MinioClient(_settings(), transport=_recording_transport(requests))
        payload = b"docx-bytes-\xe4\xb8\xad\xe6\x96\x87"

        ref = await client.upload(_KEY, payload, DOCX_CONTENT_TYPE)

        assert ref.object_key == _KEY
        assert ref.size_bytes == len(payload)
        assert ref.download_url.startswith("http://localhost:9000/clauseguard/")
        assert "X-Amz-Signature=" in ref.download_url

        (request,) = requests
        assert request.method == "PUT"
        assert request.url.path == "/clauseguard/artifacts/contract-7f3a/v1/annotated.docx"
        assert request.headers["Content-Type"] == DOCX_CONTENT_TYPE
        assert request.headers["Authorization"].startswith("AWS4-HMAC-SHA256 ")
        assert await request.aread() == payload

    asyncio.run(_run())


def test_upload_failure_raises_storage_error() -> None:
    """存储 5xx 必须转 StorageError（上层转 run.failed，不静默吞）。"""

    async def _run() -> None:
        client = MinioClient(
            _settings(), transport=_recording_transport([], status=500, body=b"boom")
        )
        with pytest.raises(StorageError, match="HTTP 500"):
            await client.upload(_KEY, b"x", DOCX_CONTENT_TYPE)

    asyncio.run(_run())


def test_download_returns_object_bytes() -> None:
    """下载：GET 预签名 URL，原样返回对象字节。"""

    async def _run() -> None:
        requests: list[httpx.Request] = []
        payload = b"%PDF-1.4 fake"
        client = MinioClient(
            _settings(), transport=_recording_transport(requests, body=payload)
        )
        data = await client.download("contracts/c1/v1/source.pdf")
        assert data == payload
        (request,) = requests
        assert request.method == "GET"
        assert "X-Amz-Signature=" in str(request.url)

    asyncio.run(_run())


def test_minio_client_requires_endpoint() -> None:
    """未配置 endpoint 直接 StorageError（不发出无意义请求）。"""
    with pytest.raises(StorageError, match="endpoint"):
        MinioClient(_settings(endpoint=""))


# ---- 本地回落：真实文件后端 ----


def test_local_storage_roundtrip_and_uri(tmp_path) -> None:
    """本地后端：落盘 + 读回 + file:// URL（开发/测试的真实存储）。"""

    async def _run() -> None:
        client = LocalStorageClient(tmp_path)
        ref = await client.upload(
            "artifacts/c1/v1/annotated.docx", b"abc", DOCX_CONTENT_TYPE
        )
        assert ref.download_url.startswith("file://")
        assert ref.download_url.endswith("annotated.docx")
        assert await client.download(ref.object_key) == b"abc"
        assert (tmp_path / "artifacts/c1/v1/annotated.docx").read_bytes() == b"abc"

    asyncio.run(_run())


def test_local_storage_rejects_path_traversal(tmp_path) -> None:
    """对象键含 .. 必须拒绝（防目录穿越写穿 local_dir）。"""

    async def _run() -> None:
        client = LocalStorageClient(tmp_path)
        with pytest.raises(StorageError, match="非法对象键"):
            await client.upload("../escape.docx", b"x", DOCX_CONTENT_TYPE)
        with pytest.raises(StorageError, match="非法对象键"):
            await client.download("a/../../etc/passwd")

    asyncio.run(_run())


def test_local_storage_missing_object_raises(tmp_path) -> None:
    async def _run() -> None:
        client = LocalStorageClient(tmp_path)
        with pytest.raises(StorageError, match="不存在"):
            await client.download("artifacts/none/v1/annotated.docx")

    asyncio.run(_run())


# ---- 选择逻辑与键约定 ----


def test_build_storage_prefers_minio(tmp_path) -> None:
    """三者齐备走 MinIO；只给 local_dir 走本地；都没有返回 None。"""
    assert isinstance(build_storage(_settings()), MinioClient)
    local = build_storage(_settings(endpoint="", local_dir=str(tmp_path)))
    assert isinstance(local, LocalStorageClient)
    assert build_storage(_settings(endpoint="", local_dir="")) is None


def test_object_key_conventions() -> None:
    """源/产物键约定固定（Java 与 MinIO 控制台按此对账）。"""
    assert source_object_key("c-1", 3, "docx") == "contracts/c-1/v3/source.docx"
    assert artifact_object_key("c-1", 3) == "artifacts/c-1/v3/annotated.docx"


def test_resolve_source_from_local_convention(tmp_path) -> None:
    """本地源解析按 {local_dir}/contracts/{id}/v{n}/source.* 约定。"""
    directory = tmp_path / "contracts" / "c-9" / "v2"
    directory.mkdir(parents=True)
    (directory / "source.docx").write_bytes(b"PK\x03\x04fake")
    resolved = resolve_source_from_local(_settings(local_dir=str(tmp_path)), "c-9", 2)
    assert resolved is not None
    assert resolved.doc_type == "docx"
    assert resolved.key.endswith("source.docx")


def test_resolve_source_from_local_missing(tmp_path) -> None:
    assert resolve_source_from_local(_settings(local_dir=str(tmp_path)), "nope", 1) is None
    assert resolve_source_from_local(_settings(local_dir=""), "c-9", 2) is None


def test_source_candidates_order_new_before_legacy() -> None:
    """候选顺序：新约定键(docx→pdf) 优先于历史遗留键(docx→pdf)。"""
    cands = source_candidates("c-1", 3)
    assert cands == [
        "contracts/c-1/v3/source.docx",
        "contracts/c-1/v3/source.pdf",
        "c-1-v3.docx",
        "c-1-v3.pdf",
    ]


def test_resolve_source_from_local_falls_back_to_legacy(tmp_path, caplog) -> None:
    """新约定键缺失时回退历史遗留键 {id}-v{n}.{ext}，并打 WARN（不报错）。"""
    # 仅放置旧格式文件（模拟修复前的上传落点）。
    (tmp_path / "c-9-v2.docx").write_bytes(b"PK\x03\x04fake")
    resolved = resolve_source_from_local(_settings(local_dir=str(tmp_path)), "c-9", 2)
    assert resolved is not None
    assert resolved.doc_type == "docx"
    assert resolved.key.endswith("c-9-v2.docx")
    assert any("历史遗留键" in r.message for r in caplog.records)


def test_resolve_source_from_local_prefers_new_over_legacy(tmp_path) -> None:
    """新约定键与遗留键同时存在时，优先新约定键。"""
    (tmp_path / "contracts" / "c-9" / "v2").mkdir(parents=True)
    (tmp_path / "contracts" / "c-9" / "v2" / "source.docx").write_bytes(b"PK\x03\x04new")
    (tmp_path / "c-9-v2.docx").write_bytes(b"PK\x03\x04old")
    resolved = resolve_source_from_local(_settings(local_dir=str(tmp_path)), "c-9", 2)
    assert resolved is not None
    assert resolved.key.endswith("source.docx")  # 新键优先，而非遗留键

