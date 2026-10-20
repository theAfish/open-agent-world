# Pack 安装与依赖管理统一方案

状态：本轮继续完成现有 Pack 迁移及安装、恢复、卸载闭环；历史审查基线为 `b2b9012`，实施基于合入 AtomSculptor 后的 `d1f6ce3`。日期：2026-10-16。

下文保留重构前的审查依据和目标设计，具体开发接口以 [Pack 开发与发行文档](../../pack-distribution.md) 为准。

## 本次落地范围与验证边界

- 13 个源 Pack 均增加 `oaw.pack.toml`；静态读取、不导入插件，保留发行 ID、运行时 owner、Pack ID 和明确声明的卡片类型 ID。共享 dependency group 只复用声明，各执行目标独立安装，不继承主项目环境。
- schema 3 代码归档、源目录、Store 共用声明模型及环境管理；schema 1 代码包和 schema 2 内容包继续兼容。Store 下载身份先核验，再准备环境；安装失败保留旧选择，操作记录可在中断后恢复或重试。
- 宿主 adapter 使用经过校验的共享依赖层，冲突在激活前拒绝；该层与宿主共享 Python 命名空间，不冒充进程隔离。MatCreator、XRD 独立引擎使用托管私有 Python worker。原生程序 provider 支持校验制品及明确记录的外部程序采用。
- 全部 10 个带界面的 Pack 已独立构建；开发、生产和发行使用同一份产物。私有宿主及跨 Pack 前端源码引用迁移为公开 SDK，包括 XRD 使用 StructureViewer 的服务。
- Skill、Agent runtime 默认命令和后台任务 provider 可声明所需 Pack 环境；AtomSculptor 普通命令、科学 Skill 和 MatCreator 科学准备、Bohrium 后台任务均接入。在当前授权的 Sandbox 命令内准备私有 Python/npm 环境，等待后重新检查授权与声明。凭据、远端求解器及许可证数据单独声明。
- 文件安装、Store 和已加载的源 Pack 提供统一依赖与环境状态；`doctor` 和 `build-project` 支持静态诊断和预构建产物发行。
- 开发脚本按实际开发 profile 准备源依赖，保持卸载意图；Hello World 入门示例与发行教程采用相同声明。部署后的卡片视图只获得发布范围及声明依赖的前端模块，复用已校验资源接口。
- 依赖按 Pack 归属，不新增 Deck 依赖模型。相同配方/制品共享资源，卸载释放引用，进程租约保护正在使用的环境；源码卸载只改变当前 profile 的 enrollment。保留的归档可以重新准备依赖并恢复，用户数据和旧外部环境不自动删除。
- Sandbox 内的引用在下一次已经授权的 profile 命令中对账和清理；其他 Pack、其他 profile 和仍在执行的命令继续持有资源。宿主卸载不会绕过 Sandbox 授权去执行远程清理命令。

实际验证包括：全部 13 个源 Pack 加载及身份一致性、13 个真实归档在只含宿主必要库的私有解释器中安装并联合加载、源码/归档声明和指纹一致性、真实本地 wheel/标准库/Node 子进程隔离、失败保留旧版本、中断恢复、Store 模拟传输和授权回归；前端单测、生产构建及 Edge 中的 CSS/资源/worker 加载。归档联合加载测试替代了安装时的科学引擎准备，adapter wheel 提取及导入是真实执行；独立环境另有本地 fixture 测试。没有执行科学依赖的大规模下载、真实远端 Sandbox/模型任务、公开 Store 发布或桌面安装包验收。

测试使用临时数据目录，不修改用户现有配置或提交科学任务。验收覆盖共享引用释放、崩溃释放 OS 锁、准备阶段并发清理保护、源码卸载/重启/恢复、归档重新准备、独立前端构建及真实浏览器 worker/渲染。各轮测试结果以当次执行记录为准，不将历史计数当成最终代码的完整回归证明。

明确保留的边界：当前 provider 不安装任意系统包，也不将未实现的锁文件格式视为已支持；Sandbox Node/npm 须预先可用。QualX/Codex 可校验并采用现有程序，当前没有臆造自动再分发制品；密钥、许可证和用户数据库不会成为可自动卸载的依赖。历史全局安装的库缺少可信所有权记录，保持原状；新托管资源按引用清理。缺少其他 Pack 时显示阻塞原因，尚不递归从 Store 获取依赖。下文中的旧实现描述为审查历史，不再代表本轮迁移结果。

