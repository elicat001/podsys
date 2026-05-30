// PODStudio 采集助手 — content script
// 职责:在商品页扫描出主图,把缩略图 URL 升级为原图(高清),上报给 popup / background。
//
// 合规边界(硬要求):抓取并复用他人商品图有平台反爬条款与著作权双重风险。
// 本脚本仅提供 URL 字符串变换与页面扫描工具,**仅可用于「已获授权 / 自有内容」场景**。
// URL 升级规则与后端 backend/app/services/collectors.py 保持一致。

(function () {
  // amazon 文件名尺寸段:._AC_SX466_ / ._SL1500_ / ._SY400,300_(段内可含内部下划线,扩展名之前,可能连续多段)
  const AMAZON_SIZE_SEG = /\.(?:_[A-Z0-9,]+)+_(?=\.)/g;
  // etsy 尺寸段:il_340x270 / il_600x600 / il_794xN
  const ETSY_SIZE_SEG = /il_\d+x[\dN]+/gi;
  // temu / tiktok 需剔除的缩放/处理 query key(小写比较)
  const SCALING_QUERY_KEYS = new Set([
    "imageview2", "imageview", "width", "w", "height", "h",
    "quality", "q", "x-oss-process", "imagemogr2", "thumbnail", "format",
  ]);

  function detectPlatform(url) {
    let host = "";
    try {
      host = new URL(url).hostname.toLowerCase();
    } catch (e) {
      host = (url || "").toLowerCase();
    }
    if (/(^|\.)amazon\.|media-amazon\.|ssl-images-amazon\./.test(host)) return "amazon";
    if (/etsy\.|etsystatic\./.test(host)) return "etsy";
    if (/temu\.|kwcdn\./.test(host)) return "temu";
    if (/tiktok\.|tiktokcdn|ttwstatic\.|ibyteimg\./.test(host)) return "tiktok";
    return "unknown";
  }

  function stripScalingQuery(url) {
    try {
      const u = new URL(url);
      const keep = [];
      for (const [k, v] of u.searchParams.entries()) {
        if (!SCALING_QUERY_KEYS.has(k.toLowerCase())) keep.push([k, v]);
      }
      u.search = "";
      for (const [k, v] of keep) u.searchParams.append(k, v);
      return u.toString();
    } catch (e) {
      // 无法解析则保守地原样返回
      return url;
    }
  }

  function upgradeToHiRes(url, platform) {
    if (!url) return url;
    platform = platform || detectPlatform(url);
    if (platform === "amazon") {
      let out = url, prev = null;
      while (out !== prev) { prev = out; out = out.replace(AMAZON_SIZE_SEG, ""); }
      return out;
    }
    if (platform === "etsy") return url.replace(ETSY_SIZE_SEG, "il_fullxfull");
    if (platform === "temu" || platform === "tiktok") return stripScalingQuery(url);
    return url; // unknown:原样返回
  }

  function collectImages() {
    const urls = new Set();
    document.querySelectorAll("img").forEach((img) => {
      const src = img.currentSrc || img.src;
      if (!src || src.startsWith("data:")) return;
      // 只收够大的主图:优先用已加载的 naturalWidth
      const w = img.naturalWidth || img.width || 0;
      if (w >= 500) urls.add(upgradeToHiRes(src));
    });
    return [...urls];
  }

  // 保持消息契约不变:popup 发 COLLECT_IMAGES,这里回 { images, page }
  chrome.runtime.onMessage.addListener((msg, _sender, sendResponse) => {
    if (msg && msg.type === "COLLECT_IMAGES") {
      sendResponse({ images: collectImages(), page: location.href });
    }
    return true;
  });

  // 暴露给单测/调试(浏览器内无副作用)
  if (typeof window !== "undefined") {
    window.__podCollectors = { detectPlatform, upgradeToHiRes, collectImages };
  }
})();
