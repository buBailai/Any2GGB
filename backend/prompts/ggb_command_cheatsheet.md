# GeoGebra 命令速查 / 避坑清单（v0）

> 目标引擎：**GeoGebra Classic（Math Apps 5.x）**，脚本 = 经典输入栏命令，一行一条。
> 凡与本清单冲突的写法一律视为错误。

## 0. 铁律
- **一行一条命令**，不写分号、不写多语句；空行与 `# 注释` 行允许（系统会跳过）。
- **命令名一律英文**（Point/Circle/Slider…），**中文只能出现在字符串里**（Text/SetCaption 的引号内）。
- 对象标签用**英文字母+数字**（A、B1、f、tri、ang1…），中文名用 `SetCaption(对象,"中文")` 显示。
- 定义即创建：`A=(1,2)`、`f(x)=x^2`、`c=Circle(A,3)`。**同名重复定义会直接覆盖旧对象**——每个标签只定义一次。
- 数学表达式里**乘号写显式 `*`**：`2*cos(t)`、`a*x^2`（隐式乘法在部分场景解析失败）。
- 字符串一律用**英文双引号** `"..."`；严禁中文引号“ ”、中文逗号，、中文括号（）。

## 1. 创建类命令（会产生新对象；失败可被系统检出）
- 点/线：`A=(1,2)` `M=Midpoint(s)` `s=Segment(A,B)` `l=Line(A,B)` `r=Ray(A,B)` `v=Vector(A,B)`
- 多边形：`tri=Polygon(A,B,C)` `sq=Polygon(A,B,4)`（正多边形）
- 圆/锥线：`c=Circle(O,3)` `c2=Circle(A,B)` `e=Ellipse(F1,F2,5)` `p=Parabola(F,l)`
- 函数：`f(x)=a*x^2+b*x+c` `g=Derivative(f)` `F=Integral(f,0,2)` `R=Root(f)` `E=Extremum(f,-5,5)`
- 交点/垂直/角：`P=Intersect(f,g)` `pl=PerpendicularLine(A,l)` `pb=PerpendicularBisector(A,B)` `ang=Angle(A,B,C)`
- 变换：`A2=Rotate(A,45°,O)` `B2=Reflect(B,l)` `C2=Translate(C,v)` `D2=Dilate(D,2,O)`
- 轨迹/序列：`loc=Locus(P,Q)` `pts=Sequence((k,k^2),k,-3,3)`
- 交互控件：`a=Slider(0,5,0.1)` `b1=Checkbox("显示辅助线")` `P=Point(c)`（圆/线上的**可拖动点**）
- 文本：`t1=Text("说明文字",(x,y))`；**LaTeX 公式**：`t2=Text("a^2+b^2=c^2",(x,y),true,true)`（第 4 参 true = LaTeX 渲染）。⚠️ **严禁对 LaTeX 文本用 SetColor**（视窗重绘后公式会消失），公式保持默认黑色
- 3D（仅 3D 模式）：三维点 `A=(1,2,3)`；`sp=Sphere((0,0,0),2)` `cu=Cube(A,B)` `py=Pyramid(poly,3)` `pr=Prism(poly,3)` `pl=Plane(A,B,C)` `co=Cone(c,3)`。先定义点/底面再定义立体，棱与对角线仍用 `Segment(A,B)`。
- 统计：`bar=BarChart({1,2,3},{4,2,5})` `h=Histogram(...)`

## 2. 脚本类命令（改属性/无新对象；执行器不按返回值判错，引用的对象必须已存在）
- 外观：`SetColor(obj,91,91,214)`（RGB 0-255）`SetLineThickness(f,5)` `SetPointSize(A,6)` `SetFilling(tri,0.3)` `SetLineStyle(l,1)`
  - ⚠️ **每个可见线对象都要明确设为黑色或深色**，不得依赖 GGB 默认色；**严禁白色或浅色线条**（预览与导出多为白底，浅色/白色会看不见）。只在需要强调 1~2 处重点时才上深色（深红/靛蓝/深绿等）。
  - `poly=Polygon(A,B,C)` 的可见边是 GGB 额外生成的 Segment，`SetColor(poly,...)` 主要改填充，**不能保证边线变色**。需要外轮廓时另建 `outline=Polyline(A,B,C,A)`（或逐边 Segment），并对 outline 设深色与线宽。