## 1. 建议结论

统一 **声明、安装计划、环境管理和状态查询**，同时保留不同执行位置的边界。Store 继续只负责获取发行产物；源码目录、预装产物、本地 `.oawpack` 和 Store 获取的产物最终使用同一份规范和生命周期。

用户提出的前端、后端、Sandbox 三类方向成立，但后端需要进一步区分“宿主进程内的适配器”和“独立进程运行的引擎”。此外，其他 Pack、外部程序、服务配置、模型和数据也需要进入依赖模型。

不建议将所有依赖装到宿主 Python，也不建议给每个 Pack 创建 venv 后仍将其加入同一个宿主 `sys.path`。当前插件注册的是宿主进程内 Python 对象；进程内模块共享问题需要通过受控公共 API 和独立 worker 边界解决。

## 2. 当前实现与具体断点

| 领域 | 当前实现 | 对统一安装的影响 |
| --- | --- | --- |
| 源码目录发现 | 扫描 `pyproject.toml` entry points，加入源码路径并直接 import factory；loader 不负责安装后端依赖 | 目录可被发现，不代表依赖已准备；无法在 import 前给出完整诊断 |
| 开发环境安装 | `install-plugins.py` editable 安装各插件及 `project.dependencies`，随后执行所选发行包的 setup hooks | Pack 自己声明依赖，但多数仍安装进同一个后端环境 |
| 桌面打包 | 复用安装脚本，支持目标目录及宿主 constraints，并运行 setup hooks | 需要保留离线预装和可迁移能力，不能只验证开发 venv |
| 本地归档与 Store | 都进入 `PackInstallationManager`；暂存、校验、不可变版本、事务选中、下次启动加载 | 这里已有统一主干，应扩展现有服务 |
| 归档后端依赖 | 只允许一个特定命名空间的纯 Python wheel；`Requires-Dist` 必须已由宿主满足；不运行 setup hooks | 缺少第三方后端依赖的交付机制，源码可用不代表归档可用 |
| 源码前端 | 固定 Vite glob 编译插件，依赖集中在宿主前端 `package.json` | Pack 的依赖归属、独立构建和移出仓库后的可发布性不完整 |
| 归档前端 | 发布已构建 JS；共享宿主 React/ReactDOM/SDK，其余可随包打入 | 此方向应保留；用户安装时不需要 npm |
| Sandbox Python | 汇总所有启用插件的要求，准备每个可用执行平台的 Shared Python | 没有按功能、实际 Sandbox 目标或版本隔离；一个冲突会影响整个聚合集合 |
| 安装状态 | 归档有 selected/loaded；另一个数据库记录插件环境 preparing/ready/failed | 未覆盖源码与独立引擎的完整状态；“安装成功”不足以说明功能可用 |

代码依据：

- [loader.py](../../../backend/plugins/loader.py)：目录发现与 import 为 29–75 行，源码 runtime 覆盖为 95–101 行，归档加载为 109–160 行。
- [install-plugins.py](../../../scripts/install-plugins.py)：依赖安装和 setup hook 为 18–64 行；[package-backend.py](../../../scripts/package-backend.py) 调用同一安装入口。
- [manifest.py](../../../backend/packs/manifest.py)：目前只有 `dependencies.packs` 和 `runtime.sandbox.python`；schema 1 为代码 Pack，schema 2 已用于内容 Pack。
- [archive.py](../../../backend/packs/archive.py)：69–110 行约束 wheel 命名、纯 Python、宿主依赖及 entry points。
- [installation.py](../../../backend/packs/installation.py)：66–104 行校验依赖选择集，123–162 行安装与选择版本。
- [store.py](../../../backend/api/store.py)：110–130 行将核验后的原始字节交给同一个安装器。
- [前端 registry](../../../frontend/src/plugins/registry.ts)、[Vite 配置](../../../frontend/vite.config.ts)、[Pack build helper](../../../frontend/pack-sdk/build.mjs)：源码与归档前端的两种加载机制。
- [bootstrap.py](../../../backend/plugins/bootstrap.py)：78–117 行汇总所有启用插件，并准备所有可用平台。
- [SandboxManager](../../../backend/sandbox/manager.py)：325–350 行显示 Python 安装最终绑定执行平台；接口中的 `sandbox_id` 并不代表独享环境。

