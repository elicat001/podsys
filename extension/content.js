// PODStudio 采集助手 — content script(Temu 商品页)
// 职责:在页面内注入「全部采集 / 单图采集」按钮,扫描商品卡(图+标题+价格+评分+来源链接),
// 交给 background 带登录态回传到采集箱(/api/collect-tasks/ingest,只暂存,选择同步才入库)。
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

  // ---- 商品图判定(只收 temu CDN、足够大的)----
  function isProductImg(img) {
    const src = img.currentSrc || img.src || "";
    if (!src || src.startsWith("data:")) return false;
    if (detectPlatform(src) !== "temu") return false;
    const w = img.naturalWidth || img.clientWidth || 0;
    return w >= 120;
  }

  // ---- 从商品图反查整张卡片,抽取标题/价格/评分/链接(启发式,不依赖易变 CSS 类名)----
  function cardOf(img) {
    let el = img.parentElement;
    for (let i = 0; i < 6 && el; i++) {
      // 含商品链接、且文本量适中的祖先,视作卡片容器
      if (el.querySelector && el.querySelector("a[href]")) return el;
      el = el.parentElement;
    }
    return img.closest("a") || img.parentElement || img;
  }
  function rootText(root) {
    try { return (root.innerText || root.textContent || "").replace(/\s+/g, " ").trim(); }
    catch (e) { return ""; }
  }
  function pickPrice(root) {
    const m = rootText(root).match(/(?:US\s*)?[$￥¥€£]\s?\d[\d.,]*/);
    return m ? m[0].replace(/\s+/g, "") : "";
  }
  function pickRating(root) {
    const star = root.querySelector('[aria-label*="star" i],[aria-label*="rating" i],[aria-label*="评分"]');
    if (star) { const m = (star.getAttribute("aria-label") || "").match(/([0-5](?:\.\d)?)/); if (m) return m[1]; }
    const m = rootText(root).match(/\b([0-5]\.\d)\b/);
    return m ? m[1] : "";
  }
  function pickTitle(img, root) {
    if (img.alt && img.alt.trim().length > 4) return img.alt.trim().slice(0, 240);
    const cand = root.querySelector("h1,h2,h3,h4,[title]");
    if (cand) {
      const t = (cand.getAttribute("title") || cand.innerText || "").trim();
      if (t.length > 4) return t.slice(0, 240);
    }
    return "";
  }
  function pickLink(img, root) {
    const a = root.querySelector("a[href]") || img.closest("a");
    if (a) { try { return new URL(a.getAttribute("href"), location.href).toString(); } catch (e) { /* ignore */ } }
    return location.href;
  }
  function cardFromImg(img) {
    const raw = img.currentSrc || img.src;
    const plat = detectPlatform(raw);
    const root = cardOf(img);
    return {
      url: raw,
      hires_url: upgradeToHiRes(raw, plat),
      platform: plat,
      title: pickTitle(img, root),
      price: pickPrice(root),
      rating: pickRating(root),
      source_url: pickLink(img, root),
    };
  }
  function gatherCards() {
    const seen = new Set();
    const cards = [];
    document.querySelectorAll("img").forEach((img) => {
      if (!isProductImg(img)) return;
      const c = cardFromImg(img);
      if (seen.has(c.hires_url)) return;
      seen.add(c.hires_url);
      cards.push(c);
    });
    return cards.slice(0, 80); // 一次最多 80 张,防滥采
  }

  // ---- 回传(交给 background,带登录态、绕过 CORS)----
  async function collect(cards, statusEl) {
    cards = (cards || []).filter((c) => c && c.url);
    if (!cards.length) { setStatus(statusEl, "没扫到商品"); return; }
    const { pod_token } = await chrome.storage.local.get("pod_token");
    if (!pod_token) { setStatus(statusEl, "⚠ 请先在 PODStudio 网站登录(打开站点登录后再来)"); return; }
    setStatus(statusEl, `采集中… 共 ${cards.length} 个商品`);
    chrome.runtime.sendMessage({ type: "COLLECT", cards }, (resp) => {
      if (chrome.runtime.lastError) { setStatus(statusEl, "出错: " + chrome.runtime.lastError.message); return; }
      if (resp && resp.ok) setStatus(statusEl, `✓ 已采集 ${resp.count} 个到采集箱,去采集页勾选同步`);
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
      '<div class="pod-st">悬停商品图可单个采集</div>' +
      '<a class="pod-link" target="_blank">打开采集箱 →</a>';
    document.body.appendChild(p);
    panelStatus = p.querySelector(".pod-st");
    p.querySelector(".pod-all").onclick = () => collect(gatherCards(), panelStatus);
    p.querySelector(".pod-x").onclick = () => { p.style.display = "none"; };
    chrome.storage.local.get("pod_api", ({ pod_api }) => {
      p.querySelector(".pod-link").href = (pod_api || "https://pod.kejing.online") + "/app/find/collect";
    });
    // 计数:定时刷新本页可采集商品数(Temu 是 SPA,滚动会加载更多)
    const upd = () => { const n = p.querySelector(".pod-n"); if (n) n.textContent = gatherCards().length; };
    upd(); setInterval(upd, 2500);
  }

  // ---- 单图悬停按钮 ----
  function makeHoverBtn() {
    const b = document.createElement("button");
    b.id = "pod-hover-btn"; b.textContent = "采集此商品"; b.style.display = "none";
    document.body.appendChild(b);
    let curImg = null;
    b.onclick = (e) => { e.stopPropagation(); if (curImg) collect([cardFromImg(curImg)], panelStatus); };
    let hideT = null;
    document.addEventListener("mouseover", (e) => {
      const img = e.target && e.target.tagName === "IMG" ? e.target : null;
      if (img && isProductImg(img)) {
        const r = img.getBoundingClientRect();
        b.style.left = window.scrollX + r.right - 96 + "px";
        b.style.top = window.scrollY + r.top + 6 + "px";
        curImg = img;
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
