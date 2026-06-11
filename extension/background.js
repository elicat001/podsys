// PODStudio 采集助手 — service worker
// 收到 content 的采集请求后:在扩展上下文(host_permissions 已授权,绕过 CORS)逐张下载图片字节,
// 带登录态 token 上传到 PODStudio 素材库(/api/assets, source=collected,后端会顺带做侵权查重)。
const DEFAULT_API = "https://pod.kejing.online";

async function getCfg() {
  const { pod_token, pod_api } = await chrome.storage.local.get(["pod_token", "pod_api"]);
  return { token: pod_token || "", api: pod_api || DEFAULT_API };
}

async function uploadOne(url, cfg) {
  // 下载图片字节(扩展上下文 + host_permission → 不受网页 CORS 限制)
  const resp = await fetch(url);
  if (!resp.ok) throw new Error("img " + resp.status);
  const blob = await resp.blob();
  const fd = new FormData();
  fd.append("file", blob, "collected.png");
  fd.append("source", "collected");
  const r = await fetch(cfg.api + "/api/assets", {
    method: "POST",
    headers: { Authorization: "Bearer " + cfg.token },
    body: fd,
  });
  if (r.status === 401) throw new Error("登录失效,请重新登录 PODStudio");
  if (!r.ok) throw new Error("upload " + r.status);
  return true;
}

chrome.runtime.onMessage.addListener((msg, _sender, sendResponse) => {
  if (msg && msg.type === "COLLECT") {
    (async () => {
      const cfg = await getCfg();
      if (!cfg.token) { sendResponse({ ok: false, error: "未登录" }); return; }
      let count = 0, failed = 0, firstErr = "";
      for (const url of msg.urls.slice(0, 80)) {
        try { await uploadOne(url, cfg); count++; }
        catch (e) { failed++; if (!firstErr) firstErr = String(e && e.message || e); }
      }
      sendResponse({ ok: count > 0, count, failed, error: count ? "" : firstErr });
    })();
    return true; // async
  }
});
