"""OpenAI gpt-image-1 ("image2") client.

Powers four tasks through one model:
  - 抠图/去背景      -> images.edit + background="transparent"
  - 文生图           -> images.generate
  - 图生图/改图      -> images.edit (image + prompt)
  - 换装/换背景      -> images.edit (image [+ mask] + prompt)

gpt-image-1 returns base64 PNG; some OpenAI-compatible 中转网关 instead return a url
(esp. for edit / background=transparent). `_decode` handles both (b64 优先,退而取 url),
两者都没有就抛可定位的错误,而不是 base64.b64decode(None) 那种看不懂的 TypeError。
NOTE: gpt-image-1 is NOT a super-resolution model — do not use it for 无损放大
of print files (it regenerates pixels and will distort the design). Keep a real
upscaler (Pillow/Real-ESRGAN) for production files. See README.
"""
from __future__ import annotations

import base64
import io
import threading

from PIL import Image

from ..config import settings

# gpt-image-1 accepts these sizes (plus "auto")
VALID_SIZES = {"1024x1024", "1536x1024", "1024x1536", "auto"}

# 下载网关返回的图片 url 时的大小上限(防 OOM:超大响应不收)。
_MAX_IMG_BYTES = 40 * 1024 * 1024

# 全局限流:同时在飞的 gpt-image 网关调用数上限。本网关并发跑多张 gpt-image 会让每张都被拖过
# 单次超时(250s)→ 整批 APITimeoutError(图裂变 4 路并发实测全超时)。这里把"同时在飞"的调用
# 压到 settings.openai_max_concurrency。进程级:threads 池下=全局;prefork 下=每进程。
_API_GATE = threading.Semaphore(max(1, settings.openai_max_concurrency))

# 视频【场景母帧】专用队列(与 _API_GATE 分开):母帧自己排队、不与批量作图(裂变/套图/文生图)抢,
# 也不把作图中转站【自己人挤爆】(实证 503「无可用账号」根因)。调用方(tasks._mufra_with_backoff)持锁期间
# 做退避重试,并让"排队等位时间"不计入母帧预算 → 多任务也不雪崩。并发由 video_mufra_concurrency 控(默认 1=串行)。
_MUFRA_GATE = threading.Semaphore(max(1, settings.video_mufra_concurrency))


def _png_bytes(img: Image.Image) -> bytes:
    buf = io.BytesIO()
    img.convert("RGBA").save(buf, format="PNG")
    return buf.getvalue()


def _jpeg_bytes(img: Image.Image, quality: int = 92) -> bytes:
    buf = io.BytesIO()
    img.convert("RGB").save(buf, format="JPEG", quality=quality)
    return buf.getvalue()


def _has_alpha(img: Image.Image) -> bool:
    """图是否含真实透明像素 —— 决定上传编码。不透明 → JPEG(体积小 5~10×,大幅降网关上传写超时
    WriteTimeout,直接减少『母帧/改图 失败退回原图』);含透明 → 必须 PNG(否则 alpha 丢失,改变模型看到的内容)。"""
    if img.mode in ("RGBA", "LA", "PA"):
        try:
            return img.getchannel("A").getextrema()[0] < 255
        except Exception:  # noqa: BLE001 — 取不到 alpha 极值就保守按"有透明"走 PNG
            return True
    if img.mode == "P":
        return "transparency" in img.info
    return False


def _cap_for_edit(img: Image.Image, max_side: int = 1024) -> Image.Image:
    """gpt-image edit/生成输出最大 1024×1536,发更大的原图纯浪费上传+网关处理时间
    (实测大图比小图慢数倍)。把最长边缩到 ≤ max_side(Lanczos),显著提速,产出质量不受影响。"""
    m = max(img.size)
    if m <= max_side:
        return img
    s = max_side / m
    return img.resize((max(1, round(img.width * s)), max(1, round(img.height * s))), Image.LANCZOS)


def _file_tuple(img: Image.Image, name: str = "image", force_png: bool = False):
    """上传文件元组(网关稳固):不透明图 → JPEG(体积小 5~10× → 降上传写超时);含透明 / 强制 → PNG(保 alpha)。
    gpt-image edit/compose 的输入只作参考(输出像素重生),JPEG q92 对结果无实质影响,纯粹为更稳更快上网关。
    mask 必须 force_png=True(它就是 alpha 区域,绝不能 JPEG)。"""
    if force_png or _has_alpha(img):
        return (f"{name}.png", _png_bytes(img), "image/png")
    return (f"{name}.jpg", _jpeg_bytes(img), "image/jpeg")


_SDK_CACHE: dict = {}


def _get_sdk_client(api_key: str, base_url: str | None, timeout: float, max_retries: int = 2):
    """复用 SDK 客户端(P1-2:避免每次调用重建 httpx 连接池;按凭证缓存)。

    max_retries:SDK 自带指数退避重试,自动覆盖网关瞬时抖动(502/超时/连接错误),
    无需在业务层手写重试循环。
    """
    key = (api_key, base_url, timeout, max_retries)
    client = _SDK_CACHE.get(key)
    if client is None:
        from openai import OpenAI  # lazy import
        client = OpenAI(api_key=api_key, base_url=base_url, timeout=timeout, max_retries=max_retries)
        _SDK_CACHE[key] = client
    return client


