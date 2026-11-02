# ECS 发布与 PR CI 边界

> **Phase 2:** See [PREVIEW.md](PREVIEW.md) for the current trusted bootstrap,
> first Preview installation, and acceptance gates. The new operator requires
> independent execution registration and blocks production deployment while the
> root provisioner imports application code. The Phase 1 commands below are
> historical context, not an instruction to upgrade or restart the live service.

`scripts/aliyun_release.py` 用于已经按照 [ECS.md](ECS.md) 安装的原生多用户服务。
它复用现有账号、systemd worker 隔离、卡包准备机制和阿里 Sandbox；不创建云资源，也不修改个人版入口。

本次只实施第一阶段：无部署凭据的 PR 检查与本地构建产物边界。下文现有手动发布命令继续要求可信操作者，
不构成 PR Preview 或合并后自动发布入口。第二阶段设计见文末；本阶段不创建 Preview、修改服务器或云资源、
修改 GitHub 环境/密钥/保护规则，也不启用自动发布。

第一阶段复用 `ci.yml`、`desktop-check.yml` 和 `docs.yml`，新增 PR 目标分支 `feat/aliyun-agentcore-poc`；
push 触发范围保持原配置。PR checkout 不保留凭据，继续使用现有 GitHub-hosted runner 和只读仓库权限。
Docs 的发布步骤仍只用于非 PR 的 dev，桌面 release workflow 未接入阿里发布。

本地 `build`/`prepare` 不读取服务器状态，不 fetch、merge、推进分支或 push；同步分支属于单独的 `sync` 命令。
新产物清单为 `version: 2`、`kind: oaw-build`，记录源提交、测试比较提交、routine 标记、检查及文件哈希，
不包含服务器 baseline、目标或 `operator_files`。这些字段说明构建内容，不授予部署权限。
手动 `deploy` 先在本机检查固定 bundle，再用独立 `deployment.json` 绑定目标服务器与当时的 baseline；不重写构建包。
服务器只调用预先独立安装的可信 operator，且必须在提取产物或执行应用代码前，以 root 所有的外部信任策略批准目标、
archive digest 和提交。缺失策略或未知产物会拒绝；本阶段不替现有服务器安装 operator 或写入批准策略。
旧 v1 固定产物仍须通过同一外部策略。该机制仅面向可信人工发布，不能代替 GitHub 审阅和 PR 来源验证。

## 一次性配置

将 [release.example.json](release.example.json) 复制到本机
`~/.config/oaw/aliyun-release.json`，填写仓库路径、构建用 Python、SSH 身份文件和服务器。
配置只引用密钥文件路径，不填写模型 Key、登录密码或云令牌。SSH 必须已经可信地录入服务器 host key。
构建机需要 Git、Node/npm，以及安装过项目测试依赖的 Python；服务器需保留现有 uv 和共享 Pack bundle。
仅执行 `build` 时不需要此配置或服务器访问。手动部署前，管理员还须通过单独审阅的 bootstrap 在服务器安装
`/opt/oaw-release-operator/release_remote.py`、同目录的 `release_worker.py` 及外部信任策略；客户端使用 `/usr/bin/python3 -I` 调用该固定 operator，
不会上传调用者 checkout 中的 operator 代码。本阶段没有执行该 bootstrap；旧服务器缺少它时部署会拒绝。
该固定系统 Python 必须支持 Python 3.11 及以上，且独立于应用 venv；bootstrap 时需验证版本、所有权和完整导入路径。
策略固定为 `/opt/oaw-release-operator/trusted-artifacts.json`：`version: 1`、`purpose: trusted_manual`，
`target` 精确列出 `app`、`hosting`、`releases`、`service`，`approved_artifacts` 中每项仅含 `archive_sha256` 和 `source_commit`。
operator、helper 和父目录必须由 root 持有且不可被其他用户写入，策略文件还必须仅 root 可读写；链接或宽松权限均被拒绝。

脚本在阿里分支里。可以从单独的工具工作树调用，主工作区继续留在 dev：

