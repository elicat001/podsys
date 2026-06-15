#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────────────
# 一次性安装「每日 retention」systemd timer(阶段三:释放应用盘)。**幂等可重跑**。
#
# 用法:  ssh pod-kejing 'bash /www/wwwroot/podsys/scripts/setup-retention.sh'
#
# 前提:已部署 MinIO(scripts/setup-minio.sh)+ podsys 的 .env 里设了
#       POD_STORAGE_BACKEND=s3 且 POD_S3_RETENTION_DAYS>0(否则 retention 会自动跳过、不删任何东西)。
# 行为:每天 03:30 以 www 用户跑一次 scripts/retention.py(删超 N 天且 MinIO 有副本的本地产物缓存)。
# ─────────────────────────────────────────────────────────────────────────────
set -euo pipefail

REPO=/www/wwwroot/podsys
VENV="$REPO/backend/.venv"
RUNAS=www
SVC=/etc/systemd/system/podsys-retention.service
TIMER=/etc/systemd/system/podsys-retention.timer

[ "$(id -u)" = "0" ] || { echo "请用 root 运行"; exit 1; }

cat > "$SVC" <<EOF
[Unit]
Description=podsys 本地产物缓存清理(retention,释放应用盘)
After=minio.service

[Service]
Type=oneshot
User=$RUNAS
Group=$RUNAS
WorkingDirectory=$REPO/backend
ExecStart=$VENV/bin/python $REPO/scripts/retention.py
EOF

cat > "$TIMER" <<EOF
[Unit]
Description=每日跑一次 podsys retention

[Timer]
OnCalendar=*-*-* 03:30:00
Persistent=true

[Install]
WantedBy=timers.target
EOF

systemctl daemon-reload
systemctl enable --now podsys-retention.timer
echo "✅ 已安装每日 retention timer(03:30)。"
echo "   下次触发:$(systemctl list-timers podsys-retention.timer --no-legend 2>/dev/null | awk '{print $1, $2}')"
echo "   手动跑一次:systemctl start podsys-retention.service && journalctl -u podsys-retention.service -n 5 --no-pager"
