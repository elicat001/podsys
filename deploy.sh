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
BE="$REPO/backend"
FE="$REPO/frontend-vue"
VENV="$BE/.venv"
BUILD_HOME=/tmp/wwwbuild        # www 真实 home 属 root,npm/pip 没法写;给它一个可写的 scratch HOME
SVC=podsys.service
WORKER=podsys-worker.service    # 老的单实例 worker(回退用;装了蓝绿模板后会被停用)
WORKER_TPL=podsys-worker@       # 蓝绿:模板单元 podsys-worker@blue / @green(scripts/setup-bluegreen-worker.sh 装)
COLOR_FILE="$REPO/.worker-color"  # 记录当前活跃颜色(blue/green),发版时切到另一个
RUNAS=www                       # 后端服务的运行用户;用它构建,保证产物属主一致
PORT=10000

echo "==> [0/6] 准备:npm/pip 的可写 HOME"
mkdir -p "$BUILD_HOME"; chown "$RUNAS:$RUNAS" "$BUILD_HOME"

echo "==> [1/6] 拉取最新代码(ff-only)"
cd "$REPO"
BEFORE=$(sudo -u "$RUNAS" git rev-parse HEAD 2>/dev/null || echo "")
sudo -u "$RUNAS" git pull --ff-only origin main
HEAD=$(sudo -u "$RUNAS" git rev-parse --short HEAD)
AFTER=$(sudo -u "$RUNAS" git rev-parse HEAD)
echo "    HEAD=$HEAD"
# 是否动了"后端"?纯前端改动已由 dist 原子替换生效,无需重启后端/worker → 运营任务零打断。
# 保守默认 1(重启);仅当本次有新提交且改动**全部**落在 frontend-vue/ 下时,才置 0 跳过重启。
BACKEND_TOUCHED=1
if [ -n "$BEFORE" ] && [ "$BEFORE" != "$AFTER" ]; then
  CHANGED=$(sudo -u "$RUNAS" git diff --name-only "$BEFORE" "$AFTER")
  if [ -n "$CHANGED" ] && ! echo "$CHANGED" | grep -qvE '^frontend-vue/'; then
    BACKEND_TOUCHED=0
  fi
fi

echo "==> [2/6] 安装后端依赖(pip,只补缺的;celery/redis 等)"
# 已满足的会跳过,首次会装上 celery/redis。HOME 指向可写 scratch 让 pip 能写缓存。
sudo -u "$RUNAS" env HOME="$BUILD_HOME" "$VENV/bin/pip" install -q -r "$BE/requirements.txt"

echo "==> [2b/6] 数据库迁移(alembic)"
cd "$BE"
# 首次:库里还没 alembic 版本记录(老库已有表)→ 标记为基线(不重建表);之后:应用新迁移。
# 连接串由 alembic/env.py 从 .env 读;配合 app 启动的 create_all 安全网,即使空库也能起。
if sudo -u "$RUNAS" env HOME="$BUILD_HOME" "$VENV/bin/alembic" current 2>/dev/null | grep -qE '[0-9a-f]{12}'; then
  sudo -u "$RUNAS" env HOME="$BUILD_HOME" "$VENV/bin/alembic" upgrade head
  echo "    alembic upgrade head 完成"
else
  sudo -u "$RUNAS" env HOME="$BUILD_HOME" "$VENV/bin/alembic" stamp head
  echo "    首次:已把现有库 stamp 为基线(后续改表会自动 upgrade)"
fi

echo "==> [3/6] 安装前端依赖(npm ci,按 package-lock 可复现)"
cd "$FE"
sudo -u "$RUNAS" env HOME="$BUILD_HOME" npm ci --no-audit --no-fund --prefer-offline

echo "==> [4/6] 构建前端到临时目录 dist.new(不动现网 dist)"
sudo -u "$RUNAS" env HOME="$BUILD_HOME" npm run build -- --outDir dist.new --emptyOutDir
if [ ! -f dist.new/index.html ] || [ "$(ls dist.new/assets 2>/dev/null | wc -l)" -lt 10 ]; then
  echo "    !! 构建产物不完整,中止(现网 dist 保持不变)"; rm -rf dist.new; exit 1
