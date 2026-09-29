# 1000 卡片渲染重构与验证

本次将相机覆盖范围、卡片世界几何和具体视图的生命周期分开。远视野使用轻量表示；选中、焦点、编辑和操作中的卡片可以保留完整视图。`surfaceLevels`、保存的尺寸、卡片配置和坐标不由 LOD 修改。

## 渲染策略

- `canvas/cardRendering.ts` 的 `CardRenderModel` 持有瞬态覆盖集合、LOD、交互租约和空间索引。2048 world-unit 网格支持负坐标、嵌套卡片的绝对矩形及超大容器；覆盖查询不遍历每对卡片。
- `useCardRendering` 直接订阅 React Flow 的 camera。只有覆盖集合改变才发布新的几何投影；LOD 由卡片各自的订阅获取，不再写回 React Flow 的整批节点数据。普通 camera 更新不写卡片 model。
- 进入 overscan 为 240 CSS px，退出保留带为 480 px；100 px 的 safe region 内不重新选择挂载集合。尺寸和位置变化会使索引失效。
- 按每张卡片的投影面积等效尺寸判断：`sqrt(shortSide × min(longSide, 4 × shortSide)) × zoom`。窄条形卡片不会只因长边很长就挂载完整 Workspace。Node、Preview、Inspector、Workspace 使用不同的屏幕像素阈值，见下方语义缩放修订。
- far 使用类型图标、标题及状态；mid 保留原卡片的标题栏、内容比例、图片和摘要。Sandbox mid 使用工具栏、文件侧栏、文件预览和终端分区，读取已有 model/draft；已浏览文件保留最多 1600 字符的瞬态只读投影，不请求文件、不挂载编辑器或设置。
- full 使用既有 renderer，并遵守用户保存的展示层级。保存为 Node/Preview 的卡片不会因放大而被写成 Workspace。
- 选中、焦点、拖拽、调整尺寸、连接源及编辑/异步操作租约立即覆盖 LOD 和视口剔除。稳定的卡片事件边界持有轻量视图上的指针手势：升级不等待松开；若原 DOM 被替换，未移动的主指针松开会沿原 React Flow 祖先分发一次 click，保留打开和选择语义，屏蔽重复兼容 click。拖拽、取消、连接和修饰键单独处理。
- 逻辑节点仍在 React Flow 中，离屏视图返回空，轻量几何代理隐藏显示。这样 `getNodes`、选择、minimap、嵌套归属和跨视口连接线仍有完整几何；虚拟化不复用逻辑 `hidden`。这会保留约每卡一个空 wrapper，不能声称 DOM 数量完全与可见卡片数成正比。
- 合成缓存的 512 个名额按实际挂载视图统计，由小卡片和静态 Workspace 缩略图共享；空几何代理不占名额，完整 Workspace 不因这个缩略图规则进入缓存。

## 状态和重子树

复用 `nodeSurfaces` 的瞬态 draft store，而非另建持久状态系统。Sandbox 的命令、历史输入、选中文件、展开目录、发布路径、设置和配置输入，Agent 的指令/页签，Conversation 的编辑草稿，以及 Toolbox、Barracks、执行配置的草稿和 revision 在视图卸载后仍由 model 持有。

私密环境输入单独保存在 host 内存的 `privateDrafts`，不进入 preferences 或插件 `host.draft`。卡片删除会清理这些草稿。执行配置重新挂载时刷新无修改的模型；存在草稿时保留原 revision，由显式 Reload 或 Save 处理冲突。

Sandbox 的 Workspace/Settings 页签现在 conditional mount；Inspector 中未展开的 Configuration 也不挂载 `SandboxSettings`。已有插件可以继续使用按 card/session 隔离的 `host.draft`；任意第三方组件自行保存的 React 本地状态并不会被自动序列化，插件需要遵守这个草稿契约。

文件预览意图不再随 Sandbox/Conversation 视图卸载清空；删除、Sandbox 绑定变化和 Conversation session 切换才使它失效。原有 Sandbox 请求代次和过期响应保护保留。

## 几何计算

`WorldCanvas.renderCards` 使用已加载且属于当前 session 的世界卡片，不再依赖 active chunks。`createDisplacementCache` 的输入签名仅包含卡片坐标、展示尺寸/层级和障碍物几何；名称、状态、camera 和 visible chunks 不触发碰撞重算。既有 displacement 算法和保存坐标的规则保留，临时避让现在基于完整已加载世界，避免相机跨 chunk 时重排。

空间索引用于视图覆盖查询。实际世界几何变化时，displacement 仍使用现有算法；没有宣称任意规模的布局编辑已变成常数时间。

