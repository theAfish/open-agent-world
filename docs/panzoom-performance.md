# 画布平移与缩放性能（2026-09-25）

本轮基于 `dev` 的 `5c79c40`，保留 React Flow、`onlyRenderVisibleElements`、chunk 获取、terrain geometry worker、插件 lazy import 和现有 surface 层级。没有增加性能模式、配置项或卡牌角标。

**结论：本轮减少了不必要的文档读取、隐藏界面工作和大 Legion 筛选成本，但没有证明高密度真实卡牌已经获得普遍的平滑 pan/zoom。生产保留优化后的 SVG。** 浏览器结果波动明显，最终主矩阵的混合 SVG 帧间隔 p95 为 433.1 ms，基线为 416.4 ms；不能用中途某次较好的结果代替最终代码的测量。

## 实际瓶颈与最终主对照

真实源码 profile 确认 Legion descendants 筛选反复扫描全数组，局部 parent→children 查找表显著降低该函数成本。数据侧确认 Task Board preview 读取了不需要的完整任务文档，已打开后隐藏的 inspector 也保留了重组件及订阅。连续 zoom 的 terrain tile React 更新被移除，但浏览器仍需更新描边样式并绘制。

密集混合场景的剩余成本同时来自卡牌与地形。最终 SVG 的 `RecalcStyleDuration` 为 6.37 s，Script 2.93 s，Layout 0.28 s；隐藏地形后 style 仍为 5.93 s。改成已有 node shell 后 style 为 2.47 s；隐藏地形加 shell 为 1.89 s。这里的 style 是浏览器样式计算，不等于 React render，也不能仅据这些总量归因到某条 CSS selector。后续应定位剩余样式失效/合成工作，而不是继续猜测 geometry worker 或把所有瓶颈归于 SVG。

主对照来源为 `.outputs/panzoom-before-1790341087416/report.json` 和最终 `.outputs/panzoom-after-1790343017098/report.json`，均 CPU 1×、关闭详细 trace。单元格为 Before → After，帧间隔单位 ms，越小越好：

| 场景 | 全程 p95 | Pan p95 | Zoom p95 | CDP Task 秒 |
| --- | ---: | ---: | ---: | ---: |
| 100 Text / SVG | 416.3 → 383.0 | 416.6 → 533.0 | 349.8 → 333.1 | 5.67 → 5.35 |
| 258 混合 / SVG / 真实内容 | 416.4 → 433.1 | 316.4 → 316.4 | 432.9 → 532.9 | 13.35 → 13.50 |
| 混合 / 隐藏地形 / 真实内容 | 282.6 → 266.5 | 233.2 → 249.8 | 333.1 → 316.3 | 11.18 → 12.16 |
| 混合 / SVG / node shell | 333.3 → 482.9 | 333.0 → 466.3 | 399.7 → 499.6 | 8.19 → 8.13 |
| 混合 / 隐藏地形 / node shell | 166.4 → 116.6 | 149.4 → 116.5 | 166.7 → 133.4 | 7.11 → 5.90 |
| 混合 / Canvas / 真实内容 | — → 349.8 | — → 233.2 | — → 382.9 | — → 14.24 |

最终混合 SVG 的 long task 为 66 次 / 11.01 s（之前 66 次 / 10.89 s）；全局 React commits 217 → 229，卡牌 DOM mount/unmount 均为 671/671。100 Text 的 commits 197 → 191，mount/unmount 均为 258/258。因此不能宣称已减少 viewport 边缘的挂载次数，收益主要在单次工作和数据层。SVG shell 的单次结果甚至更慢，不能只展示 hidden-shell 的改善。

| 内存/请求指标 | 100 Text SVG | 混合真实 SVG | 混合 SVG shell |
| --- | ---: | ---: | ---: |
| 强制 GC 后 JS heap，MiB | 28.32 → 28.69 | 45.64 → 43.89 | 44.04 → 37.73 |
| 全程 API 请求（不含 Vite 模块） | 63 → 61 | 230 → 214 | 171 → 56 |
| 手势阶段请求 | 29 → 29 | 102 → 89 | 98 → 34 |
| 完整 document GET | 0 → 0 | 135 → 2 | 90 → 0 |
| summary document GET | 0 → 0 | 0 → 106 | 0 → 0 |
| 导航至首张卡牌，ms | 4698 → 5337 | 1926 → 3172 | 2188 → 1783 |