class OpenAIImageClient:
    def __init__(self) -> None:
        if not settings.openai_api_key:
            raise RuntimeError("POD_OPENAI_API_KEY 未配置,无法调用 gpt-image")
        self.model = settings.openai_image_model
        self.client = _get_sdk_client(
            settings.openai_api_key, settings.openai_base_url or None,
            settings.openai_timeout, settings.openai_max_retries,
        )

    def _decode(self, resp) -> Image.Image:
        """把 gpt-image 响应解码成 RGBA 图,对网关返回形态差异做加固:
        ① 有 b64_json → 直接 base64 解码(gpt-image-1 官方行为);
        ② 没 b64 但有 url → 下载该图(部分 OpenAI 兼容中转对 edit / background=transparent 返 url 而非 b64,
           历史上一键抠图智能运行因此抛 `base64.b64decode(None)` 的 TypeError);
        ③ 两者都没有(或 data 为空)→ 抛**可定位**的错误,交由上层退点 + 502,而不是看不懂的 TypeError。"""
        data = getattr(resp, "data", None) or []
        item = data[0] if data else None
        b64 = getattr(item, "b64_json", None) if item is not None else None
        if b64:
            return Image.open(io.BytesIO(base64.b64decode(b64))).convert("RGBA")
        url = getattr(item, "url", None) if item is not None else None
        if url:
            return self._fetch_image(url)
        raise RuntimeError("图片网关未返回图像数据(b64_json 与 url 均为空);"
                           "可能是网关超时、内容被拦截或返回了非标准响应")

    @staticmethod
    def _fetch_image(url: str) -> Image.Image:
        """下载网关返回的图片 url → RGBA。带超时 + 大小上限(防 OOM:超大响应不收)。"""
        import httpx  # 惰性(本就是 openai SDK 传递依赖,已在 requirements 显式登记)
        with httpx.Client(timeout=settings.openai_timeout, follow_redirects=True) as c:
            r = c.get(url)
            r.raise_for_status()
        if len(r.content) > _MAX_IMG_BYTES:
            raise ValueError("网关返回的图片过大,超出上限")
        return Image.open(io.BytesIO(r.content)).convert("RGBA")

    @staticmethod
    def _size(size: str) -> str:
        return size if size in VALID_SIZES else "auto"

    # ---- 文生图 ----
    def generate(self, prompt: str, size: str = "1024x1024",
                 quality: str = "auto", background: str = "auto") -> Image.Image:
        with _API_GATE:  # 限并发,避免整批超时
            resp = self.client.images.generate(
                model=self.model, prompt=prompt, n=1,
                size=self._size(size), quality=quality, background=background,
            )
        return self._decode(resp)

    # ---- 图生图 / 改图 / 换装换背景 ----
    def edit(self, image: Image.Image, prompt: str,
             mask: Image.Image | None = None, size: str = "auto",
             background: str = "auto",
             timeout: float | None = None, max_retries: int | None = None,
             use_gate: bool = True) -> Image.Image:
        image = _cap_for_edit(image)  # 缩到 ≤1024:省上传+网关耗时(输出本就≤1024,质量不变)
        kwargs = dict(
            model=self.model,
            image=_file_tuple(image),
            prompt=prompt, n=1, size=self._size(size), background=background,
        )
        if mask is not None:
            # mask 必须与图同尺寸(图缩了 mask 也要跟着缩);force_png:mask=alpha 区域,绝不能 JPEG
            kwargs["mask"] = _file_tuple(mask.resize(image.size, Image.LANCZOS), "mask", force_png=True)
        # 调用方可覆盖单次超时/重试(如视频母帧:给慢中转更长的单次出图窗口 + 不做超时翻倍重试)。
        client = self.client
        if timeout is not None or max_retries is not None:
            opts = {}
            if timeout is not None:
                opts["timeout"] = timeout
            if max_retries is not None:
                opts["max_retries"] = max_retries
            client = self.client.with_options(**opts)
        # use_gate=False:调用方(母帧)自己用 _MUFRA_GATE 排队管并发,这里不再叠 _API_GATE(否则双重排队/串错池)。
        if use_gate:
            with _API_GATE:  # 限并发,避免整批超时(图裂变 N 路并发→网关压不住→全超时)
                resp = client.images.edit(**kwargs)
        else:
            resp = client.images.edit(**kwargs)
        return self._decode(resp)

    # ---- 多图合成(产品照 + 设计 → 真实感套图)----
    def compose(self, images: list[Image.Image], prompt: str, size: str = "auto",
                input_fidelity: str = "high") -> Image.Image:
        """多张输入图一起送 gpt-image edit(如 [产品照, 新设计]):模型按 prompt 把它们融合。
        input_fidelity=high 尽量保留输入细节(设计忠实)。用于商品套图『把设计真实地印到产品上』。"""
        files = [_file_tuple(_cap_for_edit(im), f"img{i}") for i, im in enumerate(images)]
        with _API_GATE:  # 限并发,避免整批超时
            resp = self.client.images.edit(model=self.model, image=files, prompt=prompt,
                                           n=1, size=self._size(size), input_fidelity=input_fidelity)
        return self._decode(resp)

    # ---- 抠图 / 去背景 ----
    _DEFAULT_BG_PROMPT = ("Remove the background completely. Keep only the main subject / "
                          "design intact with clean edges. Output a transparent background.")

    def remove_background(self, image: Image.Image, prompt: str | None = None) -> Image.Image:
        """去背景 → 透明底。prompt 留空用通用去背景词;调用方可传更强的『主体识别+扣出』提示
        (见 ai.matting.build_subject_prompt),prompt 工程留在 matting 域,客户端只负责执行。"""
        return self.edit(image, prompt=prompt or self._DEFAULT_BG_PROMPT, background="transparent")