## 测量方法和边界

独立 headless Edge，1920×1080、DPR 1、不降速 CPU、Vite 开发构建。改动前源文件快照固定在 `.outputs/card-render-baseline`，后版本使用当前源码。使用现有 `generateStressWorld(1000)` 的确定性分布，比较 mixed（Agent/Text/Image/Sandbox）、全 Text、全 Sandbox；远视野 zoom=0.12，近视野 zoom=1。

每个场景按同一顺序运行 pan、zoom、快速反向 zoom、快速连续 pan。普通 pan 为两个闭环共 120 个输入点；zoom 为 20 次放大、20 次缩小。快速阶段按 8 ms 目标间隔提交 CDP 输入，不等待 renderer 每个输入的处理。每阶段保留 450 ms 收尾；拥塞时输入会合并或延迟，快速 pan 可能仍收到前一阶段滞留的 wheel，这也是连续交互的实际背压表现。实际 RAF 路径不保证相同。

帧数据不启用 tracing，native timeline 另外采集。p95/max 是 RAF interval，不是显示器呈现 FPS。DOM 为整个页面的元素数；mount/unmount 统计真实卡片 view，并另外记录 wrapper 和 Sandbox Workspace 的生命周期。React commit 为根级提交数，不等于每张卡片都 render。trace 的 style/layout/paint/hit-test 是各自的累计区间，可能嵌套，不能相加当作总时间。

benchmark 页面拦截持久写入和 WebSocket，只生成本地临时卡；测试脚本使用页面真实加载的 Vite store URL，拒绝空场景。前后 viewport 尺寸、输入卡片数据和持久展示选择相同；实际 mounted 数因 overscan 改变，屏幕内数量也可能因世界级临时避让和剔除边界略有差异。

这不是内置浏览器、桌面运行时或生产构建的性能结论。近视野的 overscan 和几何代理有额外 DOM/订阅成本，需与帧间隔一起评价，不能仅报告远视野收益。

## 复现

```powershell
cd frontend
node scripts/profile-stress-zoom.mjs --origin http://127.0.0.1:5174 --label after-mixed --scene mixed --zoom .12 --phases pan,zoom,reverse,rapidPan
node scripts/profile-stress-zoom.mjs --origin http://127.0.0.1:5174 --label trace-mixed --scene mixed --zoom .12 --phases pan,zoom,reverse,rapidPan --trace
# 将 --scene 分别改为 text / sandbox，--zoom 改为 1；before origin 指向固定源码快照。
node scripts/summarize-card-rendering.mjs ../.outputs/card-rendering-comparison.json <report.json ...>
```

## 验证范围

最终源码的全量 Vitest：133 个文件、833 项测试通过；生产构建及 `git diff --check` 通过。最后一轮新增/更新的 Playwright 为 3 项通过，使用独立 Edge 和隔离后端。

- 单元回归覆盖 screen-space 阈值滞回、safe region、嵌套/负坐标、1000 节点 hydration 数量、交互租约、指针事务、geometry-only 失效、草稿/remount/revision 和私密草稿不持久化。
- 独立 Playwright 覆盖 1000 卡远视野/放大、选中/焦点/失焦、草稿恢复、边缘小幅往返零 view churn、轻量卡点击与拖拽、图层缓存名额、远视野连线及离屏端点几何；现有 wheel 增益、反向和指针锚定测试通过。
- 现有 3 个连接目标测试单独通过，6 个文本选择/仅标题拖动用例通过。
- `card-surface-policy` 的 Rename 按钮被 `.card-name` 拦截导致超时，已在改动前快照中复现；不计作通过。整组 relationship 测试中先运行远距离 terrain 测试会留下相机偏移，导致后续卡片不可见；相关连接测试在新隔离世界单独运行通过。没有声称全部 E2E 通过。

## Benchmark results (2026-09-29)

以下表格记录第一轮虚拟化重构的结果；后续视觉与阈值修订的增量对比见文末，不能将这组原始数据当作修订后源码的测量。

Values use before → after. Frame cells are **p95 / max, ms**. Raw measurements and source reports: [comparison.json](../../../.outputs/card-rendering-comparison.json).

### Pan and zoom, without tracing

