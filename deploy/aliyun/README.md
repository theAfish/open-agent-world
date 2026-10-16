# 阿里云适配 POC

这是第一轮可本地验证的适配尝试，不是已完成云端验收的部署。普通桌面入口
`backend.main:app`、个人数据目录和本地 Sandbox 默认选择保持不变。

考虑 CIAM 成本，当前先验证本人使用。2026-10-16 起优先推进 **AgentCore 高代码容器直接
挂载 AgenticFS**，见 [直接托管的实际进展与部署条件](DIRECT_AGENTCORE.md)。专用 Workspace
与本地挂载探针已准备，但缺少 ACR，云端 Agent 尚未创建。
现有 OAW 云执行入口仍走独立阿里 Sandbox；其实现及历史验证见
[不依赖 CIAM 的单人方案](SINGLE_USER.md)。
后续**内部用户自行注册并运行内化 MatCreator Legion**的身份服务、访问方式和准备步骤
见 [多用户内部测试部署准备](INTERNAL_TEST.md)。CIAM 不是部署个人 OAW 的前提。
当前固定用户实例的步骤仍仅用于基础集成验证。

## POC 目标与当前差距

本次 POC 的目标是：运营方部署一套服务、提供一个网址；测试用户通过浏览器注册或登录，
系统自动开通或恢复其独立工作区，直接使用 OAW。用户不需要安装软件、准备云账号或部署实例，
运营方也不应为每位新用户手工复制配置和部署。底层每用户独立的运行环境可以由后台自动调度，
不改变统一入口的使用体验。个人版继续使用现有默认入口和本地流程。

**当前实现尚未达到这一目标。** 它只完成了固定用户实例的适配基础：
`OAW_OWNER_SUBJECT` 将每个部署绑定到一个预先指定的用户；没有统一的多用户入口、
首次登录时的用户/工作区登记、自动资源开通与恢复、按已验证身份路由到对应实例的能力。
不能通过移除固定用户校验直接开放给所有登录用户，否则会让他们进入同一 profile 和数据库。

