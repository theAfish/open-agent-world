# 文档重构处置与验证记录

本文件是本次重构的内部交付清单，不是用户手册或永久验收保证。公开网站与应用 Help 均排除 `docs/internal/`。

## 基线与范围

- 基于任务开始时抓取的 `origin/dev`：`29c641f6`。
- 独立分支：`docs/documentation-reset`；worktree：`D:/AI/open-agent-world/.tmp/documentation-reset`。
- 范围：文档、教程文案、文档入口、相关测试。未修改业务 API、权限、安装器、卡片运行时、生产部署或仓库权限；未合并到 dev。
- 清单覆盖基线中全部 Markdown/MDX/RST 文件，并单列非 Markdown 教程定义和导航。表中“保留”表示保留其当前用途与必要契约；不代表完成每个插件、平台和模型的运行验收。

## 四条阅读路径

| 读者 | 入口与连续路径 |
| --- | --- |
| 普通用户 | `docs/user-guide/index[.zh-CN].md` → 安装 → 模型连接 → `first-team` → Agent + Text 只读连接 + Conversation；Factory 单独说明 |
| Card / Pack 开发者 | `docs/developers/index[.zh-CN].md` → `card-app[.zh-CN].md`；同一 Greeter 完成 Python、数据、关系与授权、React、wheel、Schema 3 打包、安装与重启 |
| 技术查询 | `docs/reference/index.md`；`plugins.md` 作为兼容入口指向注册、文档、权限、生命周期、前端和 API 版本契约；Pack manifest 与安装边界分开 |
| 宿主维护者 | `docs/contributing/index[.zh-CN].md` → 源码启动、测试/CI、发布、部署与架构；历史证据留在 internal 且不表示当前状态 |

中文用户的安装、模型、首个任务、卡片连接、卡包和 Factory 路径，以及完整 Card App 教程均连续可读；底层参考按需链接英文。源码文档跟随 dev，安装包 Help 跟随构建版本。

## 主要事实修正

- 欢迎页是 Start Tutorial / Choose a workspace / Start blank；选择工作空间只实例化选中的蓝图，不自动向模型发送任务或启动 Sandbox。
- 区分安装 Pack、开包收集、加入 Deck 与放置 World；Store 使用现有 Marketplace 客户端和安装器，不再声称尚无在线 Store。没有递归下载缺失依赖或后台自动升级的承诺。
- Factory 传统制作界面继续可用；Pack IDE 位于 Developer tools，标明 Experimental。外部 IDE + 公开 SDK 是完整代码项目的推荐路径。
- Schema 3 用于代码及声明式项目，Schema 2 保留无代码内容分发，Schema 1 被拒绝。分发 ID、owner、Pack ID 与 node type 分开解释。
- 模型向导支持发现模型；macOS 源码支持 Seatbelt 和有平台前提的 Container VM。安装包签名、公证、自动更新和平台验收取决于具体发行构建。
- 普通用户操作使用 Text file / Save text 的真实标签，并说明标题悬停重命名、Conversation 参与者的添加。
- 普通 Greeter 使用 `frontend={"body": "greeting"}`；`settings` 是共享 Agent 控件内的槽位。实际宿主验证纠正了只构建不运行无法发现的教程错误。
- Ruleset 的实时状态不能由仓库副本证明；贡献指南描述如何查看，而不固化某日的启用状态。`release-notes.md` 仍是 `scripts/release-assets.py` 消费的用户安装正文。

## 入口与旧链接

根 README、两种语言的文档首页、MkDocs 和内置 Help 使用相同四类读者分组。沿用现有 raw imports、MkDocs hook 和链接解析器；新增分类一致性测试，没有生成或同步框架。

保留 `plugins.md` 的旧二、三级标题锚点和 `pack-distribution.md` 的章节入口；`first-plugin.md`、`agent-tools.md`、`frontend.md` 和 `product-first-use.md` 留下短迁移入口。插件 README 中仍有库内引用的章节也保留指向新分篇的短入口。源码链接在网站和 Help 中继续转为 GitHub dev 链接，本地文档与图片保持离线可读。

删除文件没有发现需要保留的库内引用；不为全部历史路径建立重定向。外部未登记的深链接无法穷尽确认。内部历史记录引用的未入库截图/JSON 改为明确的本地证据路径，避免伪装成可用下载链接。

## 验证结果与限制

本地环境：Windows x64，Node 22.14.0 / npm 10.9.2，Python 3.12.4，Chrome headless。测试档案和产物均在 `.tmp/`，不是原工作目录的日常数据。

| 层次 | 实际运行 | 结果与边界 |
| --- | --- | --- |
| 文档站点 | `python scripts/docs.py check`（准备好的 venv 在 PATH 中） | 97 源页面、16 文档单元检查、严格 MkDocs 构建；98 生成页面、12,811 本地链接/资源、43 搜索页面全部通过 |
| 全仓链接 | 一次性检查所有现存 Markdown（含插件、示例与 internal） | 180 文件、649 相对链接/锚点，无缺失；未检查所有外部 HTTP 网站的实时可用性 |
| 应用 Help / 教程单元测试 | `npx vitest run src/shell/documentation.test.ts src/shell/DocumentationPanel.test.tsx src/shell/HelpMenu.test.tsx src/onboarding/tutorial.test.ts src/tutorials/store.test.ts src/tutorials/ProgressiveTutorials.test.tsx --no-cache` | 首轮 6 文件、52 测试通过；二次正文清理后重跑 inventory 与 DocumentationPanel 共 8 测试通过；包括分类、图片、链接、中文 fallback |
| 后端教程与权限 | `python -m pytest backend/tests/test_tutorials.py backend/tests/test_capabilities.py -q -p no:cacheprovider` | 18 测试通过；文案最终修改后再次运行教程文件，13 测试通过。仅有 Starlette/httpx 弃用警告 |
| Card App 集成 | `uv run --project backend --with-editable <project> python -m pytest -p no:cacheprovider <project>/tests` | 1 个完整集成测试通过：真实注册、工具调用、配置更新、隔离、克隆、非法输入、删除后的授权撤销 |
| 示例构建 | 从中英文教程抽取代码块，检查两种语言完全一致，复制 Python Greeter；构建并安装 SDK tarball、`npm run build`、`uv build --wheel`、`doctor`、`build-project`、`inspect` | 全部通过。项目位于宿主 checkout 外，未依赖宿主私有前端 import |
| 示例安装与宿主 | 在新生产模式数据目录，通过 Library 选择文件、审阅、安装，然后停止并重启宿主 | 重启前 selected=true / loaded=false；重启后安装版本的 Python 与 frontend 生效。开包、加入 Deck、放置 Greeter、修改/保存/刷新保留值通过。Greeter 授权/撤销及 Text 读取/拒绝编辑/撤销的 HTTP 检查通过 |
| 普通用户路线 | 从 Help 使用入门进入首个任务，经 Library 开 Core essentials，加入三张卡片，放置并命名 Text file、Save text、设置 Agent 指令、拖动 Read 与 Participate、检查参与者、发送消息 | 通过。放置包含键盘激活，画布使用移动和缩放控件保持端口可见。运行时显式为 mock；没有验证真实模型读取材料、选择工具或生成议程的质量 |
| 前端生产构建 | `npm --prefix frontend run build` | TypeScript、Pack 构建与 Vite 成功；有 chunk size 警告。依赖准备限制见下文 |
| 浏览器 Help | `playwright test e2e/documentation.spec.ts`，生产前端 + 本地后端，阻断外部网络 | 1 测试通过：离线内容/图片、页面导航、搜索、焦点归还、中英文、明暗主题、800/390 宽度。初始化使用当前 profile preferences；缩放后等待布局稳定而不放宽边界断言 |