| Scene | Zoom | Pan before | Pan after | Zoom before | Zoom after |
| --- | ---: | ---: | ---: | ---: | ---: |
| Mixed | 0.12 | 116.7 / 150.0 | 33.3 / 50.0 | 33.4 / 116.8 | 16.8 / 16.8 |
| Mixed | 1 | 16.8 / 16.8 | 16.8 / 16.8 | 16.8 / 33.3 | 16.8 / 33.3 |
| Text | 0.12 | 16.8 / 49.9 | 16.8 / 33.3 | 16.8 / 33.4 | 16.8 / 16.8 |
| Text | 1 | 16.8 / 17.5 | 16.8 / 16.8 | 16.8 / 33.3 | 16.8 / 33.3 |
| Sandbox | 0.12 | 950.1 / 999.9 | 33.3 / 83.4 | 433.3 / 950.0 | 33.3 / 49.9 |
| Sandbox | 1 | 16.8 / 33.3 | 16.8 / 33.3 | 16.8 / 33.4 | 16.7 / 50.0 |

### Continuous/reversing input, without tracing

| Scene | Zoom | Reverse zoom before | Reverse zoom after | Rapid pan before | Rapid pan after |
| --- | ---: | ---: | ---: | ---: | ---: |
| Mixed | 0.12 | 83.2 / 133.5 | 16.8 / 16.8 | 400.1 / 433.3 | 33.4 / 33.4 |
| Mixed | 1 | 16.8 / 16.8 | 16.8 / 16.8 | 16.8 / 16.9 | 16.8 / 16.8 |
| Text | 0.12 | 33.3 / 33.4 | 16.8 / 16.8 | 50.0 / 66.7 | 16.8 / 33.4 |
| Text | 1 | 16.7 / 16.8 | 16.8 / 16.8 | 16.8 / 16.8 | 16.8 / 16.8 |
| Sandbox | 0.12 | 333.4 / 333.4 | 16.8 / 33.3 | 1549.9 / 1549.9 | 33.4 / 33.5 |
| Sandbox | 1 | 16.7 / 16.8 | 16.8 / 16.8 | 16.8 / 16.8 | 16.8 / 33.3 |

### Initial footprint

Mounted includes overscan. Heavy counts here mean Sandbox Workspace instances, the workspace type in these default-presentation workloads. Full renderers for ordinary Preview cards are counted by LOD in the JSON for the new implementation; baseline LOD tags are unavailable (`null` in the summary).

| Scene | Zoom | On screen | Mounted views | DOM elements | Sandbox Workspace |
| --- | ---: | ---: | ---: | ---: | ---: |
| Mixed | 0.12 | 195 → 197 | 195 → 403 | 19015 → 5300 | 48 → 0 |
| Mixed | 1 | 2 → 2 | 2 → 5 | 1255 → 3337 | 1 → 3 |
| Text | 0.12 | 187 → 187 | 187 → 359 | 8824 → 4687 | 0 → 0 |
| Text | 1 | 2 → 2 | 2 → 3 | 1051 → 3009 | 0 → 0 |
| Sandbox | 0.12 | 197 → 206 | 197 → 395 | 49990 → 6052 | 197 → 0 |
| Sandbox | 1 | 3 → 3 | 3 → 14 | 1712 → 4614 | 3 → 14 |

### View lifecycle and React commits

Mount cells are mounts / unmounts. React commits are root-level counts. Geometry-wrapper and heavy-view counts for every phase are in comparison.json.

