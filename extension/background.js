// PODStudio 采集助手 — service worker
// 收到 content 的采集请求后:带登录态 token 把商品卡(图+标题/价格/评分/链接)回传到采集箱
// (POST /api/collect-tasks/ingest,只暂存元数据+URL;用户去采集页勾选「同步」才真正取图入库)。
const DEFAULT_API = "https://pod.kejing.online";

async function getCfg() {
  const { pod_token, pod_api } = await chrome.storage.local.get(["pod_token", "pod_api"]);
  return { token: pod_token || "", api: pod_api || DEFAULT_API };
}

async function ingestCards(cards, cfg) {
  const items = (cards || []).slice(0, 80).map((c) => ({
    url: String(c.url || "").slice(0, 2048),
    hires_url: String(c.hires_url || "").slice(0, 2048),
    title: String(c.title || "").slice(0, 512),
    price: String(c.price || "").slice(0, 64),
    rating: String(c.rating || "").slice(0, 32),
    source_url: String(c.source_url || "").slice(0, 2048),
    platform: String(c.platform || "").slice(0, 32),
  })).filter((it) => it.url);
  if (!items.length) return 0;
  let r;
  try {
    r = await fetch(cfg.api + "/api/collect-tasks/ingest", {
      method: "POST",
      headers: { "Content-Type": "application/json", Authorization: "Bearer " + cfg.token },
      body: JSON.stringify({ source: "plugin", platform: "", items }),
    });
  } catch (e) {
    // "Failed to fetch" 多半是地址不可达:本地后端没开,或弹窗里选错了线上/本地
    throw new Error(`连不上后端 ${cfg.api} — 请在插件弹窗确认地址(线上/本地)且后端在线`);
  }
  if (r.status === 401) throw new Error("登录失效,请重新登录 PODStudio");
  if (!r.ok) throw new Error("ingest " + r.status);
  const d = await r.json();
  return d.count || 0;
}

chrome.runtime.onMessage.addListener((msg, _sender, sendResponse) => {
  if (msg && msg.type === "COLLECT") {
    (async () => {
      const cfg = await getCfg();
      if (!cfg.token) { sendResponse({ ok: false, error: "未登录" }); return; }
      try {
        const count = await ingestCards(msg.cards, cfg);
        sendResponse({ ok: count > 0, count, error: count ? "" : "没有有效商品" });
      } catch (e) {
        sendResponse({ ok: false, error: String((e && e.message) || e) });
      }
    })();
    return true; // async
  }
});