fi
echo "    构建 OK(assets=$(ls dist.new/assets | wc -l))"

echo "==> [5/6] 原子替换 dist"
rm -rf dist.old
[ -d dist ] && mv dist dist.old
mv dist.new dist
chown -R "$RUNAS:$RUNAS" dist
rm -rf dist.old

echo "==> [6/6] 生效 + 健康检查(前端已原子替换;后端按需重启,worker 蓝绿不断任务)"
# 蓝绿切换 worker:先起"另一个颜色"(加载新代码)接管新任务,再优雅排空旧颜色(--no-block 不阻塞发版)。
# 旧 worker 收 SIGTERM 后停止接新任务、把手头任务跑完再退(TimeoutStopSec=30min),全程不强杀=任务不断。
# 模板未装(未迁移的机器)→ 回退老单实例 restart;配合 graceful drop-in 也不强杀,只是会等排空。
restart_worker() {
  if systemctl list-unit-files "${WORKER_TPL}.service" --no-legend 2>/dev/null | grep -q "${WORKER_TPL}"; then
    local cur new old
    cur=$(cat "$COLOR_FILE" 2>/dev/null || echo "")
    if [ "$cur" = "blue" ]; then new=green; old=blue; elif [ "$cur" = "green" ]; then new=blue; old=green; else new=blue; old=""; fi
    systemctl stop "${WORKER_TPL}${new}.service" 2>/dev/null || true   # 正常瞬时;若同色上次仍在排空则等其完成(安全)
    systemctl enable "${WORKER_TPL}${new}.service" >/dev/null 2>&1 || true
    systemctl start "${WORKER_TPL}${new}.service"
    if [ -n "$old" ] && [ "$old" != "$new" ]; then
      systemctl disable "${WORKER_TPL}${old}.service" >/dev/null 2>&1 || true
      systemctl stop --no-block "${WORKER_TPL}${old}.service" 2>/dev/null || true   # 后台排空,不阻塞发版
    fi
    echo "$new" > "$COLOR_FILE"
    echo "    worker 蓝绿:新=$new($(systemctl is-active "${WORKER_TPL}${new}.service"))${old:+,旧=$old 后台排空手头任务}"
  elif systemctl list-unit-files "$WORKER" --no-legend 2>/dev/null | grep -q "$WORKER"; then
    systemctl restart "$WORKER"
    echo "    worker(单实例,优雅排空后重启)=$(systemctl is-active "$WORKER")"
  else
    echo "    (未配置 worker,跳过——异步工具走优雅降级)"
  fi
}
if [ "$BACKEND_TOUCHED" = "0" ]; then
  echo "    本次仅前端改动 → dist 已替换生效,跳过后端/worker 重启(运营任务零打断)"
else
  systemctl restart "$SVC"   # API:uvicorn 收 SIGTERM 排空在途请求(残留 ~1-2s socket 空窗,见 CLAUDE.md)
  restart_worker
fi
for i in $(seq 1 30); do
  [ "$(curl -s -o /dev/null -w '%{http_code}' --max-time 5 "http://127.0.0.1:$PORT/api/templates")" = "200" ] && break
  sleep 2
done
home=$(curl -s -o /dev/null -w '%{http_code}' --max-time 8 "http://127.0.0.1:$PORT/")
api=$(curl -s -o /dev/null -w '%{http_code}' --max-time 8 "http://127.0.0.1:$PORT/api/templates")
echo "    站点 / = $home   /api/templates = $api   服务=$(systemctl is-active "$SVC")"
# (可选)MinIO 存储探活——仅信息提示;挂了不影响发布(产物镜像失败只 warning,Job 表才是真相源)
if systemctl list-unit-files minio.service --no-legend 2>/dev/null | grep -q minio.service; then
  echo "    MinIO 存储 = $(curl -s -o /dev/null -w '%{http_code}' --max-time 5 http://127.0.0.1:9000/minio/health/live || echo N/A)"
fi
if [ "$home" = "200" ] && [ "$api" = "200" ]; then
  echo "✅ 部署完成(HEAD=$HEAD)"
else
  echo "❌ 体检不过,请查 journalctl -u $SVC"; exit 1
fi
