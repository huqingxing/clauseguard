"""MinIO 客户端：S3 SigV4 签名 + 预签名 URL，跑在 httpx 上。

为什么不引 minio SDK：版本表锁定的 Python 依赖里没有它（铁律 5：不引入版本表
外依赖），而 MinIO 完整支持 S3 SigV4——用已锁定的 httpx 手写签名即可，签名
逻辑集中在少数纯函数里，可单测（见 tests/render/test_minio_client.py）。

为什么 Settings 放本模块而非 app/config.py：存储连接只服务 Stage 10 的回写
产物存取；本阶段不改动服务级配置文件。环境变量前缀 clauseguard_minio_ 已锁定，
后续若统一收纳进 config.py，迁移只动构造处，调用方不变。

本地回落（LocalStorageClient）：未配置 MinIO 时（本地开发/测试）把产物写到
local_dir 并返回 file:// URL——这是一个真实的本地存储后端，不是伪装 MinIO 的
mock；生产环境一律走 MinioClient 的预签名 URL。
TODO(Stage 11+): MinIO 成为唯一存储后端后移除 LocalStorageClient。
"""

from __future__ import annotations

import hashlib
import hmac
import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Protocol
from urllib.parse import quote

import httpx
from pydantic_settings import BaseSettings, SettingsConfigDict

logger = logging.getLogger("app.clients.minio")

DOCX_CONTENT_TYPE = (
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
)
_ALGORITHM = "AWS4-HMAC-SHA256"
_SERVICE = "s3"
_UNSIGNED_PAYLOAD = "UNSIGNED-PAYLOAD"


class StorageError(Exception):
    """存储操作失败（网络/4xx/5xx）；上层据此转 run.failed，不静默吞掉。"""


class MinioSettings(BaseSettings):
    """MinIO/对象存储连接；环境变量前缀 CLAUSEGUARD_MINIO_（如 CLAUSEGUARD_MINIO_ENDPOINT）。"""

    model_config = SettingsConfigDict(env_prefix="clauseguard_minio_", extra="ignore")

    # 形如 "minio:9000" 或 "localhost:9000"；为空表示未配置 MinIO。
    endpoint: str = ""
    access_key: str = ""
    secret_key: str = ""
    bucket: str = "clauseguard"
    region: str = "us-east-1"
    secure: bool = False
    # 预签名下载 URL 的有效期（秒）。
    presign_expiry_s: int = 3600
    # 未配置 MinIO 时的本地落盘根目录（开发/测试）。
    local_dir: str = ""

    @property
    def base_url(self) -> str:
        scheme = "https" if self.secure else "http"
        return f"{scheme}://{self.endpoint}"


@dataclass(frozen=True)
class ArtifactRef:
    """一次上传的产物引用：对象键 + 可下载 URL + 字节数。"""

    object_key: str
    download_url: str
    size_bytes: int


class StorageClient(Protocol):
    """存储端口：render 节点只依赖这两个方法，不关心后端是 MinIO 还是本地。"""

    async def download(self, key: str) -> bytes: ...

    async def upload(self, key: str, data: bytes, content_type: str) -> ArtifactRef: ...


def source_object_key(contract_id: str, version: int, ext: str) -> str:
    """源合同对象键约定：contracts/{contract_id}/v{version}/source.{ext}。"""
    return f"contracts/{contract_id}/v{version}/source.{ext}"


def artifact_object_key(contract_id: str, version: int) -> str:
    """回写产物对象键约定：artifacts/{contract_id}/v{version}/annotated.docx。"""
    return f"artifacts/{contract_id}/v{version}/annotated.docx"


# ---------------------------------------------------------------------------
# SigV4 签名（S3 兼容；纯函数，可离线单测）
# ---------------------------------------------------------------------------


def _sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _hmac(key: bytes, message: str) -> bytes:
    return hmac.new(key, message.encode("utf-8"), hashlib.sha256).digest()


def _uri_encode(value: str, *, encode_slash: bool) -> str:
    """RFC3986 编码：未保留字符（字母/数字/-_.~）原样，空格 -> %20（不是 +）。"""
    return quote(value, safe="" if encode_slash else "/")


def _signing_key(secret: str, date_stamp: str, region: str) -> bytes:
    key = _hmac(f"AWS4{secret}".encode(), date_stamp)
    key = _hmac(key, region)
    key = _hmac(key, _SERVICE)
    return _hmac(key, "aws4_request")


def _credential_scope(amz_date: str, region: str) -> str:
    return f"{amz_date[:8]}/{region}/{_SERVICE}/aws4_request"