登录与开放注册也需要分别验收。[Agent Identity 用户管理文档](https://help.aliyun.com/zh/agentidentity/user-management)
明确描述了管理员建号、外部身份源首次登录自动建号（JIT）及用户同步；这些能力本身不能证明
用户池提供面向公众的自助注册页面。开放注册仍需核实或接入具备该能力的身份源。

最终验收必须包含：两个新用户访问同一个网址，无需运营方逐个配置即可进入各自的工作区；
跨用户 API、WebSocket、文件和任务访问被拒绝；重新登录恢复原数据；重试开通不会重复创建资源；
目标 OAW/MatCreator 工作流在真实阿里环境中完成。以下手工部署步骤仅供基础集成测试，
不能作为“部署后即可邀请别人注册测试”的交付说明。

## 当前基础集成范围

由运营方为每个测试账号手工部署一个独立 OAW 实例、profile 和数据库；同一镜像可以复用。
测试用户只需浏览器访问分配给自己的实例，无需自行安装或部署。
Agent Identity 用户池负责账号和 OIDC 登录。OAW 只验证外部身份并绑定工作区。
阿里 Agent Sandbox 负责执行隔离，OAW 不自行创建 Docker/Kubernetes 集群。
第一轮采用 **Agent 调用 Sandbox**：标准 OAW Agent/可信资产逻辑留在服务端，
生成的命令在阿里 Sandbox 中运行。整套 Agent 进程搬进用户根 Sandbox 是后续工作。

入口为 `backend.cloud.aliyun.app:create_app --factory`，必须显式选择。
它只注册阿里 Sandbox 后端，配置缺失、远端不可用、不支持的能力都会报错，
不会回退到宿主机或个人版运行环境。只有运营方预先批准的 MatCreator、结构查看器、
SQLite 和内置核心 Pack 可加载；在线安装 Pack、桌面文件选择、存储迁移和发布接口关闭。

MatCreator 知识资产、技能定义和 SQLite 卡片保留在原有核心逻辑中。
**内化 MatCreator 已接回受信任的服务端编排路径**：镜像在构建时准备 Pack 自有依赖，
worker 仅继承系统运行必需的环境变量；本次模型配置通过私有标准输入传入。
现有工具桥继续逐次授权，生成的命令只进入阿里 Sandbox。worker 本身不是 OS 隔离边界，
不得在这个部署中加载用户提供的 Python Pack。当前仍是每个部署一个固定所有者。
云端 Legion 模板关闭网络，并将默认命令超时设为 60 秒以适配托管 VM 生命周期，个人模板不变。
只读 runtime bundle、共享 Python 安装及网络授权仍未实现；涉及这些能力的科学工具
会明确报错，不能宣称任意 MatCreator 科学工作流已可用。真实云端执行仍待验收。

## 需要准备的阿里资源

1. 一个 OIDC 客户端和测试身份；单人阶段使用已有 RAM 用户即可，不需要 CIAM。
   后续双用户隔离验证也可使用 Agent Identity 用户池中的测试账号。回调地址为各实例的
   `https://<应用域名>/auth/callback`。`OAW_OWNER_SUBJECT` 必须是该用户真实 `sub`，
   不是邮箱、显示名、云 RAM AccessKey 或 AgentCore Endpoint API Key。
2. Agent Sandbox 极速版 Team、API Key、支持 Volume 的模板和每用户独立 Volume。
   官方当前文档将 Volume/AgenticFS、暂停恢复列为需要开通的能力，先确认账号权限。
   模板必须有 `/bin/sh`、`/usr/bin/python3` 和 `user` 用户；`/workspace` 挂载专属卷，
   对该用户可读写。模板不可包含 OAW 管理凭据或其他用户数据。
3. OAW profile 的持久块存储/可靠本地文件系统，保留完整目录，包括 SQLite、密钥和绑定。
   每个 profile 只允许一个运行实例/worker。不要把 SQLite WAL 放到未经验证的 NAS、
   OSS FUSE 或多个副本共享的目录。Sandbox Volume 与 OAW 数据库存储是两份不同资源。
4. AgentCore 高代码应用、ACR 镜像仓库以及 HTTPS 入口。Endpoint API Key 只保存在
   可信网关中，不交给浏览器。网关必须转发 HTTP、登录回调、Cookie、上传与 WebSocket；
   保留真实浏览器 Origin，不允许任意目标代理。

   提供了 `nginx.conf.template` 作为该网关的配置起点，放在 HTTPS 负载均衡之后。
   `OAW_AGENTCORE_ORIGIN` 填目标 Endpoint 的实际上游地址；认证 Header 名称和完整值
   必须照控制台填写到 `OAW_AGENTCORE_AUTH_HEADER` / `OAW_AGENTCORE_AUTH_VALUE`，
   不假设平台使用某个固定 Header。只替换 `OAW_*` 环境变量。网关模板尚未实测。

AgentCore 文档确认自定义 HTTP 容器部署，但没有为本 POC 证明“浏览器完整路由 +
WebSocket + 持久 profile 挂载”都能在目标账号落地。**这三项必须实测**。
如果 AgentCore 不提供适合 SQLite 的持久存储，需继续完成数据库持久化适配，才能交付
所选的托管方案。当前没有将普通 ECS 部署作为本轮替代目标。
也可以显式启用临时 profile 做一次性 AgentCore 路由测试，但不能用于用户数据。

## 构建与启动

```sh
docker build --platform linux/amd64 -f deploy/aliyun/Dockerfile -t oaw-aliyun-poc .
```

若访问 PyPI 较慢，可为这次构建添加
`--build-arg OAW_PYPI_INDEX=https://mirrors.aliyun.com/pypi/simple/`。
后端安装从 `uv.lock` 导出版本及哈希，再使用 `--require-hashes` 校验镜像源提供的文件；
不修改仓库锁文件或个人版配置。MatCreator 继续使用 Pack 自有安装流程与独立依赖目录。

Dockerfile 使用专用忽略文件，不把个人数据、环境文件或 Git 历史加入构建上下文。
镜像包含源代码和批准的 Pack，不从本机 profile 制作快照。
本机已成功构建 `linux/amd64` 镜像，验证范围见下方记录。

复制 `.env.example` 到仓库外的私密配置文件并填写真实资源信息。给每个用户准备独立
数据卷，属主 UID 为 `10001`。运营方确认存储会跨容器重建保留后，在其根目录创建
`.oaw-persistent-volume` 文件，内容为对应 `OAW_WORKSPACE_ID`。这是显式部署确认标记，
程序不能仅凭此文件证明云存储持久性。禁止复制已有个人 profile 隐式绑定到云用户。

```sh
docker run --rm --name oaw-alice --env-file /secure/oaw-alice.env \
  -v /srv/oaw/alice:/var/lib/oaw -p 127.0.0.1:8080:8080 oaw-aliyun-poc
```

AgentCore 配置：`linux/amd64`，容器启动命令使用 Dockerfile 的 CMD；端口 `8080`；
健康检查 `GET /health/ready`；**单实例、单 worker**。本入口是完整 OAW HTTP 应用，
没有实现 Chat Completions / AG-UI 兼容路径，不要将不存在的协议路径填入控制台。
用户通过 OAW 页面进入，不声称已嵌入 AgentCore 自带对话界面。

本地直接运行需要 `uv sync --project backend --extra hosting --extra aliyun --extra adk --extra litellm`，
使用同一 Python 执行 `plugins/matcreator/oaw_matcreator/native_environment.py` 准备内化引擎依赖，
然后构建 `frontend`，设置上述环境变量，运行：

```sh
backend/.venv/bin/uvicorn backend.cloud.aliyun.app:create_app --factory \
  --host 0.0.0.0 --port 8080 --workers 1 --no-proxy-headers
```

## 执行与恢复边界

- 每 profile 目前只允许一个 Sandbox Card。卡片配置 `command_timeout: 60`，保持
  `network_enabled: false`，不绑定宿主工作目录；不支持共享 Python 安装或可写资源挂载。
- `/workspace` 挂载用户的专属持久卷。命令、文件浏览、预览、上传下载使用原有 API；
  文件操作复用 OAW 的路径校验和 no-follow 实现，经远端标准库 helper 执行，不依赖共享宿主路径。
- 一次只允许一条远端操作。每条用户命令完成、取消或超时后回收执行 VM，包括其后台
  子进程；后续命令创建新 VM 并重挂同一卷。**不保留后台 shell 进程和内存状态**。
  文件读取也可能按需创建实例，持续使用期间仍会计费。闲置实例由平台 TTL 回收。
- 网络默认关闭；VPC/Volume 授权配置仍需运营方限制到该用户存储。没有承诺可运行任意
  联网工具或多日云 Sandbox 命令。多日外部计算继续使用既有持久 Jobs 机制，不能重投。
- `aliyun-sandbox.json` 保存远端 ID、创建 request key、执行回执，不保存 API Key。
  创建响应丢失时按 metadata 对账；找不到唯一实例就停止，绝不自动再创建。
  执行结果不确定时保留记录并拒绝再次执行。运营方先在云控制台核对，再显式停止。
  不要通过删除绑定文件来“修复”；那会丢失对远端工作的追踪。
- 登录重连不会自行提交命令；退出登录不会终止计算。正常关闭 OAW 沿用现有任务清理，
  本适配尚未提供跨服务重启继续观察同一条 Sandbox 命令的完整恢复协议。

## 当前基础集成检查（不代表自助测试 POC 验收）

1. 先运行测试：OIDC 签名、issuer/audience/state/nonce/过期验证；两用户 API/WS 拒绝；
   创建超时不重试、创建回执丢失对账、取消后清理、核心文件协议兼容。
2. 用户 A 登录 A 实例正常，B 登录 A 实例必须被拒绝；A/B 使用不同数据库和 Volume。
3. 各建 Sandbox Card，执行 `printf 'hello from cloud\n'`，上传并读取文件；重新连接、
   重建执行 VM 后文件仍在。用云控制台核对实例实际隔离和 Volume 归属。
4. 重建 OAW 容器，确认会话、SQLite 卡片、MatCreator 知识数据和密钥仍在。
5. 验证上传、流式输出、WebSocket、代理超时与注销；检查浏览器及命令环境中无云管理密钥。
6. 用内化 MatCreator 完成真实模型、远端命令、文件和会话恢复测试，再声明相应科学工作流可用。

## 本地验证记录

真实云接入进展（2026-10-15）：已完成 RAM OAuth 登录及身份校验、CLI OAuth 管理授权，
并使用阿里真实 Sandbox 的公共 `base` 模板完成命令执行和临时文件读写。
两次测试实例均已销毁，最终实例列表为空；部署者管理凭据未注入测试命令环境。
这些检查不包含 Volume、OAW 完整执行适配、AgentCore 托管或真实模型科学工作流。
2026-10-16 已实际创建 AgenticFS、AgenticSpace、Access Point、专用 VPC/交换机/安全组、
受访问点约束的 FC RAM 角色和 Sandbox Volume。Space 为平台最小 10 GiB 容量配额，
访问点 active、Volume AVAILABLE，但真实沙箱创建仍在 NFS 挂载阶段返回 HTTP 400。
两种公共模板、受限/默认出网及限定访问点的 RootAccess 对照均未解决；诊断权限已恢复，
最终沙箱列表为空。持久读写与重建恢复尚未通过，详见单人验证记录。
适合数据库的 profile 持久存储，以及 ACR 镜像仓库仍待解决，
详见[单人云端验证记录及部署条件](SINGLE_USER.md#真实云验证记录2026-10-15)。

单人托管适配更新（2026-10-15）：本轮 55 项相关测试通过，包含使用真实内化引擎和
受控模型端点验证的计划、递归执行、工具失败恢复、会话检查点，以及云端 worker 环境
过滤、托管命令路由、Legion 创建和 OIDC 登录探针。个人版同组回归通过。
这组本地测试中的阿里 Sandbox 使用测试 SDK 传输，不包含真实阿里账号联调或外部模型验收。
`oaw-aliyun-poc:single-user` 的 Linux 镜像已构建通过，包含前端及内化引擎依赖。
禁用外网、使用一次性 profile 的容器检查通过：UID 10001、引擎依赖可读、服务健康、
前端加载、匿名及错误所有者拦截，以及已签名测试会话下的 Legion 创建。
该检查使用测试身份，不代表真实 RAM 登录或 AgentCore 部署验收。
另外，在同一镜像中禁用外网运行 `test_aliyun_matcreator.py`，4 项全部通过；
包含真实内化引擎配合本地受控模型端点的规划、递归执行及工具失败恢复。

本地验证记录（2026-10-15）：相关测试合计 127 通过、1 项选择性原生 Windows
部署验收跳过；覆盖通用身份边界、OIDC、个人版访问与 Sandbox 管理、原有发布流程、
MatCreator 原生/工作区回归及上传协议。阿里适配部分使用测试 SDK 传输，OIDC 使用
模拟身份端点和真实签名令牌，文件 helper 使用独立 Python 进程验证协议。
`npm run build` 通过。这组本地测试不包含真实阿里隔离/存储、实际 IdP 登录、
AgentCore 公网路由及网关配置，也未进行桌面安装包或日常 GUI 验收；
后续真实服务检查的范围见本节开头。

## 官方依据（实现时核对）

- [AgentCore 高代码部署](https://help.aliyun.com/zh/agentcore/high-code-agent-deployment-guide)
- [Agent Identity 用户池](https://help.aliyun.com/zh/agentidentity/user-pool-management)
- [OIDC 客户端](https://help.aliyun.com/zh/agentidentity/client-management)
- [Agent Sandbox SDK 与锁定版本](https://help.aliyun.com/zh/agent-sandbox/using-the-cloud-sandbox-via-the-sdk)
- [创建与连接生命周期](https://help.aliyun.com/zh/agent-sandbox/lifecycle)
- [AgenticFS Volume](https://help.aliyun.com/zh/agent-sandbox/create-an-agenticfs-volume)
