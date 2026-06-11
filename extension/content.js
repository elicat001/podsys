// PODStudio 采集助手 — content script(Temu 商品页)
// 职责:在页面内注入「全部采集 / 单图采集」按钮,扫描商品高清图,交给 background 带登录态上传到素材库。
//
// 合规边界(硬要求):抓取并复用他人商品图有平台反爬条款与著作权双重风险。本脚本仅可用于
// 「已获授权 / 自有内容」场景。URL 升级规则与 backend/app/services/collectors.py 保持一致。

(function () {
  // ---- 高清升级(与后端 collectors.py 等价)----
  const AMAZON_SIZE_SEG = /\.(?:_[A-Z0-9,]+)+_(?=\.)/g;
  const ETSY_SIZE_SEG = /il_\d+x[\dN]+/gi;
  const SCALING_QUERY_KEYS = new Set([
    "imageview2", "imageview", "width", "w", "height", "h",
    "quality", "q", "x-oss-process", "imagemogr2", "thumbnail", "format",
  ]);
  function detectPlatform(url) {
    let host = "";
    try { host = new URL(url).hostname.toLowerCase(); } catch (e) { host = (url || "").toLowerCase(); }
    if (/(^|\.)amazon\.|media-amazon\.|ssl-images-amazon\./.test(host)) return "amazon";
    if (/etsy\.|etsystatic\./.test(host)) return "etsy";
    if (/temu\.|kwcdn\.|temucdn\./.test(host)) return "temu";
    if (/tiktok\.|tiktokcdn|ttwstatic\.|ibyteimg\./.test(host)) return "tiktok";
    return "unknown";
  }
  function stripScalingQuery(url) {
    try {
      const u = new URL(url); const keep = [];
      for (const [k, v] of u.searchParams.entries()) if (!SCALING_QUERY_KEYS.has(k.toLowerCase())) keep.push([k, v]);
      u.search = ""; for (const [k, v] of keep) u.searchParams.append(k, v);
      return u.toString();
    } catch (e) { return url; }
  }
  function upgradeToHiRes(url, platform) {
    if (!url) return url;
    platform = platform || detectPlatform(url);
    if (platform === "amazon") { let out = url, prev = null; while (out !== prev) { prev = out; out = out.replace(AMAZON_SIZE_SEG, ""); } return out; }
    if (platform === "etsy") return url.replace(ETSY_SIZE_SEG, "il_fullxfull");
    if (platform === "temu" || platform === "tiktok") return stripScalingQuery(url);
    return url;
  }

  // ---- 扫描商品图(只收 temu CDN、足够大的)----
  function isProductImg(img) {
    const src = img.currentSrc || img.src || "";
    if (!src || src.startsWith("data:")) return false;
    if (detectPlatform(src) !== "temu") return false;
    const w = img.naturalWidth || img.clientWidth || 0;
    return w >= 120;
  }
  function gather() {
    const urls = new Set();
    document.querySelectorAll("img").forEach((img) => {
      if (isProductImg(img)) urls.add(upgradeToHiRes(img.currentSrc || img.src));
    });
    return [...urls].slice(0, 80); // 一次最多 80 张,防滥采
  }

  // ---- 上传(交给 background,带登录态、绕过 CORS)----
  async function collect(urls, statusEl) {
    urls = (urls || []).filter(Boolean);
    if (!urls.length) { setStatus(statusEl, "没扫到商品图"); return; }
    const { pod_token } = await chrome.storage.local.get("pod_token");
    if (!pod_token) { setStatus(statusEl, "⚠ 请先在 PODStudio 网站登录(打开站点登录后再来)"); return; }
    setStatus(statusEl, `采集中… 共 ${urls.length} 张`);
    chrome.runtime.sendMessage({ type: "COLLECT", urls }, (resp) => {
      if (chrome.runtime.lastError) { setStatus(statusEl, "出错: " + chrome.runtime.lastError.message); return; }
      if (resp && resp.ok) setStatus(statusEl, `✓ 已采集 ${resp.count}/${urls.length} 张到素材库` + (resp.failed ? `(${resp.failed} 张失败)` : ""));
      else setStatus(statusEl, "失败: " + ((resp && resp.error) || "未知"));
    });
  }
  function setStatus(el, t) { if (el) el.textContent = t; }

  // ---- 注入面板 ----
  let panelStatus = null;
  function makePanel() {
    if (document.getElementById("pod-panel")) return;
    const p = document.createElement("div");
    p.id = "pod-panel";
    p.innerHTML =
      '<div class="pod-hd"><span>🦏 PODStudio 采集</span><span class="pod-x" title="隐藏">×</span></div>' +
      '<button class="pod-all">全部采集本页 (<b class="pod-n">0</b>)</button>' +
      '<div class="pod-st">悬停商品图可单张采集</div>' +
      '<a class="pod-link" target="_blank">打开素材库 →</a>';
    document.body.appendChild(p);
    panelStatus = p.querySelector(".pod-st");
    p.querySelector(".pod-all").onclick = () => collect(gather(), panelStatus);
    p.querySelector(".pod-x").onclick = () => { p.style.display = "none"; };
    chrome.storage.local.get("pod_api", ({ pod_api }) => {
      p.querySelector(".pod-link").href = (pod_api || "https://pod.kejing.online") + "/app/space";
    });
    // 计数:定时刷新本页可采集图数量(Temu 是 SPA,滚动会加载更多)
    const upd = () => { const n = p.querySelector(".pod-n"); if (n) n.textContent = gather().length; };
    upd(); setInterval(upd, 2000);
  }

  // ---- 单图悬停按钮 ----
  function makeHoverBtn() {
    const b = document.createElement("button");
    b.id = "pod-hover-btn"; b.textContent = "采集此图"; b.style.display = "none";
    document.body.appendChild(b);
    b.onclick = (e) => { e.stopPropagation(); if (b.dataset.url) collect([b.dataset.url], panelStatus); };
    let hideT = null;
    document.addEventListener("mouseover", (e) => {
      const img = e.target && e.target.tagName === "IMG" ? e.target : null;
      if (img && isProductImg(img)) {
        const r = img.getBoundingClientRect();
        b.style.left = window.scrollX + r.right - 78 + "px";
        b.style.top = window.scrollY + r.top + 6 + "px";
        b.dataset.url = upgradeToHiRes(img.currentSrc || img.src);
        b.style.display = "block";
        if (hideT) { clearTimeout(hideT); hideT = null; }
      }
    });
    document.addEventListener("mouseout", (e) => {
      if (e.target === b) return;
      hideT = setTimeout(() => { b.style.display = "none"; }, 400);
    });
  }

  function boot() {
    if (!document.body) { setTimeout(boot, 500); return; }
    makePanel();
    makeHoverBtn();
  }
  boot();
})();
