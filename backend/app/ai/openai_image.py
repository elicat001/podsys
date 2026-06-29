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

def _is_capacity_error(exc: Exception | None) -> bool:
    """异常是否=作图中转站【并发/容量满】(应回退并发):503 无可用账号 / 429 限流 / overloaded。
    其它(超时/连接/坏图/鉴权)不是容量问题,不据此回退并发。"""
    if exc is None:
        return False
    code = getattr(exc, "status_code", None)
    if code in (429, 503):
        return True
    s = str(exc).lower()
    return any(k in s for k in (
        "no available compatible accounts", "overloaded", "rate limit",
        " 429", " 503", "code: 429", "code: 503",
    ))


class _AdaptiveLimiter:
    """gpt-image 中转站【自适应并发限流】(进程级单例,所有作图共用)。
    固定信号量要么压太死、要么挤爆中转站(503「无可用账号」)。这里按中转站【实际可用并发】动态调:
    - acquire():排队等位(in_flight < limit 才放行),这是队列;
    - report(成功):说明中转站还有余量 → limit += 1(往上爬,逼近真实可用并发),封顶 max;
    - report(容量满 503/429):中转站满了 → limit -= 1(往下退,别再挤),封底 1;该请求随后等一个在飞的完成再重试;
    - report(其它错):非容量问题 → limit 不变。
    既不挤爆、又用满中转站可用并发。threads 池下=全局;acquire↔report 必须一一配平(防 in_flight 泄漏)。"""

    def __init__(self, start: int, max_limit: int) -> None:
        self._max = max(1, int(max_limit))
        self._limit = max(1, min(int(start), self._max))
        self._in_flight = 0
        self._cv = threading.Condition()

    def acquire(self) -> None:
        with self._cv:
            while self._in_flight >= self._limit:
                self._cv.wait()
            self._in_flight += 1

    def report(self, success: bool, exc: Exception | None = None) -> None:
        with self._cv:
            self._in_flight -= 1
            if success:
                self._limit = min(self._max, self._limit + 1)
            elif _is_capacity_error(exc):
                self._limit = max(1, self._limit - 1)
            self._cv.notify_all()

    def run(self, fn):
        """acquire → 调 fn → report(成败)。bulk 作图(单次调用)用它一站式管并发。"""
        self.acquire()
        try:
            out = fn()
        except Exception as exc:  # noqa: BLE001 — 失败也要 report,保证配平 + 反馈容量
            self.report(False, exc)
            raise
        self.report(True)
        return out

    def snapshot(self) -> tuple[int, int]:
        with self._cv:
            return self._limit, self._in_flight


# 全局自适应限流器:起始=openai_max_concurrency(保守),上限=openai_adaptive_max,下限=1。
# 母帧(tasks._mufra_with_backoff)自己 acquire/report(预算拿位后才起算);bulk 作图走 _API_GATE.run(...)。
_API_GATE = _AdaptiveLimiter(start=settings.openai_max_concurrency, max_limit=settings.openai_adaptive_max)


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
        resp = _API_GATE.run(lambda: self.client.images.generate(  # 自适应限流:按中转站可用并发动态调
            model=self.model, prompt=prompt, n=1,
            size=self._size(size), quality=quality, background=background,
        ))
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
        # use_gate=False:调用方(母帧 _mufra_with_backoff)自己 acquire/report 全局限流器(预算拿位后才起算),
        # 这里不再叠一层(否则双重 acquire / in_flight 计数错)。其余作图 use_gate=True → 自适应限流器一站式管。
        if use_gate:
            resp = _API_GATE.run(lambda: client.images.edit(**kwargs))
        else:
            resp = client.images.edit(**kwargs)
        return self._decode(resp)

    # ---- 多图合成(产品照 + 设计 → 真实感套图)----
    def compose(self, images: list[Image.Image], prompt: str, size: str = "auto",
                input_fidelity: str = "high") -> Image.Image:
        """多张输入图一起送 gpt-image edit(如 [产品照, 新设计]):模型按 prompt 把它们融合。
        input_fidelity=high 尽量保留输入细节(设计忠实)。用于商品套图『把设计真实地印到产品上』。"""
        files = [_file_tuple(_cap_for_edit(im), f"img{i}") for i, im in enumerate(images)]
        resp = _API_GATE.run(lambda: self.client.images.edit(  # 自适应限流
            model=self.model, image=files, prompt=prompt,
            n=1, size=self._size(size), input_fidelity=input_fidelity))
        return self._decode(resp)

    # ---- 抠图 / 去背景 ----
    _DEFAULT_BG_PROMPT = ("Remove the background completely. Keep only the main subject / "
                          "design intact with clean edges. Output a transparent background.")

    def remove_background(self, image: Image.Image, prompt: str | None = None) -> Image.Image:
        """去背景 → 透明底。prompt 留空用通用去背景词;调用方可传更强的『主体识别+扣出』提示
        (见 ai.matting.build_subject_prompt),prompt 工程留在 matting 域,客户端只负责执行。"""
        return self.edit(image, prompt=prompt or self._DEFAULT_BG_PROMPT, background="transparent")