def _canonical_uri(bucket: str, key: str) -> str:
    """S3 canonical URI：bucket 段连字符也编码，key 段保留 / 分隔。"""
    encoded_bucket = _uri_encode(bucket, encode_slash=True)
    encoded_key = _uri_encode(key, encode_slash=False)
    return f"/{encoded_bucket}/{encoded_key}"


def presigned_url(
    *,
    base_url: str,
    bucket: str,
    key: str,
    region: str,
    access_key: str,
    secret_key: str,
    expiry_s: int,
    now: datetime | None = None,
) -> str:
    """生成 GET 预签名 URL（query 参数承载签名，负载 UNSIGNED-PAYLOAD）。"""
    moment = now or datetime.now(UTC)
    amz_date = moment.strftime("%Y%m%dT%H%M%SZ")
    host = base_url.split("//", 1)[1]
    canonical_uri = _canonical_uri(bucket, key)
    query = {
        "X-Amz-Algorithm": _ALGORITHM,
        "X-Amz-Credential": (
            f"{_uri_encode(access_key, encode_slash=True)}"
            f"/{_credential_scope(amz_date, region)}"
        ),
        "X-Amz-Date": amz_date,
        "X-Amz-Expires": str(expiry_s),
        "X-Amz-SignedHeaders": "host",
    }
    canonical_query = "&".join(
        f"{name}={_uri_encode(value, encode_slash=True)}" for name, value in sorted(query.items())
    )
    canonical_request = "\n".join(
        [
            "GET",
            canonical_uri,
            canonical_query,
            f"host:{host}\n",
            "host",
            _UNSIGNED_PAYLOAD,
        ]
    )
    string_to_sign = "\n".join(
        [
            _ALGORITHM,
            amz_date,
            _credential_scope(amz_date, region),
            _sha256_hex(canonical_request.encode("utf-8")),
        ]
    )
    signature = _hmac(
        _signing_key(secret_key, amz_date[:8], region), string_to_sign
    ).hex()
    return f"{base_url}{canonical_uri}?{canonical_query}&X-Amz-Signature={signature}"


def signed_put_headers(
    *,
    base_url: str,
    bucket: str,
    key: str,
    region: str,
    access_key: str,
    secret_key: str,
    payload: bytes,
    now: datetime | None = None,
) -> dict[str, str]:
    """PUT 的 SigV4 请求头（Authorization + x-amz-date + x-amz-content-sha256）。

    写操作走请求头签名（负载哈希上链），读操作走预签名 URL——写入需鉴权、
    读取限时放行，两者都是 S3 标准实践。
    """
    moment = now or datetime.now(UTC)
    amz_date = moment.strftime("%Y%m%dT%H%M%SZ")
    host = base_url.split("//", 1)[1]
    payload_hash = _sha256_hex(payload)
    canonical_uri = _canonical_uri(bucket, key)
    canonical_request = "\n".join(
        [
            "PUT",
            canonical_uri,
            "",
            f"host:{host}\nx-amz-content-sha256:{payload_hash}\nx-amz-date:{amz_date}\n",
            "host;x-amz-content-sha256;x-amz-date",
            payload_hash,
        ]
    )
    string_to_sign = "\n".join(
        [
            _ALGORITHM,
            amz_date,
            _credential_scope(amz_date, region),
            _sha256_hex(canonical_request.encode("utf-8")),
        ]
    )
    signature = _hmac(
        _signing_key(secret_key, amz_date[:8], region), string_to_sign
    ).hex()
    authorization = (
        f"{_ALGORITHM} "
        f"Credential={access_key}/{_credential_scope(amz_date, region)}, "
        f"SignedHeaders=host;x-amz-content-sha256;x-amz-date, "
        f"Signature={signature}"
    )
    return {
        "Authorization": authorization,
        "x-amz-date": amz_date,
        "x-amz-content-sha256": payload_hash,
    }


# ---------------------------------------------------------------------------
# 客户端实现
# ---------------------------------------------------------------------------