混合 SVG 初始/结束的未 GC heap 为 108.02/51.10 → 91.46/69.68 MiB，自动 GC 时点不同，不能据此断言峰值改善。GC 后保留量减少约 1.75 MiB；shell 减少约 6.31 MiB。原始 DOM counters 在 GC 前采集，可能含 detached DOM，不作为泄漏证据。Canvas GC 后 JS heap 为 44.39 MiB，**另有**约 56.92 MiB raster backing，峰值约 63.98 MiB，在 64 MiB 预算内；浏览器完整 GPU 内存未测得。

混合 SVG 同 URL document 重叠请求从 8 降为 2，均发生在预热；手势阶段两版都为 0。重复 URL 数不等于重复无效请求，3 秒摘要 freshness、运行更新、重新打开详情及取消请求交接都可能触发合法重读。没有声称请求数已经归零。完整数据改摘要的效果明显，但摘要传输也仍有成本。

各组合无 page error。完整精确数值、启动、DOM、请求分相位、React、微基准及限制整理于 `.outputs/panzoom-final-comparison.{json,md}`；原始截图、JSON 和服务日志保留于上述运行目录。

为检查时间顺序影响，最终主矩阵之后又依次运行原版和最终版（均无 trace），来源为 `.outputs/panzoom-before-1790343389427/report.json`、`.outputs/panzoom-after-1790343540348/report.json`。两轮的全程 p95 范围如下，未取最佳值替换主表，也未将中途代码当作最终版重复：

| 场景 | Before 两轮范围，ms | Final 两轮范围，ms |
| --- | ---: | ---: |
| 100 Text / SVG | 299.9–416.3 | 233.2–383.0 |
| 混合真实内容 / SVG | 416.3–416.4 | 416.2–433.1 |
| 混合真实内容 / Canvas | — | 349.8–416.3 |

两轮都不足以得出统计显著的整体提升。密集真实内容的最终 p95 与基线相当，仍有明显卡顿；Canvas 第二轮与 SVG 相当，未显示值得额外内存和清晰度代价的稳定优势。100 Text 存在改善迹象，但重叠范围与 pan 单项波动不支持给出可靠提升百分比。

## 实现与作用