| Scene | Zoom | Phase | Views before | Views after | Heavy before | Heavy after | Commits |
| --- | ---: | --- | ---: | ---: | ---: | ---: | ---: |
| Mixed | 0.12 | pan | 430/428 | 207/169 | 118/116 | 1/0 | 370 → 191 |
| Mixed | 0.12 | zoom | 139/139 | 0/0 | 34/34 | 0/0 | 214 → 128 |
| Mixed | 0.12 | reverse | 137/137 | 0/0 | 32/32 | 0/0 | 64 → 45 |
| Mixed | 0.12 | rapidPan | 422/422 | 198/198 | 114/114 | 0/0 | 69 → 135 |
| Mixed | 1 | pan | 2/2 | 12/11 | 0/0 | 2/2 | 127 → 168 |
| Mixed | 1 | zoom | 0/0 | 0/0 | 0/0 | 0/0 | 167 → 129 |
| Mixed | 1 | reverse | 0/0 | 1/0 | 0/0 | 0/0 | 47 → 51 |
| Mixed | 1 | rapidPan | 4/4 | 12/12 | 0/0 | 4/4 | 126 → 149 |
| Text | 0.12 | pan | 444/444 | 228/163 | 0/0 | 0/0 | 235 → 183 |
| Text | 0.12 | zoom | 132/132 | 0/0 | 0/0 | 0/0 | 218 → 131 |
| Text | 0.12 | reverse | 130/130 | 0/0 | 0/0 | 0/0 | 59 → 49 |
| Text | 0.12 | rapidPan | 443/443 | 202/202 | 0/0 | 0/0 | 140 → 141 |
| Text | 1 | pan | 2/2 | 6/5 | 0/0 | 0/0 | 127 → 149 |
| Text | 1 | zoom | 1/1 | 0/0 | 0/0 | 0/0 | 129 → 168 |
| Text | 1 | reverse | 1/1 | 0/0 | 0/0 | 0/0 | 49 → 50 |
| Text | 1 | rapidPan | 2/2 | 4/4 | 0/0 | 0/0 | 124 → 134 |
| Sandbox | 0.12 | pan | 432/423 | 218/160 | 432/423 | 1/0 | 385 → 191 |
| Sandbox | 0.12 | zoom | 421/421 | 0/0 | 421/421 | 0/0 | 214 → 167 |
| Sandbox | 0.12 | reverse | 118/220 | 0/0 | 118/220 | 0/0 | 44 → 44 |
| Sandbox | 0.12 | rapidPan | 261/261 | 201/201 | 261/261 | 0/0 | 42 → 134 |
| Sandbox | 1 | pan | 2/2 | 5/8 | 2/2 | 5/8 | 131 → 169 |
| Sandbox | 1 | zoom | 1/1 | 0/0 | 1/1 | 0/0 | 169 → 131 |
| Sandbox | 1 | reverse | 1/1 | 0/0 | 1/1 | 0/0 | 51 → 49 |
| Sandbox | 1 | rapidPan | 6/6 | 10/10 | 6/6 | 10/10 | 139 → 170 |

### Native timeline, separate traced runs

Cumulative ms within each input phase; event intervals can nest. These numbers must not be summed or mixed with the frame samples above.

| Scene | Zoom | Phase | Style | Layout | Paint | Hit-test | Layerize |
| --- | ---: | --- | ---: | ---: | ---: | ---: | ---: |
| Mixed | 0.12 | pan | 1309.1 → 386.9 | 245.8 → 52.6 | 762.6 → 229.0 | 1731.3 → 189.6 | 1017.6 → 1119.9 |
| Mixed | 0.12 | zoom | 503.7 → 29.8 | 111.8 → 24.5 | 327.8 → 204.7 | 185.5 → 192.6 | 622.8 → 601.7 |
| Mixed | 0.12 | reverse | 249.5 → 8.8 | 78.7 → 6.7 | 95.9 → 60.4 | 117.1 → 50.0 | 115.5 → 225.8 |
| Mixed | 0.12 | rapidPan | 507.3 → 291.5 | 128.6 → 26.5 | 105.6 → 149.5 | 149.8 → 268.2 | 165.4 → 473.0 |
| Mixed | 1 | pan | 34.9 → 75.2 | 6.7 → 15.4 | 34.4 → 50.4 | 31.5 → 34.9 | 50.0 → 57.1 |
| Mixed | 1 | zoom | 29.4 → 22.1 | 9.8 → 18.3 | 50.7 → 46.1 | 21.1 → 24.1 | 40.9 → 30.1 |
| Mixed | 1 | reverse | 8.9 → 9.0 | 2.8 → 6.3 | 12.3 → 17.0 | 6.5 → 9.2 | 13.5 → 13.7 |
| Mixed | 1 | rapidPan | 27.8 → 52.9 | 7.0 → 15.9 | 31.3 → 44.2 | 54.1 → 100.2 | 30.0 → 32.0 |
| Text | 0.12 | pan | 783.4 → 319.7 | 138.3 → 22.8 | 454.5 → 114.2 | 810.9 → 202.9 | 922.7 → 530.9 |
| Text | 0.12 | zoom | 420.9 → 22.3 | 74.9 → 14.1 | 164.7 → 86.7 | 179.0 → 110.3 | 477.4 → 306.8 |
| Text | 0.12 | reverse | 138.8 → 8.6 | 32.3 → 4.8 | 69.5 → 29.0 | 45.5 → 37.5 | 191.3 → 129.1 |
| Text | 0.12 | rapidPan | 439.8 → 272.4 | 104.3 → 20.3 | 296.3 → 85.6 | 457.6 → 395.6 | 303.1 → 257.7 |
| Text | 1 | pan | 30.9 → 56.6 | 6.4 → 11.3 | 28.6 → 38.6 | 31.9 → 35.7 | 44.5 → 49.4 |
| Text | 1 | zoom | 23.0 → 32.2 | 7.2 → 23.9 | 26.2 → 46.7 | 14.1 → 23.1 | 26.8 → 34.9 |
| Text | 1 | reverse | 9.5 → 8.9 | 2.7 → 5.9 | 9.7 → 13.1 | 5.1 → 5.9 | 12.1 → 11.7 |
| Text | 1 | rapidPan | 26.0 → 35.8 | 7.1 → 10.6 | 25.1 → 35.6 | 55.3 → 92.4 | 26.7 → 27.5 |
| Sandbox | 0.12 | pan | 2372.5 → 411.3 | 392.2 → 55.3 | 1503.9 → 394.2 | 3321.7 → 228.8 | 1496.0 → 1202.7 |
| Sandbox | 0.12 | zoom | 880.2 → 29.4 | 225.1 → 22.7 | 530.3 → 403.6 | 737.7 → 153.8 | 986.0 → 485.8 |
| Sandbox | 0.12 | reverse | 237.5 → 8.1 | 54.9 → 6.1 | 100.1 → 103.3 | 178.9 → 42.6 | 156.9 → 208.5 |
| Sandbox | 0.12 | rapidPan | 307.4 → 284.4 | 91.2 → 25.9 | 36.8 → 271.2 | 93.4 → 297.1 | 34.4 → 581.0 |
| Sandbox | 1 | pan | 48.5 → 84.1 | 12.8 → 18.6 | 56.4 → 78.9 | 48.1 → 82.3 | 67.9 → 77.7 |
| Sandbox | 1 | zoom | 23.8 → 22.6 | 7.6 → 12.4 | 41.5 → 55.4 | 19.7 → 42.8 | 32.5 → 33.2 |
| Sandbox | 1 | reverse | 10.4 → 8.7 | 3.5 → 4.8 | 15.6 → 21.8 | 7.7 → 14.9 | 15.1 → 15.5 |
| Sandbox | 1 | rapidPan | 48.1 → 61.9 | 14.7 → 18.9 | 45.3 → 62.7 | 83.5 → 138.7 | 42.2 → 47.0 |

