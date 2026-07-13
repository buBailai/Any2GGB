#!/bin/sh
cd "$(dirname "$0")"
[ -d .venv ] || python3 -m venv .venv
. .venv/bin/activate
pip install -q -r requirements.txt
# 首次运行自动拉取自托管的 GeoGebra 引擎（约 115MB，不随仓库分发）
python scripts/setup_ggb.py || { echo "GeoGebra 引擎未就绪，预览将无法渲染。"; exit 1; }
IP=$(ipconfig getifaddr en0 2>/dev/null || hostname -I 2>/dev/null | awk '{print $1}')
echo "Any2GGB → http://${IP:-127.0.0.1}:8868"
exec python -m uvicorn backend.main:app --host 0.0.0.0 --port 8868