最终示例归档为 `community.greeter@0.1.0`，SHA-256：`cee70a87eab789b7025876b5e6638f8c6abad1fd30641fc2f4c7d2b98471151c`。本地文件在 `D:/AI/open-agent-world/.tmp/documentation-card-app-1793012134/dist/greeter-body-0.1.0.oawpack`；该路径是本次验证产物，不是仓库依赖。构建命令在正文使用可自行选择的项目路径。

### 独立待处理问题

**全新依赖安装未通过。** 在未改动的 `plugins/structure_viewer/frontend/package.json` 和 lockfile 上，本机 Node 22.14.0 / npm 10.9.2 执行构建中的 `npm ci` 报 `EUSAGE`，要求 `vite@8.3.5`、`esbuild@0.28.2` 等条目；当前 lockfile 包含另一组已锁定版本。另有 matterviz 要求 Node 24 的 engine 警告。未修改依赖或锁文件。宿主 frontend 的 `npm ci` 已成功；随后逐项核对并复用了另一 checkout 中与各 Pack lockfile 相符的依赖，普通生产 build 才成功。应另开构建依赖问题，使用 CI 的 Node 24 核对；本次不能声称“全新环境一键安装通过”。

**未覆盖的运行事实。** 没有使用真实模型账号、真实 Marketplace 发布/下载、macOS/Linux 原生 Sandbox、Windows 安装后的 Tauri、签名或更新服务。最小后端环境中的 AtomSculptor、Knowledge Base、Research Library 分别缺少可选 adapter 依赖 litellm、openpyxl、pymupdf，因此这些 Pack 的完整功能未在本次验证；未把发现和文档检查当作插件验收。平台要求、权限和兼容事实来自当前源码及测试定义，不能替代相应平台实跑。

没有运行全项目 pytest/Vitest/E2E，没有手动触发 CI。原 `D:/AI/open-agent-world` 工作区仍是干净的 dev，指向同一基线。日志与截图保存在本 worktree 的 `.tmp/`（不入库）；上述命令、归档 digest 和限制是可随提交审阅的证据摘要。


## 公开正文二次清理

用户指出首轮可能仍偏重移动和拆分。复核确认有遗漏：首轮提交 `8217faa6` 的净减少 2,521 行中，根目录 Markdown 净减 1,254 行、internal 净减 877 行；公开 `docs/` 净减 251 行，插件 Markdown 净减 231 行。行数不能证明逐篇内容已经清理充分。

本轮直接删除正文中的过程记录，不新增 Archive，也不改业务代码：

- MatCreator 用户路径改为安装、任务、读结果与排查；删除旧分支迁移、布局 revision、源码目录与测试覆盖叙述。
- MatCreator 的真实声明为 Pack 0.10.0 / API 1.36；旧正文分别写成 0.7/1.31 和 0.9/1.35。参考文档改为链接当前 manifest，避免再复制版本号。
- 对照当前 ExecutionGraph.tsx，移除旧的三轨道自动折叠、Dependency lens / Step graph 及 160 节点镜头说明。用户文档说明当前的行内详情和引用导航；API 文档保留数据语义，删除像素、动画周期和测试 fixture 的实现叙述。
- AtomSculptor 累计 token 预算已经从 HarnessLimits 移除；删除与此冲突的 262144 累计预算说法，保留每次请求上限、显式角色上限、超时和旧配置处理规则。
- XRD 使用内置 OAW_XRDfit，用户无需外部 PyWPEM checkout 或手动补丁；谱库入口更新为当前的挂载槽位，保留科学解释限制和来源许可。
- Pack IDE、声明式卡包、源码 setup、发布、图渲染和生命周期文档删除验收叙述与演化故事；Sandbox 保留完整安全前提和可复现维护步骤，删除某次 Windows/Linux 实跑状态。

本轮的内容事实以源码、测试定义和现有 UI 定义为依据；没有声称重新实跑 MatCreator 或 XRD 的科学流程。尝试执行 AtomSculptor 的两个输出限额测试时，收集阶段缺少 `google.genai`，没有测试运行或通过；未为文档改动扩大依赖安装。

独立维护问题：`plugins/xrd/engines/test_pywpem_bounded_cell.py` 仍从 `<OAW_XRD_ROOT>/PyWPEM` 导入外部源码。已撤下正文对该命令的推荐；测试脚本本身未修改，仍需维护者另行迁移到内置引擎路径。

## 完整处置清单

基线共 166 文件：158 份保留或重构，8 份删除；另新增 22 份文件（含本记录）。

每一行对应一个基线文件。新文件另列，避免把拆分后的目的路径误当作原文件。事实依据区分源码/测试位置与本次实际运行结果。

