// PODStudio 采集助手 — popup 逻辑 (MVP 骨架)
let selected = null;

document.getElementById("scan").onclick = async () => {
  const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
  chrome.tabs.sendMessage(tab.id, { type: "COLLECT_IMAGES" }, (resp) => {
    const grid = document.getElementById("grid");
    grid.innerHTML = "";
    (resp?.images || []).forEach((url) => {
      const img = document.createElement("img");
      img.src = url;
      img.onclick = () => {
        document.querySelectorAll("#grid img").forEach((i) => i.classList.remove("sel"));
        img.classList.add("sel");
        selected = url;
        document.getElementById("send").disabled = false;
      };
      grid.appendChild(img);
    });
    setStatus(`找到 ${resp?.images?.length || 0} 张图`);
  });
};

document.getElementById("send").onclick = () => {
  if (!selected) return;
  setStatus("发送中…");
  chrome.runtime.sendMessage({ type: "SEND_TO_POD", imageUrl: selected }, (resp) => {
    setStatus(resp?.ok ? "已发送，作业号 " + resp.data.job_id : "失败: " + (resp?.error || ""));
  });
};

function setStatus(t) { document.querySelector("#status small").textContent = t; }
