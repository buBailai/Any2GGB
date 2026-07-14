<div align="center">

# Any2GGB

**一句话，生成可以继续加工的 GeoGebra 题目配图。**

<p>
  <a href="LICENSE"><img src="https://img.shields.io/badge/license-MIT-brightgreen" alt="MIT License"></a>
  <img src="https://img.shields.io/badge/python-3.10%2B-blue" alt="Python 3.10+">
  <img src="https://img.shields.io/badge/version-v0.4.5-5B5BD6" alt="v0.4.5">
  <img src="https://img.shields.io/badge/engine-GeoGebra-D95F02" alt="GeoGebra">
</p>

</div>

---

数理老师的 **AI 配图工具**：一句话描述题目图形 → AI 生成 GGB 脚本 → 浏览器 GeoGebra 实时渲染 →
工具栏继续手动加工 → 一键复制 / 导出高清 PNG 插进题目。勾选「加入互动」后，AI 会按需求设计滑杆、动点、勾选框或动画。

姊妹项目 [Any2Manim](https://github.com/buBailai/Any2Manim)（教学动画视频）——
Any2GGB 产出的是**题目配图与可交互图形**，两者互补。

## 快速开始

```bash
cd app && ./start.sh     # 打开 http://localhost:8868
```

`start.sh` 会自建虚拟环境、装依赖，并在首次运行时自动下载自托管的 GeoGebra 引擎
（约 115MB，GeoGebra 版权、非商业许可，因此不随仓库分发）。也可手动获取：

```bash
python scripts/setup_ggb.py   # 下载 GeoGebra Math Apps Bundle 到 frontend/vendor/ggb/
```

无需任何 API Key 即可体验演示模式；配置自己的 Key（DeepSeek 等 10 家预设）后可自由生成。
要用「上传参考图让 AI 复刻」功能，请配置支持视觉的模型。

## 当前能力

- 四种生成场景：自由、解题、配图、复刻参考图。
- 2D/3D 可选；3D 立体图形可直接旋转观察，2D/3D 脚本可稳定来回切换。
- 互动可选且默认关闭：关闭时只画静态图；开启后按题目需求设计滑杆、动点、勾选框或动画。
- 3D 脚本经过确定性视觉整理：在关系明确时补齐棱线、统一点线面样式并控制面透明度，降低不同模型的出图波动。
- 2D 坐标自动保持 x/y 单位长度一致；线条会确定性收口为白底可见的深色，Polygon 自动边会安全重命名并同步颜色/线宽，避免对象同名覆盖。
- 完全一致的题目、附件、生成选项和模型会复用本机成功结果，并在当前 GeoGebra 中重新验证，减少等待与模型费用。
- 生成过久时可随时点「停止」；刷新页面后仍会识别后台任务和遗留的未完成版本，不会把项目永久卡在生成中。
- AI 生成后可用 GeoGebra 工具栏或脚本继续手改，并把手改结果保存为新的历史版本。
- 支持图片/文本参考资料、对话式定向修改、步骤播放、`.ggb`、高清 PNG 与可互动 HTML 导出、复制图片到 Word/PPT。互动 HTML 打开时需要联网加载 GeoGebra。

## 致谢与许可

- 本项目 MIT 开源。
- 内嵌 [GeoGebra](https://www.geogebra.org) Math Apps 引擎，版权归 GeoGebra GmbH，
  遵循 GeoGebra 非商业许可（GeoGebra Non-Commercial License Agreement）——
  **本项目及任何衍生分发不得用于商业收费**。
