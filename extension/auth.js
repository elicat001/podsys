// PODStudio 采集助手 — 登录态同步(运行在 PODStudio 站点上)
// 把网站 localStorage 里的登录 token 同步到扩展存储,采集请求才能带鉴权。
// 同时记录该站点对应的 API 地址(线上=同源;本地 vite:5173 → 后端 10000)。
(function () {
  function sync() {
    try {
      const token = localStorage.getItem("pod_token");
      const origin = location.origin;
      const api = origin.indexOf("localhost") >= 0 ? "http://localhost:10000" : origin;
      if (token) chrome.storage.local.set({ pod_token: token, pod_api: api });
    } catch (e) { /* ignore */ }
  }
  sync();
  // token 可能登录后才写入:监听变化 + 兜底轮询几次
  window.addEventListener("storage", (e) => { if (e.key === "pod_token") sync(); });
  let n = 0;
  const t = setInterval(() => { sync(); if (++n >= 10) clearInterval(t); }, 1500);
})();
