"""测试用的内存假对象存储 client —— 替代真实 boto3/MinIO,保持离线确定性。

只实现 `app.storage` 真正用到的 4 个方法(upload_file/download_file/head_object/delete_object),
方法签名与 boto3 S3 client 对齐。比引入 moto 重依赖轻得多。用法见 conftest 的 `s3_backend` fixture。
"""
from __future__ import annotations

from pathlib import Path


class FakeS3NotFound(Exception):
    """模拟对象不存在(boto3 真实会抛 botocore ClientError;storage 层一律 except 兜底,类型无所谓)。"""


class FakeS3Client:
    """单桶内存模拟:store[key] = bytes。"""

    def __init__(self) -> None:
        self.store: dict[str, bytes] = {}

    # boto3: upload_file(Filename, Bucket, Key) —— storage 层按位置传 (filename, bucket, key)
    def upload_file(self, filename: str, bucket: str, key: str) -> None:
        self.store[key] = Path(filename).read_bytes()

    # boto3: download_file(Bucket, Key, Filename) —— storage 层按位置传 (bucket, key, filename)
    def download_file(self, bucket: str, key: str, filename: str) -> None:
        if key not in self.store:
            raise FakeS3NotFound(key)
        Path(filename).write_bytes(self.store[key])

    def head_object(self, Bucket: str, Key: str) -> dict:  # noqa: N803 — 对齐 boto3 关键字参数名
        if Key not in self.store:
            raise FakeS3NotFound(Key)
        return {"ContentLength": len(self.store[Key])}

    def delete_object(self, Bucket: str, Key: str) -> dict:  # noqa: N803
        self.store.pop(Key, None)
        return {}
