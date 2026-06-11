// PODStudio 采集助手 — content script(Temu / Amazon / Shopee / MercadoLibre / TikTok Shop 商品页)
// 职责:注入一个可拖动的悬浮球(默认右上角),点击展开采集面板;扫描商品卡(图+标题+价格+评分+链接),
// 交给 background 带登录态回传到采集箱(/api/collect-tasks/ingest,只暂存,选择同步才入库)。
//
// 合规边界(硬要求):抓取并复用他人商品图有平台反爬条款与著作权双重风险。本脚本仅可用于
// 「已获授权 / 自有内容」场景。URL 升级规则与 backend/app/services/collectors.py 保持一致。

(function () {
  const DEFAULT_API = "https://pod.kejing.online";

  // ---- 高清升级(与后端 collectors.py 等价)----
  const AMAZON_SIZE_SEG = /\.(?:_[A-Z0-9,]+)+_(?=\.)/g;
  const ETSY_SIZE_SEG = /il_\d+x[\dN]+/gi;
  const SCALING_QUERY_KEYS = new Set([
    "imageview2", "imageview", "width", "w", "height", "h",
    "quality", "q", "x-oss-process", "imagemogr2", "thumbnail", "format",
  ]);
  const SUPPORTED = new Set(["temu", "amazon", "shopee", "mercadolibre", "tiktok", "etsy"]);
  function detectPlatform(url) {
    let host = "";
    try { host = new URL(url).hostname.toLowerCase(); } catch (e) { host = (url || "").toLowerCase(); }
    if (/(^|\.)amazon\.|media-amazon\.|ssl-images-amazon\./.test(host)) return "amazon";
    if (/etsy\.|etsystatic\./.test(host)) return "etsy";
    if (/temu\.|kwcdn\.|temucdn\./.test(host)) return "temu";
    if (/shopee\.|susercontent\.|shopeemobile\./.test(host)) return "shopee";
    if (/mercadoli(?:bre|vre)\.|mlstatic\./.test(host)) return "mercadolibre";
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
    // shopee:去缩放 query + 去缩略图后缀 _tn;mercadolibre:把 D_NQ_NP_ 提到 2X 高清
    if (platform === "shopee") return stripScalingQuery(url).replace(/_tn(?=$|\?)/i, "");
    if (platform === "mercadolibre") return url.replace(/D_NQ_NP_(?!2X_)/i, "D_NQ_NP_2X_");
    if (platform === "temu" || platform === "tiktok") return stripScalingQuery(url);
    return url;
  }

  // ---- 商品图判定(支持平台的 CDN 大图)----
  function isProductImg(img) {
    const src = img.currentSrc || img.src || "";
    if (!src || src.startsWith("data:")) return false;
    if (!SUPPORTED.has(detectPlatform(src))) return false;
    const w = img.naturalWidth || img.clientWidth || 0;
    return w >= 140;
  }

  // ---- 从商品图反查整张卡片,抽取标题/价格/评分/链接(启发式,不依赖易变 CSS 类名)----
  function cardOf(img) {
    let el = img.parentElement;
    for (let i = 0; i < 6 && el; i++) {
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
  let panelStatus = null;
  async function collect(cards) {
    cards = (cards || []).filter((c) => c && c.url);
    if (!cards.length) { setStatus("没扫到商品(确认页面已加载出商品图)"); return; }
    const { pod_token } = await chrome.storage.local.get("pod_token");
    if (!pod_token) { setStatus("⚠ 未登录:先在 PODStudio 网站登录,再回 Temu"); return; }
    setStatus(`采集中… 共 ${cards.length} 个商品`);
    chrome.runtime.sendMessage({ type: "COLLECT", cards }, (resp) => {
      if (chrome.runtime.lastError) { setStatus("出错: " + chrome.runtime.lastError.message); return; }
      if (resp && resp.ok) setStatus(`✓ 已采集 ${resp.count} 个到采集箱,去采集页勾选同步`);
      else setStatus("失败: " + ((resp && resp.error) || "未知"));
    });
  }
  function setStatus(t) { if (panelStatus) panelStatus.textContent = t; }

  // ============ 悬浮球 + 面板 UI ============
  let ballEl = null, panelEl = null;

  function positionPanel() {
    if (!panelEl || !ballEl) return;
    const r = ballEl.getBoundingClientRect();
    const pw = 240, ph = panelEl.offsetHeight || 200;
    // 默认贴在球的下方、右缘对齐;越界则翻到上方/收回视口内
    let left = r.right - pw;
    let top = r.bottom + 10;
    if (top + ph > window.innerHeight - 8) top = Math.max(8, r.top - ph - 10);
    left = Math.max(8, Math.min(window.innerWidth - pw - 8, left));
    panelEl.style.left = left + "px";
    panelEl.style.top = top + "px";
  }

  function togglePanel(show) {
    if (!panelEl) return;
    const willShow = show === undefined ? panelEl.style.display === "none" : show;
    if (willShow) { positionPanel(); panelEl.style.display = "block"; refreshTarget(); }
    else panelEl.style.display = "none";
  }

  function refreshCount() {
    const n = gatherCards().length;
    const pn = panelEl && panelEl.querySelector(".pod-n"); if (pn) pn.textContent = n;
    const bn = ballEl && ballEl.querySelector(".pod-badge"); if (bn) { bn.textContent = n; bn.style.display = n ? "block" : "none"; }
  }
  function refreshTarget() {
    chrome.storage.local.get(["pod_api", "pod_token"], ({ pod_api, pod_token }) => {
      const api = pod_api || DEFAULT_API;
      const t = panelEl && panelEl.querySelector(".pod-target");
      if (t) t.innerHTML = `目标:<b>${api}</b>` + (pod_token ? "" : " · <span style='color:#ff7a6b'>未登录</span>");
      const lk = panelEl && panelEl.querySelector(".pod-link");
      if (lk) lk.href = api + "/app/find/collect";
    });
  }

  function makeUI() {
    if (document.getElementById("pod-ball")) return;
    // 悬浮球
    ballEl = document.createElement("div");
    ballEl.id = "pod-ball";
    ballEl.title = "PODStudio 采集 — 点击展开,可拖动";
    ballEl.innerHTML = '🦏<span class="pod-badge">0</span>';
    document.body.appendChild(ballEl);
    // 面板(默认隐藏)
    panelEl = document.createElement("div");
    panelEl.id = "pod-panel";
    panelEl.style.display = "none";
    panelEl.innerHTML =
      '<div class="pod-hd"><span>🦏 PODStudio 采集</span><span class="pod-x" title="收起">×</span></div>' +
      '<button class="pod-all">全部采集本页 (<b class="pod-n">0</b>)</button>' +
      '<div class="pod-st">悬停商品图可单个采集</div>' +
      '<div class="pod-target muted"></div>' +
      '<a class="pod-link" target="_blank">打开采集箱 →</a>';
    document.body.appendChild(panelEl);
    panelStatus = panelEl.querySelector(".pod-st");
    panelEl.querySelector(".pod-all").onclick = () => collect(gatherCards());
    panelEl.querySelector(".pod-x").onclick = () => togglePanel(false);

    // 恢复球的位置(默认右上角)
    chrome.storage.local.get("pod_ball", ({ pod_ball }) => {
      if (pod_ball && typeof pod_ball.left === "number") {
        ballEl.style.left = pod_ball.left + "px";
        ballEl.style.top = pod_ball.top + "px";
        ballEl.style.right = "auto";
      }
    });

    // 拖动 + 点击(位移小=点击展开,位移大=拖动并记忆位置)
    let down = null, moved = false;
    ballEl.addEventListener("mousedown", (e) => {
      down = { x: e.clientX, y: e.clientY, left: ballEl.offsetLeft, top: ballEl.offsetTop };
      moved = false; e.preventDefault();
    });
    document.addEventListener("mousemove", (e) => {
      if (!down) return;
      const dx = e.clientX - down.x, dy = e.clientY - down.y;
      if (Math.abs(dx) + Math.abs(dy) > 4) moved = true;
      let nl = down.left + dx, nt = down.top + dy;
      nl = Math.max(0, Math.min(window.innerWidth - ballEl.offsetWidth, nl));
      nt = Math.max(0, Math.min(window.innerHeight - ballEl.offsetHeight, nt));
      ballEl.style.left = nl + "px"; ballEl.style.top = nt + "px"; ballEl.style.right = "auto";
      if (panelEl.style.display !== "none") positionPanel();
    });
    document.addEventListener("mouseup", () => {
      if (!down) return;
      if (!moved) togglePanel();
      else chrome.storage.local.set({ pod_ball: { left: ballEl.offsetLeft, top: ballEl.offsetTop } });
      down = null;
    });

    refreshCount(); refreshTarget();
    setInterval(refreshCount, 2500);
    window.addEventListener("resize", () => { if (panelEl.style.display !== "none") positionPanel(); });
  }

  // ---- 单图悬停按钮 ----
  function makeHoverBtn() {
    const b = document.createElement("button");
    b.id = "pod-hover-btn"; b.textContent = "采集此商品"; b.style.display = "none";
    document.body.appendChild(b);
    let curImg = null;
    b.onclick = (e) => { e.stopPropagation(); if (curImg) collect([cardFromImg(curImg)]); };
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
    makeUI();
    makeHoverBtn();
  }
  boot();
})();