| 范围 | 修改 | 解决的问题 |
| --- | --- | --- |
| `state/chunks.ts`、`state/worldStore.ts` | 比较四个 chunk 边界；相同 viewport 不发布状态，相同 coverage 只保存 camera | move end 不再创建同值 `activeChunkKeys`、触发依赖链或调用 `ensureChunks`；跨边界立即获取 |
| `state/chunks.ts` | 每次筛选只建立一次 parent→children 映射，按原顺序遍历 descendants | 大 Legion 中每个叶子反复扫描全卡牌数组的二次复杂度；没有维护全局空间索引 |
| `cards/CardFrame.tsx` | 仅 preview surface 挂载 `NodePreview`；已迁出草稿的宿主 inspector 在关闭时卸载；详情 footer/关闭按钮仅在 inspector 挂载 | node 不初始化隐藏 preview；关闭后的 editor/list/subscription 不永久保留；去掉折叠 Agent 的第二个隐藏背包及其全卡牌扫描，保留可见背包入口 |
| `cards/TaskBoard.tsx`、`cards/taskBoardCache.ts`、`api/client.ts` | preview 获取 summary；按卡牌/scope/session 及是否部署的上下文隔离缓存；合并并发读取，按 revision 接收响应 | 不再为计数传输和解析 task 描述、依赖图及执行配置；完整读取也能提供 preview 摘要 |
| `backend/api/node_documents.py`、`backend/deployment_workspace.py` | 原 document GET 支持 `summary_only=true` | 复用原权限和部署投影，返回 revision 与 summary；原接口保持兼容 |
| `state/nodeSurfaces.ts`、Text/Conversation/Task Board | 使用现有临时 store 保存草稿、原始 revision、附件和未完成发送状态 | viewport culling、关闭后重开、会话切换不再丢失关键未保存输入；已确认删除清理对应条目 |
| `plugins/PluginSurface.tsx`、`plugins/sdk.ts`、Matcreator Task Board | 可选 `host.draft` 由宿主隔离 scope；研究任务 preview 读取轻量投影，隐藏 body 停止 collect 轮询 | 插件无需获得 session ID；preview 不再重复执行完整收集；编辑草稿可跨卸载恢复 |
| `state/cardState.ts` | 订阅最终解析出的 session 标量 | 该订阅仅在解析结果变化时触发重渲染；store 通知时仍需执行解析 |
| Agent/NodeExecution | 隐藏时停止运行信息读取、执行轮询及不必要的全量世界订阅 | 保留必要状态，减少隐藏表现层工作 |
| `canvas/ContourLayer.tsx`、`theme.css` | tile 不接收 zoom prop；父层 imperative 更新继承的描边/图层 CSS 变量 | 同 coverage/LOD 下连续缩放不重新运行 terrain React 树；描边仍按屏幕像素补偿 |
| `canvas/terrain.ts` | LOD hysteresis：低→中 0.48，中→低 0.42；中→高 1.20，高→中 1.10 | 避免在旧 0.45/1.15 阈值附近反复生成和替换 geometry；初次载入仍采用旧阈值 |
| `api/client.ts` | `normalizeWorldSnapshot` 保留有效 uint32 `terrain_seed` | 修复原有响应转换遗漏；此前不同世界都使用前端默认种子，重载不能呈现实际种子 |
| `canvas/TerrainCanvasExperiment.tsx`、`terrainRaster.ts`、`terrainRaster.worker.ts` | 开发期有界 tiled Canvas 对照 | 单独评估 raster/compositing 与内存、线宽的取舍，不直接替换默认 SVG |
| `scripts/benchmark-panzoom.mjs`、`scripts/profile-chunk-filter.cjs` | 真实 API 压力场景、浏览器观测及实际源码微基准 | 可重复运行；采样数据和截图只写入开发产物 |

表中前端宿主路径相对 `frontend/src/`，两个脚本位于 `frontend/scripts/`，Matcreator 实现在 `plugins/matcreator/frontend/TaskBoard.tsx` 与 `plugins/matcreator/oaw_matcreator/tasks.py`。对应单测和新增 `frontend/e2e/panzoom-lifecycle.spec.ts`、`frontend/e2e/terrain-canvas.spec.ts` 一并保留；`frontend/e2e/card-state-lifecycle.spec.ts` 更新为检查切回 A 时恢复未保存草稿。插件草稿能力的文档位于 `docs/developers/frontend.md`。

summary projection 减少的是传输和浏览器解析。后端仍读取、验证原 document 并调用已有 summarize，并未引入额外的持久化摘要表。Task Board 缓存在退订或请求结束时，将空闲且无在途请求的条目回收至 48 项目标；活跃/在途条目不按该上限淘汰，挂载批次也可能暂时超出，因此它是条目回收目标，不是硬字节上限。未保存草稿独立保留，不以 LRU 自动丢弃。草稿为当前页面的临时状态，页面刷新或 profile reset 会丢弃它们，不替代保存文档。

种子修复不移动卡牌，也不改地形生成算法。已有世界若保存的后端种子与旧默认值不同，地形外观会首次按其真实种子更新。性能对照固定为原默认种子 `0x5eeda11`，因此此修复没有改变比较中的地形拓扑。

## SVG 与 Canvas 实验

生产默认保留 SVG。开发环境内部使用 `/?terrainRenderer=canvas` 对照，生产构建不启用实验模块，也没有用户界面入口。

