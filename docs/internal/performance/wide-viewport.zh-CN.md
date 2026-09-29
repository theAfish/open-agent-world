# 1000 卡最广视野性能诊断（2026-09-29）

基于 `9f3523b`，当前允许的最小缩放 `0.12`。结论：主要瓶颈是完整卡片，尤其是 Sandbox 工作区的 DOM 生命周期与浏览器渲染成本。视野边缘不断挂载/卸载重组件会放大开销；保留在视野内的重组件也继续产生命中检测、绘制和合成成本。缩放跨分块边界时，另有卡片避让计算造成长帧。

本次只增加诊断脚本和报告，没有修改产品渲染行为。

## 条件与边界

- 独立 headless Edge，Vite 开发构建，1920 × 1080、DPR 1、不降速 CPU。
- 使用开发面板相同的 `generateStressWorld(1000)`，保留确定性的类型、分布和默认展示状态。最初有 195 张卡实际挂载，其中 48 个完整 Sandbox 工作区；世界中仍有 1000 张卡。最小缩放并不意味着 1000 张同时可见。
- 测试浏览器单独投影空的持久世界、拦截 API 写操作和 WebSocket，仅在该浏览器内生成临时卡片。所有诊断替换通过页面请求拦截或 CSS 注入完成，不改变源码中的产品行为或用户保存的数据。
- Pan：中键两轮相同闭环，每轮 60 个点。Zoom：20 次放大、20 次缩小，每次 wheel delta 24，沿用现有平滑缩放。每阶段包含 450 ms 收尾。
- 所有 A/B 的卡片位置、React Flow 外框尺寸保持不变。`shell` 替换的是内部组件，不是切换到尺寸更小的 node 展示状态。
- 帧间隔来自 RAF，不能当作实际显示器呈现 FPS。输入发送等待浏览器响应，慢场景会延长测试时间。Pan 路径一致；Zoom 输入一致，但反向从当时显示的相机开始，实际中间路径和挂载数量可能不同。
- 详细 trace/CPU profile 单独采集，不与无 trace 的帧时间混为同一组。内置浏览器连接失败，结果不代表内置浏览器、桌面运行时、生产构建或全量回归。

## 无 trace 的对照

前两轮正常场景：Pan p95 为 133.3 / 133.4 ms，Zoom p95 为 50.0 / 66.7 ms。正常 Pan 最大帧间隔 150.1 / 166.6 ms；Zoom 为 200.1 / 233.4 ms。

| 诊断条件 | Pan p95（ms） | Zoom p95（ms） | 含义 |
| --- | ---: | ---: | --- |
| 正常，前两轮 | 133.3–133.4 | 50.0–66.7 | 复现明显卡顿 |
| 完全不挂载地形 renderer | 133.3 | 66.6 | 地形不是主因 |
| 隐藏卡片绘制，仍挂载组件 | 116.6 | 33.4 | 仅隐藏画面仍有明显开销 |
| 保留外框，移除 SandboxWorkspace，前两轮 | 33.4–50.0 | 16.8–33.3 | Sandbox 内部内容是主要放大因素 |
| 仅跳过 SandboxSettings | 66.8 | 50.0 | 未打开的设置组件也有明显成本 |
| 全部卡片内部换成简单矩形 | 16.8 | 16.8 | 同样几何、裁剪和背景可流畅运行 |
| 关闭 React Flow 可见性裁剪 | 50.1 | 33.4 | 消除 Pan 挂载仍有持续渲染成本 |
| 禁用卡片子树 pointer-events | 116.8 | 50.1 | 不是简单禁止事件即可解决 |
| 给 Sandbox 外框加 will-change | 133.3 | 33.4 | 图层提升不能解决 Pan 主因 |
| 取消卡片阴影 | 166.6 | 66.8 | 不能把主因归结为阴影 |

这些是定位用的删减实验，不是已实现的优化，也不能按单次差值承诺性能提升。全部运行均无 page error，且无 synthetic ID 后端请求。

## 具体的耗时链路

### 1. 每帧相机变化触发可见卡片集合变化

`useSmoothWheelZoom.tick → setViewport` 或原生拖动更新 React Flow 的 transform。`WorldCanvas` 开启 `onlyRenderVisibleElements`，XYFlow 的 `useVisibleNodeIds → getNodesInside → NodeRenderer` 在可见 ID 变化时挂载/卸载卡片。

正常两轮 Pan 分别观察到 **430 / 434 次挂载，428 / 432 次卸载**，以及 **372 / 371 次全局 React commit**。commit 数不等于每张卡都重渲染。Pan 世界 store 的 viewport 写入为 **0**：闭环回到同一相机，且拖动过程中没有全局 viewport 写入风暴。

关闭 XYFlow 裁剪时，Pan 挂载和卸载都为 0，样式重算累计从约 1.27–1.37 s 降到 0.10 s，但仍有长帧；该变体会挂载 452 张卡、125 个 Sandbox、约 4.5 万 DOM。它只用于证明生命周期成本，不能作为直接修复方案。世界分块筛选仍在，因此该变体 Zoom 期间仍有挂载。

入口：`frontend/src/canvas/WorldCanvas.tsx:974`；XYFlow 当前依赖实现见 `node_modules/@xyflow/react/dist/esm/index.js` 的 `useVisibleNodeIds`、`NodeRendererComponent`、`useResizeObserver`。

### 2. 缩小了视觉尺寸，却仍保留完整工作区组件

`WorldCardNodeComponent → WorkspaceSurface → SandboxWorkspace → SandboxSettings`。