| 旧路径 | 处置 | 当前路径 | 事实依据或保留理由 |
| --- | --- | --- | --- |
| `.github/CI.md` | 重写 | `.github/CI.md` | .github/workflows CI aggregate names; rulesets are remote state |
| `.github/pull_request_template.md` | 保留 | `.github/pull_request_template.md` | .github/workflows；PR 模板是维护流程，不是线上 Ruleset 状态证明 |
| `AGENTS.md` | 保留 | `AGENTS.md` | 用户给定工程与测试约束；不改团队规则 |
| `README.md` | 重写 | `README.md` | README entrances; code version vs release distinction |
| `README.zh-CN.md` | 重写 | `README.zh-CN.md` | README entrances; code version vs release distinction |
| `RESEARCH_PLUGINS.md` | 重写为简洁目录 | `RESEARCH_PLUGINS.md` | plugin manifests and maintained READMEs; remove historical migration/test claims |
| `docs/README.md` | 重写 | `docs/README.md` | onboarding/Onboarding.tsx; shell/HelpMenu.tsx; cardLibrary.ts |
| `docs/README.zh-CN.md` | 重写 | `docs/README.zh-CN.md` | same as English landing; version scope |
| `docs/architecture.md` | 校正 | `docs/architecture.md` | frontend/src/cards/CardFrame.tsx; backend/plugins/registry.py; graph ownership |
| `docs/assets/demos/README.md` | 保留 | `docs/assets/demos/README.md` | 保留图像采集时间与示例数据说明；素材来源不是当前验收记录 |
| `docs/canvas-control.md` | 保留 | `docs/canvas-control.md` | backend/canvas_control.py; backend/tests/test_canvas_control.py |
| `docs/canvas-performance.md` | 精简 | `docs/canvas-performance.md` | frontend benchmark scripts; remove public links to old measurement reports |
| `docs/card-factory.md` | 重写 | `docs/card-factory.md；docs/reference/factory-recipes.md` | FactoryViews.tsx; Packer; actual activation status |
| `docs/card-factory.zh-CN.md` | 重写 | `docs/card-factory.zh-CN.md` | FactoryViews; publishing handoff; schema validation |
| `docs/card-library.md` | 保留 | `docs/card-library.md` | backend/card_library.py; frontend/src/shell/CardLibrary.tsx |
| `docs/card-state.md` | 校正 | `docs/card-state.md` | backend/card_state.py; backend/tests/test_card_state.py; retained compatibility limits |
| `docs/concepts.md` | 保留 | `docs/concepts.md` | backend/plugins/builtin.py; backend/plugins/registry.py; frontend/src/cards |
| `docs/configuration.md` | 保留 | `docs/configuration.md` | backend/config.py; backend/security/model_connections.py; frontend/src/shell |
| `docs/context-management.md` | 保留 | `docs/context-management.md` | backend/agents/context.py; backend/agents |
| `docs/contributing/docs-checklist.md` | 重写 | `docs/contributing/docs-checklist.md` | AGENTS.md and existing source/link tests |
| `docs/contributing/docs-checklist.zh-CN.md` | 重写 | `docs/contributing/docs-checklist.zh-CN.md` | same checklist |
| `docs/contributing/hosting.zh-CN.md` | 保留 | `docs/contributing/hosting.zh-CN.md` | backend/hosting; backend/hosting; docs/deployment.zh-CN.md |
| `docs/contributing/index.md` | 重写 | `docs/contributing/index.md` | .github/workflows; AGENTS.md; no assertion of live GitHub settings |
| `docs/creator-packs.md` | 保留 | `docs/creator-packs.md` | backend/packs/factory.py; frontend/src/shell/PackPublishing.tsx |
| `docs/deployment.md` | 保留 | `docs/deployment.md` | backend/deployments.py; backend/deployment_runtime.py; examples/deployed-workspace |
| `docs/deployment.zh-CN.md` | 保留 | `docs/deployment.zh-CN.md` | backend/deployments.py; backend/deployment_runtime.py; examples/deployed-workspace |
| `docs/desktop.md` | 校正 | `docs/desktop.md` | scripts/build-desktop.ps1; .github/workflows/release.yml; signing is build-specific |
| `docs/developers/agent-tools.md` | 合并为迁移入口 | `docs/developers/card-app.md (old path retained)` | examples/plugins/greeter and continuous card-app tutorial |
| `docs/developers/card-design.md` | 保留 | `docs/developers/card-design.md` | frontend/src/factory; frontend/src/cards; backend/packs/factory.py |
| `docs/developers/card-finishes.md` | 校正 | `docs/developers/card-finishes.md` | backend/card_finishes.py; frontend/src/cards; removed development-history sentence |
| `docs/developers/context-compaction.md` | 保留 | `docs/developers/context-compaction.md` | backend/plugins/execution.py; backend/agents; backend/tests |
| `docs/developers/data-sources.md` | 保留 | `docs/developers/data-sources.md` | backend/plugins/data_sources.py; plugins/sqlite; plugins/knowledge_base; plugins/visualization |
| `docs/developers/declarative-packs.md` | 精简 | `docs/developers/declarative-packs.md` | backend/packs and Notes example; replace acceptance narrative with verification link; remove unrelated XRD refactor work |
| `docs/developers/documentation.md` | 重写 | `docs/developers/documentation.md` | scripts/docs.py; docs_hooks; documentation tests; docs workflow |
| `docs/developers/execution-graph.md` | 精简 | `docs/developers/execution-graph.md` | ExecutionGraph.tsx and execution graph models; retain provider/paging contract; remove pixel-level design history and fixture narrative |
| `docs/developers/extension-points.md` | 保留 | `docs/developers/extension-points.md` | open_agent_world/plugin_api; backend/plugins/registry.py |
| `docs/developers/first-plugin.md` | 合并为迁移入口 | `docs/developers/card-app.md (old path retained)` | examples/plugins/greeter and continuous card-app tutorial |
| `docs/developers/frontend.md` | 合并为迁移入口 | `docs/developers/card-app.md (old path retained)` | examples/plugins/greeter and continuous card-app tutorial |
| `docs/developers/index.md` | 重写 | `docs/developers/index.md` | public SDK modules; existing Greeter; Factory and Pack IDE entries |
| `docs/developers/index.zh-CN.md` | 重写 | `docs/developers/index.zh-CN.md` | same as English developer entry |
| `docs/developers/pack-ide.md` | 精简 | `docs/developers/pack-ide.md` | PackIDE.tsx and source Pack CLI; remove delivery report claims; preserve experimental scope and compatibility |
| `docs/developers/progressive-tutorials.md` | 保留 | `docs/developers/progressive-tutorials.md` | backend/plugins/tutorials.py; frontend/src/tutorials; backend/tests/test_tutorials.py |
| `docs/developers/progressive-tutorials.zh-CN.md` | 保留 | `docs/developers/progressive-tutorials.zh-CN.md` | backend/plugins/tutorials.py; frontend/src/tutorials; backend/tests/test_tutorials.py |
| `docs/developers/quickstart-pack.md` | 重写 | `docs/developers/quickstart-pack.md` | pack CLI init; Store; default source setup |
| `docs/developers/setup.md` | 精简 | `docs/developers/setup.md` | scripts/setup.py / scripts/dev.py; keep profile and setup commands; remove launcher cache implementation and test narrative |
| `docs/developers/testing.md` | 重写 | `docs/developers/testing.md` | scripts/build-external-greeter.py; run-pack-e2e.mjs; test_greeter_plugin.py |
| `docs/developers/workspace-design.md` | 保留 | `docs/developers/workspace-design.md` | frontend/src/plugins/sdk.ts; frontend/src/legions/workspaceLayout.ts |
| `docs/enterprise-foundations.md` | 重写 | `docs/enterprise-foundations.md` | backend/control_plane.py; request_context; health; persistence idempotency |
| `docs/execution-configuration.md` | 保留 | `docs/execution-configuration.md` | backend/execution_config.py; examples/skills/execution-configuration |
| `docs/execution.md` | 保留 | `docs/execution.md` | backend/background_jobs.py; backend/runs; open_agent_world/plugin_api |
| `docs/getting-started.md` | 保留 | `docs/getting-started.md` | scripts/setup.py; scripts/dev.py; scripts/start.ps1; scripts/start.sh |
| `docs/getting-started.zh-CN.md` | 保留 | `docs/getting-started.zh-CN.md` | scripts/setup.py; scripts/dev.py; scripts/start.ps1; scripts/start.sh |
| `docs/graph-architecture.md` | 重写 | `docs/graph-architecture.md` | frontend/src/graph; Pack graph adapters; frontend/package.json |
| `docs/install.md` | 重写 | `docs/install.md` | release workflow supports optional signing; source instructions target dev |
| `docs/install.zh-CN.md` | 重写 | `docs/install.zh-CN.md` | release workflow supports optional signing; source instructions target dev |
| `docs/internal/README.md` | 重写 | `docs/internal/README.md` | historical evidence policy and excluded navigation |
| `docs/internal/builtin-card-ui-redesign.md` | 删除 | —（不另存副本） | one-time rollout/checklist superseded by maintained API, setup and UI contracts; Git retains history |
| `docs/internal/design/reader-transition.md` | 保留历史并标注 | `docs/internal/design/reader-transition.md` | specific investigation/operational evidence; excluded from public site and Help |
| `docs/internal/design/shadow-collection.md` | 保留历史并标注 | `docs/internal/design/shadow-collection.md` | specific investigation/operational evidence; excluded from public site and Help |
| `docs/internal/design/shadow-gas-boundary.md` | 保留历史并标注 | `docs/internal/design/shadow-gas-boundary.md` | specific investigation/operational evidence; excluded from public site and Help |
| `docs/internal/execution-history-audit.md` | 保留历史并标注 | `docs/internal/execution-history-audit.md` | specific investigation/operational evidence; excluded from public site and Help |
| `docs/internal/hosted-profile-boundary.md` | 保留历史并标注 | `docs/internal/hosted-profile-boundary.md` | specific investigation/operational evidence; excluded from public site and Help |
| `docs/internal/performance/canvas.md` | 保留历史并标注 | `docs/internal/performance/canvas.md` | specific investigation/operational evidence; excluded from public site and Help |
| `docs/internal/performance/card-rendering.zh-CN.md` | 保留历史并标注 | `docs/internal/performance/card-rendering.zh-CN.md` | specific investigation/operational evidence; excluded from public site and Help |
| `docs/internal/performance/legion.zh-CN.md` | 保留历史并标注 | `docs/internal/performance/legion.zh-CN.md` | specific investigation/operational evidence; excluded from public site and Help |
| `docs/internal/performance/panzoom.zh-CN.md` | 保留历史并标注 | `docs/internal/performance/panzoom.zh-CN.md` | specific investigation/operational evidence; excluded from public site and Help |
| `docs/internal/performance/stress-zoom.md` | 保留历史并标注 | `docs/internal/performance/stress-zoom.md` | specific investigation/operational evidence; excluded from public site and Help |
| `docs/internal/performance/terrain-webgl.md` | 保留历史并标注 | `docs/internal/performance/terrain-webgl.md` | specific investigation/operational evidence; excluded from public site and Help |
| `docs/internal/performance/wide-viewport.zh-CN.md` | 保留历史并标注 | `docs/internal/performance/wide-viewport.zh-CN.md` | specific investigation/operational evidence; excluded from public site and Help |
| `docs/internal/plans/marketplace-production.md` | 保留历史并标注 | `docs/internal/plans/marketplace-production.md` | specific investigation/operational evidence; excluded from public site and Help |
| `docs/internal/plans/matcreator-demo.md` | 删除 | —（不另存副本） | one-time rollout/checklist superseded by maintained API, setup and UI contracts; Git retains history |
| `docs/internal/plans/pack-installation-unification.zh-CN.md` | 删除 | —（不另存副本） | one-time rollout/checklist superseded by maintained API, setup and UI contracts; Git retains history |
| `docs/internal/temporal-braid-audit.md` | 保留历史并标注 | `docs/internal/temporal-braid-audit.md` | specific investigation/operational evidence; excluded from public site and Help |
| `docs/internal/validation/batch-0.md` | 删除 | —（不另存副本） | one-time rollout/checklist superseded by maintained API, setup and UI contracts; Git retains history |
| `docs/internal/validation/batch-1-closeout.md` | 删除 | —（不另存副本） | one-time rollout/checklist superseded by maintained API, setup and UI contracts; Git retains history |
| `docs/internal/validation/batch-1.md` | 删除 | —（不另存副本） | one-time rollout/checklist superseded by maintained API, setup and UI contracts; Git retains history |
| `docs/internal/validation/enterprise-foundations.md` | 保留历史并标注 | `docs/internal/validation/enterprise-foundations.md` | specific investigation/operational evidence; excluded from public site and Help |
| `docs/internal/validation/marketplace-distribution.md` | 保留历史并标注 | `docs/internal/validation/marketplace-distribution.md` | specific investigation/operational evidence; excluded from public site and Help |
| `docs/internal/validation/official-marketplace.md` | 保留历史并标注 | `docs/internal/validation/official-marketplace.md` | specific investigation/operational evidence; excluded from public site and Help |
| `docs/internal/validation/pack-store.md` | 保留历史并标注 | `docs/internal/validation/pack-store.md` | specific investigation/operational evidence; excluded from public site and Help |
| `docs/internal/workspace-design-refactor.md` | 删除 | —（不另存副本） | one-time rollout/checklist superseded by maintained API, setup and UI contracts; Git retains history |
| `docs/layout.md` | 保留 | `docs/layout.md` | frontend/src/state/nodeSurfaces.ts; frontend/src/legions/workspaceLayout.ts |
| `docs/legions.md` | 重写 | `docs/legions.md` | onboarding/controller.ts fromBlueprints; BlueprintChooser.tsx |
| `docs/lifecycle-artifacts.md` | 重写 | `docs/lifecycle-artifacts.md` | backend artifacts, runs lifecycle; security and migration constraints retained |
| `docs/macos-seatbelt-network-extension.md` | 保留 | `docs/macos-seatbelt-network-extension.md` | backend/sandbox/darwin_seatbelt.py; desktop/src-tauri/tauri.macos.conf.json（无 Network Extension target） |
| `docs/minister.md` | 保留 | `docs/minister.md` | backend/minister.py; frontend/src/cards/Minister.tsx |
| `docs/node-effects.md` | 保留 | `docs/node-effects.md` | frontend/src/effects; frontend/src/cards |
| `docs/pack-distribution.md` | 拆分并重写入口 | `docs/pack-distribution.md；docs/reference/pack-manifest.md` | backend/packs/build.py; spec.py; installer; setup defaults verified |
| `docs/pack-store.md` | 重写 | `docs/pack-store.md` | backend/api/store.py; packs/marketplace.py; Store UI; installation manager |
| `docs/plugin-deployment.md` | 保留 | `docs/plugin-deployment.md` | backend/plugins/deployment.py; backend/tests/test_plugin_deployment.py |
| `docs/plugin-deployment.zh-CN.md` | 保留 | `docs/plugin-deployment.zh-CN.md` | backend/plugins/deployment.py; backend/tests/test_plugin_deployment.py |
| `docs/plugins.md` | 拆分并保留旧锚点 | `docs/plugins.md；docs/reference/plugin-{registration,lifecycle,documents,capabilities,frontend,compatibility}.md` | public API contracts split by query; historical external fragments preserved |
| `docs/product-first-use.md` | 重写 | `docs/product-first-use.md` | Onboarding.tsx; Conversation Review and resend; merge duplicate first use |
| `docs/reference/index.md` | 重写 | `docs/reference/index.md` | split contracts and four audience paths |
| `docs/release-notes.md` | 重写 | `docs/release-notes.md` | scripts/release-assets.py consumes this file; release workflow signing/updater inputs; retain user-facing body |
| `docs/release-notes.zh-CN.md` | 重写 | `docs/release-notes.zh-CN.md` | scripts/release-assets.py consumes this file; release workflow signing/updater inputs; retain user-facing body |
| `docs/releasing.md` | 校正并精简 | `docs/releasing.md` | .github/workflows/desktop-release.yml; remove old branch and version history; retain immutable tags, signing and release gates |
| `docs/runs.md` | 保留 | `docs/runs.md` | backend/runs; backend/runs/manager.py; backend/tests |
| `docs/sandbox-networking.md` | 校正 | `docs/sandbox-networking.md` | Windows WFP and Linux networking implementation/tests; retain reproducible native procedure; remove dated platform acceptance claims |
| `docs/sandbox-workspace.md` | 校正 | `docs/sandbox-workspace.md` | backend sandbox/runtime contracts; link to native procedures without promising prior acceptance |
| `docs/security.md` | 保留 | `docs/security.md` | backend/capabilities; backend/packs; backend/sandbox; corresponding negative tests |
| `docs/shared-python-runtime.md` | 保留 | `docs/shared-python-runtime.md` | backend/sandbox/environment.py; backend/packs; scripts/setup.py |
| `docs/state.md` | 保留 | `docs/state.md` | backend/state; backend/tests/test_state.py |
| `docs/terrain-webgl.md` | 精简 | `docs/terrain-webgl.md` | TerrainBackground / TerrainRendererWebGL; keep resource budgets and diagnostics; remove retired-renderer story and past fixture results |
| `docs/tool-execution.md` | 精简 | `docs/tool-execution.md` | backend/agents/tool_execution.py and test_tool_execution.py; retain cancellation/failure contract; remove coverage recital |
| `docs/tutorial.md` | 重写 | `docs/tutorial.md` | steps/controller/Onboarding and progressive tutorial definitions |
| `docs/tutorial.zh-CN.md` | 重写 | `docs/tutorial.zh-CN.md` | same tutorial sources |
| `docs/user-guide/canvas.md` | 校正 | `docs/user-guide/canvas.md` | frontend/src/cards/CardFrame.tsx; frontend/src/state/worldStore.ts; backend/plugins/builtin.py |
| `docs/user-guide/first-team.md` | 重写 | `docs/user-guide/first-team.md` | Onboarding.tsx; steps.ts; backend/plugins/builtin.py relationships; Text resource and Conversation UI |
| `docs/user-guide/index.md` | 重写 | `docs/user-guide/index.md` | Onboarding.tsx; HelpMenu.tsx |
| `docs/user-guide/index.zh-CN.md` | 重写 | `docs/user-guide/index.zh-CN.md` | onboarding; HelpMenu; PackStore; VersionPanel |
| `docs/user-guide/matcreator-example.zh-CN.md` | 重写 | `docs/user-guide/matcreator-example.zh-CN.md` | MatCreator 0.10.0 descriptor / API 1.36 manifest; native_card.py and native_runtime.py; current ExecutionGraph.tsx; remove old versions, source migration and acceptance narrative |
| `docs/user-guide/models.md` | 重写 | `docs/user-guide/models.md` | model discovery and manual settings UI |
| `docs/user-guide/plugins.md` | 重写 | `docs/user-guide/plugins.md` | Store client and installation UI |
| `docs/user-guide/troubleshooting.md` | 保留 | `docs/user-guide/troubleshooting.md` | frontend/src/shell/HelpMenu.tsx; frontend/src/shell/helpChecks.ts |
| `examples/deployed-workspace/README.md` | 保留 | `examples/deployed-workspace/README.md` | examples/deployed-workspace 内源码、manifest 与 runner；保留用途明确的独立示例 |
| `examples/deployed-workspace/checklist.md` | 保留 | `examples/deployed-workspace/checklist.md` | examples/deployed-workspace 内源码、manifest 与 runner；保留用途明确的独立示例 |
| `examples/deployed-workspace/guide.md` | 保留 | `examples/deployed-workspace/guide.md` | examples/deployed-workspace 内源码、manifest 与 runner；保留用途明确的独立示例 |
| `examples/knowledge-legion-deploy/README.md` | 保留 | `examples/knowledge-legion-deploy/README.md` | examples/knowledge-legion-deploy 内源码、manifest 与 runner；保留用途明确的独立示例 |
| `examples/packs/analysis-card/README.md` | 重写 | `examples/packs/analysis-card/README.md` | Library Developer tools entry |
| `examples/packs/greeter/README.md` | 重写 | `examples/packs/greeter/README.md` | external CLI wrapper; this fixture tests presentation not tools |
| `examples/packs/notes/README.md` | 重写 | `examples/packs/notes/README.md` | remove batch terminology; source vs archive distinction |
| `examples/plugins/greeter/README.md` | 重写 | `examples/plugins/greeter/README.md` | actual example and test; no direct source-discovery claim without manifest |
| `examples/plugins/hello_world/README.md` | 重写 | `examples/plugins/hello_world/README.md` | setup.py; Hello manifest |
| `examples/skills/execution-configuration/SKILL.md` | 保留 | `examples/skills/execution-configuration/SKILL.md` | 对应 Skill 的 scripts/references 与使用入口；保留科学约束；未执行全部科学工作流 |
| `frontend/pack-sdk/README.md` | 保留 | `frontend/pack-sdk/README.md` | frontend/pack-sdk/build*.mjs；frontend/src/plugins/sdk.ts；教程实际 tarball 构建 |
| `frontend/src/i18n/README.md` | 保留 | `frontend/src/i18n/README.md` | frontend/src/i18n/index.ts；messages.json |
| `plugins/agent_barracks/README.md` | 保留 | `plugins/agent_barracks/README.md` | plugins/agent_barracks/oaw.pack.toml；同目录 Python/前端实现及已有测试；插件功能未全部实跑 |
| `plugins/atomsculptor/README.md` | 重写并删除历史流水账 | `plugins/atomsculptor/README.md；plugins/atomsculptor/docs/{modelling-contract,diagnostics}.md` | oaw.pack.toml; frontend/index.tsx; execution_steps; retained safety protocols |
| `plugins/atomsculptor/src/oaw_atomsculptor/skills/crystal-builder/SKILL.md` | 保留 | `plugins/atomsculptor/src/oaw_atomsculptor/skills/crystal-builder/SKILL.md` | 对应 Skill 的 scripts/references 与使用入口；保留科学约束；未执行全部科学工作流 |
| `plugins/atomsculptor/src/oaw_atomsculptor/skills/crystal-builder/references/notes.md` | 保留 | `plugins/atomsculptor/src/oaw_atomsculptor/skills/crystal-builder/references/notes.md` | 对应 Skill 的 scripts/references 与使用入口；保留科学约束；未执行全部科学工作流 |
| `plugins/atomsculptor/src/oaw_atomsculptor/skills/interface-builder/SKILL.md` | 保留 | `plugins/atomsculptor/src/oaw_atomsculptor/skills/interface-builder/SKILL.md` | 对应 Skill 的 scripts/references 与使用入口；保留科学约束；未执行全部科学工作流 |
| `plugins/atomsculptor/src/oaw_atomsculptor/skills/interface-building/SKILL.md` | 保留 | `plugins/atomsculptor/src/oaw_atomsculptor/skills/interface-building/SKILL.md` | 对应 Skill 的 scripts/references 与使用入口；保留科学约束；未执行全部科学工作流 |
| `plugins/atomsculptor/src/oaw_atomsculptor/skills/materials-project-search/SKILL.md` | 保留 | `plugins/atomsculptor/src/oaw_atomsculptor/skills/materials-project-search/SKILL.md` | 对应 Skill 的 scripts/references 与使用入口；保留科学约束；未执行全部科学工作流 |
| `plugins/atomsculptor/src/oaw_atomsculptor/skills/mof-structure-creation/SKILL.md` | 保留 | `plugins/atomsculptor/src/oaw_atomsculptor/skills/mof-structure-creation/SKILL.md` | 对应 Skill 的 scripts/references 与使用入口；保留科学约束；未执行全部科学工作流 |
| `plugins/atomsculptor/src/oaw_atomsculptor/skills/molecular-structure-creation/SKILL.md` | 保留 | `plugins/atomsculptor/src/oaw_atomsculptor/skills/molecular-structure-creation/SKILL.md` | 对应 Skill 的 scripts/references 与使用入口；保留科学约束；未执行全部科学工作流 |
| `plugins/atomsculptor/src/oaw_atomsculptor/skills/nanostructure-creation/SKILL.md` | 保留 | `plugins/atomsculptor/src/oaw_atomsculptor/skills/nanostructure-creation/SKILL.md` | 对应 Skill 的 scripts/references 与使用入口；保留科学约束；未执行全部科学工作流 |
| `plugins/atomsculptor/src/oaw_atomsculptor/skills/nanotube-builder/SKILL.md` | 保留 | `plugins/atomsculptor/src/oaw_atomsculptor/skills/nanotube-builder/SKILL.md` | 对应 Skill 的 scripts/references 与使用入口；保留科学约束；未执行全部科学工作流 |
| `plugins/atomsculptor/src/oaw_atomsculptor/skills/sandwich-structure-creation/SKILL.md` | 保留 | `plugins/atomsculptor/src/oaw_atomsculptor/skills/sandwich-structure-creation/SKILL.md` | 对应 Skill 的 scripts/references 与使用入口；保留科学约束；未执行全部科学工作流 |
| `plugins/atomsculptor/src/oaw_atomsculptor/skills/structure-image/SKILL.md` | 保留 | `plugins/atomsculptor/src/oaw_atomsculptor/skills/structure-image/SKILL.md` | 对应 Skill 的 scripts/references 与使用入口；保留科学约束；未执行全部科学工作流 |
| `plugins/atomsculptor/src/oaw_atomsculptor/skills/structure-inspect/SKILL.md` | 保留 | `plugins/atomsculptor/src/oaw_atomsculptor/skills/structure-inspect/SKILL.md` | 对应 Skill 的 scripts/references 与使用入口；保留科学约束；未执行全部科学工作流 |
| `plugins/atomsculptor/src/oaw_atomsculptor/skills/structure-perturbation/SKILL.md` | 保留 | `plugins/atomsculptor/src/oaw_atomsculptor/skills/structure-perturbation/SKILL.md` | 对应 Skill 的 scripts/references 与使用入口；保留科学约束；未执行全部科学工作流 |
| `plugins/atomsculptor/src/oaw_atomsculptor/skills/supercell-builder/SKILL.md` | 保留 | `plugins/atomsculptor/src/oaw_atomsculptor/skills/supercell-builder/SKILL.md` | 对应 Skill 的 scripts/references 与使用入口；保留科学约束；未执行全部科学工作流 |
| `plugins/atomsculptor/src/oaw_atomsculptor/skills/supercell-dimension-check/SKILL.md` | 保留 | `plugins/atomsculptor/src/oaw_atomsculptor/skills/supercell-dimension-check/SKILL.md` | 对应 Skill 的 scripts/references 与使用入口；保留科学约束；未执行全部科学工作流 |
| `plugins/atomsculptor/src/oaw_atomsculptor/skills/surface-adsorption/SKILL.md` | 保留 | `plugins/atomsculptor/src/oaw_atomsculptor/skills/surface-adsorption/SKILL.md` | 对应 Skill 的 scripts/references 与使用入口；保留科学约束；未执行全部科学工作流 |
| `plugins/atomsculptor/src/oaw_atomsculptor/skills/surface-builder/SKILL.md` | 保留 | `plugins/atomsculptor/src/oaw_atomsculptor/skills/surface-builder/SKILL.md` | 对应 Skill 的 scripts/references 与使用入口；保留科学约束；未执行全部科学工作流 |
| `plugins/atomsculptor/src/oaw_atomsculptor/skills/surface-creation/SKILL.md` | 保留 | `plugins/atomsculptor/src/oaw_atomsculptor/skills/surface-creation/SKILL.md` | 对应 Skill 的 scripts/references 与使用入口；保留科学约束；未执行全部科学工作流 |
| `plugins/atomsculptor/src/oaw_atomsculptor/skills/surface-creation/references/checklist.md` | 保留 | `plugins/atomsculptor/src/oaw_atomsculptor/skills/surface-creation/references/checklist.md` | 对应 Skill 的 scripts/references 与使用入口；保留科学约束；未执行全部科学工作流 |
| `plugins/atomsculptor/src/oaw_atomsculptor/skills/surface-creation/references/lpscl_and_sio2.md` | 保留 | `plugins/atomsculptor/src/oaw_atomsculptor/skills/surface-creation/references/lpscl_and_sio2.md` | 对应 Skill 的 scripts/references 与使用入口；保留科学约束；未执行全部科学工作流 |
| `plugins/atomsculptor/src/oaw_atomsculptor/skills/surface-defect-creation/SKILL.md` | 保留 | `plugins/atomsculptor/src/oaw_atomsculptor/skills/surface-defect-creation/SKILL.md` | 对应 Skill 的 scripts/references 与使用入口；保留科学约束；未执行全部科学工作流 |
| `plugins/codex/README.md` | 校正 | `plugins/codex/README.md` | plugins/codex/src; plugins/codex/tests; removed dated CLI acceptance claim |
| `plugins/knowledge_base/README.md` | 重写并拆分 | `plugins/knowledge_base/README.md；plugins/knowledge_base/docs/{workflows,standalone,agent-contract}.md` | current knowledge_base frontend, 29c641f6 draft implementation and manifest |
| `plugins/matcreator/README.md` | 重写并拆分 | `plugins/matcreator/README.md；plugins/matcreator/docs/runtime.md` | MatCreator manifest; presets; runtime contracts; keep upstream attribution |
| `plugins/matcreator/oaw_matcreator_engine/UPSTREAM.md` | 保留 | `plugins/matcreator/oaw_matcreator_engine/UPSTREAM.md` | 引擎来源、许可证及 vendor 边界；不删除归属信息 |
| `plugins/skill_packages/README.md` | 校正 | `plugins/skill_packages/README.md` | open_agent_world/skill_packages.py; scripts/setup.py; fixed UI separator and preparation command |
| `plugins/sqlite/README.md` | 保留 | `plugins/sqlite/README.md` | plugins/sqlite/oaw.pack.toml；同目录 Python/前端实现及已有测试；插件功能未全部实跑 |
| `plugins/structure_viewer/README.md` | 保留 | `plugins/structure_viewer/README.md` | plugins/structure_viewer/oaw.pack.toml；同目录 Python/前端实现及已有测试；插件功能未全部实跑 |
| `plugins/task_board/README.md` | 保留 | `plugins/task_board/README.md` | plugins/task_board/oaw.pack.toml；同目录 Python/前端实现及已有测试；插件功能未全部实跑 |
| `plugins/visualization/README.md` | 保留 | `plugins/visualization/README.md` | plugins/visualization/oaw.pack.toml；同目录 Python/前端实现及已有测试；插件功能未全部实跑 |
| `plugins/visualization/src/oaw_visualization/docs/guide.en.md` | 保留 | `plugins/visualization/src/oaw_visualization/docs/guide.en.md` | plugins/visualization/oaw.pack.toml；同目录 Python/前端实现及已有测试；插件功能未全部实跑 |
| `plugins/visualization/src/oaw_visualization/docs/guide.zh-CN.md` | 保留 | `plugins/visualization/src/oaw_visualization/docs/guide.zh-CN.md` | plugins/visualization/oaw.pack.toml；同目录 Python/前端实现及已有测试；插件功能未全部实跑 |
| `plugins/xrd/README.md` | 重写操作入口并校正 | `plugins/xrd/README.md` | InputDock.tsx / LibraryView.tsx / MatchControls.tsx; bundled engine.py; remove external PyWPEM requirement and test narrative |
| `plugins/xrd/SQL_HISTORY.md` | 保留 | `plugins/xrd/SQL_HISTORY.md` | plugins/xrd/oaw.pack.toml；同目录 Python/前端实现及已有测试；插件功能未全部实跑 |
| `plugins/xrd/TEMPLATE_CONTRACT.md` | 保留 | `plugins/xrd/TEMPLATE_CONTRACT.md` | plugins/xrd/oaw.pack.toml；同目录 Python/前端实现及已有测试；插件功能未全部实跑 |
| `plugins/xrd/engines/README.md` | 校正 | `plugins/xrd/engines/README.md` | bundled OAW_XRDfit; preserve QualX source/license attribution; remove manual PyWPEM patch instruction and obsolete external-tree test command |
| `project_plan.md` | 删除 | —（不另存副本） | obsolete POC plan; current concepts/architecture cover supported behavior; inbound links checked |

