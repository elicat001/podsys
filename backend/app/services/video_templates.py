"""POD 视频后台默认场景(中性动作链)。

> 历史:本库曾有 `STORY_TEMPLATES`(按类目写死的 OOTD/晨间/咖啡 故事 few-shot)+ `templates_for()`。
> 智能向导早已改为「完全围绕上传商品 AI 原创故事/场景」、不再用它;那套写死库内嵌「出门/咖啡店」单一文化,
> 正是后来「去『出门带货』单一文化」整改要消除的东西,且生产无人引用(仅测试 pin)→ **已删**(audit T3-8)。
> 若将来要做「类目感知融合」,应走 Scene Profile(audit N3)的结构化路线,而不是复活写死的故事表。

现仅保留**中性、品类无关**的动作链场景兜底:`default_scenes()`。纯数据 + 纯函数,离线可测、不依赖网关。
"""
from __future__ import annotations

# 中性【动作链】场景递进(setup→拿起使用→继续活动),适配任意品类,天然各拍不同。
# 刻意通用(非品类专属如 OOTD),避免给非服装商品套穿搭场景。可向 N 拍扩展(取前 n 个)。
_DEFAULT_SCENE_CHAIN: list[str] = [
    "这件商品自然出现在它该在的真实生活场景里(摆好/穿在身上)、暖色自然光",
    "有人自然地拿起 / 穿上 / 用起这件商品,开始做手头的事",
    "带着这件商品在生活场景里继续活动(走动 / 落座 / 使用),自然收尾",
]


def default_scenes(category: str = "通用", n: int = 2) -> list[str]:
    """后台默认的【中性动作链】场景母帧(共 n 拍),适配任意品类、各拍天然不同。
    用途:① 手动「视频类型」路径多分镜的 per-shot 自动融合;② 向导模型漏给场景时的兜底。
    n 不足 3 取前 n;超过 3 则末段循环复用(够当前 2/3 分镜用,不写死段数)。
    category 暂不细分(中性链适配所有品类);保留入参以兼容调用方、且为未来 Scene Profile 留扩展位。"""
    chain = _DEFAULT_SCENE_CHAIN
    return [chain[i] if i < len(chain) else chain[-1] for i in range(max(2, n))]
