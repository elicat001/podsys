"""转矢量图(raster→SVG):**真矢量描摹**,放大无锯齿、可无损缩放。

引擎链(全本地、不依赖网关,两级都输出平滑 `<path>`,绝不退化成像素方块):
- 主引擎 **vtracer**:工业级彩色描摹,spline 贝塞尔曲线 + 分层/合并/去碎块,
  效果接近 Adobe Image Trace。
- 降级 **cv2 轮廓追踪**:量化配色后逐色取轮廓 → Douglas-Peucker 简化 →
  平滑多边形 `<path>`(fill-rule=evenodd 自动镂空内孔)。仍是真矢量,只是边角略硬。

两者都缺失/失败时抛 ValueError(由 router 转 400 + 退点),**不再回退到像素块 `<rect>`**
——那是旧版"放大一股像素风"的根因,已彻底移除。
"""
from __future__ import annotations

from PIL import Image

_TRACE_CAP = 900          # cv2 降级法的分析长边上限
_MIN_AREA = 3.0           # cv2 降级法:丢弃面积小于此的轮廓/内孔(去噪点)

# 工业级管线(vtracer)参数
# 🔑 细节保留的命门 = 在『接近原生分辨率』上描摹 + 『不做去噪』。
#   - 早期把图缩到 1200px 再描摹 → 发丝/细线条在降采样这一步就没了(放大后一坨水彩)。
#   - 早期还套 bilateralFilter 双边去噪 → 把发丝/细纹理直接抹平。两者是"细节糊"的真凶,已去除。
#   cap 抬到 2400 只为 bound 最坏耗时/体积(原生 18MP 要 ~80s/40MB,太重);2400px 实测发丝清晰、~10s/~9MB。
_WORK_CAP = 2400          # 默认工作分辨率上限:近原生描摹,仅超此才降采样(bound 时间/体积/防 OOM)
_FINE_CAP = 3200          # fine 档放宽上限,换更极致的毛发/细节(更慢、文件更大)
# 精细度档位:logo→扁平图形(收拢调色板、消 AA 碎边),illustration→默认高保真,fine→极致细节
# 🔑 治"边缘马赛克碎边":扁平图(卡通/logo/印花稿)的软边是几百种 AA 过渡色,cp=8 会把每种都描成
#   独立碎边路径 → 灰色马赛克框。对策 = 调低 color_precision(cp=6)把 AA 过渡色收拢进最近主色 → 干净边。
#   照片/毛发本身就是连续色调、没有"平底+软边"问题,反而要 cp=8 才保细节 —— 故由 _detect_content 分流。
# filter_speckle 越小越保留细节;color_precision 越低越收拢调色板(消碎边)/越高越保留颜色;
# layer_difference 越小相邻色越不被合并;corner_threshold 越低边角越锐。
_PRESETS = {
    # 扁平图形(logo/卡通/印花稿):低色深收拢 AA 过渡色 → 边缘干净无碎边、文件最小
    "logo": dict(filter_speckle=10, color_precision=6, layer_difference=16, corner_threshold=60, path_precision=2),
    # 默认高保真(照片/插画):近原生 + 低去碎块 + 高色深 → 保留细节/毛发、颜色不混、放大无锯齿
    "illustration": dict(filter_speckle=4, color_precision=8, layer_difference=8, corner_threshold=40, path_precision=3),
    # 极致细节(毛发/照片):几乎不去碎块、最高色深、最锐角;代价是路径多、SVG 大(几~十几 MB)、较慢。
    "fine": dict(filter_speckle=1, color_precision=8, layer_difference=6, corner_threshold=30, path_precision=3),
}


def to_svg(img: Image.Image, colors: int = 8, preset: str = "auto") -> tuple[str, int]:
    """位图转 SVG,返回 (svg字符串, 形状数)。

    - colors:降级引擎(cv2)的量化色数,2<=colors<=64。
    - preset:精细度档位,auto / logo / illustration / fine(auto=按内容自动判别)。

    引擎优先级:vtracer(工业级平滑贝塞尔)→ cv2 多边形。两者都不可用时抛 ValueError。
    """
    if not (2 <= colors <= 64):
        raise ValueError(f"colors 必须在 2..64 之间,收到 {colors}")

    rgb = img.convert("RGB")
    orig_w, orig_h = rgb.size
    if orig_w < 1 or orig_h < 1:
        raise ValueError("图片尺寸非法")

    try:
        return _vtracer_svg(rgb, orig_w, orig_h, preset)
    except Exception:  # noqa: BLE001  vtracer 缺失/失败 → 降级到 cv2 多边形
        pass
    try:
        return _trace_svg(rgb, colors, orig_w, orig_h)
    except Exception as exc:  # noqa: BLE001  两级矢量引擎都失败 → 干净退点,绝不回退像素块
        raise ValueError("矢量化失败:描摹引擎不可用或图片无法解析,请重试或更换图片") from exc


