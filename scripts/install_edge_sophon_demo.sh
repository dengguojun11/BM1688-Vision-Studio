#!/usr/bin/env bash
# 将生产机 offline/sophon-demo.tar.gz 解压到边端 bm1688 容器可访问路径
set -euo pipefail
CFG="${1:-/data/labeIU/labelu_appdata/edge_ssh_config.json}"
DEMO_TAR="${2:-/data/labeIU/offline/sophon-demo.tar.gz}"
HOST=$(python3 -c "import json; print(json.load(open('$CFG'))['host'])")
USER=$(python3 -c "import json; print(json.load(open('$CFG'))['user'])")
PASS=$(python3 -c "import json; print(json.load(open('$CFG'))['password'])")
PORT=$(python3 -c "import json; print(json.load(open('$CFG')).get('port',22))")
DC=$(python3 -c "import json; print(json.load(open('$CFG')).get('docker_container','bm1688'))")
EDGE_ROOT="/data/labelu_edge"
UPLOAD="${EDGE_ROOT}/tmp/sophon-demo.tar.gz"
TARGET="${EDGE_ROOT}/runtime/sophon-demo"

if [ ! -f "$DEMO_TAR" ]; then
  echo "missing $DEMO_TAR"
  exit 1
fi

PREPARE="set -e
test ! -L $EDGE_ROOT
mkdir -p $EDGE_ROOT 2>/dev/null || sudo -n install -d -o \"\$(id -u)\" -g \"\$(id -g)\" $EDGE_ROOT
root_real=\$(readlink -m $EDGE_ROOT)
for path in ${EDGE_ROOT}/tmp $TARGET; do
  path_real=\$(readlink -m \"\$path\")
  case \"\$path_real\" in \"\$root_real\"/*) ;; *) echo 'remote path escapes edge root' >&2; exit 73;; esac
done
mkdir -p ${EDGE_ROOT}/tmp 2>/dev/null || sudo -n install -d -o \"\$(id -u)\" -g \"\$(id -g)\" ${EDGE_ROOT}/tmp"
sshpass -p "$PASS" ssh -o StrictHostKeyChecking=no -p "$PORT" "$USER@$HOST" "$PREPARE"

echo "upload sophon-demo.tar.gz to edge host $UPLOAD ..."
sshpass -p "$PASS" scp -o StrictHostKeyChecking=no -P "$PORT" "$DEMO_TAR" "$USER@$HOST:$UPLOAD"

REMOTE="set -e
test ! -L $EDGE_ROOT
root_real=\$(readlink -m $EDGE_ROOT)
target_real=\$(readlink -m $TARGET)
case \"\$target_real\" in \"\$root_real\"/*) ;; *) echo 'remote path escapes edge root' >&2; exit 73;; esac
sudo mkdir -p $TARGET
sudo tar -xzf $UPLOAD -C $TARGET --strip-components=1 2>/dev/null || sudo tar -xzf $UPLOAD -C $TARGET
rm -f $UPLOAD
sudo docker exec $DC bash -lc 'test -f $TARGET/sample/YOLOv8_plus_det/python/yolov8_bmcv.py && echo sophon-demo-ok || (ls -la $TARGET 2>/dev/null; ls -la $TARGET/sample 2>/dev/null; echo sophon-demo-missing)'"

sshpass -p "$PASS" ssh -o StrictHostKeyChecking=no -p "$PORT" "$USER@$HOST" "$REMOTE"
