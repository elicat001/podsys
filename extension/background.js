// PODStudio 采集助手 — service worker (MVP 骨架)
// 把采集到的图片转发到本地/远程的 PODStudio /api/process。
const POD_API = "http://127.0.0.1:8000";

chrome.runtime.onMessage.addListener((msg, _sender, sendResponse) => {
  if (msg?.type === "SEND_TO_POD") {
    (async () => {
      try {
        const blob = await (await fetch(msg.imageUrl)).blob();
        const fd = new FormData();
        fd.append("file", blob, "collected.png");
        fd.append("template", msg.template || "tshirt");
        const r = await fetch(`${POD_API}/api/process`, { method: "POST", body: fd });
        sendResponse({ ok: r.ok, data: await r.json() });
      } catch (e) {
        sendResponse({ ok: false, error: String(e) });
      }
    })();
    return true; // async
  }
});