`CardFrame` 的 `visualLevel = level` 取自持久展示状态，不随画布缩放降低内部渲染复杂度。初始约 1.9 万 DOM 中，48 个 Sandbox 包含 **11760 个后代元素**；其中未打开的设置窗口仍有 **5856 个后代元素**。设置窗口通过 `hidden` 隐藏，却仍挂载 `SandboxSettings`。隐藏 DOM 不等于直接参与可见绘制，但仍产生 React 初始化、DOM 创建、订阅与清理等工作。

移除 Sandbox 内部后，初始 DOM 从 19014 降到 8309；全部换为同尺寸 shell 后降到 1733。后者仍执行视野裁剪，两轮 Pan 仍有 430 次挂载，却没有超过 34 ms 的帧间隔。

定位：`frontend/src/cards/CardFrame.tsx:118`、`:222`；`frontend/src/cards/NodeWorkspace.tsx:215`；`frontend/src/cards/SandboxWorkspace.tsx:421`、`:423`。

### 3. 大 DOM 子树带来浏览器原生流水线开销

正常场景单独 trace 的 Pan 长约 9.16 s，渲染主线程任务累计约 9.05 s。下列为 trace 区段累计时间，包含嵌套，**不可相加计算百分比**：

| 区段 | Pan 累计 |
| --- | ---: |
| JavaScript FunctionCall | 1.86 s |
| LayoutView::HitTest | 1.82 s |
| UpdateLayoutTree（样式重算） | 1.37 s |
| Layerize（向合成器提交绘制产物） | 1.07 s |
| Blink.Paint.UpdateTime | 0.81 s |
| Layout | 0.26 s |

因此不能将全部耗时称为 React render，也不能把 TaskOtherDuration 当作 GPU 硬件利用率。单独组件函数体的 CPU 采样不是其完整成本：创建、销毁和处理它们产出的 DOM，大量时间落在 React DOM 与浏览器原生工作中。

### 4. Zoom 的次级阻塞：重新计算临时避让

`commitViewport → worldStore.setViewport → activeChunkKeys 变化 → renderCards / surfaceObstacles 变化 → displacedPositions`。

正常详细采样中，Zoom 的 `WorldCanvas` 累计 inclusive CPU 约 **405 ms**，其中 `displacedPositions` 约 **298 ms**；Pan 中这条重算链几乎没有采样。因此它是缩放长帧的次级来源，不是本次持续 Pan 卡顿的主因。

算法最多迭代 28 轮，每轮包含卡片对工作区障碍的检查和卡片对之间的碰撞检查。它依赖当前筛选后的 renderCards，使相机跨分块边界也能触发布局计算。本次 Zoom 正常两轮分别产生 4 / 7 次世界 viewport 写入，shell 为 1 次；阻塞下动画分段结束及分块变化会增加重算机会，不能把所有写入都判为重复写入 bug。

定位：`frontend/src/state/worldStore.ts:636`；`frontend/src/canvas/WorldCanvas.tsx:182`、`:204`、`:209`；`frontend/src/canvas/nodeDisplacement.ts:133`、`:145`、`:187`。

## 后续修复应对准的边界

1. 未访问的 Sandbox 设置面板延迟挂载；访问后的草稿由现有状态所有者保存，避免卸载丢输入。
2. 为远视野提供较轻的工作区渲染表示，保留用户的真实展示状态、尺寸、坐标和交互意图；不要将保存的 workspace 自动改成 node。
3. 降低视野边缘重组件反复挂载的频率，例如有界预留区和滞回；不能简单关闭全部裁剪。
4. 将临时避让的重算依赖与纯相机分块切换分开，并限制碰撞候选集合；保留用户手动位置及既有避让规则。

## 复现与证据

从 `frontend`，连接正在运行的开发前端：

```powershell
node scripts/profile-stress-zoom.mjs --origin http://127.0.0.1:5174 --zoom .12 --variants normal,noTerrain,noSandbox,noSettings,shell --phases pan,zoom --label wide
node scripts/profile-stress-zoom.mjs --origin http://127.0.0.1:5174 --zoom .12 --phases pan,zoom --trace --profile --label wide-trace
node scripts/analyze-stress-profile.mjs ../.outputs/<trace-run-directory>
```

本次本地原始证据（`.outputs` 不提交版本库）：

- `stress-zoom-wide-isolation-1790651532353/`：正常、关闭背景、隐藏卡片、简单矩形。
- `stress-zoom-wide-drilldown-1790651700697/`：移除 Sandbox、关闭裁剪、正常复测。
- `stress-zoom-wide-browser-cost-1790651795186/`：命中检测、图层、阴影对照，以及按卡片类型的 DOM 计数。
- `stress-zoom-wide-settings-1790651883357/`：隐藏设置组件对照、移除 Sandbox 复测。
- `stress-zoom-wide-profile-1790651625012/`：完整 trace、CPU profile、分阶段分析与截图。
- `stress-zoom-wide-confirmation-1790651946974/`：减少挂载计数器观察范围后的最终确认。

最终确认仅观察 React Flow 节点父容器的直接 childList，不扫描每个新挂载的 DOM 子树，降低诊断计数器自身开销。

| 最终确认 | Pan p95 / max（ms） | Zoom p95 / max（ms） |
| --- | ---: | ---: |
| 正常 | 116.8 / 166.6 | 66.6 / 266.7 |
| 移除 SandboxWorkspace 内部 | 33.4 / 66.7 | 16.8 / 33.4 |
| 同尺寸简单矩形 | 16.8 / 16.8 | 16.7 / 16.8 |

正常场景三轮未加 trace 的 Pan p95 范围为 116.8–133.4 ms，Zoom 为 50.0–66.7 ms；不同复测间存在波动，定性归因一致。最终正常 Zoom 最大输入排队时间约 449 ms，简单矩形约 33 ms。
