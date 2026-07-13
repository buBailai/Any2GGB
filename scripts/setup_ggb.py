#!/usr/bin/env python3
"""下载并安装 GeoGebra Math Apps Bundle 到 frontend/vendor/ggb/。

Any2GGB 的预览完全靠这份自托管的 GeoGebra 离线引擎运行。它约 115MB、
版权归 GeoGebra GmbH、遵循 GeoGebra 非商业许可，因此不随源码仓库分发；
克隆仓库后首次运行会自动执行本脚本把它拉下来（start.sh 已内置调用）。

也可手动运行：  python scripts/setup_ggb.py
"""
from __future__ import annotations

import io
import shutil
import sys
import urllib.request
import zipfile
from pathlib import Path

BUNDLE_URL = "https://download.geogebra.org/package/geogebra-math-apps-bundle"
APP_DIR = Path(__file__).resolve().parent.parent
VENDOR = APP_DIR / "frontend" / "vendor" / "ggb"


def already_installed() -> bool:
    return (VENDOR / "deployggb.js").exists() and (VENDOR / "GeoGebra" / "HTML5").is_dir()


def main() -> int:
    if already_installed():
        print("[setup_ggb] GeoGebra 引擎已就位，跳过。")
        return 0

    print(f"[setup_ggb] 正在下载 GeoGebra Math Apps Bundle（约 33MB 压缩包）…\n           {BUNDLE_URL}")
    try:
        req = urllib.request.Request(BUNDLE_URL, headers={"User-Agent": "Any2GGB-setup"})
        with urllib.request.urlopen(req, timeout=120) as resp:
            data = resp.read()
    except Exception as e:  # noqa: BLE001
        print(f"[setup_ggb] 下载失败：{e}\n"
              "  可手动从 https://www.geogebra.org/download 下载 Math Apps Bundle，"
              f"解压到 {VENDOR}（使其下有 GeoGebra/ 目录）。", file=sys.stderr)
        return 1

    print(f"[setup_ggb] 已下载 {len(data)} 字节，正在解压到 {VENDOR} …")
    VENDOR.mkdir(parents=True, exist_ok=True)
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as zf:
            zf.extractall(VENDOR)
    except zipfile.BadZipFile as e:
        print(f"[setup_ggb] 压缩包损坏：{e}", file=sys.stderr)
        return 1

    # 引擎入口 deployggb.js 在 GeoGebra/ 下，前端按 vendor/ggb/deployggb.js 引用，复制上来一份
    src = VENDOR / "GeoGebra" / "deployggb.js"
    if src.exists():
        shutil.copy2(src, VENDOR / "deployggb.js")

    if already_installed():
        print("[setup_ggb] ✅ GeoGebra 引擎安装完成。")
        return 0
    print("[setup_ggb] 安装后校验未通过，请检查解压结果。", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