## 新增文件

| 新路径 | 用途与依据 |
| --- | --- |
| `docs/contributing/index.zh-CN.md` | same contributor path |
| `docs/developers/card-app.md` | examples/plugins/greeter source and integration test; pack CLI; frontend SDK |
| `docs/developers/card-app.zh-CN.md` | same tested project and executable blocks as English tutorial |
| `docs/reference/factory-recipes.md` | backend/packs/factory.py; recipe model; existing Factory tests |
| `docs/reference/pack-manifest.md` | backend/packs/spec.py; scripts/setup.py; test_pack_spec.py |
| `docs/reference/plugin-capabilities.md` | docs/plugins.md contracts; open_agent_world/plugin_api; backend/plugins |
| `docs/reference/plugin-compatibility.md` | docs/plugins.md contracts; open_agent_world/plugin_api; backend/plugins |
| `docs/reference/plugin-documents.md` | docs/plugins.md contracts; open_agent_world/plugin_api; backend/plugins |
| `docs/reference/plugin-frontend.md` | docs/plugins.md contracts; open_agent_world/plugin_api; backend/plugins |
| `docs/reference/plugin-lifecycle.md` | docs/plugins.md contracts; open_agent_world/plugin_api; backend/plugins |
| `docs/reference/plugin-registration.md` | docs/plugins.md contracts; open_agent_world/plugin_api; backend/plugins |
| `docs/user-guide/canvas.zh-CN.md` | onboarding steps; world canvas; legion workspace |
| `docs/user-guide/first-team.zh-CN.md` | same as first-team.md |
| `docs/user-guide/models.zh-CN.md` | ModelConnectionWizard.tsx; Models settings; Storage settings |
| `docs/user-guide/plugins.zh-CN.md` | CardLibrary.tsx; PackStore.tsx; PackInstallations; passive activation |
| `plugins/atomsculptor/docs/diagnostics.md` | scripts/export-atomsculptor-test.py; shared run invocation storage |
| `plugins/atomsculptor/docs/modelling-contract.md` | harness.py / test_harness.py: retired cumulative token cap; execution_steps.py and measurement schemas; retain current scientific validation boundaries |
| `plugins/knowledge_base/docs/agent-contract.md` | knowledge_base capabilities, revisioned stores and authorization tests |
| `plugins/knowledge_base/docs/standalone.md` | knowledge_base standalone HTTP/MCP/CLI and layering tests |
| `plugins/knowledge_base/docs/workflows.md` | knowledge_base frontend workflow routes; source/draft/experiment stores |
| `plugins/matcreator/docs/runtime.md` | current descriptor/manifest and ExecutionGraph.tsx; runtime authorization/recovery contract; removed stale version and lens claims |
| `docs/internal/documentation-reset.md` | 本次完整清单、验证层次与未完成的环境验证；不进入公开文档 |