```powershell
$releaseTool = 'D:/AI/open-agent-world/.tmp/ali-kb-deploy/scripts/aliyun_release.py'
$releasePython = 'D:/AI/open-agent-world/backend/.venv/Scripts/python.exe'

# 查看线上提交；不改服务
& $releasePython $releaseTool status

# 构建当前 checkout；无需发布配置或 SSH，仅输出固定产物
& $releasePython $releaseTool build --source 'D:/AI/open-agent-world' --output 'D:/AI/open-agent-world/.tmp/aliyun-builds' --python $releasePython

# 可信维护者的旧流程：单独同步并推送阿里分支；开发者日常使用 GitHub 原生 PR
& $releasePython $releaseTool sync --merge origin/dev

# 在独立 detached worktree 构建配置中的阿里分支；不推送、不连接服务器
& $releasePython $releaseTool prepare

# 已审阅的小范围界面/业务处理更新：不涉及数据库、依赖或发布声明时
& $releasePython $releaseTool prepare --routine

# 上传、校验文件和目标、准备 Pack 依赖；不重启服务、不迁移用户数据
& $releasePython $releaseTool deploy --check

# 构建产物也可从其他 runner/目录取回后原样指定；需已通过服务器的独立人工批准
# & $releasePython $releaseTool deploy --artifact 'D:/path/to/fixed-build' --check

# 对刚准备的固定版本执行备份、更新、迁移和验证
& $releasePython $releaseTool deploy
```

`prepare` 仅构建本机已有的阿里分支，使用配置中的独立 **detached worktree**；它不再接受合并或继续合并的职责。
`build` 使用明确指定的干净 checkout；开发者未提交修改不会进入产物，也不会被移动或覆盖。
只有显式 `sync` 会拉取、合并、推进并正常推送配置中的分支；它不会切换主目录的分支、强推或自动解决冲突。
阿里分支不能同时在别的工作树检出；发生冲突时应在同步工作树审阅解决，之后再独立构建。
冲突解决并提交后，用 `sync --resolved` 正常推送该固定合并，再执行 `prepare`。
可用 `--compare-ref` 选择本机已有的比较基线；不指定时使用当前提交的第一父提交，初始提交则检查全部文件。
这只影响构建测试选择，不是服务器部署基线或 GitHub 发布授权。

默认运行部署/鉴权/Sandbox/发布相关后端测试、发生修改的插件测试，以及 hosted 前端测试和正式前端构建。
项目拥有者也可使用 `prepare --test-plan 路径.json` 指定本次需要的检查，替代自动扩大的测试列表。
文件格式为 `{"backend": ["backend/tests/test_default_publication.py"], "frontend": ["src/deployment/PublishApplication.test.tsx"]}`；
列表可包含具体测试函数，也可为空（例如当前提交已完成对应验证）。该选项保持正常构建、备份、迁移、版本与线上验证，
适用于发布机制等核心改动，不受 `--routine` 的文件路径限制。完整跨项目回归仍交给 PR CI。
可用 `--pytest-path` 或配置中的 `backend_tests`、`frontend_tests` 增加检查；这不等同于完整 PR CI。
构建记录源提交、比较提交、测试路径和部署包 SHA-256；服务器目标与线上基线在手动部署阶段单独绑定。
已绑定的线上版本发生变化后，旧部署请求会拒绝，应重新核对并绑定；不能仅凭包内字段覆盖新版本。

`--routine` 是操作者审阅后的精简路径：运行改动涉及的测试文件，以及用 `--pytest-path` 指定的相关测试；
不会固定重跑无关的阿里 Sandbox、Pack IDE 等测试。Python 改动没有对应测试时拒绝准备发布。
`--pytest-path` 也接受 `文件::测试函数`。宿主核心、卡包声明、注册、生命周期及常见 schema 文件的改动会被拒绝，
必须改用完整模式；这项路径保护不代替代码审阅，业务处理文件内也可能包含迁移或权限变化。

精简模式仍执行完整备份、哈希校验、任务空闲检查、失败恢复和入口鉴权检查，但不重写发布声明、
不额外运行每个历史 profile 的离线迁移，只冷启动并验证网关当前选中的登录入口。
其他历史工作区保留原声明，日后按需启动。发布声明或数据库结构发生变化时必须使用完整模式。
Node 版本及 npm 锁文件未变时复用已安装的依赖；卡包前端继续使用项目既有构建缓存。
服务器上已就绪的受管理环境复用其有效准备记录，不重复运行重型科学库的安装验证。

## 更新过程和边界

