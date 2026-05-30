// PODStudio 采集助手 — content script (MVP 骨架)
// 职责：在商品页扫描出高清图 URL，上报给 background / popup。
// 注意：抓取与复用他人图片有平台合规与著作权风险，仅用于授权场景。

(function () {
  function upgradeToHiRes(url) {
    // 各平台缩略图 → 原图的常见替换规则（示例，需按平台实际规则补全）
    return url
      .replace(/_\d+x\d+(\.\w+)/, "$1")        // amazon 风格 _SX300_
      .replace(/(\?|&)(w|width|imageView2)=[^&]+/g, "");
  }

  function collectImages() {
    const urls = new Set();
    document.querySelectorAll("img").forEach((img) => {
      const src = img.currentSrc || img.src;
      if (!src || src.startsWith("data:")) return;
      const w = img.naturalWidth || img.width || 0;
      if (w >= 500) urls.add(upgradeToHiRes(src)); // 只要够大的主图
    });
    return [...urls];
  }

  chrome.runtime.onMessage.addListener((msg, _sender, sendResponse) => {
    if (msg?.type === "COLLECT_IMAGES") {
      sendResponse({ images: collectImages(), page: location.href });
    }
    return true;
  });
})();
