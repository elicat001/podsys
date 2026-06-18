#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────────────
# 一次性安装「蓝绿双 worker + 优雅排空」,把发版从"重启强杀正在跑的 AI 任务"变成"零打断"。
# **幂等可重跑**。用法:  ssh pod-kejing 'bash /www/wwwroot/podsys/scripts/setup-bluegreen-worker.sh'
#
# 解决的问题:deploy.sh 旧逻辑 `systemctl restart podsys-worker` 在 90s(systemd 默认 TimeoutStopSec)
# 就 SIGKILL,长任务(图生视频可达 25min)会被半路杀死 → Job 卡 running、运营扣了点没结果。
#
# 装完后:
#   - 模板单元 podsys-worker@blue / @green:TimeoutStopSec=30min(>最长任务)+ KillMode=mixed,
#     stop 时只对 celery 主进程发 SIGTERM → 停止接新任务、把手头任务跑完再退,**绝不强杀**。
#   - deploy.sh 走蓝绿:先起新颜色(新代码)接管新任务,再优雅排空旧颜色 → 发版期间新任务也不暂停。
#   - 老单实例 podsys-worker.service 被优雅排空 + 停用(并加 graceful drop-in 作回退保险)。
#   - 只动 podsys,不碰 Django/6379。
# ─────────────────────────────────────────────────────────────────────────────
set -euo pipefail

REPO=/www/wwwroot/podsys
VENV="$REPO/backend/.venv"
RUNAS=www
TPL=/etc/systemd/system/podsys-worker@.service
OLD=podsys-worker.service
COLOR_FILE="$REPO/.worker-color"

[ "$(id -u)" = "0" ] || { echo "请用 root 运行"; exit 1; }

mkdir -p /var/log/podsys; chown "$RUNAS:$RUNAS" /var/log/podsys 2>/dev/null || true

echo "==> [1/3] 写模板单元 $TPL(优雅排空 + 蓝绿)"
# %i=实例名(blue/green);%H=主机名(给每个颜色一个唯一 celery 节点名,避免 mingle 撞名)。
cat > "$TPL" <<EOF
[Unit]
Description=PODStudio Celery worker [%i] (async jobs, blue-green)
After=network.target redis-server@podsys.service
Wants=redis-server@podsys.service

[Service]
User=$RUNAS
Group=$RUNAS
WorkingDirectory=$REPO/backend
Environment=U2NET_HOME=$REPO/backend/data/u2net
ExecStart=$VENV/bin/celery -A app.celery_app worker -l info --concurrency=3 -n podsys-%i@%H
Restart=always
RestartSec=5
# 优雅排空:stop 发 SIGTERM → celery 停接新任务、跑完手头任务再退;给到 > 最长任务(图生视频 25min)。
TimeoutStopSec=1800
# mixed:SIGTERM 只发给主进程(celery master,触发 warm shutdown),超时后才对残留子进程 SIGKILL。
KillMode=mixed
KillSignal=SIGTERM
StandardOutput=append:/var/log/podsys/worker-%i-out.log
StandardError=append:/var/log/podsys/worker-%i-err.log

[Install]
WantedBy=multi-user.target
EOF

echo "==> [2/3] 给老单实例 $OLD 加 graceful drop-in(发版回退路径也不强杀)"
mkdir -p "/etc/systemd/system/${OLD}.d"
cat > "/etc/systemd/system/${OLD}.d/graceful.conf" <<EOF
[Service]
TimeoutStopSec=1800
KillMode=mixed
EOF

systemctl daemon-reload

echo "==> [3/3] 迁移到蓝绿(非破坏:先起 blue 接管新任务,再优雅排空+停用老单实例)"
CUR=$(cat "$COLOR_FILE" 2>/dev/null || echo "")
if [ "$CUR" != "blue" ] && [ "$CUR" != "green" ]; then
  systemctl enable podsys-worker@blue.service >/dev/null 2>&1 || true
  systemctl start podsys-worker@blue.service
  echo blue > "$COLOR_FILE"; chown "$RUNAS:$RUNAS" "$COLOR_FILE" 2>/dev/null || true
  if systemctl is-enabled "$OLD" >/dev/null 2>&1 || systemctl is-active "$OLD" >/dev/null 2>&1; then
    systemctl disable "$OLD" >/dev/null 2>&1 || true
    systemctl stop --no-block "$OLD" 2>/dev/null || true   # 优雅排空(跑完手头任务)后退出
    echo "    老单实例 $OLD 已停用,正在后台排空手头任务(不强杀)"
  fi
  echo "✅ 已迁移到蓝绿:当前 active=blue($(systemctl is-active podsys-worker@blue.service))"
else
  echo "✅ 已是蓝绿模式(当前=$CUR),本次仅刷新模板单元/drop-in;下次发版生效"
fi
echo "   排障:systemctl status podsys-worker@blue podsys-worker@green"
echo "   日志:/var/log/podsys/worker-<color>-{out,err}.log   当前颜色:$COLOR_FILE"
