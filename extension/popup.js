// PODStudio 采集助手 — popup:显示登录态 + 选择 PODStudio 地址 + 打开站点登录。
const apiSel = document.getElementById("api");
const openLink = document.getElementById("open");
const loginEl = document.getElementById("login");

function refreshOpen() { openLink.href = apiSel.value; }

chrome.storage.local.get(["pod_token", "pod_api"], ({ pod_token, pod_api }) => {
  if (pod_api) apiSel.value = pod_api;
  refreshOpen();
  if (pod_token) {
    loginEl.innerHTML = '登录状态:<span class="ok">已登录 ✓</span>';
  } else {
    loginEl.innerHTML = '登录状态:<span class="bad">未登录</span> — 请先打开站点登录';
  }
});

apiSel.onchange = () => {
  chrome.storage.local.set({ pod_api: apiSel.value });
  refreshOpen();
};
