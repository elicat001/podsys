"""视频连续性【能力层 Capability Layer】单一真相源(2026-06)。

北极星(docs/plans/2026-06-30-ai-pipeline-architecture-audit.md):AI 能力优先抽象为 **Capability**,其次 Strategy,
最后才落地 Prompt;Prompt 只是能力的【载体】。不同模型(CogVideoX / Vidu / 未来 Runway / Kling)**共享同一套能力**,
各自只挂一份渲染变体,而不是各抄一套越来越长的 prompt。

本模块把连续性的每一族失败对策建成一个 **Capability**(N1),用一个注册表 `CAPABILITIES` + 组装器
`build_continuity_guide()`(N2)按【风险】选择性启用、为不同模型渲染。当前风险由【看图的 LLM】在 prompt 内自判
(每条能力自带『若…才』的自门控文本);未来接入 Scene Profile(N3)后,改由 `enabled=` 直接传入该启用的能力集,
builder 只渲染这些能力 —— 接口已就位,届时无需改 builder。

为什么(失败共性):image-to-video 是逐帧预测、非物理仿真。商品+人物交互视频的系统性失败 = 「需跨时间维持、
但①首帧没锚定 ②模型先验不强制」的结构约束,归为:对象身份(复制/替换/增减)、物理接触(悬空/穿插/脱手)、
时序(光照/尺度/空间/镜头 跳变),外加最上游的 Scene Init(母帧≠脚本第0帧)与最难的 复杂状态前移。

铁律(写进每条能力的载体文本):只对真实存在的风险写、没有就不写;这些是连续性『保持』要求,不是动作约束;
普通动作与镜头表现力默认满自由度(natural_motion 能力受保护)。
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Capability:
    """一个连续性能力(N1):单一职责、自门控、可被不同模型各自渲染、按风险启用(N2)。

    key   : 能力标识(scene_initialization / complex_state / object_identity / physical_contact /
            temporal_consistency / natural_motion)。
    slot  : 在指引里的位置——"scene_init"(独立块)/ "continuity"(风险门控 bullet 主体)/ "motion_floor"(收尾·受保护)。
    risk  : 何时该启用(人类可读)。当前由 LLM 在 prompt 内自判;未来由 Scene Profile.risk 驱动 builder 选择性启用。
    guide : 默认(CogVideoX/通用)渲染文本。
    guide_vidu : Vidu 变体渲染(None=复用 guide)。新模型 = 加一份变体,不是抄整段 prompt。
    """
    key: str
    slot: str
    risk: str
    guide: str
    guide_vidu: str | None = None

    def render(self, model: str = "cogvideox") -> str:
        if model == "vidu" and self.guide_vidu is not None:
            return self.guide_vidu
        return self.guide


# ── 能力注册表(N1)── 加/调一族连续性对策 = 改这一处;各 builder 经 build_*() 组装,不再各写 prompt。
CAPABILITIES: dict[str, Capability] = {
    "scene_initialization": Capability(
        key="scene_initialization", slot="scene_init",
        risk="母帧 ≠ 脚本第 0 帧(被当独立成品图生成,与脚本开头脱节)→ 开头崩",
        guide=(
            "【起始状态一致 · Scene Init(母帧=视频第 0 帧,最优先)】记住:母帧就是这段视频【真正的第 0 帧】、不是独立的商品展示/成品图。\n"
            "所以你写的【场景(母帧)描述】与【脚本开头】要落在同一个【清晰、动作即将开始的起始状态】上——"
            "人物位置、商品状态与位置、环境布局、镜头构图都从这一刻自然展开,让视频能从这一帧顺畅接着演。\n"
            "严防脱节:别出现『母帧是举着商品对镜头笑、脚本却从走进来从台面拿起它开始』这种第0帧≠脚本开头的矛盾(视频开头必崩)。\n"
            "多分镜时:每个分镜的场景就是该镜脚本的第 0 帧;分镜②③的母帧 = 承接上一镜结束那一刻的延续状态(不另起静止 pose)。"
        ),
    ),
    "complex_state": Capability(
        key="complex_state", slot="continuity",
        risk="需要难机械状态变化(开盖/拉链/穿脱/拆封/倾倒)才能开始用 → 现场做易穿模/变形",
        guide=("· 复杂状态变化(开盖/拉链/穿脱/拆封/倾倒等难机械动作才能开始用)→ 让母帧直接呈现『已就绪/可直接使用』,"
               "脚本从可用状态写起、不在视频里现场做这个机械过程。"),
        guide_vidu=("· 复杂状态变化(开盖/拉链/穿脱/拆封/倾倒)→ 让母帧出『已就绪』、脚本从可用状态写起;"
                    "但按压、旋转、捏压回弹这类把玩互动是 Vidu 强项,【不算难动作、不前移】,正常在视频里演。"),
    ),
    "object_identity": Capability(
        key="object_identity", slot="continuity",
        risk="商品小/可复制/会被大幅移动或遮挡 → 模型复制、替换、凭空增减",
        guide="· 对象身份(商品小/可复制/会被大幅移动或遮挡,易被模型复制、替换、凭空增减)→ 点明『全程自始至终只有这一件、是同一个个体』。",
        guide_vidu="· 对象身份(商品小/可复制/大幅移动遮挡)→ 『全程只有同一件、是同一个体,不复制/替换/增减』。",
    ),
    "physical_contact": Capability(
        key="physical_contact", slot="continuity",
        risk="手持/拿取/穿戴 → 悬空、穿插、脱手",
        guide="· 物理接触(手持/拿取/穿戴,易悬空/穿插/脱手)→ 点明『商品始终有真实支撑(被稳稳握住或放在表面),手与它保持接触、不穿插、不悬空』。",
        guide_vidu="· 物理接触(手持/拿取/穿戴)→ 『始终有真实支撑、手与物体接触、不悬空穿插』。",
    ),
    "temporal_consistency": Capability(
        key="temporal_consistency", slot="continuity",
        risk="多分镜/场景有明确空间布置 → 光照/尺度/空间/位置 前后跳变",
        guide="· 时序一致(多分镜或场景有明确空间布置,易前后跳变)→ 点明『分镜间光照、尺度、空间关系与商品位置保持连贯,从上一刻自然延续』。",
        guide_vidu="· 时序一致(场景有明确空间布置)→ 『光照/尺度/空间/位置连贯、自然延续、不跳变』。",
    ),
    "natural_motion": Capability(
        key="natural_motion", slot="motion_floor",
        risk="永远(默认·受保护):防上面的连续性要求被误读成动作限制 → 视频变僵",
        guide="再次强调:没有对应风险就别写那条;普通安全动作(拿起/喝/挥手/转身/走动/把玩)保持自由、有真实运动幅度。",
        guide_vidu="没有对应风险就别写;普通动作保持自由、有真实运动幅度。",
    ),
}

# 风险门控的连续性 bullet 顺序(组成指引主体)
_CONTINUITY_ORDER = ("complex_state", "object_identity", "physical_contact", "temporal_consistency")

_HEADER = (
    "【连续性自检 · 按风险动态(铁律:只对这件商品/交互真实存在的风险写,没有就别写、保持简洁;"
    "以下是『对象/物理/时序的连续性保持』、不是动作限制——普通自然动作与镜头表现力一律放开、别拘谨)】\n"
    "写场景(母帧)与动作(脚本)前,先判断最可能让 AI 视频翻车的点,只把【确实存在的那一两条】自然融进描述:\n"
)
_HEADER_VIDU = (
    "【连续性自检 · 按风险动态(铁律:只对真实存在的风险写、没有就别写;是连续性保持、不是动作限制;普通动作放开)】\n"
)


def build_continuity_guide(model: str = "cogvideox", enabled: set[str] | None = None) -> str:
    """组装连续性指引(N2):按 `enabled` 选择性启用风险门控能力,为 `model` 渲染。

    enabled=None → 含全部风险门控能力(每条自带『若…才』自门控文本,由 LLM 自判,= 当前行为)。
    enabled={...} → 仅渲染这些能力(未来 Scene Profile/N3 据 risk_level 传入)。natural_motion 永远附上(受保护底线)。
    """
    keys = [k for k in _CONTINUITY_ORDER if (enabled is None or k in enabled)]
    bullets = "\n".join(CAPABILITIES[k].render(model) for k in keys)
    header = _HEADER_VIDU if model == "vidu" else _HEADER
    floor = CAPABILITIES["natural_motion"].render(model)
    return f"{header}{bullets}\n{floor}"


# ── Scene Profile(N3)→ 能力选择 ──────────────────────────────────────────────
# Scene Profile 的 interaction_risks 取值 = 【由图像可判定的 per-product 连续性风险】(看图 LLM 在 Step1 给出)。
# 时序风险(temporal_consistency)不由单图判定、而由【是否多分镜】决定,故单列。
PROFILE_RISK_KEYS = ("complex_state", "object_identity", "physical_contact")


def profile_to_capabilities(interaction_risks: list[str] | None, *, multi_shot: bool = False) -> set[str]:
    """Scene Profile(N3)→ 该启用的连续性能力集,喂给 build_continuity_guide(enabled=)。

    interaction_risks 来自 Vision(Step1 看图判定的 per-product 风险);multi_shot=True 时追加 temporal_consistency。
    返回空集 = 无风险 → 不写连续性约束、满自由度(natural_motion 底线仍由 builder 永远附上)。
    """
    enabled = {k for k in (interaction_risks or []) if k in PROFILE_RISK_KEYS}
    if multi_shot:
        enabled.add("temporal_consistency")
    return enabled


# ── 派生导出(各 builder 仍 import 这些名字;现由能力注册表组装,杜绝两变体重复)──
SCENE_INIT_GUIDE = CAPABILITIES["scene_initialization"].render()
CONTINUITY_GUIDE = build_continuity_guide("cogvideox")
CONTINUITY_GUIDE_VIDU = build_continuity_guide("vidu")
