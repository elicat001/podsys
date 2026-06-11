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

  // ---- 够大判定(滤掉图标/小缩略):用内在宽或渲染框 ≥140px ----
  function bigEnough(el) {
    if ((el.naturalWidth || 0) >= 140) return true;
    const r = el.getBoundingClientRect();
    return r.width >= 140 && r.height >= 140;
  }

  // ---- 取图片 URL:支持 <img>、CSS background-image、<video poster>、srcset/data-src(懒加载)----
  function srcsetTop(ss) {
    if (!ss) return "";
    const parts = ss.split(",").map((s) => s.trim().split(/\s+/)[0]).filter(Boolean);
    return parts.length ? parts[parts.length - 1] : "";  // 末项通常分辨率最大
  }
  function bgUrl(el) {
    let bg = "";
    try { bg = getComputedStyle(el).backgroundImage || ""; } catch (e) { return ""; }
    const m = bg.match(/url\(["']?([^"')]+)["']?\)/);
    return m ? m[1] : "";
  }
  function imgUrlOf(el) {
    if (!el || el.nodeType !== 1) return "";
    const tag = el.tagName;
    if (tag === "IMG") return el.currentSrc || el.src || el.getAttribute("data-src") || srcsetTop(el.getAttribute("srcset")) || "";
    if (tag === "VIDEO") return el.getAttribute("poster") || "";
    if (tag === "SOURCE") return srcsetTop(el.getAttribute("srcset")) || el.getAttribute("src") || "";
    return bgUrl(el);
  }
  function isSupportedUrl(url) {
    return !!url && !url.startsWith("data:") && SUPPORTED.has(detectPlatform(url));
  }

  // ---- 过滤「非商品图」(品牌商标/导航雪碧图/UI 图标/横幅),与后端 _looks_like_junk 等价 ----
  const JUNK_URL_RE = /sprite|nav[-_]?sprite|\/logos?[-_/.]|[-_/]logo[-_/.]|favicon|\/icons?[-_/.]|[-_/]icon[-_/.]|\/badges?[-_/.]|placeholder|loading|grey-?pixel|transparent[-_]?pixel|\/captcha|x-locale|prime[-_]?(?:logo|badge)|\/g\/01\/|smile\.amazon/i;
  function isJunkUrl(url) {
    const u = (url || "").toLowerCase();
    if (!u) return true;
    if (JUNK_URL_RE.test(u)) return true;
    // 亚马逊商品主图在 /images/I/;/images/G/、/images/S/ 等是站点 UI/品牌/导航资源
    if (detectPlatform(u) === "amazon" && u.includes("/images/") && !u.includes("/images/i/")) return true;
    return false;
  }
  function inSiteChrome(el) {
    return !!(el && el.closest && el.closest("header,nav,footer,[role=banner],[role=navigation],[role=contentinfo]"));
  }
  function okAspect(el) {
    let w = el.naturalWidth || 0, h = el.naturalHeight || 0;
    if (!w || !h) { const r = el.getBoundingClientRect(); w = r.width; h = r.height; }
    if (!w || !h) return true;                 // 量不到就放过
    const ar = w / h;
    return ar >= 0.4 && ar <= 2.6;             // 商品图大致方形;极宽/极高多是雪碧图/横幅
  }
  // 综合:支持平台 + 足够大 + 非垃圾 + 非站点 UI + 比例正常 = 真商品图
  function isCollectable(el, url) {
    return isSupportedUrl(url) && bigEnough(el) && !isJunkUrl(url) && !inSiteChrome(el) && okAspect(el);
  }

  // ---- 从被悬停元素反查整张卡片,抽取标题/价格/评分/链接(启发式,不依赖易变 CSS 类名)----
  function cardOf(el) {
    let node = el;
    for (let i = 0; i < 7 && node; i++) {
      if (node.querySelector && node.querySelector("a[href]")) return node;
      node = node.parentElement;
    }
    return (el.closest && el.closest("a")) || el.parentElement || el;
  }
  function rootText(root) {
    try { return (root.innerText || root.textContent || "").replace(/\s+/g, " ").trim(); }
    catch (e) { return ""; }
  }
  function pickPrice(root) {
    const m = rootText(root).match(/(?:US\s*)?[$￥¥€£R]\s?\d[\d.,]*/);
    return m ? m[0].replace(/\s+/g, "") : "";
  }
  function pickRating(root) {
    const star = root.querySelector && root.querySelector('[aria-label*="star" i],[aria-label*="rating" i],[aria-label*="评分"]');
    if (star) { const m = (star.getAttribute("aria-label") || "").match(/([0-5](?:\.\d)?)/); if (m) return m[1]; }
    const m = rootText(root).match(/\b([0-5]\.\d)\b/);
    return m ? m[1] : "";
  }
  function pickTitle(el, root) {
    if (el && el.tagName === "IMG" && el.alt && el.alt.trim().length > 4) return el.alt.trim().slice(0, 240);
    const im = root.querySelector && root.querySelector("img[alt]");
    if (im && im.alt && im.alt.trim().length > 4) return im.alt.trim().slice(0, 240);
    const cand = root.querySelector && root.querySelector("h1,h2,h3,h4,[title]");
    if (cand) {
      const t = (cand.getAttribute("title") || cand.innerText || "").trim();
      if (t.length > 4) return t.slice(0, 240);
    }
    return "";
  }
  function pickLink(el, root) {
    const a = (root.querySelector && root.querySelector("a[href]")) || (el.closest && el.closest("a"));
    if (a) { try { return new URL(a.getAttribute("href"), location.href).toString(); } catch (e) { /* ignore */ } }
    return location.href;
  }
  function buildCard(url, el) {
    const plat = detectPlatform(url);
    const root = cardOf(el);
    return {
      url,
      hires_url: upgradeToHiRes(url, plat),
      platform: plat,
      title: pickTitle(el, root),
      price: pickPrice(root),
      rating: pickRating(root),
      source_url: pickLink(el, root),
    };
  }
  function gatherCards() {
    const seen = new Set();
    const cards = [];
    document.querySelectorAll("img,video").forEach((el) => {
      const u = imgUrlOf(el);                       // 含懒加载 data-src/srcset、视频海报
      if (!isCollectable(el, u)) return;            // 过滤掉商标/雪碧图/UI/横幅
      const c = buildCard(u, el);
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

  // ---- 单图悬停按钮(用 elementsFromPoint 跟踪光标,稳过覆盖层/背景图/悬停视频/懒加载)----
  function makeHoverBtn() {
    const b = document.createElement("button");
    b.id = "pod-hover-btn"; b.textContent = "📥 采集此商品"; b.style.display = "none";
    document.body.appendChild(b);
    let curUnit = null, curCard = null, hideT = null, raf = 0, mx = 0, my = 0;

    const cancelHide = () => { if (hideT) { clearTimeout(hideT); hideT = null; } };
    const hide = () => { b.style.display = "none"; curUnit = null; curCard = null; };
    const scheduleHide = () => { cancelHide(); hideT = setTimeout(hide, 600); };

    // 在某容器内找第一张「够大的商品图」(<img> / <video poster>),返回 {url, box}
    function imgInside(node) {
      if (!node.querySelectorAll) return null;
      for (const im of node.querySelectorAll("img,video")) {
        const u = imgUrlOf(im);
        if (!isSupportedUrl(u) || isJunkUrl(u)) continue;
        const r = im.getBoundingClientRect();
        if (r.width >= 60 && r.height >= 60) return { url: u, box: r };
      }
      return null;
    }
    // 光标处的「商品单元」:BTN=在按钮上;{el,url,box}=找到商品;null=没有
    function unitAt(x, y) {
      const stack = document.elementsFromPoint(x, y) || [];
      if (stack.indexOf(b) >= 0) return "BTN";
      // ① 光标正下方的元素直接是商品图(含背景图/视频海报)
      for (const el of stack) {
        const u = imgUrlOf(el);
        if (isSupportedUrl(u) && !isJunkUrl(u)) {
          const r = el.getBoundingClientRect();
          if (r.width >= 60 && r.height >= 60) return { el, url: u, box: r };
        }
      }
      // ② 兜底:从最上层元素往上,容器内搜商品图(应对覆盖层/视频盖住图的卡片)
      let node = stack[0];
      for (let i = 0; i < 6 && node; i++) {
        const hit = imgInside(node);
        if (hit) return { el: node, url: hit.url, box: hit.box };
        node = node.parentElement;
      }
      return null;
    }
    function position(box) {
      b.style.display = "block";
      b.style.left = (window.scrollX + box.right - b.offsetWidth - 8) + "px";
      b.style.top = (window.scrollY + box.top + 8) + "px";
    }
    function update() {
      raf = 0;
      const u = unitAt(mx, my);
      if (u === "BTN") { cancelHide(); return; }   // 光标在按钮上 → 保持
      if (u) {
        cancelHide();
        if (u.el !== curUnit) {                     // 换了单元才重建/重定位,避免抖动
          curUnit = u.el;
          curCard = buildCard(u.url, u.el);
          position(u.box);
        }
      } else if (curUnit) {
        scheduleHide();
      }
    }

    b.addEventListener("mousedown", (e) => { e.stopPropagation(); e.preventDefault(); });
    b.addEventListener("click", (e) => {
      e.stopPropagation(); e.preventDefault();
      if (curCard) collect([curCard]);
    });

    // 节流:每帧最多算一次 elementsFromPoint(mousemove 很密)
    document.addEventListener("mousemove", (e) => {
      mx = e.clientX; my = e.clientY;
      if (!raf) raf = requestAnimationFrame(update);
    }, { passive: true });
    window.addEventListener("scroll", () => { if (curUnit) hide(); }, { passive: true });
  }

  function boot() {
    if (!document.body) { setTimeout(boot, 500); return; }
    makeUI();
    makeHoverBtn();
  }
  boot();
})();