- `--check` 会上传暂存包并准备共享依赖缓存，因此不是完全只读；用户服务和数据保持当前版本。
- 更新前检查正在运行的 Agent、知识库转换和工作区部署任务。有任务时拒绝更新，请空闲后重试。
- 停止网关和 workers 后，备份应用及 `control`、`instances`（包括账号、文件、数据库和配置）。
- 每个 profile 的迁移复用原 worker 的 UID、绑定目录、环境和 systemd 隔离；不会以 root 迁移用户数据库。
- 已发布 Workspace 保留身份、布局和已有卡片范围，只刷新这些卡片的 Pack 发布声明与版本。
  新预设/新布局仍由 admin 创建和发布；不会自动重建用户工作区。声明与布局不兼容时停止并回滚。
- 验证每个 worker 的冷启动、应用 bootstrap，以及匿名请求和 runtime 工程画布访问被拒绝，之后恢复网关。
  最后检查公网 HTTPS 登录入口；不自动登录用户或调用付费模型/真实科学计算。
- 普通更新失败会自动恢复更新前代码和数据。宿主 Python 依赖、`backend/uv.lock` 或 systemd 生成逻辑变化会明确拒绝，
  这些更新需要先单独处理宿主运行环境/服务定义；不会靠通用脚本猜测迁移方法。

分支同步、构建和更新是三个明确步骤；`build`/`prepare` 完成后仍未推送或更新线上。
实际切换有短暂停机；发布目录和备份包含私有数据，只允许服务器 operator 访问。
旧前端 hash 资源会保留，供尚未刷新页面的用户使用。备份暂不自动清理，由管理员按保留策略维护。

## 查询、断线和回滚

```powershell
# prepare/deploy 输出 release ID；本机也保存在 output/latest.json
& $releasePython $releaseTool status --release <release-id>

# 恢复上传/接续等待同一个版本；已完成的版本不会重复部署
& $releasePython $releaseTool deploy --release <release-id>

# 显式恢复该版本更新前的代码和数据快照
& $releasePython $releaseTool rollback --release <release-id> --restore-data
```

正式切换由服务器独立 systemd 任务执行，本机断线不会取消事务。
服务器断电或任务被强制终止时，先查状态及私有日志，再对当前发布执行上述显式恢复；不会盲目重跑迁移。
同一服务器通过发布锁串行执行操作。已被后续发布替代的旧版本不能直接回滚。

**手动回滚会把用户数据恢复到更新前**。期间新产生的数据会另存于备份目录的 `replaced-data-*` 中，
不会自动合并回数据库。失败详情保存在对应发布目录的 `prepare.log`、`migration.log`、`verification.log`、`failure.log`。
若出现 `recovery-required`，服务保持停止，需要先检查恢复失败原因。脚本不会输出环境变量或用户内容。

## 第二阶段设计：一个共享 Preview 与合并后发布（尚未实施）

开发者直接向阿里目标分支提交 GitHub 原生 PR，在 PR 中查看检查、Preview 地址和部署版本，审阅后正常合并。
不在后台执行另一次 `sync --merge` 来替代 PR，也不要求开发者复制 SHA、选择构建机或运行 SSH 发布命令。
合并目标、workflow 和操作者允许的环境清单由维护者维护；这些部署配置不能由 PR 产物提供。

### 构建、接收与版本关联