### Interpretation

- Mixed overview: 197 cards actually on screen, 403 views including overscan, zero full renderers initially. The interaction trace retains one Sandbox after it receives focus. Pan p95 improves 116.7 → 33.3 ms; max improves 150.0 → 50.0 ms. Zoom mounts/unmounts drop 139/139 → 0/0.
- Sandbox overview: pan p95 improves 950.1 → 33.3 ms, with zero full Sandbox instances initially and one after interaction. This is a large reduction in heavy DOM and lifecycle cost, not a claim of universal 60 FPS.
- Text overview: pan p95 remains 16.8 ms, max decreases 49.9 → 33.3 ms; rapid pan p95 improves 50.0 → 16.8 ms.
- Near-view p95 stays around 16.8 ms. Overscan/proxies increase initial DOM, some mount/commit counts and native costs. The Sandbox near-view zoom maximum is 33.4 → 50.0 ms in this single sample; there is no claim that every metric improves.
- Rapid-input baselines can coalesce input and produce very few RAF samples (Sandbox rapid pan: six intervals). Read their maxima and input backlog together with p95, not as a stable distribution.
- These are paired local runs, not repeated-run confidence intervals. Timeline startup itself causes long first-frame samples; only untraced frame data is used above.

## 语义缩放修订：外观与交互（2026-09-29）

第一轮的最短边阈值让小卡片长时间停留在摘要视图；mid 的几行文字也没有保留 Workspace 结构。本轮改用面积等效的屏幕尺寸，并区分紧凑卡片和多面板 Workspace。

| 保存的展示形式 | 进入 mid / 退出 mid | 进入 full / 退出 full |
| --- | ---: | ---: |
| Node | 16 / 12 px | 32 / 26 px |
| Preview | 44 / 36 px | 80 / 64 px |
| Inspector | 48 / 38 px | 160 / 132 px |
| Workspace | 48 / 38 px | 200 / 168 px |

例如 96×96 Node 在 zoom≈0.34、224×300 Preview 在 zoom≈0.31 已进入完整交互；默认 1020×700 Sandbox 在 zoom≈0.24（约 245×168 屏幕像素）进入完整 Workspace。2000×800 Sandbox 即使在 zoom=0.12 也使用 faithful mid，而 3000×1500 已是 full。选择、焦点和编辑不受这些阈值限制。

