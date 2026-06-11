"""把浏览器采集扩展(仓库根 extension/ 目录)即时打包成 zip 供前端下载。

不预生成二进制塞进 git(会过期),改为后端按需从源码目录现打包,永远和代码同步。
"""

from __future__ import annotations

import io
import zipfile
from pathlib import Path

from app.config import _BACKEND_DIR

# extension/ 在仓库根(backend/ 的上一级),用 config 锚定的目录回推,避免依赖启动目录
_EXT_DIR = _BACKEND_DIR.parent / "extension"

# 只打包扩展自身需要的文件类型,避免误收临时文件
_ALLOWED_SUFFIXES = {".json", ".js", ".css", ".html", ".md", ".png", ".svg"}


def _ext_dir() -> Path:
    return _EXT_DIR


def build_extension_zip() -> bytes:
    """把 extension/ 目录打包为 zip 字节;目录缺失时返回空 zip。"""
    root = _ext_dir()
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        if root.is_dir():
            for f in sorted(root.rglob("*")):
                if not f.is_file():
                    continue
                if f.suffix.lower() not in _ALLOWED_SUFFIXES:
                    continue
                if any(part.startswith(".") for part in f.relative_to(root).parts):
                    continue
                # 顶层放一个 pod-collector/ 目录,解压后直接「加载已解压」该目录
                arcname = Path("pod-collector") / f.relative_to(root)
                zf.write(f, arcname.as_posix())
    return buf.getvalue()
