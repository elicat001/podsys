# Batch 7: 以图搜图 / 转矢量 / 采集任务 / 店铺管理 Implementation Plan

**Goal:** 补齐对标灵图的剩余缺口(找图-以图搜图、作图-转矢量、找图-采集任务列表、上架-店铺管理),达到 v0.6。全部离线可验证。

**Architecture:** 4 块独立。新增 DB 表放各自独立模块(`models_collect.py`/`models_shop.py`),import 即注册到 `Base.metadata`;Tech Lead 在 `db.init_db()` 里 import 这些模块 + main.py 注册路由。其余复用既有 phash/collectors/publish/billing/auth/storage。

**Tech Stack:** FastAPI、SQLAlchemy 2.0、Pillow、pytest。

**复用约定(必读):**
- 鉴权 `from app.auth import current_user`;DB `from app.db import get_db`;计费 `from app.services.billing import charge_for, refund`;存储 `from app import storage`。
- 读图退点用 `from app.web_utils import read_image_or_refund`(单次预扣端点)。
- 测试用 conftest 的 `client`/`auth_headers` fixtures(**勿改 conftest**)。
- venv `backend/.venv/Scripts/python.exe`;`python -m pytest tests/<your>.py -q`。
- **禁止**改:main.py / db.py / models_db.py / conftest.py / requirements.txt / 他人文件;**禁止**启 uvicorn、删 data/podstudio.db。
- 新表:`from app.db import Base` 定义 `Mapped`/`mapped_column`(参考 models_db.py 写法),放你自己的新模块文件里。

**团队拓扑(并行 4 + 评审 1):**
| 角色 | 任务 | 仅碰文件 |
|---|---|---|
| E1 以图搜图 | 在用户素材库里按相似度检索 | `services/search.py` `routers/search.py` `tests/test_search.py` |
| E2 转矢量图 | 离线 raster→SVG | `services/vectorize.py` `routers/vectorize.py` `tests/test_vectorize.py` |
| E3 采集任务 | 采集任务/采集图持久化列表 | `models_collect.py` `services/collect_tasks.py` `routers/collect_tasks.py` `tests/test_collect_tasks.py` |
| E4 店铺管理 | 店铺 CRUD + 按店铺上架 | `models_shop.py` `routers/shops.py` `tests/test_shops.py` |

**Tech Lead 收口:** main.py 注册 4 路由;db.init_db import `models_collect`/`models_shop`。

---

### Task E1 以图搜图(/api/search)
- `services/search.py`:`search_assets(db, owner_id, image, top_k=10) -> list[dict]`:对该用户 `Asset` 表,用 `phash.dhash`+`hamming` 与 `phash.color_sig`+`color_distance` 算综合相似度,按相似度降序返回 top_k:`[{asset_id,name,struct_distance,color_distance,similarity}]`。
- `routers/search.py` 前缀 `/api/search`:`POST /by-image`(multipart 图,`top_k:int=10`,`Depends(current_user)`,**不扣点**——搜自己库免费)。先读图(失败 400,无需退点因未扣)。
- 测试:先用 `/api/assets` 传入 2~3 张结构各异的图入库,再 `/by-image` 传其中一张 → 该资产排第一且 similarity 最高;未登录 401;空库返回 []。

### Task E2 转矢量图(/api/vectorize)
- `services/vectorize.py`:`to_svg(img, colors=8, max_dim=128) -> str`(**纯离线**):缩放到 max_dim 内 → 量化到 colors 种色 → 同色相邻像素按行合并成矩形 → 输出 SVG 字符串(`<svg ...><rect .../>...</svg>`,viewBox 用原图尺寸)。设像素/矩形数上限防爆。
- `routers/vectorize.py` 前缀 `/api/vectorize`:`POST /`(multipart 图,`colors:int=8`,`op="process"` 扣 2,读图失败退点);把 SVG 存 `storage.output_path(jid,"vector.svg")`,返回 `{job_id, svg_url, rect_count, colors}`。
- 测试(离线真实):传一张图 → 200,svg_url 可取回且内容含 `<svg` 与 `<rect`;扣 2 点;colors 越界(<2 或 >64)400 并退点;非图 400 退点。

### Task E3 采集任务(/api/collect-tasks)
- `models_collect.py`:`CollectionTask(id:str pk, owner_id, source:str, status:str='collected', count:int, created_at)`;`CollectedImage(id pk, task_id FK, url, hires_url, platform, title, selected:bool=False)`。`from app.db import Base`。
- `services/collect_tasks.py`:创建任务时对每个 url 用 `from app.services.collectors import detect_platform, upgrade_to_hires` 填 platform/hires_url。
- `routers/collect_tasks.py` 前缀 `/api/collect-tasks`(均 `Depends(current_user)`):`POST ""`(body `{source?:str, urls:[str]}` → 建任务+图,返回 task_id+count);`GET ""`(列当前用户任务,最近在前);`GET /{id}`(任务详情含图列表,非本人 404);`POST /{id}/select`(body `{image_ids:[int]}` 标记 selected)。
- 测试:建任务传 3 个不同平台 url → count==3 且 hires_url 正确升级;GET 列表含该任务;GET 他人任务 404;select 后 detail 中对应图 selected==True;未登录 401。

### Task E4 店铺管理(/api/shops)
- `models_shop.py`:`Shop(id pk, owner_id, platform:str, name:str, status:str='active', created_at)`。`from app.db import Base`。
- `routers/shops.py` 前缀 `/api/shops`(均 `Depends(current_user)`):`POST ""`(body `{platform, name}` 建店);`GET ""`(列本人店铺);`POST /{shop_id}/publish-product`(body `{product_id:int}`:校验 shop 与 product 都属本人;用 `from app.services.publish import build_listing_payload, get_publisher` 构建 payload(platform=shop.platform)并 publish,创建 `from app.models_db import Listing` 行,payload 里塞 shop_id;返回 listing 信息)。**勿改** products.py/publish.py/models_db.py。
- 测试:建店 → GET 列表含之;建商品(/api/products)后 publish-product 到 local 店 → status published 且 listing payload 含 shop_id;publish 他人商品 404;未登录 401。

---

## 集成与验收(Tech Lead)
1. db.init_db import models_collect/models_shop;main.py 注册 search/vectorize/collect_tasks/shops 4 路由。
2. `pytest -q` 全绿。
3. 代码评审 agent 过 diff,修 P0/P1。
4. 合并 master。