Far 是类型图标和名称；普通 mid 沿用真实卡片的标题栏比例、图片和有上限的文档摘要。Sandbox mid 是从现有模型生成的静态工作区缩略图，包含工具栏、文件侧栏、文件预览、终端及当前 Settings 页签。它使用单个 SVG 图片资源，内部图形不成为页面 DOM 或命中测试目标；本征长边 320 px，按内容、主题和尺寸共享最多 128 个缓存条目。用户文本进行 XML 转义，文件内容只取已浏览文件的最多 1600 字符，运行时绑定和文件路径必须匹配。没有创建隐藏的完整视图来截图。

这一阶段不会主动访问文件或插件。第三方工作区没有宿主可读的轻量内容模型时，显示其图标、名称和公开摘要；不宣称任意插件都拥有像素级截图。放大或交互后仍使用原插件 renderer。

可查看浏览器实际截图：[中距离](../../../.outputs/semantic-lod-mid.png)、[常用距离](../../../.outputs/semantic-lod-near.png)。其中同一 zoom 下，大 Sandbox 可以是 full，小 Sandbox 是 mid；普通卡片在常用距离已经 full。

同尺寸卡片集中越过阈值时，自动 renderer 替换会分批完成：每次策略更新最多 12 个轻量切换、3 个 full 升级，剩余工作在后续动画帧重新按最新 camera 计算。反向缩放不会执行旧目标，交互 pin 始终立即升级；卸载画布会取消待执行工作。Preview 的 mid 退出阈值高于最远视野下的默认投影尺寸，避免缩回总览后仍保留整批中距离视图。

分批切换只通知发生变化的卡片。React Flow 的节点身份、data 和几何投影不会随同卡片的 far/mid/full 转换而改变，避免每个批次触发全部 NodeWrapper 和 minimap 的状态检查。样式也关闭静态标题栏继承的展开动画，静态代理不执行表面展开过渡。生命周期计数器已覆盖稳定交互 wrapper 内的真实卡片，并对同一批 mutation 去重。

本轮验证：全量 134 个 Vitest 文件、838 项测试通过，生产构建通过。10 项独立 Edge/Playwright 测试通过，包含普通距离的大小卡片分级、图片解码、按下即升级、首次点击选择并打开、Workspace 标题拖拽、Ctrl 点击、pointercancel、离屏边缘、草稿恢复、连线、6 种卡片的文本选择/仅标题拖拽及滚轮锚定。最终合成层标记与 CSS 修改后，再次通过 4 项卡片分级/滚轮测试，并断言静态图片允许缓存、完整 Workspace 不进入该缓存。连接测试现在恢复原 viewport preferences，避免影响后续用例。不是全量 E2E 或桌面运行时验收。

增量 benchmark 与第一轮完成后的源码快照比较，沿用上述浏览器、1000 卡输入和无 tracing 测量方法。zoom=0.12 检查远视野，zoom=0.35 检查常用距离；camera、尺寸和持久展示配置保持一致。DOM 比最简占位更丰富，不能将“仍没有重 Workspace”理解为所有指标零成本。原始数据见 [semantic-lod-comparison.json](../../../.outputs/semantic-lod-comparison.json)。


### Incremental frame comparison (previous LOD → current semantic LOD)

Each frame cell is p95 / max in milliseconds. These are untraced, sequential local runs, one sample per scene; the current frontend was started in a fresh isolated Vite process (5190), with the immutable previous implementation served separately (5189). This avoids accumulated HMR history. These figures do not establish repeated-run confidence intervals.

| Scene | Zoom | pan | zoom |
| --- | --- | --- | --- |
| mixed | 0.12 | 33.3/50.0 → 33.4/50.1 | 16.8/16.8 → 49.9/66.7 |
| mixed | 0.35 | 16.8/33.4 → 16.8/33.3 | 16.8/16.8 → 16.8/16.8 |
| text | 0.12 | 16.8/33.5 → 33.3/33.5 | 16.8/16.8 → 33.4/33.4 |
| text | 0.35 | 16.8/16.8 → 16.8/16.9 | 16.8/16.9 → 16.8/16.8 |
| sandbox | 0.12 | 33.4/83.4 → 33.4/66.6 | 33.4/50.0 → 16.8/16.8 |
| sandbox | 0.35 | 16.8/49.9 → 33.3/66.7 | 16.8/33.4 → 16.8/33.4 |

| Scene | Zoom | reverse | rapidPan |
| --- | --- | --- | --- |
| mixed | 0.12 | 16.8/16.8 → 33.4/49.9 | 33.4/50.0 → 50.1/66.7 |
| mixed | 0.35 | 16.8/16.8 → 16.7/16.8 | 16.8/33.3 → 33.3/50.0 |
| text | 0.12 | 16.8/16.8 → 33.4/50.1 | 33.3/33.4 → 33.4/66.7 |
| text | 0.35 | 16.8/16.8 → 16.8/16.8 | 16.8/16.9 → 16.8/16.8 |
| sandbox | 0.12 | 33.3/33.4 → 16.8/16.8 | 50.0/66.7 → 66.6/66.8 |
| sandbox | 0.35 | 33.3/33.4 → 16.8/33.4 | 33.4/50.1 → 33.4/66.7 |