最终主矩阵 Canvas 的 p95 比 SVG 低（349.8 vs 433.1 ms），但 Task 更高（14.24 vs 13.50 s）、style 更高（7.55 vs 6.37 s）、long task 更多（82/11.68 s vs 66/11.01 s），并额外占用约 57 MiB backing。中途版本的 Canvas p95 为 432.9 ms。单次较低 p95 不足以证明稳定收益，加上细线略软和 zoom 暂态差异，不采用为生产 renderer。

单独启用详细 trace 的同轨迹数据位于 `.outputs/panzoom-after-1790343211995/report.json`。它增加采样开销，不参与上表帧间隔比较。下表为事件 duration 累计 ms；事件可能嵌套、跨线程，**不可相加成 wall time**，GPUTask 也不是纯 GPU 执行时间：

| 事件 | SVG 真实内容 | 隐藏地形真实内容 | Canvas 真实内容 |
| --- | ---: | ---: | ---: |
| Paint | 1134.4 | 852.8 | 958.5 |
| RasterTask | 929.8 | 230.1 | 269.6 |
| UpdateLayoutTree | 7408.2 | 6718.0 | 7756.0 |
| Layout | 318.7 | 366.3 | 315.9 |
| GPUTask | 18696.8 | 2782.6 | 10557.6 |

Canvas 确实减少了该次浏览器 trace 中的 raster 工作，但没有同步减少样式计算和总体主线程工作；worker raster 不等同于浏览器 RasterTask，缓存/合成也有成本。terrain off 仍保留 geometry generation，因而对照主要反映显示侧成本，而非 worker 生成成本。

实验保留被 Canvas 覆盖时隐藏的 SVG fallback DOM，zoom 仍可能承担该子树的样式计算。因此结论针对当前有界混合实现，不代表所有 Canvas-only 设计都无收益；本轮没有证据支持继续扩大 renderer 重写范围。

实验复用同一 seed、LOD、世界坐标和 geometry 路径；OffscreenCanvas worker 使用 `Path2D`、相同填充规则、颜色、透明度及 1.15/1.65 屏幕像素描边。主线程接收 bitmap 并合成，有界 LRU 缓存实际 canvas backing；总预算 64 MiB，另在该预算内预留一个任务的 raster/transfer/replacement 空间。失效结果会关闭 bitmap，淘汰会缩小 canvas，卸载或失败时终止 worker。高倍率或高 DPR 超出预算时释放缓存并回退 SVG。

连续 zoom 会暂时缩放已有位图；输入停止 120 ms 后，SVG 接替覆盖等待精确 DPR 和描边的新位图完成，缺少 raster 时也由 SVG 覆盖。这个暂态线宽/清晰度差异属于实验代价。`data-raster-bytes` 和 peak 指标只是 backing 字节估算，不能当作浏览器完整 GPU 内存。

## Legion 筛选的独立微基准

使用实际源码函数、四个 active chunks、实际 `cardIndex` 与 catalog，100 次预热后采样 300 次；Windows / Node 24.12.0，无 CPU throttle。八组场景验证了优化前后返回的卡牌和顺序完全一致。单位 ms，表内为 median / p95；这是函数耗时，不是 pan FPS。

| 卡牌数 / 结构 | Before | After |
| --- | ---: | ---: |
| 250 / 平铺 | 0.026 / 0.049 | 0.070 / 0.212 |
| 250 / 单 Legion | 0.755 / 1.424 | 0.084 / 0.157 |
| 250 / 三层 | 1.109 / 2.832 | 0.072 / 0.260 |
| 250 / 深度 10 | 3.217 / 5.548 | 0.168 / 0.478 |
| 1000 / 平铺 | 0.189 / 0.360 | 0.112 / 0.234 |
| 1000 / 单 Legion | 4.943 / 13.194 | 0.183 / 0.475 |
| 1000 / 三层 | 14.210 / 30.660 | 0.517 / 1.133 |
| 1000 / 深度 10 | 48.304 / 89.927 | 1.128 / 2.618 |

250 平铺有小幅额外 map 构建成本及采样噪声，不能宣称所有场景都更快。深层遍历仍与容器和子树规模有关；本轮收益足够支持局部查找表，不支持扩大为通用空间索引。原始结果位于 `.outputs/legion-filter-{before,after}.json`。从 `frontend` 运行 `node scripts/profile-chunk-filter.cjs ../.outputs/panzoom-baseline/frontend` 可重新采样；参数为优化前源码的 frontend 目录，省略时只测当前实现。