### 2.1 现有 Pack 说明了哪些不同需求

| Pack | 当前依赖形式 | 建议归属 |
| --- | --- | --- |
| Task Board、SQLite 等 | 轻量宿主 API/Pydantic 适配器 | 宿主公共依赖契约 |
| Knowledge Base | `pyproject.toml` 声明固定 Git revision 的 mat-know-base、文档解析库等，运行在宿主进程 | 保留业务和持久状态归属；把需要独立依赖的解析/转换部分逐步迁到 worker |
| Library | PyMuPDF 等宿主 Python 依赖；前端 PDF 渲染 | 后端解析适合 worker；前端依赖在 Pack 构建时交付 |
| MatCreator | setup hook 安装私有依赖与必要的 Python；`-I -S` worker；目录位于 `sys.prefix/oaw-runtimes` | OAW 管理的独立引擎环境，迁出宿主 venv 路径 |
| XRD | 独立科学计算 venv、脚本及外部引擎程序；单独安装脚本 | 同一个引擎环境管理器，声明平台与 Python 限制 |
| Codex | 发现和验证外部可执行文件，再启动子进程 | 外部工具 prerequisite；登录/服务配置另行表达 |
| MatCreator Bohrium 任务 | 本地 Sandbox 中运行提交工具，远端任务由 image/command 定义 | 本地提交环境与远端计算镜像分别声明；不能从本地 Python ready 推断远端 ready |
| Skill 工具箱 | `defaults` 是任意数据；部分技能把依赖写成说明文字，部分写入 `defaults.runtime.dependencies` | 增加类型明确的环境引用，并接入技能执行前的准备流程 |

对应实现：[Knowledge Base 声明](../../../plugins/knowledge_base/pyproject.toml)、[Library 声明](../../../plugins/library/pyproject.toml)、[MatCreator 环境](../../../plugins/matcreator/oaw_matcreator/native_environment.py)、[XRD 安装器](../../../plugins/xrd/src/oaw_xrd/install_engine_runtime.py)、[Codex discovery](../../../plugins/codex/src/oaw_codex/discovery.py)、[Bohrium jobs](../../../plugins/matcreator/oaw_matcreator/background_jobs.py)。

### 2.2 迁移不能忽略的兼容性

1. **身份并不一致。** 归档当前要求 distribution ID、runtime owner ID、唯一 Pack ID 相同；内置插件可拥有多个 Pack。MatCreator 的 owner 为 `matcreator`，Pack 为 `matcreator.default`。需要保留现有 Pack/card/type/owner ID，不能为适配归档改名而破坏 World、Deck、Legion 和历史记录。
2. **依赖身份也有两套。** `requires_plugins` 引用 owner 且没有版本；`dependencies.packs` 引用 Pack 并有版本。统一模型必须显式区分被依赖对象的种类，并校验 Pack 到 owner/distribution 的映射。
3. **代码结构不能只靠 manifest 修复。** 源码包命名通常为 `oaw_*`，归档只允许派生的 `oaw_pack_*` 根。MatCreator 还有两个 Python 包根及真实文件路径访问；XRD 也会启动邻近脚本。归档 ZIP import 不等价于展开后的引擎资源目录。
4. **存在宿主私有引用。** 部分源码插件依赖 `backend.*` 或宿主前端源码路径。需要迁到公共 SDK，或明确保留为仅随宿主交付的内部组件。
5. **前端构建不只有 React TSX。** MatterViz/Svelte、CSS、PDF worker、WASM/其他静态资源需要构建与资源清单支持；现有简单 esbuild helper 不能直接代表这些 Pack 的独立发行能力。
6. **内容 Pack 保持无代码属性。** schema 2 已有内容 Pack，不可直接把下一版代码协议叫 schema 2；建议 schema 3 先仅用于代码 Pack，内容继续使用 schema 2。归一化保留 `kind` 并继续拒绝内容 Pack 自带 backend/frontend 代码、worker、安装配方和 hooks；内容可以引用依赖代码 Pack 已提供的能力。
7. **文档有陈旧表述。** `pack-distribution.md` 和 `plugins.md` 开头仍称 Store 为 placeholder，与现有 Store 客户端实现不一致；迁移时同步整理开发者和用户文档。