### Incremental footprint

Initial settled scene, before benchmark input. Mounted views include overscan; Heavy means Sandbox Workspace instances. Full is the renderer count, including ordinary cards. Page DOM excludes the contents of SVG image resources.

| Scene | Zoom | On screen | Mounted | DOM | Heavy | Full |
| --- | --- | --- | --- | --- | --- | --- |
| mixed | 0.12 | 197 → 197 | 403 → 403 | 5301 → 7353 | 0 → 0 | 0 → 0 |
| mixed | 0.35 | 24 → 24 | 46 → 46 | 4526 → 5749 | 11 → 11 | 11 → 46 |
| text | 0.12 | 187 → 187 | 359 → 359 | 4688 → 6581 | 0 → 0 | 0 → 0 |
| text | 0.35 | 22 → 22 | 42 → 42 | 3229 → 4671 | 0 → 0 | 0 → 42 |
| sandbox | 0.12 | 206 → 206 | 402 → 394 | 6109 → 8113 | 0 → 0 | 0 → 0 |
| sandbox | 0.35 | 28 → 28 | 56 → 56 | 9781 → 9935 | 56 → 56 | 56 → 56 |

### Incremental lifecycle, commits, style and layout

Views and Heavy are mounts/unmounts during the phase, not live instance counts. Commits count React root commits. Style and Layout are cumulative untraced CDP durations in milliseconds. Geometry wrappers remain mounted (0/0 lifecycle in every phase); visibility and renderer hydration are measured separately.

| Scene | Zoom | Phase | Views | Heavy | Commits | Style ms | Layout ms |
| --- | --- | --- | --- | --- | --- | --- | --- |
| mixed | 0.12 | pan | 207/169 → 207/169 | 1/0 → 1/0 | 191 → 190 | 338.3 → 395.3 | 45.4 → 48.0 |
| mixed | 0.12 | zoom | 0/0 → 0/0 | 0/0 → 0/0 | 129 → 186 | 20.1 → 375.8 | 17.0 → 80.1 |
| mixed | 0.12 | reverse | 0/0 → 0/0 | 0/0 → 0/0 | 47 → 66 | 8.1 → 317.6 | 6.0 → 48.4 |
| mixed | 0.12 | rapidPan | 198/198 → 198/198 | 0/0 → 0/0 | 131 → 119 | 266.2 → 349.1 | 23.3 → 28.3 |
| mixed | 0.35 | pan | 27/22 → 27/22 | 7/5 → 7/5 | 199 → 235 | 172.9 → 173.7 | 19.0 → 20.6 |
| mixed | 0.35 | zoom | 0/0 → 0/0 | 0/0 → 0/0 | 142 → 152 | 21.8 → 23.4 | 17.1 → 17.4 |
| mixed | 0.35 | reverse | 0/0 → 0/0 | 0/0 → 0/0 | 55 → 67 | 8.8 → 9.7 | 5.8 → 6.1 |
| mixed | 0.35 | rapidPan | 28/24 → 34/26 | 8/8 → 10/9 | 186 → 196 | 159.8 → 192.1 | 19.2 → 25.2 |
| text | 0.12 | pan | 228/163 → 228/163 | 0/0 → 0/0 | 183 → 183 | 305.3 → 321.4 | 19.3 → 19.9 |
| text | 0.12 | zoom | 0/0 → 0/0 | 0/0 → 0/0 | 129 → 185 | 20.7 → 364.4 | 11.6 → 53.6 |
| text | 0.12 | reverse | 0/0 → 0/0 | 0/0 → 0/0 | 48 → 74 | 8.4 → 360.2 | 4.2 → 38.8 |
| text | 0.12 | rapidPan | 202/202 → 202/202 | 0/0 → 0/0 | 139 → 132 | 242.3 → 301.9 | 17.2 → 18.8 |
| text | 0.35 | pan | 28/21 → 28/21 | 0/0 → 0/0 | 161 → 227 | 99.7 → 116.0 | 10.2 → 14.0 |
| text | 0.35 | zoom | 0/0 → 0/0 | 0/0 → 0/0 | 130 → 155 | 21.9 → 23.7 | 18.1 → 12.8 |
| text | 0.35 | reverse | 0/0 → 0/0 | 0/0 → 0/0 | 49 → 64 | 8.1 → 9.2 | 6.1 → 4.4 |
| text | 0.35 | rapidPan | 33/26 → 29/22 | 0/0 → 0/0 | 156 → 216 | 95.8 → 114.2 | 9.8 → 13.6 |
| sandbox | 0.12 | pan | 211/160 → 219/160 | 1/0 → 1/0 | 191 → 190 | 386.6 → 415.0 | 47.8 → 51.0 |
| sandbox | 0.12 | zoom | 0/0 → 0/0 | 0/0 → 0/0 | 155 → 167 | 26.3 → 26.4 | 19.1 → 19.6 |
| sandbox | 0.12 | reverse | 0/0 → 0/0 | 0/0 → 0/0 | 40 → 47 | 7.3 → 8.6 | 4.9 → 5.6 |
| sandbox | 0.12 | rapidPan | 199/203 → 199/203 | 0/0 → 0/0 | 120 → 108 | 344.0 → 363.9 | 28.5 → 30.5 |
| sandbox | 0.35 | pan | 18/16 → 18/16 | 18/16 → 18/16 | 249 → 242 | 183.3 → 208.7 | 25.2 → 25.7 |
| sandbox | 0.35 | zoom | 0/0 → 0/0 | 0/0 → 0/0 | 191 → 195 | 30.6 → 31.0 | 15.5 → 17.4 |
| sandbox | 0.35 | reverse | 0/0 → 0/0 | 0/0 → 0/0 | 68 → 68 | 11.6 → 11.1 | 4.4 → 4.5 |
| sandbox | 0.35 | rapidPan | 30/26 → 30/26 | 30/26 → 30/26 | 188 → 179 | 194.3 → 200.6 | 31.8 → 29.6 |