交付脚本再次实际运行成功，八组有序输出一致；结果保留在 `.outputs/legion-filter-reproduced.json`。复测 1000 单 Legion 的 median 为 3.49 → 0.11 ms，深度 10 为 59.04 → 0.73 ms，支持函数级收益，但没有用这些微基准替代浏览器整体结果。

## 有意保留的边界

- 没有第二套 virtualization、强制 overscan 或永久保留全部 DOM。继续使用现有一圈 chunk prefetch 与 React Flow culling，先降低 mount 代价，复用摘要并独立保存草稿。
- 没有通用 activation scheduler 或 `requestIdleCallback`；显式打开详情立即加载，不等待空闲窗口。
- 没有新增活动事件索引。已有 `useNodeActivity` shallow selector 保留；不能仅因存在扫描就称它为纯 pan 的主要瓶颈。
- 未改插件原有 surface 语义。未知第三方插件的 local React state 仍由插件负责；宿主不能自动序列化任意组件状态。可选草稿接口兼容旧插件，已用于内置研究任务板；不能声称所有第三方插件的离屏草稿都已自动解决。
- SVG 的 CSS 描边补偿仍可能触发 repaint。React 渲染减少需要与实际 browser paint/raster 数据一起判断。

## 复现

从 `frontend` 运行 `node scripts/benchmark-panzoom.mjs --label after --canvas --cpu 1 --repeats 1 --max-variant-ms 180000`。脚本创建独立 backend 数据目录，使用 8028/5188 端口，保留 JSON、截图和日志于 `.outputs/panzoom-*`；不重置日常工作区。不要与其他性能测试同时运行。`--frontend-root` 可指向相同依赖版本的优化前 frontend 副本，`--repeats` 控制重复次数。脚本默认 CPU throttle 为 4；本报告主对照显式使用 1，不将不同 throttle 的结果混在一起。

场景使用真实 API 创建的卡牌和实际卡牌组件，包括 100 张 Text、200+ 混合卡牌、带任务数据的 Task Board、图片、Agent、复杂插件、Legion/container 及访问过的 inspector。对照覆盖 terrain 显示/隐藏、真实内容/已有 node shell 和开发期 Canvas。shell 使用现有 96px node surface，因此尺寸与 preview 不同，不把它当作像素完全相同的 renderer 对照。

本机 Windows 10.0.19045、i7-1165G7 @ 2.80 GHz（8 logical）、31.41 GiB RAM，Chrome 154.0.8037.57 headless，Vite development build，1600×1000、DPR 1、初始 camera `(40,40,0.35)`。混合场景共 258 张：100 Text，加六类各 24 张（Task Board、Image、Agent、Conversation、Sandbox、XRD），76 张 Text 组成 Legion，另有 barracks 与 12 Agent。每个任务板 40 项、图片 1280×800、XRD 1800 点；访问四个 inspector 后关闭，手势期间启动两个 mock Agent 产生运行更新。

混合卡牌刻意重叠于前 12 列，行距 245，小于 preview 高度；初始加载 258 张、实际挂载 244 张。这是高密度压力场景，不代表普通稀疏画布。开始采样前要求挂载数稳定一秒、chunk 全部到达、数据请求结束、连接 online。手势为四次 1320px 的中键跨 chunk 平移（12 steps，往返），以及连续 wheel 放大/缩小并反复越过低/中 LOD；报告记录实际 transform，浏览器丢合并输入可能造成单段略短。terrain hidden 用 CSS visibility，geometry worker 仍运行。

首次卡牌延迟从导航到首个 frame 附着计算，包含开发服务器编译与启动，不能归因成单个 card mount 成本。rAF p95 是采样帧间隔，不直接换算“真实 FPS”；全局 commit 不等于各组件 render 次数。主矩阵每个组合一轮，额外重复用于检查波动，不能宣称统计显著。4× CPU 探索曾出现秒级卡顿且混合场景预热未完成，那批数据排除于主对照；这也意味着尚未完成低配实机的平滑度验收。

