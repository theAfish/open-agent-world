# 从 dev 更新现有 ECS 部署

`scripts/aliyun_release.py` 用于已经按照 [ECS.md](ECS.md) 安装的原生多用户服务。
它复用现有账号、systemd worker 隔离、卡包准备机制和阿里 Sandbox；不创建云资源，也不修改个人版入口。

## 一次性配置

将 [release.example.json](release.example.json) 复制到本机
`~/.config/oaw/aliyun-release.json`，填写仓库路径、构建用 Python、SSH 身份文件和服务器。
配置只引用密钥文件路径，不填写模型 Key、登录密码或云令牌。SSH 必须已经可信地录入服务器 host key。
构建机需要 Git、Node/npm，以及安装过项目测试依赖的 Python；服务器需保留现有 uv 和共享 Pack bundle。

脚本在阿里分支里。可以从单独的工具工作树调用，主工作区继续留在 dev：

```powershell
$releaseTool = 'D:/AI/open-agent-world/.tmp/ali-kb-deploy/scripts/aliyun_release.py'
$releasePython = 'D:/AI/open-agent-world/backend/.venv/Scripts/python.exe'

# 查看线上提交；不改服务
& $releasePython $releaseTool status

# 拉取并合并 dev，运行相关测试，构建前端和卡包，打包并正常推送阿里分支
& $releasePython $releaseTool prepare --merge origin/dev

# 已审阅的小范围界面/业务处理更新：不涉及数据库、依赖或发布声明时
& $releasePython $releaseTool prepare --merge origin/dev --routine

# 也可指定经过审阅的 dev 提交，避免把后续实验内容一起合入
# & $releasePython $releaseTool prepare --merge <commit>

# 上传、校验文件和目标、准备 Pack 依赖；不重启服务、不迁移用户数据
& $releasePython $releaseTool deploy --check

# 对刚准备的固定版本执行备份、更新、迁移和验证
& $releasePython $releaseTool deploy
```

`prepare` 不带 `--merge` 时，仅构建当前阿里分支。dev 的未提交修改不会进入发布，也不会被移动或覆盖。
脚本在配置中的独立 **detached worktree** 合并；不会切换主目录的分支、强推或自动解决冲突。
阿里分支不能同时在别的工作树检出，因为成功构建后脚本才以原提交为条件推进该分支。
合并冲突会停在构建工作树：在那里解决并提交后，用 `prepare --resolved` 继续。

默认运行部署/鉴权/Sandbox/发布相关后端测试、发生修改的插件测试，以及 hosted 前端测试和正式前端构建。
项目拥有者也可使用 `prepare --test-plan 路径.json` 指定本次需要的检查，替代自动扩大的测试列表。
文件格式为 `{"backend": ["backend/tests/test_default_publication.py"], "frontend": ["src/deployment/PublishApplication.test.tsx"]}`；
列表可包含具体测试函数，也可为空（例如当前提交已完成对应验证）。该选项保持正常构建、备份、迁移、版本与线上验证，
适用于发布机制等核心改动，不受 `--routine` 的文件路径限制。完整跨项目回归仍交给 PR CI。
可用 `--pytest-path` 或配置中的 `backend_tests`、`frontend_tests` 增加检查；这不等同于完整 PR CI。
每次记录提交、测试路径、部署包 SHA-256、目标服务器和线上基线；线上版本变化后必须重新 prepare。

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

构建和更新是两个明确步骤：`prepare` 会推送阿里分支，但尚未更新线上。
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