身份约束见 [registry.py](../../../backend/plugins/registry.py) 571–597 行；源码 Pack 例子见 [MatCreator 注册](../../../plugins/matcreator/oaw_matcreator/__init__.py)。

还有一个具体 SDK 一致性问题：[源码 SDK](../../../frontend/src/plugins/sdk.ts) 导出 `Settings2` 等图标，XRD 已使用，但 [发行 build helper](../../../frontend/pack-sdk/build.mjs) 的 SDK 导出清单没有这些名称。共享 ABI 清单应由同一声明生成并验证，不能继续手写两份。

Skill 的缺口见 [Skill 模型](../../../open_agent_world/skill_packages.py)、[技能执行投影](../../../backend/skill_runtime.py) 与 [MatCreator 技能生成](../../../plugins/matcreator/build_packages.py)。当前执行器投影 instructions/files/directories，不将 `defaults` 变成安装指令；不能通过扫描 SKILL.md 中的命令代替正式依赖声明。

## 3. 统一的依赖模型

不要只按包管理器分组。每条依赖至少能回答：谁需要、用于哪项功能、在哪里运行、如何获得、何时准备、如何验证。

| 类型 | 开发者声明 | 用户侧动作 | 边界 |
| --- | --- | --- | --- |
| 前端构建依赖 | Pack 自己的 `package.json` 与 lockfile | 安装已编译 JS/CSS/assets | React/SDK 属于宿主 ABI，其余依赖随产物交付 |
| 宿主适配器依赖 | adapter `pyproject.toml`、宿主 API 与公开依赖兼容范围 | 验证宿主兼容性 | 第三方安装不修改宿主 Python；禁止依靠偶然存在的包 |
| 独立引擎依赖 | worker 的 Python/Node/二进制配方、lock、平台及入口 | OAW 下载或复用私有环境 | 独立进程；不是 Agent Sandbox，也不自动授予资源权限 |
| Sandbox 执行依赖 | 环境 profile：Python/Node/CLI/镜像/技能资源 | 在选定 Sandbox 的 provider 中准备并验证 | 保留实际 Sandbox 的隔离、授权和网络策略 |
| 其他 Pack | 目标种类、ID、版本范围、必需/可选 | 统一规划依赖闭包；Store 可获取，离线说明缺失 | 不等于“开包收藏卡片”，不自动修改 Deck/World |
| 外部条件 | 程序/服务能力、凭据 slot、许可证、GPU/驱动等要求 | 检测、配置或指引用户提供 | 不将密码写入 manifest；不将登录误判为包安装 |
| 模型与数据 | 资源 ID、版本/digest、大小、平台、来源和许可证 | 选择性下载、挂载或使用现有资源 | 缓存与用户数据分离；未经许可不重新分发 |

Python、Node、二进制是可扩展的 provider 类型；`host-worker` 和 `sandbox` 是执行目标。相同技术类型可以用于不同目标，但授权、文件系统和生命周期不能合并。

对于跨 Pack 依赖，建议新声明显式包含 `distribution_id`、版本范围和预期提供的 owner/Pack ID；发行 metadata 声明静态 `provides` 并在加载时核对。这样尚未安装的依赖也能定位到下载产物。现有 Store metadata 没有这套映射，需同步协商 manifest schema 与展示协议；Marketplace 仍只分发静态元数据和产物。不能仅靠本地 Registry 推断远端身份，也不能假设旧服务已经支持新规范。

### 3.1 开发者文件组织

建议增加 Pack 根目录 `oaw.pack.toml`，作为 OAW 元数据与组件关系的唯一手写入口。保留各生态原生依赖文件，不让开发者在 TOML、descriptor 和 JSON 中重复维护同一列表。

```text
plugins/example/
  oaw.pack.toml                   # 身份、组件、环境、功能及外部条件
  pyproject.toml                  # 宿主 adapter 的依赖与 build 配置
  frontend/package.json          # 前端依赖
  frontend/package-lock.json
  runtimes/engine/pyproject.toml  # 私有引擎依赖，独立于 adapter
  runtimes/engine/uv.lock
  sandbox/analysis/requirements.in
  sandbox/analysis/locks/         # 按目标平台生成的完整依赖锁定
```