### Incremental native trace (separate run)

Mixed, zoom=0.12; cumulative inclusive event time in milliseconds. Events may nest and must not be added together. The traced run is used only for native cost attribution, not the frame table above.

| Phase | Style | Layout | Paint | Hit-test | Layerize |
| --- | --- | --- | --- | --- | --- |
| pan | 375.4 → 448.1 | 53.1 → 59.9 | 237.6 → 362.9 | 145.7 → 205.0 | 1135.2 → 1287.5 |
| zoom | 33.5 → 309.6 | 30.4 → 84.1 | 253.5 → 307.6 | 201.6 → 293.5 | 667.3 → 928.5 |

### 本轮结果与限制

- 常用距离 zoom=0.35 下，混合场景的完整 renderer 从 11 增至 46，普通卡片从 0 增至 42；混合与普通场景 pan/zoom p95 均为 16.8 ms。Sandbox 的完整实例数仍是 56，没有因降低普通卡阈值而增加。
- 远视野实际可见 187–206 张卡，包含 overscan 的挂载视图为 359–403，三个场景初始均为 0 个 full renderer。交互最多保留 1 个获得焦点的完整 Sandbox；缩放及快速反向缩放的视图 mount/unmount 仍为 0/0。保留了第一轮减少完整 DOM 和边缘抖动的主要收益。
- 外观提升有成本：远视野 mixed 的 DOM 从 5301 增至 7353，zoom p95/max 从 16.8/16.8 升至 49.9/66.7 ms；text 的 pan/zoom p95 从 16.8 增至约 33.4 ms。Sandbox 远视野 zoom 改善至 16.8 ms，但快速 pan p95 从 50.0 增至 66.6 ms；常用距离 Sandbox pan p95 也从 16.8 增至 33.3 ms。当前版本不能宣称所有场景均保持上一版极简占位的帧率，更不能宣称稳定 60 FPS。
- 混合远视野 trace 的 zoom 样式、布局、绘制及 hit-test 均增加，而视图仍未 mount/unmount；性能代价并非来自重新挂载大量 Workspace。单轮 trace 只能定位代价分布，不能证明每一项回退都由某个 CSS 属性独立造成。React commit 是根级次数，不是 commit 耗时，也不能单凭次数判定卡顿。
- 第一轮报告中未重构版本的 mixed/Sandbox 远视野 pan p95 为 116.7/950.1 ms；当前为 33.4/33.4 ms。这个历史数据说明重型 DOM 优化收益仍在，但不替代本轮与上一版 LOD 的直接对比。
- 本轮 14 个报告均通过输入数量、页面错误和合成卡片后端请求检查；没有修改用户的持久 visual level、卡片位置、尺寸或草稿。完整数据含每阶段 DOM、生命周期、commit、style/layout 和原始报告路径。