1. **PR 构建**：复用现有 GitHub-hosted runner，通过 `pull_request` 执行测试和构建，仅读取仓库，
   不注入 SSH、云、生产或 Preview 凭据，不授予 OIDC 写权限，不使用生产 ECS 作为 runner。
   PR 代码、依赖安装和测试均按不可信代码处理。记录仓库 ID、PR 编号、head/base SHA、实际检出的合并 SHA、
   workflow 标识、run ID、run attempt；`GITHUB_SHA` 在 PR 上通常是临时合并提交，不能当作 PR head。
   这些关系来自 GitHub 事件和 API，不接受开发者输入 SHA 作为发布授权。[事件语义](https://docs.github.com/en/actions/reference/workflows-and-actions/events-that-trigger-workflows)
2. **可信接收**：由受保护维护分支上的 controller 接收完成事件，只执行预装或固定版本的可信控制代码。
   它独立查询 GitHub，校验仓库/允许的 workflow、事件、成功结论、run/attempt、artifact ID、未过期状态，
   并确认 PR 仍开放、目标正确且 head、base、临时合并关系仍与本次构建一致。过期、歧义、重复或已被新提交替代时拒绝。
   artifact 中的自述 JSON 只能与平台记录交叉校验；不能单凭它取得权限。
   部署凭据仅交给受保护环境中的 controller，其传输身份只调用固定 operator 协议与允许的目标/操作，
   不向 PR 构建或 Preview 应用提供生产 SSH、任意 root shell 或 GitHub 仓库写权限。
3. **固定产物**：按本次 run 和精确 artifact ID 下载，不按名称搜索“最新”。核对平台 digest、外层归档及内部文件清单，
   拒绝越界路径、重复成员、链接、特殊文件、额外文件、`operator/` 控制载荷和私有/可变数据。
   controller 将已验产物写入不可覆盖的环境发布目录，并记录所用可信 operator 版本；失败或丢失时重新构建，不能静默替换。
   平台 artifact 元数据包含 digest 与关联 run；这些校验说明产物来源和完整性，不能证明 PR 代码安全。
   [Artifact API](https://docs.github.com/en/rest/actions/artifacts)
4. **Preview**：只将该已验 PR 合并产物交给 Preview 的非 root 运行身份。共享 URL 明确显示 PR、head、base、构建合并 SHA
   和 run；提交更新后旧 Preview 结果自动失效，页面与 PR 检查都不得继续将其标为当前版本。
5. **生产候选**：合并到阿里目标分支后，由 `push` 检查并重新构建 GitHub 实际落地的提交。
   squash、rebase 或 base 更新均可能使它不同于 PR 临时合并提交，因此不直接把 PR 包晋升生产。
   生产接收器验证实际分支提交、对应合并 PR 和通过的必需检查，再绑定产物；不执行上传者填写的 SHA。
   受保护环境通过后才发起更新，并在拿到部署锁后再次核对目标分支/当前安装版本，拒绝已被后续提交替代的候选。

`workflow_run` 可以作为可信接收通知，但其上游产物仍是不可信输入；接收 job 不 checkout PR、执行其脚本、导入其 Python，
也不从其缓存恢复控制工具。`pull_request_target` 不用于构建或运行 PR。
GitHub 明确指出，特权 workflow 执行上游 PR 内容会暴露凭据和仓库权限。
[不可信内容边界](https://docs.github.com/en/actions/reference/security/secure-use)

### Preview 与生产隔离

只有一个共享 Preview，同一时间承载一个 PR；新候选可以覆盖正在进行的 Preview 集成测试，页面显示当前占用版本。
被替代的测试不得把结果归给新版本。默认复用现有 ECS，先验收严格的非 root、mount、环境和网络隔离；
同机 Unix 用户与 systemd 限制不提供独立内核边界，不能因此声称任意 PR 代码与生产已安全隔离。
允许进入该 Preview 的贡献者/分支范围由可信 controller 限定；恶意或未知 fork 的 PR 只运行无密钥 CI，
需要动态执行时另选阿里 Sandbox VM。新增 ECS 不作为默认前提。

| 边界 | Preview 要求 |
| --- | --- |
| 身份与服务 | 独立 gateway、worker 身份和 systemd 单元；关闭 Preview 的应用内 root provisioner，资源操作走独立可信 broker；无 sudo、Docker socket 或生产服务控制权限 |
| 代码与依赖 | 每个版本独立目录；构建/Pack prepare 以受限非 root 身份执行，运行时将已准备代码和依赖只读挂载；不共用可写 venv 或依赖缓存 |
| 账号与数据 | 独立 control/账号库、每个 profile 的数据库、上传/知识库/日志/备份目录和 volumes；不用生产数据目录或可写生产快照 |
| 设置与密钥 | 独立 encrypted settings、加密主密钥、Cookie/会话密钥、OAuth 应用/回调和环境文件；测试 fixture 不含真实用户信息 |
| 云 Sandbox | 独立受限凭据、profile/工作区/请求标识、绑定 metadata、文件快照与 Volume；停止/回收权限不能触及生产 Sandbox |

Preview 切换采用专用测试账号和可重建 fixture；需要保留的测试结果先导出到本环境，再重置或迁移 Preview 自身数据。
生产数据库、模型密钥、云管理凭据、加密 settings 和现有 Sandbox 绑定均不复制到 Preview。
Preview 运行模型/云命令时只可使用其专用、限额的凭据；PR CI 本身不执行付费模型或真实云计算。
普通设置按现有管理入口保存；密钥只进入环境内的私有加密存储或 root 持有的环境文件，
不进入仓库、artifact、工作流日志、PR 评论、浏览器版本信息或通用 Sandbox 环境变量。

### 可信操作者、串行切换与恢复

第一阶段已将 operator 传输与构建包分离，并增加外部批准策略，但当前手动路径仍只适合可信代码：
`deploy --check` 的 prepare、安装后验证及 root provisioner 的完整导入链仍可能执行已批准应用代码。
systemd 限制或手动 digest 白名单不会把这类 root 执行变成安全的 PR 执行边界，不能直接连接到 PR Preview。

第二阶段必须先由可信管理员从已审阅版本独立 bootstrap operator、协议校验器、Python/依赖和 root provisioner 的完整导入链，
将它们放在 PR/application 发布目录之外的 root 所有只读路径；后续只能走独立审阅的 operator 升级流程。
root 进程不执行或导入 artifact 中的应用、Pack、验证 helper、recipe 或 venv。
应用/Pack prepare、迁移和版本检查均在对应环境的受限非 root 身份运行；root 只处理固定协议允许的路径、服务和事务操作。
bootstrap 先验证 operator 协议兼容版本，应用更新不能顺带覆盖可信控制面。

Preview 和生产部署共用一个 GitHub concurrency group，`cancel-in-progress: false`；PR 的纯构建可按 PR 取消旧 run。
GitHub 队列不保证提交顺序，不能用它判断候选是否最新。[并发规则](https://docs.github.com/en/actions/how-tos/write-workflows/choose-when-workflows-run/control-workflow-concurrency)
可信 controller 还持有跨环境的部署 lease，各服务器保留事务锁；这样 Preview 切换、生产更新、回滚不能并发更改依赖或服务。
lease 记录环境、产物/run、事务 ID 和递增 generation，切换前再次核对占用者；旧 run 不能在新占用者之后恢复写入。
SSH 或 Actions 断线后服务器事务继续，controller 先查已记录事务状态，再续租/恢复；未知状态或 lease 超时不能直接抢占重跑迁移。

生产继续保留空闲检查、应用与数据备份、既有 UID 下迁移和失败恢复；上线前后核对安装 receipt、source commit、artifact digest、
operator 协议版本、服务冷启动、bootstrap 和匿名访问拒绝。共享 Preview 也必须核对同一版本关联，不能只以 HTTP 200 判成功。
回滚只允许恢复当前安装对应的已验旧产物，并验证备份/数据迁移兼容性；需要恢复数据时明确说明会回退用户写入，
保留被替换数据，不能跨越更新后的版本静默恢复。恢复失败保持停止，检查私有状态后再处理。

### 下一阶段实施清单（当前全部未启用）

- 确认阿里目标分支、必需检查和合并规则；建立可信 controller 维护位置及 operator/bootstrap 独立升级边界。
- 在现有 ECS 准备并验收 Preview 的非 root 执行、可信资源 broker 及上述数据/身份/密钥/网络/Sandbox 隔离；
  先用合成数据验证跨环境读写、服务控制和云回收均被拒绝，确认不可信 fork 不能进入共享 Preview。
- 实现平台 run/attempt/artifact 校验与 head/base/merge 关联；覆盖 PR 更新/关闭、错误仓库、伪造清单、过期产物、重跑和乱序完成的拒绝测试。
- 实现非 root prepare/迁移/验证、共享 lease 与事务锁；覆盖取消、断线、超时、占用者变化和新生产版本阻止旧候选恢复。
- 在隔离 Preview 演练准确版本验证、失败恢复与备份损坏拒绝后，再设置 GitHub 环境凭据和保护规则。
  环境审批能力取决于仓库可见性与 GitHub 套餐，启用时现场核实；本阶段不假定已有保护。
  [环境能力与密钥可见范围](https://docs.github.com/en/actions/how-tos/deploy/configure-and-manage-deployments/manage-environments)
- Preview 验收与人工审阅通过后，单独启用阿里分支合并后的生产发布；第一次切换仍验证冷启动、鉴权、数据保留及回滚，再扩大自动化。
