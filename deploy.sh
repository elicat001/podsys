#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────────────
# 生产部署脚本(在生产服务器上运行)。标准流程:拉代码 → 装依赖 → 构建前端 → 重启 → 体检。
#
# 用法(在你本机,先把代码 commit + push 到 origin/main,然后):
#     ssh pod-kejing 'bash /www/wwwroot/podsys/deploy.sh'
#   (pod-kejing 是 ~/.ssh/config 里配的别名;没配就用 ssh root@pod.kejing.online)
#
# 设计要点(小白也安心):
#   - 只快进拉取(--ff-only),版本不对就停,不会乱 merge;
#   - 前端构建到临时目录 dist.new,**构建成功才原子替换**现网 dist——
#     万一构建失败,现网页面纹丝不动(零停机);
#   - 全程幂等:任何一步断了(比如 SSH 抖),直接重跑即可;
#   - 不碰同机的另一个项目(Django/kejing-gunicorn)。
# ─────────────────────────────────────────────────────────────────────────────
set -euo pipefail

REPO=/www/wwwroot/podsys
FE="$REPO/frontend-vue"
BUILD_HOME=/tmp/wwwbuild        # www 真实 home 属 root,npm 没法写;给它一个可写的 scratch HOME
SVC=podsys.service
RUNAS=www                       # 后端服务的运行用户;用它构建,保证产物属主一致
PORT=10000

echo "==> [0/5] 准备:npm 的可写 HOME"
mkdir -p "$BUILD_HOME"; chown "$RUNAS:$RUNAS" "$BUILD_HOME"

echo "==> [1/5] 拉取最新代码(ff-only)"
cd "$REPO"
sudo -u "$RUNAS" git pull --ff-only origin main
HEAD=$(sudo -u "$RUNAS" git rev-parse --short HEAD)
echo "    HEAD=$HEAD"

echo "==> [2/5] 安装依赖(npm ci,按 package-lock 可复现)"
cd "$FE"
sudo -u "$RUNAS" env HOME="$BUILD_HOME" npm ci --no-audit --no-fund --prefer-offline

echo "==> [3/5] 构建前端到临时目录 dist.new(不动现网 dist)"
sudo -u "$RUNAS" env HOME="$BUILD_HOME" npm run build -- --outDir dist.new --emptyOutDir
if [ ! -f dist.new/index.html ] || [ "$(ls dist.new/assets 2>/dev/null | wc -l)" -lt 10 ]; then
  echo "    !! 构建产物不完整,中止(现网 dist 保持不变)"; rm -rf dist.new; exit 1
fi
echo "    构建 OK(assets=$(ls dist.new/assets | wc -l))"

echo "==> [4/5] 原子替换 dist"
rm -rf dist.old
[ -d dist ] && mv dist dist.old
mv dist.new dist
chown -R "$RUNAS:$RUNAS" dist
rm -rf dist.old

echo "==> [5/5] 重启后端 + 健康检查"
systemctl restart "$SVC"
for i in $(seq 1 30); do
  [ "$(curl -s -o /dev/null -w '%{http_code}' --max-time 5 "http://127.0.0.1:$PORT/api/templates")" = "200" ] && break
  sleep 2
done
home=$(curl -s -o /dev/null -w '%{http_code}' --max-time 8 "http://127.0.0.1:$PORT/")
api=$(curl -s -o /dev/null -w '%{http_code}' --max-time 8 "http://127.0.0.1:$PORT/api/templates")
echo "    站点 / = $home   /api/templates = $api   服务=$(systemctl is-active "$SVC")"
if [ "$home" = "200" ] && [ "$api" = "200" ]; then
  echo "✅ 部署完成(HEAD=$HEAD)"
else
  echo "❌ 体检不过,请查 journalctl -u $SVC"; exit 1
fi