class MinioClient:
    """S3 兼容对象存储客户端（MinIO）；transport 可注入（测试用 MockTransport）。"""

    def __init__(
        self,
        settings: MinioSettings,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
        timeout_s: float = 30.0,
    ) -> None:
        if not settings.endpoint:
            raise StorageError("MinIO endpoint 未配置")
        self._settings = settings
        self._transport = transport
        self._timeout_s = timeout_s

    def _client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(transport=self._transport, timeout=self._timeout_s)

    async def presigned_get(self, key: str, *, expiry_s: int | None = None) -> str:
        return presigned_url(
            base_url=self._settings.base_url,
            bucket=self._settings.bucket,
            key=key,
            region=self._settings.region,
            access_key=self._settings.access_key,
            secret_key=self._settings.secret_key,
            expiry_s=expiry_s or self._settings.presign_expiry_s,
        )

    async def upload(self, key: str, data: bytes, content_type: str) -> ArtifactRef:
        url = (
            f"{self._settings.base_url}/{self._settings.bucket}"
            f"/{_uri_encode(key, encode_slash=False)}"
        )
        headers = signed_put_headers(
            base_url=self._settings.base_url,
            bucket=self._settings.bucket,
            key=key,
            region=self._settings.region,
            access_key=self._settings.access_key,
            secret_key=self._settings.secret_key,
            payload=data,
        )
        headers["Content-Type"] = content_type
        async with self._client() as client:
            response = await client.put(url, content=data, headers=headers)
        if response.status_code >= 300:
            raise StorageError(
                f"上传 {key} 失败: HTTP {response.status_code} {response.text[:200]}"
            )
        logger.info("artifact uploaded: %s (%d bytes)", key, len(data))
        return ArtifactRef(
            object_key=key,
            download_url=await self.presigned_get(key),
            size_bytes=len(data),
        )

    async def download(self, key: str) -> bytes:
        url = await self.presigned_get(key)
        async with self._client() as client:
            response = await client.get(url)
        if response.status_code >= 300:
            raise StorageError(
                f"下载 {key} 失败: HTTP {response.status_code} {response.text[:200]}"
            )
        return response.content


class LocalStorageClient:
    """本地落盘存储后端（开发/测试）：写入 local_dir 并返回 file:// URL。

    TODO(Stage 11+): MinIO 成为唯一存储后端后移除本类。
    """

    def __init__(self, root: str | Path) -> None:
        self._root = Path(root)

    def _path(self, key: str) -> Path:
        # 对象键是受控约定字符串（contracts/...、artifacts/...），仍防目录穿越。
        safe = key.replace("\\", "/").lstrip("/")
        if ".." in safe.split("/"):
            raise StorageError(f"非法对象键: {key}")
        return self._root / safe

    async def upload(self, key: str, data: bytes, content_type: str) -> ArtifactRef:
        path = self._path(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        return ArtifactRef(
            object_key=key, download_url=path.as_uri(), size_bytes=len(data)
        )

    async def download(self, key: str) -> bytes:
        path = self._path(key)
        if not path.is_file():
            raise StorageError(f"本地对象不存在: {path}")
        return path.read_bytes()


def build_storage(settings: MinioSettings) -> StorageClient | None:
    """按配置选择存储后端：MinIO 优先，其次本地目录，都未配置返回 None。"""
    if settings.endpoint and settings.access_key and settings.secret_key:
        return MinioClient(settings)
    if settings.local_dir:
        return LocalStorageClient(settings.local_dir)
    return None


def build_source_client(settings: MinioSettings) -> MinioClient | None:
    """源合同读取只走 MinIO（本地源走文件系统解析，不走存储端口）。"""
    if settings.endpoint and settings.access_key and settings.secret_key:
        return MinioClient(settings)
    return None


def is_docx(data: bytes) -> bool:
    """按 zip 魔数判定是否 docx（PDF 回写属后续阶段，见 render service TODO）。"""
    return data[:4] == b"PK\x03\x04"


def source_candidates(contract_id: str, version: int) -> list[str]:
    """源对象键候选顺序：先 docx 后 pdf（本阶段只回写 docx）。"""
    return [
        source_object_key(contract_id, version, "docx"),
        source_object_key(contract_id, version, "pdf"),
    ]


@dataclass(frozen=True)
class ResolvedSource:
    """解析到的源合同字节 + 对象键 + 类型。"""

    data: bytes
    key: str
    doc_type: str


async def resolve_source_from_minio(
    settings: MinioSettings, contract_id: str, version: int
) -> ResolvedSource | None:
    """按候选键顺序从 MinIO 拉取源合同；全部 404 返回 None。"""
    client = build_source_client(settings)
    if client is None:
        return None
    for key in source_candidates(contract_id, version):
        try:
            data = await client.download(key)
        except StorageError as exc:
            logger.info("source candidate %s unavailable: %s", key, exc)
            continue
        doc_type = "docx" if is_docx(data) else "pdf"
        return ResolvedSource(data=data, key=key, doc_type=doc_type)
    return None


def resolve_source_from_local(
    settings: MinioSettings, contract_id: str, version: int
) -> ResolvedSource | None:
    """本地开发：从 local_dir 下的约定目录读源合同（glob source.*）。"""
    if not settings.local_dir:
        return None
    directory = Path(settings.local_dir) / "contracts" / contract_id / f"v{version}"
    if not directory.is_dir():
        return None
    for candidate in sorted(directory.glob("source.*")):
        data = candidate.read_bytes()
        doc_type = "docx" if is_docx(data) else candidate.suffix.lstrip(".").lower()
        return ResolvedSource(data=data, key=str(candidate), doc_type=doc_type)
    return None