下面仅为提议中的字段示意，不是当前可执行格式：

```toml
schema_version = 3

[distribution]
id = "example.research"
version = "1.0.0"

[plugin]
owner = "example.research"
packs = ["example.research.default"]

[components.frontend]
project = "frontend/package.json"
entry = "frontend/index.tsx"

[components.backend]
project = "pyproject.toml"
mode = "host-adapter"

[environments.engine]
target = "host-worker"
provider = "python"
project = "runtimes/engine/pyproject.toml"
lock = "runtimes/engine/uv.lock"

[environments.analysis]
target = "sandbox"
provider = "python"
requirements = "sandbox/analysis/requirements.in"
lock_directory = "sandbox/analysis/locks"
prepare = "on-use"

[features.research]
environments = ["engine", "analysis"]
prerequisites = ["model-connection"]

[prerequisites.model-connection]
kind = "model-connection"
```

构建器生成发行 `manifest.json`、目标平台锁定结果、文件与资源 digest，并检查它们与 wheel metadata、前端 ABI、版本及 descriptor 一致。兼容期读取旧 `python_requirements` / `[tool.open-agent-world.runtime].python`，归一化为明确的 legacy Sandbox profile；有多个相互矛盾的来源时给出错误，不静默覆盖。

锁定结果应覆盖传递依赖、Python/Node ABI、OS/架构、必要的 libc/GPU 条件和资源哈希。Git 依赖在开发者构建阶段解析固定 revision，转为可验证 wheel/资源；用户安装不依赖临时 Git clone 或未固定分支。按目标生成锁定，不能用开发机上一次安装成功替代跨平台支持。

### 3.2 共享声明与目标专属增量

开发者不应为同一个依赖手写两份。允许命名依赖组及显式引用，采用“共享声明 + 各目标增量”：

```text
计算公共组：numpy、ase                 # 版本约束仅维护一次
Pack 引擎：引用计算公共组 + 引擎专属依赖
Sandbox：引用计算公共组 + bohr CLI
```

这里只展示组合关系；依赖及其版本仍存放在各生态原生文件中，OAW spec 保存对项目依赖、命名组或配方的引用。跨生态的环境配方可以组合 Python 包与 npm CLI，但分别由对应 provider 解析和准备。

- Sandbox 可以显式复用某个 Pack 项目的完整运行依赖集合，也可以只引用共享计算组；不自动继承 OAW 主项目、所有已安装 Pack 或开发机当前的全部包。
- 如确实全部复用，开发者只需写一次引用，再声明 Sandbox 专属增量。此引用不包含测试、构建等开发依赖，也不意味着自动把宿主 adapter 安装到 Sandbox；需要在 Sandbox 使用项目本身时，另行声明可交付组件。
- `bohr CLI` 这类仅供 Sandbox 使用的工具只声明在 Sandbox 部分，其 Node 等运行前提由 provider 验证或准备。它不应进入宿主或 Pack 引擎环境。
- 共享的是依赖意图，各目标仍独立解析平台条件、生成锁定结果、安装和验证。Windows 宿主与 Linux Sandbox 不能直接共用宿主 `site-packages` 或可执行路径；缓存只能复用经兼容性验证的产物。
- 依赖组不改变第 3 节的宿主边界：若共享组超出宿主公开依赖契约，Pack 后端应由独立引擎消费它，不借组引用修改宿主 Python。
- 环境组合要求不冲突；目标确实需要不同约束时，在各自目标声明并单独解析。不要用“增量覆盖”静默删除公共组约束。
- `doctor` 展示展开后的依赖及来源，发布验收在干净目标环境运行功能冒烟，发现被开发机已有包掩盖的漏声明。静态 import 扫描可以辅助提示，但不能代替这项验证。

这样 Sandbox 的作者输入可以只有“引用哪个公共基础 + 增加什么”，安装器仍能得到自包含的完整依赖计划。已有方案中的独立 requirements 文件是可选组织形式，不要求每个目标复制一份相同的依赖清单。

## 4. 宿主服务与统一生命周期

建议在现有组件上扩展，而不是替换 Registry、Library 和 Store：