性能数字来自同机同脚本的测量；CPU throttle 只是浏览器模拟，不能代替低配实机验收。全局 React commit、DOM mount、main-thread task、paint/raster、请求和内存分别记录；嵌套 trace 时长不能相加当作总耗时。正式帧间隔比较默认关闭详细 trace；`--trace` 单独测 paint/raster，避免将高开销 tracing 的帧率当作产品帧率。`--seed-only` 可以先验证真实 API 测试数据；所有隔离环境固定相同地形 seed。

## 本轮之外

后续值得在低配实机的 production build 上重复采样，并对剩余的大量 style/layout 开销做更细的浏览器归因。真实运行时的事件频率、不同第三方插件 preview 的成本及显卡驱动内存也需要专门场景；本轮 mock runtime 和 canvas backing 估算不覆盖这些差异。只有证据显示它们占主导时，才继续考虑 preview 的短暂稳定窗口、活动事件索引或其他 renderer。本轮不扩展为新的调度、虚拟化或插件表单框架。

## 验证范围

生产构建 `npm.cmd run build` 通过（3699 modules）；现有大 chunk 提示仍在。实验 Canvas 由 DEV 条件隔离，生产产物未发现实验模块标记。相关前端测试分批运行，未将其描述为全仓库测试：

- worldStore、viewport/chunks、container/equipment/shadow：94 项。
- canvas、terrain、nodeSurfaces、glue、relationship、resize、layout 等 20 个文件：88 项。
- Task Board、共享缓存、PluginSurface、Matcreator：最终 37 项，覆盖过期响应、并发合并、失败隔离、summary/full 交错、保存期间卸载与新输入保护。
- Text、Conversation、cardState、AgentSchemaSettings：29 项，覆盖草稿、附件、session 切换和隐藏请求。
- CardFrame 与 SandboxWorkspace：12 项；API client：16 项，包括 seed 0、最大 uint32 和无效旧值。
- 后端 9 项：Task Board、session 隔离、部署 summary 和 Matcreator summary。

浏览器回归覆盖实际 selection、drag、resize、glue、边界 connection handles、插件 inspector/workspace、Legion/container、共享和 session scoped 文档、未保存草稿、离屏返回、保存、reload、minimap、宽视口、主题、seed、Canvas 内存回退。地形新测验证连续 pan 后 coverage、world transform 对齐、不同 seed 的实际 path 变化；高 DPR 超预算时 backing 回到 0 且 SVG 保持屏幕线宽。静态明暗主题截图无明显 seam，Canvas 细线略软，SVG 更清晰；静态截图不能证明任意连续 zoom 都无差异。

最终分批通过 20 项浏览器用例：`panzoom-lifecycle` 1、`selection-gesture` 2、`glue` 2、`task-board` 2、`card-state-lifecycle` 2、`viewport-grid` 1、`minimap` 1、`terrain-canvas` 2、`viewport-wide` 1、`legion-pan` 1、`plugin-frontend` 1、`relationship-canvas` 的 Agent boundary drag 1、`surface-resize` 的详情四角/Legion 左上角/plugin container 3。最后一次 Legion 用例在明确初始化 camera 后通过（6.2 s）。这不是整个 E2E 仓库的全量运行。

`panzoom-lifecycle.spec.ts` 额外验证 node 不读取任务文档、preview 只读 summary、inspector 才读 full；关闭详情卸载 body，跨多个 chunk 返回后两类草稿仍在，保存后由真实 API 核验。

测试中修正了既有定位问题：`Contents` 使用精确 textbox role；插件设置点击稳定图标而非缩放后的边界坐标；Legion 按实际画布尺寸检查 fit，并显式初始化 camera，避免继承其他测试的远处 viewport。E2E runner 支持复用指定 Vite cache 配置，超时也清理自己启动的浏览器及服务进程。初次冷启动预编译超时、旧定位失败不计为已通过结果。