def _enhance(rgb: Image.Image, cap: int = _WORK_CAP) -> Image.Image:
    """第一层:近原生分辨率描摹(细节不糊的关键;降采样会抹掉发丝/细线条)。
    仅长边超过 cap 才降采样,控 SVG 体积/防 OOM。"""
    rgb = rgb.convert("RGB")
    long_side = max(rgb.size)
    if long_side > cap:
        scale = cap / long_side
        return rgb.resize((max(1, round(rgb.width * scale)), max(1, round(rgb.height * scale))), Image.LANCZOS)
    return rgb


def _detect_content(arr) -> str:
    """第二层(自动):区分『扁平图形』与『连续色调/照片』→ 选档位。

    判据 = 前 8 主色覆盖率(top8cov)。扁平图(卡通/logo/印花稿)由少数纯色块构成,
    几个主色就覆盖绝大多数像素(其余几百种都是边缘 AA 过渡色);照片是连续色调,
    颜色分散、没有少数主色独大。
    - top8cov 高(≥0.80)→ 扁平图 → "logo"(低 color_precision 收拢 AA 过渡色 → 边缘干净无碎边)
    - 否则 → 照片/插画 → "illustration"(高 color_precision 保细节/毛发;照片无"平底软边",不产生碎边)
    旧版用 edge_density 判别,被卡通的黑描边骗成 illustration → cp=8 把 AA 软边描成马赛克碎边,已弃用。
    """
    import numpy as np
    a = np.asarray(arr)
    h, w = a.shape[:2]
    step = max(1, max(h, w) // 512)         # 子采样到 ~512:统计色彩分布足够,无需插值/cv2
    small = a[::step, ::step]
    q = (small.astype(np.int16) // 16 * 16).reshape(-1, 3)   # //16 量化,合并极近似色
    counts = np.unique(q, axis=0, return_counts=True)[1]
    top8cov = float(counts[np.argsort(counts)[::-1][:8]].sum()) / counts.sum()
    return "logo" if top8cov >= 0.80 else "illustration"


def _optimize_svg(svg: str, orig_w: int, orig_h: int) -> str:
    """第六层:精简 SVG —— 去 xml 声明/生成器注释;根标签加 viewBox(工作坐标)、
    显示尺寸归一回原图;压掉标签间多余空白。"""
    import re
    m = re.search(r'<svg[^>]*\bwidth="(\d+(?:\.\d+)?)"[^>]*\bheight="(\d+(?:\.\d+)?)"', svg)
    vw, vh = (m.group(1), m.group(2)) if m else (orig_w, orig_h)
    svg = re.sub(r"<\?xml[^>]*\?>", "", svg)
    svg = re.sub(r"<!--.*?-->", "", svg, flags=re.S)
    svg = re.sub(
        r"<svg[^>]*>",
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {vw} {vh}" '
        f'width="{orig_w}" height="{orig_h}">',
        svg, count=1,
    )
    return re.sub(r">\s+<", "><", svg).strip()


def _vtracer_svg(rgb: Image.Image, orig_w: int, orig_h: int, preset: str = "auto") -> tuple[str, int]:
    """工业级矢量化管线(全本地、不依赖网关):
    ①近原生归一(不去噪!)→ ②按内容选档位 → ③vtracer spline 描摹(内部已做 分层/合并/Bezier/去碎块)
    → ⑥精简 SVG。输出接近 Adobe Image Trace:细节保留、曲线平滑、放大无锯齿、可无损缩放。
    """
    import os
    import tempfile

    import numpy as np
    import vtracer

    # 选档位 + 对应工作分辨率上限。auto 在 logo/illustration 间判别(均用 _WORK_CAP);
    # fine 是用户显式选的极致档,放宽到 _FINE_CAP 换更多毛发/细节。
    if preset in _PRESETS:
        kind = preset
        work = _enhance(rgb, _FINE_CAP if kind == "fine" else _WORK_CAP)
    else:
        work = _enhance(rgb, _WORK_CAP)
        kind = _detect_content(np.asarray(work))
    params = _PRESETS[kind]
    # ⚠️ 不做任何去噪(双边/中值都会抹掉发丝/细线条)。细小噪点交给 vtracer 的 filter_speckle 处理。
    arr = np.asarray(work)

    with tempfile.TemporaryDirectory() as d:
        ip = os.path.join(d, "in.png")
        op = os.path.join(d, "out.svg")
        Image.fromarray(arr).save(ip)
        vtracer.convert_image_to_svg_py(
            ip, op, colormode="color", mode="spline",
            length_threshold=4.0, splice_threshold=45, **params,
        )
        with open(op, encoding="utf-8") as fh:
            svg = fh.read()

    svg = _optimize_svg(svg, orig_w, orig_h)
    n = svg.count("<path")
    if n == 0:
        raise RuntimeError("vtracer 无输出")
    return svg, n


def _trace_svg(rgb: Image.Image, colors: int, orig_w: int, orig_h: int) -> tuple[str, int]:
    """cv2 轮廓追踪 → 平滑矢量路径。"""
    import cv2
    import numpy as np

    long_side = max(orig_w, orig_h)
    if long_side > _TRACE_CAP:
        s = _TRACE_CAP / long_side
        aw, ah = max(1, round(orig_w * s)), max(1, round(orig_h * s))
        work = rgb.resize((aw, ah), Image.LANCZOS)
    else:
        aw, ah, work = orig_w, orig_h, rgb

    # 矢量化前先做边缘保持平滑(bilateral):抹掉 JPEG 噪点/细纹理、合并成平整色块。
    # 否则照片噪点会被量化成上千个碎块,既多又大又难看。
    arr = cv2.bilateralFilter(np.asarray(work), 9, 75, 75)
    work = Image.fromarray(arr)
    # 量化到 colors 种色,拿到调色板索引图。
    pal_img = work.quantize(colors=colors, method=Image.Quantize.MEDIANCUT)
    idx = np.asarray(pal_img)
    palette = pal_img.getpalette() or []
    sx, sy = orig_w / aw, orig_h / ah

    min_area = max(_MIN_AREA, 0.00025 * aw * ah)  # 相对面积阈:丢掉噪点碎块
    kernel = np.ones((3, 3), np.uint8)
    layers: list[str] = []
    total = 0
    for ci in (int(v) for v in np.unique(idx)):
        mask = np.where(idx == ci, np.uint8(255), np.uint8(0))
        if not mask.any():
            continue
        mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, kernel)  # 填小针孔、减碎块
        # RETR_CCOMP:外轮廓 + 内孔两级;全部塞进一个 path 用 evenodd 自动镂空。
        cnts, _ = cv2.findContours(mask, cv2.RETR_CCOMP, cv2.CHAIN_APPROX_SIMPLE)
        subpaths: list[str] = []
        for c in cnts:
            if cv2.contourArea(c) < min_area:
                continue
            eps = max(0.6, 0.006 * cv2.arcLength(c, True))  # 简化:越大越平滑、点越少
            pts = cv2.approxPolyDP(c, eps, True).reshape(-1, 2)
            if len(pts) < 3:
                continue
            d = "M" + " ".join(f"{round(px * sx, 1):g},{round(py * sy, 1):g}" for px, py in pts) + "Z"
            subpaths.append(d)
            total += 1
        if subpaths:
            r, g, b = palette[ci * 3:ci * 3 + 3]
            layers.append(
                f'<path d="{" ".join(subpaths)}" fill="#{r:02x}{g:02x}{b:02x}" fill-rule="evenodd"/>'
            )

    if not layers:
        raise ValueError("追踪未得到任何路径")  # 交给 to_svg 转成干净的 400
    body = "".join(layers)
    svg = (
        f'<svg xmlns="http://www.w3.org/2000/svg" '
        f'viewBox="0 0 {orig_w} {orig_h}" width="{orig_w}" height="{orig_h}">{body}</svg>'
    )
    return svg, total