| 服务职责（建议命名） | 对现有代码的处理 |
| --- | --- |
| `PackSpec` / `PackSource` | 在 `backend/packs` 增加静态规范和来源适配器；在 import 前完成发现 |
| `PackPlanner` | 解析 Pack 闭包、平台兼容、环境需求、下载体积、已有缓存、冲突和重启要求 |
| `PackInstallationManager` | 保留版本、校验、staging、selection，增加 plan 与持久 operation；不要另建 Store 安装器 |
| `PackEnvironmentManager` | 管理环境定义、安装实例、receipt、健康检查、引用和回收；替代散落 setup 逻辑 |
| 环境 providers | Python worker、可执行文件、Sandbox profile 等各自实现 probe/prepare/verify，不开放通用 shell postinstall |
| Registry / RuntimeProvider | 继续注册卡片与运行时；通过环境句柄启动引擎，业务协议仍由 Pack 定义 |
| SandboxManager | 继续拥有目标 Sandbox、执行授权和隔离；增加环境 profile 的准备与执行选择 |

统一阶段：**发现来源 → 静态校验 → 生成计划 → 获取与暂存 → 准备必需宿主环境 → 验证 → 选中版本 → 重启加载 → 按功能准备 Sandbox**。

具体规则：

- 预装目录、源码开发目录、本地归档、Store 都生成同一内部 spec；来源与获取方式可以不同。开发模式允许 editable，不将可变源码伪装成不可变发行版本。
- 发布时可继续使用 ZIP 和 wheel；适配器维持受控命名空间，独立引擎资源按清单安全展开。为历史 owner/Pack ID 提供明确兼容映射；新第三方不能借此占用 core/其他发行的身份。
- 先安装和验证新版本所必需的宿主环境，再原子切换 desired selection。选择提交前的准备失败保持旧版本可用；已有 selected/loaded 分离及重启激活继续保留。重启后的 import/registration 失败仍可能阻止启动，需要保留不导入故障发行的恢复 CLI 和清晰诊断；不承诺自动降级已迁移的用户状态。
- 必需 Pack 的获取与选择使用整体计划，下载可并行，selection 集合原子提交。计划必须绑定当前安装 revision、目标版本与 digest，执行前重检，避免预览后状态变化。
- 当前内容 Pack 使用已加载 Registry 验证；若一次计划包含新代码 Pack 和依赖它的内容 Pack，必须在正式选择前构造候选依赖闭包，在独立验证进程中检查注册结果、模板及配置。此步骤属于已授权安装阶段，会加载受信任代码，不能放进纯静态 inspect；旧 `_check_content` 不能直接充当候选验证。
- 注册适配器不依赖大型引擎即时启动。可选引擎、Sandbox、登录条件缺失只限制对应功能；保留卡片显示、历史数据和配置入口。某功能必须具备的依赖则在真正执行前检查。
- 安装操作先持久化 operation ID，再执行副作用。界面关闭或等待超时不重新提交；进程重启后根据 receipt、子进程和远端 operation 查询恢复，状态不明时先 reconcile。
- 暂存文件、数据库提交和远端安装并非一个事务；必须记录阶段和归属，恢复时识别孤儿 staging、已发布但未选择的版本及不确定远端结果。
- 校验不等于信任认证。保留现有归档路径、体积、checksum、重复身份防护。宿主适配器仍是受信任代码；独立进程环境解决依赖隔离，不自动提供 OS 沙箱。

### 4.1 环境位置、身份和清理

```text
<data_root>/packs/installed/<distribution>/<version>/  # 保留现有版本存储
<data_root>/runtime/environments/<fingerprint>/        # 宿主私有引擎
<data_root>/runtime/cache/                             # 可共享下载缓存
<data_root>/runtime/operations/                        # 有界日志或操作附件
```

此目录示意只适用于宿主托管环境；远端 Sandbox 存储 provider 返回的环境 ID/receipt，不构造宿主绝对路径。

fingerprint 至少包含规范/锁定 digest、执行目标、平台、ABI、provider 版本和相关构建参数。绑定记录另外包含 owner/version/feature、Sandbox 身份与其重建代次、镜像 digest。身份相同才允许复用，源声明未变不代表环境仍健康。

成熟后允许完全相同的环境复用，先按 Pack 管理也可以。缓存、环境、当前运行租约、用户状态分别记账；GC 仅清理无引用且无运行/清理任务占用的环境。升级和回滚绑定相应环境版本，不原地覆盖旧引擎。环境回滚不等于用户数据库回滚，已有 state migration 仍需兼容性约束和独立验证。

