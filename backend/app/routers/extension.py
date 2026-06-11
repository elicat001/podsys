"""采集浏览器扩展下载:GET /api/extension/download → 即时打包 extension/ 为 zip。

公开端点(非敏感产物,且 <a download> 无法带 Bearer);前端采集页给用户下载后,
解压 → chrome://extensions「加载已解压」即可。
"""

from __future__ import annotations

from fastapi import APIRouter, Response

from app.services.extension_pack import build_extension_zip

router = APIRouter(prefix="/api/extension", tags=["extension"])


@router.get("/download")
def download_extension() -> Response:
    data = build_extension_zip()
    return Response(
        content=data,
        media_type="application/zip",
        headers={
            "Content-Disposition": 'attachment; filename="pod-collector-extension.zip"',
            "Content-Length": str(len(data)),
        },
    )
