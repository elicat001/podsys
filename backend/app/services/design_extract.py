"""印花提取(真正的):从图里把『印刷的图案』单独抠出来,输出透明 PNG。

和『一键抠图』(rembg 去背景留主体)是两回事:
- 一键抠图:背景去掉、留下主体(人/物)。
- 印花提取:把衣服/物品上印的那块图案单独抠出来(保真,用于复印套版)。

流程(全本地、确定性、不依赖网关):
  ① 框出『产品本体』:衣服 → cloth-seg(能排除人/皮肤);非衣服(枕头/杯子/袋子等)→ 通用 rembg。
  ② 衣服先压平缓变光照/阴影(`_flatten_illumination`,只为算 mask;产品本就平整,不压)。
  ③ 产品 mask 小幅向内腐蚀(只削轮廓一圈明暗,不伤袖子/边缘印花)。
  ④ 双阈值滞后(hysteresis)去主导材质色:强信号(种子)+ 与种子连通的弱信号 = 印花及其淡色细节;
     孤立的淡噪点/淡褶皱丢掉。这样能多留细节而不带进孤立褶皱。
  ⑤ 连通块清理:衣服→留主体(最大成片块)+ 附近块,丢远处/细长褶皱条;产品→只丢极小噪点(保全部铺满的设计)。
  ⑥ 双分辨率出图:粗区域在 1000px 定『印花在哪』(去阴影稳),再把其 bbox 放大裁出『全分辨率印花块』,
     在原始像素上按真实色差重新描边 → 边缘清晰、细线/淡色细节都在(不再是 1000px 放大的软边)。
  ⑦ 补细节后处理 `_deepen`:加饱和 + 加对比,印花颜色更实更深(只加深、不变浅)。
都没分到产品(输入本身就是设计图)→ 退化为整图去底(`extract_on_fabric`)。

为速度&内存:分割/估色/聚类在缩小图(1000px)上做;精细描边只在裁出的印花块(全分辨率,长边封顶 2600)上做。
**已知边界 & 别再过拟合**:重褶皱浅色布料(白衣)的『锐利褶皱折痕』——同时像边缘(滤波抹不掉)、颜色接近浅色印花
(色差分不开)、又和印花连通(连通性丢不掉)——颜色法/边缘保留滤波/光照压平都只能缓解(压平只压缓变阴影、压不掉折痕)。
**绝不能用"像素级开运算/降饱和/取最大块只留一块/暴力降阈值"硬凑**(会碎掉细线印花、丢浅色印花、白衣褶皱反压过印花)。
要彻底干净只能上 AI 编辑(语义"看懂褶皱 vs 印花",需 edit key)。宁留残留也保印花完整。
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from PIL import Image, ImageEnhance, ImageFilter
from scipy import ndimage

from ..ai.upscale import get_upscale_provider
from ..config import settings

_MAX_PX = 40_000_000   # 输入像素上限,防 OOM
_ANALYZE = 1000        # 分析(分割/估色/聚类)用的缩放边长;太大反而放大褶皱噪点(踩过坑)
_CROP_CAP = 4096       # 印花裁剪块的长边上限:精细 alpha 在『全分辨率印花块』上算 → 尽量保原生分辨率(更清晰),封顶防 OOM
_DETAIL_STD = 7.0      # 局部纹理(标准差)阈:高于此=有图案细节。产品上用来补『和底色同色但有纹理』的浅色图案
_DETAIL_FLOOR = 7      # 纹理保留的最低色差:防把纯底色(完全没色差)当细节收进来
_CLOTH = None          # cloth-seg 会话(缓存复用)


# ── 材质提取策略(T1-3:把散落 6 处的 kind=="garment" 分叉常数/开关收敛到一处)──────────
# ⚠ 这些是【不可约的经验常数】(CLAUDE.md 史料:每次试图用原则替代都退化)——本次【只收敛"散落",一字不改数值】,
# 算法分支保持原样、只把字面量换成读 strategy 字段。加新材质类 = 加一个 _Strategy 实例 + _strategy_for 一处,不再改 6 处。
@dataclass(frozen=True)
class _Strategy:
    name: str                   # "garment"(衣服,有褶皱)/ "product"(枕头/杯/袋等,平整)。也用作 method 标签。
    flatten_illumination: bool  # 衣服压平缓变阴影(只为算粗区域);产品本就平整、不压
    use_detail_mask: bool       # 产品补『同色但有纹理』的浅色花;衣服不开(免收褶皱纹理)
    fine_lo: int                # 精细描边色差阈(区域内已无阴影,可低):衣服 37 / 产品 12
    fine_inner_erosion: int     # 精细估底色前对本体的腐蚀圈数(防轮廓明暗污染):衣服 2 / 产品 0(整块)
    coarse_erosion_frac: float  # 粗抠时本体向内腐蚀比例(仅衣服路径用)
    seed_dist: int              # 粗抠强信号种子色差阈(仅衣服)
    weak_dist: int              # 粗抠弱信号色差阈(仅衣服)
    product_dist: int           # 粗抠色差阈(仅产品路径)


# 数值与历史一字不差(garment: 0.03/90/50/37/2;product: 16/12/整块)。
_GARMENT = _Strategy("garment", flatten_illumination=True, use_detail_mask=False, fine_lo=37,
                     fine_inner_erosion=2, coarse_erosion_frac=0.03, seed_dist=90, weak_dist=50, product_dist=16)
_PRODUCT = _Strategy("product", flatten_illumination=False, use_detail_mask=True, fine_lo=12,
                     fine_inner_erosion=0, coarse_erosion_frac=0.03, seed_dist=90, weak_dist=50, product_dist=16)


def _strategy_for(kind: str) -> _Strategy:
    return _GARMENT if kind == "garment" else _PRODUCT


def _cloth_session():
    global _CLOTH
    if _CLOTH is None:
        from rembg import new_session  # lazy import(重依赖)

        _CLOTH = new_session("u2net_cloth_seg")
    return _CLOTH


def _rembg_object(img: Image.Image) -> Image.Image | None:
    """用通用 rembg 把印花当『前景物体』整体抠出 —— 深色照片印深色衣时颜色法会碎裂,这是兜底。

    返回二值化(crisp)RGBA;依赖缺失/失败 → None(调用方回退颜色法)。
    """
    try:
        from ..ai.matting import RembgMattingProvider

        out = RembgMattingProvider().cutout(img).convert("RGBA")
    except Exception:  # noqa: BLE001
        return None
    out.putalpha(out.getchannel("A").point(lambda v: 255 if v >= 128 else 0))  # 去半透明柔边
    return out


def _product_mask(img: Image.Image) -> tuple[np.ndarray | None, str]:
    """框出『产品本体』的布尔 mask。衣服 → cloth-seg(能排除人/皮肤);非衣服(枕头/杯子/
    袋子等)→ 通用 rembg。返回 (mask, kind);都没分到 → (None, ...)。"""
    w, h = img.size
    cloth_cov = 0.0  # cloth-seg 看到的衣服占比(用于判定是不是衣服)
    try:
        from rembg import remove  # lazy import

        seg = remove(img, session=_cloth_session())  # 上衣/下装/全身 三段纵向堆叠
        cmask = np.asarray(seg.convert("RGBA").crop((0, 0, w, h)).getchannel("A")) > 30
        cloth_cov = cmask.sum() / (w * h)
        if cloth_cov > 0.02:  # cloth-seg 干净分出了衣服 → 直接用它(能排除人/皮肤)
            return cmask, "garment"
    except Exception:  # noqa: BLE001
        pass
    try:  # cloth-seg 没分干净 → 用通用 rembg 拿完整产品本体
        from ..ai.matting import RembgMattingProvider

        obj = RembgMattingProvider().cutout(img).convert("RGBA")
        mask = np.asarray(obj.getchannel("A")) > 30
        if mask.sum() > 0.02 * w * h:
            # cloth-seg 看到过衣服碎片(平铺衣常这样)→ 仍按衣服(高tol+聚类);完全没看到 → 非衣服产品
            return mask, ("garment" if cloth_cov > 0.005 else "product")
    except Exception:  # noqa: BLE001
        pass
    return None, "none"


def _flatten_illumination(rgb: np.ndarray, mask: np.ndarray) -> np.ndarray:
    """压平缓变的光照/阴影:用大尺度模糊估出低频光照场,除掉它 → 布料变均匀。

    只用于『算 mask』(让褶皱的缓变阴影不被当印花);最终输出仍用原图像素,不影响颜色。
    注:只压得掉『缓变阴影』(如衣服一侧偏暗);压不掉锐利的褶皱折痕(中频、本身像边缘)。
    """
    g = rgb.mean(axis=2)
    illum = np.clip(ndimage.gaussian_filter(g, sigma=max(g.shape) / 9.0), 1.0, None)
    target = float(np.median(g[mask])) if mask.any() else float(g.mean())
    return np.clip(rgb * (target / illum)[..., None], 0, 255)


def _dominant_color(rgb: np.ndarray, mask: np.ndarray) -> np.ndarray:
    """region 内出现最多的颜色(粗量化后取众数)= 布料底色。"""
    px = (rgb[mask] // 24 * 24)
    cols, cnts = np.unique(px, axis=0, return_counts=True)
    return cols[cnts.argmax()].astype(int)


def _detail_mask(rgb: np.ndarray, win: int = 5) -> np.ndarray:
    """局部纹理图(灰度的局部标准差):图案(花/字/纹理)处高,平整底色处低。

    用途:产品(枕头/杯子等本就平整、无褶皱)上,把『颜色和底色接近、但有纹理细节』的浅色图案
    也保留下来——光靠色差会把浅花/淡叶/细茎当底色删掉。衣服不能用(褶皱也有纹理,会误收)。
    """
    g = rgb.mean(axis=2)
    mean = ndimage.uniform_filter(g, win)
    var = ndimage.uniform_filter(g * g, win) - mean * mean
    return np.sqrt(np.clip(var, 0, None))


def _keep_print_components(labels: np.ndarray, n: int) -> np.ndarray:
    """从连通块里只留『印花』:保留主体(最大块)+ 主体附近的成片块;
    丢掉① 极小噪点 ② 远离主体的零散残留 ③ 细长的『条』(褶皱)。
    用形状/位置判定,不在像素级做开运算,所以不会把成片印花/细线印花打碎。"""
    sizes = ndimage.sum(np.ones_like(labels), labels, range(1, n + 1))
    coms = ndimage.center_of_mass(np.ones_like(labels), labels, range(1, n + 1))
    objs = ndimage.find_objects(labels)

    def _is_streak(idx):  # 细长的『条』(褶皱)= 一边很窄 + 长宽比大
        bh = objs[idx][0].stop - objs[idx][0].start
        bw = objs[idx][1].stop - objs[idx][1].start
        return min(bh, bw) < 6 and max(bh, bw) / (min(bh, bw) + 1) > 4

    # 印花主体 = 最大的『成片块』(跳过细长褶皱条,免得锚错到褶皱上)
    big = int(sizes.argmax()) + 1
    for idx in np.argsort(sizes)[::-1]:
        if not _is_streak(idx):
            big = int(idx) + 1
            break
    ys, xs = np.where(labels == big)
    cy, cx = ys.mean(), xs.mean()
    radius = max(int(np.ptp(ys)), int(np.ptp(xs))) * 1.25 + 60  # 主体覆盖范围
    keep = [big]                                             # 主体永远保留
    for i in range(1, n + 1):
        if i == big or sizes[i - 1] < 6:                     # 主体 / 极小噪点(阈值调低,保细节)
            continue
        y, x = coms[i - 1]
        if (y - cy) ** 2 + (x - cx) ** 2 >= radius ** 2:     # 远离主体 → 丢
            continue
        if _is_streak(i - 1):                                # 细长褶皱条 → 丢
            continue
        keep.append(i)
    return np.isin(labels, keep)


def _drop_tiny(labels: np.ndarray, n: int) -> np.ndarray:
    """只丢极小噪点,保留所有成片块 —— 用于产品(枕头/袋子等):设计铺满整个产品、无褶皱,
    不能像衣服那样按"主体附近"聚类(会把散布的花卉等远处图案误删)。"""
    sizes = ndimage.sum(np.ones_like(labels), labels, range(1, n + 1))
    thr = max(4, sizes.max() * 0.0012)  # 阈值调低,保留更多细小图案
    return np.isin(labels, list((np.where(sizes > thr)[0] + 1).tolist()))


def _print_alpha(rgb: np.ndarray, region: np.ndarray, kind: str = "garment") -> np.ndarray:
    """在 region(产品本体)内去主导材质色,返回印花的 alpha(uint8)。

    - 产品(枕头/袋子等,平整无褶皱):**不腐蚀、不做种子门控** —— 直接『色差大 或 有纹理』即印花,
      最大限度保留淡色/靠边的花(印花常贴到产品边缘,腐蚀会削掉边花);再丢极小噪点。
    - 衣服(有褶皱):双阈值滞后(hysteresis)防褶皱——强信号(dist>70)作种子,弱信号(dist>50)
      只有和种子连通才留;只小腐蚀 2 像素估底色 + 定种子(避免把本体轮廓明暗当种子),弱信号用
      **整块本体**(让袖子/贴边印花也能被种子带出),再聚类清理。
    """
    strat = _strategy_for(kind)
    if kind != "garment":  # 产品:平整,不腐蚀不门控,保边缘/淡色花
        fabric = _dominant_color(rgb, region)
        dist = np.sqrt(((rgb - fabric) ** 2).sum(axis=2))
        detail = _detail_mask(rgb)
        keep = region & ((dist > strat.product_dist) | ((detail > _DETAIL_STD) & (dist > _DETAIL_FLOOR)))
        labels, n = ndimage.label(keep)
        pm = _drop_tiny(labels, n) if n else keep
        return np.where(pm, 255, 0).astype("uint8")

    # 衣服:印花通常在中间一块,边缘是衣服结构(轮廓/领口/袖口/下摆)→ 腐蚀掉边缘一圈再检测,
    # 否则会把整件衣服的轮廓当印花抠出来(浅衣拍浅底时尤其严重)。
    shrink = max(10, int(min(region.shape) * strat.coarse_erosion_frac))
    inner = ndimage.binary_erosion(region, iterations=shrink)
    if inner.sum() < 50:
        inner = region
    fabric = _dominant_color(rgb, inner)
    dist = np.sqrt(((rgb - fabric) ** 2).sum(axis=2))
    seeds = inner & (dist > strat.seed_dist)    # 强信号种子(阈值偏高:淡褶皱阴影不自成种子)
    weak = inner & (dist > strat.weak_dist)     # 弱信号限制在内圈 → 衣服边缘结构不进来
    labels, n = ndimage.label(weak, structure=np.ones((3, 3)))  # 8 邻接,利于连住细节
    if n:
        seed_labels = set(np.unique(labels[seeds]).tolist()) - {0}
        pm = np.isin(labels, list(seed_labels))  # 只留含强信号的弱区域 = 印花+其淡色细节
    else:
        pm = np.zeros(weak.shape, dtype=bool)
    labels2, n2 = ndimage.label(pm)
    if n2:
        pm = _keep_print_components(labels2, n2)
    return np.where(pm, 255, 0).astype("uint8")


def extract_on_fabric(crop: Image.Image, tol: int = 60) -> Image.Image:
    """整图去底(设计图降级路径):从四边估布料色,洪水填充去掉与边相连的底色,保留图案内部。

    不写死颜色:边缘色(白/黑/任意)都从图里估;本地、确定性、可离线测。
    """
    rgb = np.asarray(crop.convert("RGB"), dtype=np.int16)
    h, w = rgb.shape[:2]
    border = np.concatenate([rgb[0, :], rgb[h - 1, :], rgb[:, 0], rgb[:, w - 1]], axis=0)
    fabric = np.median(border, axis=0)
    dist = np.sqrt(((rgb - fabric) ** 2).sum(axis=2))
    near = dist < tol
    labels, n = ndimage.label(near)
    if n:
        bd = set(labels[0, :]) | set(labels[h - 1, :]) | set(labels[:, 0]) | set(labels[:, w - 1])
        bd.discard(0)
        bg = np.isin(labels, list(bd))
    else:
        bg = np.zeros((h, w), dtype=bool)
    alpha = np.where(bg, 0, 255).astype("uint8")
    out = Image.fromarray(np.dstack([np.asarray(crop.convert("RGB")), alpha]), "RGBA")
    a = (
        out.getchannel("A")
        .filter(ImageFilter.MaxFilter(3))
        .filter(ImageFilter.MinFilter(3))
        .filter(ImageFilter.GaussianBlur(0.6))
    )
    out.putalpha(a)
    return out


def _autocrop(rgba: Image.Image) -> Image.Image:
    bbox = rgba.getchannel("A").getbbox()
    return rgba.crop(bbox) if bbox else rgba


def _drop_speckle(fine: np.ndarray) -> np.ndarray:
    """全分辨率上清掉极小的孤立散点(如文字周围的噪点);阈值随分辨率缩放,不误删小图案。"""
    lab, n = ndimage.label(fine)
    if not n:
        return fine
    sizes = ndimage.sum(np.ones_like(lab), lab, range(1, n + 1))
    minpx = max(24, fine.size * 0.00004)  # 极小:只清噪点,正常花瓣/笔画远大于此
    return np.isin(lab, list((np.where(sizes >= minpx)[0] + 1).tolist()))


def _capped(img: Image.Image) -> Image.Image:
    """长边封顶到 _CROP_CAP,防超大印花块算色差时 OOM(小图原样返回)。"""
    if max(img.size) > _CROP_CAP:
        img = img.copy()
        img.thumbnail((_CROP_CAP, _CROP_CAP))
    return img


def _fill_subject_holes(rgba: Image.Image) -> Image.Image:
    """补回『主体内部、和布料同色被误删』的区域(如狗头的白肚皮、人物的白衣)。

    关键区分(解决"内容缺失"又不毁文字):
    - 被『厚实图案』包围的洞 = 主体内部(白肚皮)→ 填回,用洞自身原色(白→白,不会染成身体色)。
    - 被『细笔画』包围的洞 = 描边文字字心 / 镂空 → 不填,保持透明(黑底也不会变黑块)。
    判据:把图案按到边缘的距离腐蚀掉一圈(厚度阈 k),细笔画会被腐蚀没→其洞『露出去』不再封闭;
    厚实主体腐蚀后仍封闭→其洞保留。只填腐蚀后仍封闭的洞。
    """
    arr = np.asarray(rgba)
    a0 = arr[..., 3] > 0
    if not a0.any():
        return rgba
    holes = ndimage.binary_fill_holes(a0) & ~a0
    if not holes.any():
        return rgba
    k = max(10, int(min(a0.shape) * 0.02))             # 厚度阈:> 笔画宽、< 主体厚
    deep = ndimage.binary_fill_holes(ndimage.distance_transform_edt(a0) > k)
    hlab, hn = ndimage.label(holes)
    sizes = ndimage.sum(np.ones_like(hlab), hlab, range(1, hn + 1))
    cap = a0.sum() * 0.35                               # 上限:超主体 35% 的洞不填(防大片框住的背景)
    deep_ids = set(np.unique(hlab[holes & deep]).tolist()) - {0}
    keep_ids = [i for i in deep_ids if sizes[i - 1] < cap]  # 腐蚀后仍封闭 且 不过大 = 主体内部
    if not keep_ids:
        return rgba
    a = a0 | np.isin(hlab, keep_ids)
    out = np.dstack([arr[..., :3], np.where(a, 255, 0).astype(np.uint8)])  # 各像素保留自身原色
    return Image.fromarray(out, "RGBA")


def _supersample(rgba: Image.Image) -> Image.Image:
    """超分:结果长边低于目标时,用 upscale Provider(默认 Lanczos)放大到目标。

    放大后把 alpha『重新二值化』——避免插值产生半透明柔边(那会让边缘变糊、贴白底变浅)。
    Provider 可插拔:配 POD_UPSCALE_PROVIDER=fsrcnn 即换本地超分(更锐)。倍数封顶防过度插值。
    """
    target = settings.print_target_px
    long = max(rgba.size)
    if not target or long >= target:
        return rgba
    scale = min(target / long, settings.print_max_upscale)
    if scale <= 1.01:
        return rgba
    up = get_upscale_provider().upscale(rgba, scale).convert("RGBA")
    up.putalpha(up.getchannel("A").point(lambda v: 255 if v >= 128 else 0))
    return up


def _deepen(rgba: Image.Image, deep_bg: bool = False) -> Image.Image:
    """补细节后处理:加饱和 + 加对比 → 印花颜色更实更深(只加深、不变浅)。只动 RGB,不动 alpha。

    按底色深浅分两路(深浅是连续亮度判断,不是只认黑/白):
    - 浅色底(白/米白/浅灰等):颜色本就准,温和加深——饱和 ×1.42、对比 ×1.18。
    - 深色底(黑/深蓝/墨绿/深红等):印花常蒙一层灰、动态范围被压窄 → 先按『不透明像素』亮度做
      去灰拉伸(单一 scale,不偏色),再更强饱和 ×1.5,恢复质感。
    """
    if deep_bg:
        arr = np.asarray(rgba).astype(np.float32)
        rgb_a, alpha = arr[..., :3], arr[..., 3]
        a = alpha > 0
        if a.any():  # 去灰:把不透明像素亮度从 [p3, p99] 拉伸到 [0, 255](去黑色蒙灰、恢复对比)
            val = rgb_a[a].mean(axis=1)
            lo, hi = float(np.percentile(val, 3)), float(np.percentile(val, 99))
            rgb_a = np.clip((rgb_a - lo) * (255.0 / max(hi - lo, 1.0)), 0, 255)
        out = Image.fromarray(np.dstack([rgb_a, alpha]).astype(np.uint8), "RGBA")
        base = ImageEnhance.Color(out.convert("RGB")).enhance(1.5)
        res = base.convert("RGBA")
        res.putalpha(out.getchannel("A"))
        return res

    rgb = ImageEnhance.Color(rgba.convert("RGB")).enhance(1.42)
    rgb = ImageEnhance.Contrast(rgb).enhance(1.18)
    out = rgb.convert("RGBA")
    out.putalpha(rgba.getchannel("A"))
    return out


def extract_design(image: Image.Image) -> tuple[Image.Image, dict]:
    """返回 (透明印花图, meta)。meta.method ∈ garment / whole_image / whole_image_fallback。"""
    full = image.convert("RGB")
    W, H = full.size
    if W * H > _MAX_PX:
        raise ValueError("图片过大,请压缩后再试")

    small = full.copy()
    small.thumbnail((_ANALYZE, _ANALYZE))  # 分割/聚类/估色:小图快且对褶皱噪点稳
    sw, sh = small.size
    mask, kind = _product_mask(small)
    strat = _strategy_for(kind)             # T1-3:该材质的提取常数/开关(散落 6 处收敛到此)

    if mask is None:  # 衣服/产品都没分到 → 输入当作一张设计图,整图去底
        cut = _supersample(_autocrop(_deepen(_fill_subject_holes(extract_on_fabric(_capped(full))))))
        return cut, {"method": "whole_image", "size": list(cut.size)}

    # ① 粗区域(1000px):定『哪些区域是印花』——去阴影 + 连通块聚类,对褶皱稳
    orig_small = np.asarray(small).astype(int)
    rgb_small = orig_small.astype(float)
    if strat.flatten_illumination:  # 衣服先压平缓变阴影(只为算粗区域);产品本就平整、不压
        rgb_small = _flatten_illumination(rgb_small, mask)
    alpha_small = _print_alpha(rgb_small.astype(int), mask, kind)
    region_small = Image.fromarray(alpha_small, "L").point(lambda v: 255 if v >= 90 else 0)
    bbox = region_small.getbbox()
    if bbox is None:  # 没抠出图案 → 退回整图去底
        cut = _supersample(_autocrop(_deepen(_fill_subject_holes(extract_on_fabric(_capped(full))))))
        return cut, {"method": "whole_image_fallback", "size": list(cut.size)}

    # ② 把粗区域 bbox 放大到全分辨率、加边距,裁出『印花块』在全分辨率上处理(清晰、保细节)
    pad = 6
    x0 = max(0, int(bbox[0] * W / sw) - pad); y0 = max(0, int(bbox[1] * H / sh) - pad)
    x1 = min(W, int(bbox[2] * W / sw) + pad); y1 = min(H, int(bbox[3] * H / sh) + pad)
    crop = _capped(full.crop((x0, y0, x1, y1)))
    cw, ch = crop.size

    # ③ 精细 alpha:在粗区域『内』,按全分辨率印花块的真实色差重新描边
    #    → 边缘清晰、细线/淡色细节都在;阴影已被粗区域挡在外面,故阈值可压低多留细节。
    region = (
        region_small.crop(bbox).resize((cw, ch), Image.BILINEAR)
        .point(lambda v: 255 if v >= 90 else 0)
        .filter(ImageFilter.MaxFilter(5))  # 稍外扩,给精细色差描边留出边缘余量
    )
    region_arr = np.asarray(region) > 0
    if strat.fine_inner_erosion:  # 衣服小腐蚀 2 估底色(防轮廓明暗污染);产品平整(0),整块估色
        inner = ndimage.binary_erosion(mask, iterations=strat.fine_inner_erosion)
        if inner.sum() < 50:
            inner = mask
    else:
        inner = mask
    fabric = _dominant_color(orig_small, inner)  # 用原图色(非压平)估布料色
    lo = strat.fine_lo                            # 比粗阈值低:区域内已无阴影,可多留细节
    rgb_crop = np.asarray(crop).astype(int)
    dist = np.sqrt(((rgb_crop - fabric) ** 2).sum(axis=2))
    keep = dist > lo
    if strat.use_detail_mask:  # 产品:补『和底色同色但有纹理』的浅色图案(衣服不开,免得收褶皱)
        detail = _detail_mask(rgb_crop)
        keep |= (detail > _DETAIL_STD) & (dist > _DETAIL_FLOOR)
    fine = region_arr & keep
    fine = _drop_speckle(fine)  # 全分辨率再清极小散点(Family 周围那种)
    cut = crop.convert("RGBA")
    cut.putalpha(Image.fromarray(np.where(fine, 255, 0).astype("uint8"), "L"))

    method = kind  # garment(衣服)/ product(枕头/杯子/袋子 等)
    if cut.getchannel("A").getbbox() is None:  # 没抠出 → 退回整图
        cut = extract_on_fabric(crop)
        method = "whole_image_fallback"
    # 深色底 vs 浅色底(感知亮度判断,不写死黑/白——深蓝/墨绿/深红等都算深色底)
    deep_bg = bool(0.299 * fabric[0] + 0.587 * fabric[1] + 0.114 * fabric[2] < 115)

    # 衣服:rembg 物体抠图 与 颜色法『合并』。rembg 给完整干净主体(补回浅色绒毛手臂、保住暗部
    # /照片调,如狗头手臂、NEO 死神),颜色法补 rembg 漏掉的『独立物体』(刀、四周文字)。
    # rembg 出雾时其二值前景很小、合并退化为≈颜色法(自带兜底);平面/水彩印花照样走颜色法。
    if method == "garment":
        obj = _rembg_object(crop)
        if obj is not None:
            ca = np.asarray(cut)[..., 3] > 0           # 颜色法 alpha
            oa = np.asarray(obj)[..., 3] > 0           # rembg 二值前景
            lab, nlab = ndimage.label(ca)
            extra = np.zeros_like(ca)
            ncomp = 0
            min_extra = max(50, int(ca.size * 0.0005))
            for c in range(1, nlab + 1):
                comp = lab == c
                cs = int(comp.sum())
                # 颜色块『大部分在 rembg 主体之外』= 伸出主体的独立物体(被爪子抓着的刀:握柄与
                # 主体重叠、刀身/柄头伸在外)→ 整块补上。主体本身的色块大部分在 oa 内 → 不补(用 rembg 干净版)。
                if cs >= min_extra and int((comp & ~oa).sum()) >= cs * 0.35:
                    extra |= comp
                    ncomp += 1
            # extra 是少数几块『独立物体』(如刀,≤2 块):在物体周围一圈区域内放宽阈值,补回被
            # 删掉的浅色金属(刀刃),再桥接 + 填内缝 → 整把刀完整。缝/补处填的都是原图真实像素。
            # 文字类(多块)跳过,免得字母糊在一起。
            if 1 <= ncomp <= 2:
                grow = max(40, int(min(ca.shape) * 0.09))  # 覆盖整把刀(刀身→护手→握柄)
                obj_region = ndimage.binary_dilation(extra, iterations=grow)
                # 在刀的活动区内按色差抓全刀(含被删的浅色金属);阈值 28 够高,排除旁边的白衬衫。
                # 不做闭运算/填洞——那会按形状把刀旁的白衬衫一起填进来(深色底上现白边)。
                extra = extra | (obj_region & (dist > 28))
            # 补 rembg 边缘缺口:浅色绒毛贴浅底时 rembg 会咬掉一小块(如狗前臂),用紧贴主体边缘
            # 一圈(8px)的颜色法内容补回——只补边缘、不带远处毛边。
            edge_fill = ca & ndimage.binary_dilation(oa, iterations=8)
            merged = oa | extra | edge_fill
            if merged.sum() >= ca.sum() * 0.5:         # 防 rembg 异常导致内容反而变少
                cut = crop.convert("RGBA")
                cut.putalpha(Image.fromarray(np.where(merged, 255, 0).astype("uint8"), "L"))
                method = "garment_merged"

    # 收尾:补主体内部空洞 → 加深颜色(深色底去灰) → 自动裁剪 → 超分到目标分辨率
    cut = _supersample(_autocrop(_deepen(_fill_subject_holes(cut), deep_bg=deep_bg)))
    return cut, {"method": method, "size": list(cut.size)}
