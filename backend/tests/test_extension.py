"""采集扩展下载端点测试。"""

import io
import zipfile


def test_extension_download_returns_zip(client):
    r = client.get("/api/extension/download")
    assert r.status_code == 200
    assert r.headers["content-type"] == "application/zip"
    assert "attachment" in r.headers.get("content-disposition", "")
    assert len(r.content) > 0


def test_extension_zip_contains_manifest(client):
    r = client.get("/api/extension/download")
    zf = zipfile.ZipFile(io.BytesIO(r.content))
    names = zf.namelist()
    # 顶层目录 pod-collector/,含 manifest.json 与核心脚本
    assert any(n.endswith("manifest.json") for n in names)
    assert any(n.endswith("content.js") for n in names)
    assert any(n.endswith("background.js") for n in names)
    assert all(n.startswith("pod-collector/") for n in names)


def test_extension_download_no_auth_required(client):
    # 公开端点:不带 token 也能下(<a download> 无法带 Bearer)
    r = client.get("/api/extension/download")
    assert r.status_code == 200