### 4.2 Sandbox：从全局汇总走向按功能与目标准备

新协议使用不可变环境 profile 或 provider 支持的等价机制。目标不是立即废弃 Shared Python；旧 Pack 继续使用标明范围的 `legacy-shared` 环境，并保留聚合冲突检查。

这里的 profile 指依赖配方。现有 [Environment Profile / Compute Target](../../../backend/execution_config.py) 分别保存命令配置与计算目标；应继续复用其凭据、路径绑定与实时授权。新增依赖环境句柄使用独立字段，例如 `runtime_environment_id`，不能改变现有 `environment_id` 的含义，也不创建第二套 secret store。

1. 用户选用某功能并绑定 Sandbox，或 Agent 调用已授权工具时，解析它实际需要的 profiles。
2. 验证该 Sandbox provider 是否支持所需 Python/Node/CLI/镜像、平台和资源；不支持时说明缺少的能力。
3. 自动准备允许的依赖，保留实时 Start、执行、网络、目录和凭据授权边界。安装声明本身不授予这些权限。
4. 在 provider 内安装或挂载并执行健康检查，记录环境句柄及 receipt。后续工具执行显式绑定该句柄。
5. 同一执行进程确实需要组合多个 Pack 时，构建组合 profile 并解析完整约束；存在冲突时报告来源。能分进程调用的功能则保留各自环境，不尝试将两个冲突环境同时导入一个进程。
6. Agent 的临时依赖进入 Sandbox/session 可写层或新 profile，不改写 Pack 锁定环境；记录声明来源和使用范围。

Skill 增加类型明确的 `runtime_requirements` 或 profile 引用，在 build 阶段校验其 owner/version/digest。执行时在既有 Skill 与 Sandbox 双重授权之后解析配方，再准备和绑定环境。用户可编辑的 Skill 不能仅靠引用字符串自动触发新的受信任宿主代码安装；超出既有安装与权限范围时进入正常安装计划。对历史 `defaults.runtime` 只迁移已知格式，其余保留为说明，不解释执行任意文本。

当前内置 `SandboxRuntimeRegistry` 注册本地 Linux、Windows 与 WSL，见 [registry.py](../../../backend/sandbox/registry.py)。未来云 Sandbox 需要独立 adapter 和验收；本地环境准备成功不证明云实例或远端 Batch 镜像已就绪。

## 5. 用户体验与状态契约

用户仍然执行“安装 Pack”。技术分类主要用于解释安装计划和修复，不应要求用户分别运行 npm、pip 和插件脚本。

- 安装预览：来源与版本、将安装的其他 Pack、必需环境及预计下载量、可选功能、外部配置、是否重启；未知体积应明确标为未知。
- 安装进度：可恢复的逐步骤进度，失败定位到具体环境/依赖/目标；重试复用已验证产物。
- 安装后：分别显示已安装、待重启、已加载，以及功能准备/待配置/不支持/失败。用户能看到“卡包已安装；计算功能待选择 Sandbox”，而不是笼统失败。
- 首次使用：按实际功能自动准备；没有凭据则进入对应卡片配置，不重新安装整个 Pack。
- 更新与卸载：沿用运行占用、World 对象、依赖与 pending cleanup 检查。删除代码不删除用户资料、Deck、会话或外部工作目录。

后端状态至少拆为：

- distribution：`source`、`installed_version`、`selected_version`、`loaded_version`、`restart_required`。
- environment：`spec_digest`、`target`、`fingerprint`、`state`、`operation_id`、`error_code`、有界且脱敏的错误。
- feature：依赖的环境与外部条件，以及对当前目标推导出的 `ready / preparing / needs_configuration / unsupported / failed`。

不将这些合成一个互斥枚举：同一 Pack 可以“已加载”，同时一个 Sandbox 功能正在准备，另一个平台不受支持。读取状态不应隐式运行安装。

API 可在现有 `/api/packs` 下增加 `plans`、`operations`、`environments` 查询与执行入口；现有 install/activate/retry 作为兼容入口调用同一服务。CLI 增加 `doctor`、项目构建/锁定与 dev 注册；现有 `build` 只封装已构建产物的行为要通过新子命令或兼容参数保留。

