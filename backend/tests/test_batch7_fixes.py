"""Batch 7 评审整改回归:P1-1 采集 urls 限长(防 DoS)。"""
from __future__ import annotations


def test_collect_tasks_url_count_capped(client, auth_headers):
    # 501 个 url 超过 max_length=500 → pydantic 校验 422
    urls = [f"https://x.com/{i}.jpg" for i in range(501)]
    r = client.post("/api/collect-tasks", headers=auth_headers, json={"urls": urls})
    assert r.status_code == 422, r.text


def test_collect_tasks_url_too_long_rejected(client, auth_headers):
    long_url = "https://x.com/" + "a" * 3000
    r = client.post("/api/collect-tasks", headers=auth_headers, json={"urls": [long_url]})
    assert r.status_code == 400, r.text


def test_collect_tasks_normal_still_ok(client, auth_headers):
    r = client.post("/api/collect-tasks", headers=auth_headers,
                    json={"urls": ["https://m.media-amazon.com/images/I/71x._AC_SX466_.jpg"]})
    assert r.status_code == 200 and r.json()["count"] == 1