- 标签：`SetCaption(A,"顶点")` `ShowLabel(A,true)`
- 值/可见：`SetValue(a,2.5)` `SetVisibleInView(obj,1,false)` `SetConditionToShowObject(t1,b1)`（勾选框控制显隐）
- 动画：`StartAnimation(a)`（滑杆/圆上点）`SetFixed(A,true)`
- 视窗：**用指令行 `# view: xmin ymin xmax ymax`**（如 `# view: -5 -3 5 5`）——每个课件开头必须设定合适视窗。⚠️ 严禁用 ZoomIn/ZoomOut 命令（会导致 LaTeX 公式消失）
- 空间/3D 视窗：脚本第一条有效指令写 `# perspective: 2d` 或 `# perspective: 3d`；3D 可用 `# view3d: xmin ymin zmin xmax ymax zmax`，不可把 2D 的 `# view:` 用于 3D。
- 删除：`Delete(obj)`（依赖它的对象会级联删除，慎用）

## 3. 高频错误 → 正确写法
| ❌ 错误 | ✅ 正确 |
|---|---|
| `SetCoordSystem(...)` / `ZoomIn(...)` 设视窗（前者不存在，后者会让 LaTeX 公式消失） | 指令行 `# view: -5 -3 5 3`（xmin ymin xmax ymax） |
| `甲=(1,2)` 中文标签 | `A=(1,2)` + `SetCaption(A,"甲")` |
| `Text("公式 a²+b²=c²")` 拿 Unicode 上标凑公式 | `Text("a^2+b^2=c^2",(1,3),true,true)` LaTeX 渲染 |
| `f(x)=ax^2` 隐式乘 | `f(x)=a*x^2` |
| `Circle(O, 半径3)` 参数里塞中文 | `Circle(O,3)` |
| 中文逗号/引号/括号 `SetCaption(A，“顶点”)` | 全部英文标点 `SetCaption(A,"顶点")` |
| 重复定义 `A=(1,2)` … `A=Midpoint(s)` | 换新标签 `M=Midpoint(s)` |
| `Slider[0,5]` 方括号旧语法 | `Slider(0,5,0.1)` 圆括号 |
| `SetColor(LaTeX文本, …)` | 不要给 LaTeX 公式上色（重绘后会消失），公式默认黑色即可 |
| `SetColor(l,255,255,255)` / 任何浅色线（白/浅灰/浅黄…） | 白底看不见 → 每个可见线对象都明确 `SetColor`为黑或深色 |
| 只写 `SetColor(poly,0,0,0)` 试图改 Polygon 边线 | 多边形边是独立 Segment → 另建封闭 `Polyline`/逐边 `Segment` 并设深色 |

## 4. 配图与布局要求（硬性）
- **默认画静态配图**：忠实反映题目，不自作主张加滑杆/动点/动画。**只有用户明确要求交互时**才做滑杆（参数探究）/ 动点 / 勾选框，且克制（至多 1 个 `StartAnimation`）。
- **所有可见线条明确设为黑色/深色**，白底可印；颜色只用于强调 1~2 处重点。
- **开头先 `# view: ...` 设视窗**：按题目真实数据算好所有对象范围再定，图形居中、留 10%~20% 边距，别挤角落或超屏。
- 标注规范：关键点大写字母 `SetCaption`，边长/角度按题意标注，直角处用 `Angle(...)` 画直角符号；文字放**不遮挡图形**的空白处、不压线不出窗。
- 深色可选色板：纯黑 `0,0,0`、深靛蓝 `40,60,120`、深红 `170,40,40`、深绿 `30,110,60`、深灰 `70,70,80`。

## 5. 强约束输出
- 只输出命令脚本文本：无 markdown 围栏、无解释文字。
- 按绘图步骤分段，段首注释：`# step_01：<这一步画什么>`（系统靠它做步骤播放与定向编辑）。
- 每段 2~8 行命令；全片 2~5 个 step。