## 6. 建议实施顺序

| 阶段 | 交付内容 | 验收点 |
| --- | --- | --- |
| A：规范与来源 | schema 3 / PackSpec、身份映射、旧协议转换、静态发现、统一 inventory 与 doctor | 所有现有 Pack ID 保持；三个安装来源产生一致需求模型；旧代码/内容归档可读取 |
| B：引擎环境主线 | PackEnvironmentManager、Python worker provider、receipt/operation、MatCreator 与 XRD 迁移 | 同一声明支持源码、离线预装和归档；失败不损坏旧环境；宿主 venv 迁移不丢引擎定位 |
| C：构建与发行 | Pack 前端依赖归属、公开 SDK 清理、复杂前端构建与资源、独立 adapter/engine 产物 | 离开仓库根依赖后仍可构建；干净桌面宿主从 `.oawpack` 使用真实 Pack |
| D：Sandbox profiles | 定向准备、环境句柄、组合 profile、兼容 Shared Python、Agent 临时层 | 冲突依赖可按环境隔离；不影响无关 Pack；权限撤销/目标重建后正确重检 |
| E：统一安装界面 | 依赖闭包计划、operation 进度、功能状态、定点重试、Store 同入口 | 本地文件与 Store 展示相同计划和状态；断线重开不重复安装 |

阶段 B 之前先做一个小型贯通样例验证 schema 3 的 adapter + worker 归档边界；阶段 C 再推广到复杂真实 Pack。后端 operation/status 契约与基础界面随 B 一起交付，不等到 E 才能诊断。

Knowledge Base/Library 的进程边界调整会涉及文件、数据库和已有 API，不宜仅为迁移依赖仓促整体远程化。先迁无状态解析/转换，审查状态与事务归属后再决定其余服务边界。

默认决策建议：第三方不修改宿主 Python；独立引擎由 OAW 托管；Sandbox 按需准备；包管理生态文件保留为依赖真源；源码开发允许 editable，但正式预装与发布使用相同编译规范。暂不承诺任意 apt/choco/brew 安装、完整插件 RPC 化或 Python 热卸载。

## 7. 验收矩阵

必须覆盖实际交付路径，不能只证明 manifest 能解析：

1. 来源一致性：同一个样例从源码、预装构建目录、本地归档和 Store 进入，得到一致的依赖计划与功能结果；editable 身份单独标识。
2. 最小可用环境：干净宿主缺少 Pack 私有依赖仍可完成安装；宿主依赖清单不被第三方安装改变。
3. 发布可用性：复杂前端及 worker 资源可独立构建、校验、安装；启动不依赖仓库路径或开发机缓存。
4. 版本兼容：保留旧 schema 1/2、历史 owner/Pack/card ID、World/Deck/Legion 数据；重复 owner 和命名空间冲突仍拒绝。
5. 冲突范围：两个 Pack 对同一库有不同要求时，独立环境各自可用；必须共进程时在准备前明确拒绝；legacy shared 继续保护已有要求。
6. 失败恢复：下载中断、构建失败、验证失败、进程退出、提交前后崩溃和客户端断线；旧版本保留，未完成环境不得 ready。
7. 运行一致性：升级不替换运行中的引擎；worker、Sandbox 命令及清理占用阻止回收；重启后绑定正确版本。
8. Sandbox 边界：用实际支持的 Windows/Linux/WSL 目标验证准备与执行；未来远端 provider 单独验证。无 Start/执行/网络许可时不能绕过授权。
9. 前端运行：共享宿主 React/SDK、动态资源加载、CSS/worker 支持、错误只影响相应视图；桌面环境另测。
10. 真实 Pack：至少 MatCreator 独立引擎、一个 Sandbox 依赖功能、一个非平凡前端；再补 XRD 和文档解析链路。

保留并扩展现有 [Pack 安装测试](../../../backend/tests/test_pack_installation.py)、[Store 测试](../../../backend/tests/test_store.py)、[Shared Python 测试](../../../backend/tests/test_shared_python_runtime.py)、[归档端到端测试](../../../frontend/e2e/pack-host.spec.ts)。报告中分别标注静态/隔离测试、实际运行时、真实服务和桌面 GUI 的验证结果。
