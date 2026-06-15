#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────────────
# 一次性在生产机部署 MinIO 对象存储(podsys 专用)。**幂等可重跑**。
#
# 用法(在你本机 push 后):
#     ssh pod-kejing 'bash /www/wwwroot/podsys/scripts/setup-minio.sh'
#
# 设计要点(照搬本项目 redis-server@podsys 的隔离范式):
#   - 全 systemd 原生(不引入 docker);二进制放 /usr/local/bin。
#   - **只绑 127.0.0.1**(API 9000 / Console 9001),不进 nginx、不开防火墙——
#     文件仍经 podsys 的 /files 端点出去,MinIO 全私有(owner 隔离红线)。
#   - 与同机 Django(8000)、系统 Redis(6379)、podsys 的 6380/MySQL **物理隔离**,绝不碰它们。
#   - 凭据强随机、只写 /etc/default/minio-podsys(root:600),**绝不入 git**;重跑不改密码(避免与 .env 失配)。
# ─────────────────────────────────────────────────────────────────────────────
set -euo pipefail

BIN_MINIO=/usr/local/bin/minio
BIN_MC=/usr/local/bin/mc
DATA_DIR=/www/wwwroot/podsys-data/minio
ENV_FILE=/etc/default/minio-podsys
UNIT=/etc/systemd/system/minio.service
RUNAS=www
API_ADDR=127.0.0.1:9000
CONSOLE_ADDR=127.0.0.1:9001
BUCKET=podsys
ALIAS=podsys-local

[ "$(id -u)" = "0" ] || { echo "请用 root 运行"; exit 1; }

echo "==> [1/6] 下载 minio / mc 二进制(已存在则跳过)"
if [ ! -x "$BIN_MINIO" ]; then
  curl -fsSL -o "$BIN_MINIO" https://dl.min.io/server/minio/release/linux-amd64/minio
  chmod +x "$BIN_MINIO"
fi
if [ ! -x "$BIN_MC" ]; then
  curl -fsSL -o "$BIN_MC" https://dl.min.io/client/mc/release/linux-amd64/mc
  chmod +x "$BIN_MC"
fi
echo "    minio: $("$BIN_MINIO" --version 2>/dev/null | head -1 || echo installed)"

echo "==> [2/6] 数据目录 $DATA_DIR(www:www, 750)"
mkdir -p "$DATA_DIR"
chown -R "$RUNAS:$RUNAS" "$(dirname "$DATA_DIR")"
chmod 750 "$(dirname "$DATA_DIR")" "$DATA_DIR"

echo "==> [3/6] 凭据 $ENV_FILE"
if [ ! -f "$ENV_FILE" ]; then
  ROOT_PASS="$(openssl rand -hex 24)"
  umask 077
  cat > "$ENV_FILE" <<EOF
MINIO_ROOT_USER=podsys
MINIO_ROOT_PASSWORD=$ROOT_PASS
MINIO_VOLUMES=$DATA_DIR
MINIO_OPTS=--address $API_ADDR --console-address $CONSOLE_ADDR
EOF
  chmod 600 "$ENV_FILE"
  echo "    已生成强随机 root 凭据(user=podsys;密码见该文件,勿外泄)"
else
  echo "    复用已存在凭据(不改密码,避免与 podsys/.env 失配)"
fi

echo "==> [4/6] systemd 单元 minio.service"
if [ ! -f "$UNIT" ]; then
  cat > "$UNIT" <<EOF
[Unit]
Description=MinIO object storage for podsys (localhost-only)
After=network-online.target
Wants=network-online.target

[Service]
User=$RUNAS
Group=$RUNAS
EnvironmentFile=$ENV_FILE
ExecStart=$BIN_MINIO server \$MINIO_OPTS \$MINIO_VOLUMES
Restart=on-failure
RestartSec=3
LimitNOFILE=65536

[Install]
WantedBy=multi-user.target
EOF
fi
systemctl daemon-reload
systemctl enable --now minio.service

echo "==> [5/6] 等待 MinIO 健康"
code=""
for _ in $(seq 1 30); do
  code=$(curl -s -o /dev/null -w '%{http_code}' "http://$API_ADDR/minio/health/live" || true)
  if [ "$code" = "200" ]; then break; fi
  sleep 1
done
if [ "$code" != "200" ]; then
  echo "    MinIO 未就绪 (health=$code),排查:journalctl -u minio.service -n 50"; exit 1
fi
echo "    health=200 ✓  服务=$(systemctl is-active minio.service)"

echo "==> [6/6] 建私有桶 $BUCKET"
# 注意:不能 `source` env 文件——MINIO_OPTS 行含空格未加引号,bash 会把它当命令执行报错
# (systemd 的 EnvironmentFile 解析没这问题,所以服务本身正常)。这里只精确取两个凭据。
MINIO_ROOT_USER=$(grep -E '^MINIO_ROOT_USER=' "$ENV_FILE" | cut -d= -f2-)
MINIO_ROOT_PASSWORD=$(grep -E '^MINIO_ROOT_PASSWORD=' "$ENV_FILE" | cut -d= -f2-)
"$BIN_MC" alias set "$ALIAS" "http://$API_ADDR" "$MINIO_ROOT_USER" "$MINIO_ROOT_PASSWORD" >/dev/null
"$BIN_MC" mb --ignore-existing "$ALIAS/$BUCKET" >/dev/null
"$BIN_MC" anonymous set none "$ALIAS/$BUCKET" >/dev/null   # 强制私有,杜绝匿名读
echo "    桶 $BUCKET 就绪(私有)✓"

cat <<EOF

✅ MinIO 部署完成(localhost-only,与 Django/6379 物理隔离)。
   下一步把这几行加进 /www/wwwroot/podsys/backend/.env 再重启 podsys 即切到对象存储:
     POD_STORAGE_BACKEND=s3
     POD_S3_ENDPOINT_URL=http://127.0.0.1:9000
     POD_S3_ACCESS_KEY=$MINIO_ROOT_USER
     POD_S3_SECRET_KEY=<见 $ENV_FILE 里的 MINIO_ROOT_PASSWORD>
   (POD_S3_BUCKET=podsys、POD_S3_ADDRESSING=path 用默认即可)
   重启:systemctl restart podsys.service podsys-worker.service
   Console(可选,运维用):SSH 隧道  ssh -L 9001:127.0.0.1:9001 pod-kejing  然后浏览器开 http://127.0.0.1:9001
EOF