## 非 Markdown 入口、教程与测试

| 路径 | 处置与核对依据 |
| --- | --- |
| `mkdocs.yml` | 重组为四类；每个公开文件只登记一次；中英文归入同一读者组 |
| `frontend/src/shell/documentation.ts` | 明确四类清单，删除把剩余页面一律归为参考的兜底；保留原链接机制 |
| `frontend/src/i18n/messages.json` | 增加开发者分组名称的中英文；校正教程中的 Text file / 文本文件名称；核对欢迎页翻译 |
| `frontend/src/onboarding/Onboarding.tsx`、`BlueprintChooser.tsx`、`controller.ts`、`steps.ts` | 仅校正 steps.ts 中的卡片名称，其余保留；核对入口、蓝图放置、连接、模型设置、Sandbox 和 Legion 操作；不改教程状态机 |
| `backend/plugins/builtin_tutorials.py` | 校正文案：Conversation 明确 Participate、选择 Agent 或 @；Text 明确 Read / Read + edit；不改授权或触发逻辑 |
| `plugins/structure_viewer/src/oaw_structure_viewer/tutorials.py` | 保留；与查看器 follow/pin/reload、格式/尺寸限制及源码 UI 核对 |
| `plugins/visualization/src/oaw_visualization/tutorials.py` 与 `docs/guide.{en,zh-CN}.md` | 保留；核对 schema 选择、字段、数据上限、刷新和离线文档机制 |
| `examples/packs/greeter` 内教程定义 | 保留；明确它是打包/宿主 fixture，与完整教程的 community.greeter 不同 |
| `frontend/src/shell/documentation.test.ts` | 增加网站/应用分类一致性；保留 inventory、图片、翻译和链接检查 |
| `frontend/src/shell/DocumentationPanel.test.tsx` | 更新入口标题与仍无中文译本的 fallback 示例 |
| `frontend/e2e/documentation.spec.ts` | 更新中文主题名；使用真实 profile preferences 初始化；等待缩放布局稳定，保留边界断言 |
| `scripts/docs.py`、`docs_tests.py`、`docs_hooks.py`、`docs_check_site.py` | 保留现有机制并实际运行；不新增文档框架 |
| `scripts/release-assets.py`、`.github/workflows/` | 只读核对发布文档消费者与 CI 命令；不修改发布或权限 |
