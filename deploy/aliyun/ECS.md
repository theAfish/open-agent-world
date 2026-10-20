# ECS 多用户画布测试

本方案使用 ECS 承载 OAW HTTPS 网关及每用户独立后台，复用 `backend.hosting`
的账号、鉴权、systemd 隔离和持久目录。Agent 命令只允许交给阿里 Agent Sandbox；
不需要 ACR 或 AgentCore 托管 OAW 镜像。个人版入口和默认 Sandbox 保持不变。

## 运行结构

- Nginx 提供 HTTPS 和 WebSocket 转发，网关只监听 `127.0.0.1:8080`。
- 每个工作区对应独立 Linux UID、Unix socket、OAW profile 和 SQLite 数据库；账号可保留多个版本，选择一个登录入口。
- 账号由管理员预先创建，用户通过同一个地址登录；暂不开放自行注册。
- admin 在工程画布搭建 Legion、编排 Workspace 并发布，再从 `/admin` 的“搭建与部署”
  选择版本、账号以及 `builder` 画布或 `runtime` Workspace 界面。部署生成全新的私有工作区，
  就绪后显式设为账号入口；旧画布、对话、文件和后台任务保留，可切回。
- 账户库和用户数据位于 ECS 本地持久磁盘。备份必须包含完整
  `/srv/oaw-hosting/control` 和 `/srv/oaw-hosting/instances`，并保持权限及凭据一致。
- 工作文件也保存在各自 ECS profile 的 `ecs-workspace` 目录。每次执行前恢复到该用户的
  阿里临时 Sandbox，执行结束后保存到 ECS，再释放 Sandbox。后续执行会使用新的 VM。
  账号库、OAW 数据库、登录凭据及云管理密钥不会传入 Sandbox。
- ECS 文件模式在首次浏览文件时准备本地私有目录，无需启动或创建云端计算实例；
  计算仍由阿里 Sandbox 执行。个人版及其他存储后端保留原有启动规则。

## 阿里执行适配

云端直接加载独立 MatCreator 卡包，使用卡包自带的 `MatCreator research` 完整研究布局：
会话、Agent Graph、Jobs、Know-Do Graph、结构查看器、成果文件与 Sandbox 文件/预览。
发布使用 dev 的通用卡片接口和权限声明，无需另写阿里专用页面。
早期 POC 的 `MatCreator hosted workspace` 专用预设已退出新建目录；
已创建的 Legion 和发布版本持有自己的布局与配置，升级不会覆盖这些实例。
需要完整页面时，在管理员工程画布创建新预设实例（或手动调整已有布局），检查兼容性并发布新版本，
再通过 `/admin` 为使用账号创建独立工作区。就绪后显式设为入口，原工作区仍可切回。
终端命令仍需发布时单独授权；成果文件只开放列表、预览与下载，工程配置和管理接口不对使用用户公开。

子进程环境隔离由通用 `AgentCapabilityProvider.subprocess_environment()` 提供，
阿里层不再继承 MatCreator 内部类或替换卡包的预设。所有新建云端 Sandbox 使用统一的
60 秒默认命令超时；显式保存的配置仍保留，执行时继续校验阿里 VM 生命周期上限。

管理员在 `/srv/oaw-hosting/worker-common.env` 统一配置一次（root 持有、0600）：

```ini
OAW_HOSTED_SERVICES_FACTORY=backend.cloud.aliyun.hosted:create_hosted_services
OAW_ALIYUN_REGION=cn-hangzhou
OAW_ALIYUN_SANDBOX_API_KEY=<管理员配置，不能提交到仓库>
OAW_ALIYUN_SANDBOX_TEMPLATE=code-interpreter-v1
OAW_ALIYUN_WORKSPACE_STORE=ecs
OAW_ALIYUN_SANDBOX_LIFETIME=600
OAW_ALIYUN_SANDBOX_READY=0
OAW_ALIYUN_SANDBOX_PYTHON_REQUIREMENTS="[\"numpy>=2.1,<3\",\"scipy>=1.14,<2\",\"ase>=3.23,<4\",\"pymatgen>=2025.1\",\"matplotlib>=3.9,<4\"]"
```

工厂校验 profile 身份，只加载核心、MatCreator、结构查看器和 SQLite 这几组批准的代码。
profile ID 取自已验证的 worker 身份；新老用户共用基础配置，各自的存储、绑定和 VM 仍独立。
无需为新增用户复制 API Key、模板或依赖清单。旧部署若保留 `OAW_ALIYUN_PROFILE_ID`，仍会检查其是否匹配，
但不应把此变量放进公共文件。更新公共环境后在空闲时滚动重启 worker；这是部署操作，用户无需配置。
新生成的 systemd 单元自动读取公共文件；升级旧部署时需更新其单元。公共配置优先于实例环境文件中的同名项。
它不注册本机执行后端。`ecs` 模式不需要 Volume、OSS 或 AgenticFS；管理员启用前必须验证
该部署的文件同步和用户隔离。远程命令以普通用户运行，文件传输和 Python 安装助手在 VM 内以 root 运行。

`OAW_ALIYUN_SANDBOX_PYTHON_REQUIREMENTS` 的值是 JSON 数组；上面展示的是 systemd 环境文件的转义形式。
未配置时不默认安装科学计算包。配置后，每台新 VM 在执行前通过[阿里 PyPI 公网镜像](https://developer.aliyun.com/mirror/pypi)
的 HTTPS 地址准备 `/opt/oaw-python` 环境，复用所选阿里模板自带的库，只补齐缺少的包，
`python3`、`python` 和 `pip` 使用该环境。`install_python_packages` 也能安装索引上的 wheel 包；
安装请求与部署依赖一起保存为该用户的配方，下一台 VM 自动重建。依赖文件不进入 32 MiB 工作文件快照。
仅支持二进制 wheel，不运行源码构建；不支持包 URL、私有索引凭据或安装系统软件。
冷启动准备最多等待 240 秒，Sandbox 生命周期至少为 300 秒；网络关闭时无法准备新环境。
安装结果未知时保留 PID 和远程 ID，先核对并停止对应 VM，再显式重试；不能自动重复提交。
`base` 是裸 Python 模板；当前科学计算测试使用 `code-interpreter-v1`，减少基础库下载。
已有绑定更换模板时，须先确认空闲并停止 VM、备份环境文件和绑定，然后同步修改配置中的模板名；
不能只改环境变量，也不能删除整个 profile 来绕过绑定校验。工作文件和数据库保持原样。

默认 `READY=0`：前端会显示云 Sandbox 不可用，启动或重新连接 VM 均被拒绝。
只有验证该部署的独立存储、写入、释放、重建、读取及跨用户访问拒绝后，
才能设置 `READY=1` 并重启后台。ECS 模式按每个 profile 自动创建独立目录；更改这个标志本身不会完成验证。
本次部署验证通过后已统一设置为 `1`。Volume 模式仍需逐用户开通独立卷，不能以一个公共卷取代用户隔离。

将来使用阿里持久卷时，明确设置 `OAW_ALIYUN_WORKSPACE_STORE=volume` 和
`OAW_ALIYUN_WORKSPACE_VOLUME=oaw-<该 profile UUID>`。不要让多个 profile 使用同一个卷。
已绑定的 Sandbox 不允许直接切换存储类型：先停止执行、导出文件并确认备份，显式迁移后再重建绑定；
修改环境变量本身不会搬运数据。

### POC 边界

- 每个 profile 一个逻辑 Sandbox，命令串行执行；VM 生命周期默认为 600 秒。
- 每次文件同步最多 **32 MiB、2048 个文件及目录**，只支持普通文件和目录；拒绝符号链接、
  硬链接、特殊文件及不安全路径。适合小型验证，不适合大规模材料计算数据。
- ECS 按代保存云端快照，完整校验成功后原子切换指针，并保留前一代。文件 API 修改当前工作副本；
  尚未成功收集的 VM 文件不属于已保存的 ECS 数据。
- 传输或命令结果不确定时保留远程 ID 和操作记录，阻止后续执行。先核实远程状态、收集文件并停止，
  不得因超时重新提交任务。VM 到期或 ECS 崩溃可能使未回传的工作丢失。
- 云端 Legion 默认开启公网出网，保留入站鉴权，拒绝访问私网及云元数据地址；仍可在卡片上关闭网络。
  有 Execute + Start/Stop 权限的 Agent 调用执行工具时自动启动沙盒，无需单独调用 `start_sandbox`。
  空闲/任务完成后释放执行 VM，后续命令再自动创建，不持续占用 VM。
- Python 依赖在阿里 VM 内准备和运行，ECS 只保留配方和验证记录。只有 `/workspace` 内普通文件
  持久保存；HOME、临时目录及手工安装在其他路径的依赖随 VM 释放。
  每条命令后的 VM 释放意味着下一条命令仍有冷启动安装开销，当前方案用于小型 POC。
  仍不支持任意主机目录、私有 Pack 运行时挂载或长期后台进程。
- MatCreator 服务端代码和原生依赖已安装；科学计算环境的基础验证不代表完整科研流程已验证。
  各项科研工具、私有 Pack 环境及耗时任务需要分别验收。

2026-10-20 实测：旧的 `oaw-poc-workspace` 在 `base` 模板仍返回
`Mount NFS:// command failed`；查询确认本次请求无残留 Sandbox。本次部署选择 ECS 文件快照，
因此账号、画布、数据库以及基础云命令测试不再依赖该挂载问题解决。

2026-10-20 部署验证：三个账号可以从同一个 HTTPS 入口登录，数据库记录在服务重启后保留；
两个账号通过正式 Sandbox API 执行真实云命令，独立文件回传 ECS，跨账号访问拒绝。
服务再次重启后，两个账号均恢复已保存的文件，并在新 VM 中继续处理成功。
独立云验证还确认两台不同 VM 能连续恢复同一工作目录，执行后均已释放。
三个测试账号均初始化了 MatCreator Legion，浏览器实际加载 51 张卡片并完成同步。
基础登录/退出浏览器验证通过，阿里适配的 35 项本地回归测试通过。
当前仍为画布模式，没有切换成仅 MatCreator 的入口。

2026-10-20 联网验证：三个现有 Legion 的 Sandbox 均已开启网络。通过正式执行 API，
从停止状态自动启动真实阿里 VM，普通用户进程访问阿里官网和 PyPI 均返回 HTTP 200；
云元数据请求未成功。命令结束后 VM 释放，第二条命令自动创建新 VM 并读回之前的工作文件。
验证文件已清理。这里的自动启动使用已有 Execute + Start/Stop 授权，不绕过启动权限；
错误/未知执行状态仍须核对，不能自动重放。

2026-10-20 科学环境验证：`test3` 的正式 `python/packages` API 安装成功；
随后以 UID 1000 执行 NumPy/SciPy 线性方程求解、ASE 分子距离、pymatgen 晶体构建和 Matplotlib PNG 出图。
本次版本为 NumPy 2.5.3、SciPy 1.18.1、ASE 3.29.0、pymatgen 2026.9.24、Matplotlib 3.11.2。
第一台 VM 释放后，下一条命令自动在新 VM 准备同一依赖配方，并导入五个库、读回上一台生成的图片；
验证文件已删除。首次准备约 124 秒，重建后约 114 秒；这是实测耗时，不是时延保证。
SDK 会通过登录 shell 执行命令，适配器在其启动文件之后设置托管 Python 路径；
调用方若再次显式打开登录 shell，应使用 `/opt/oaw-python/bin/python`。
本次验证没有覆盖所有 MatCreator Skill、私有 Pack runtime 或完整长时科研任务。

2026-10-20 统一配置验证：三个 worker 已改为读取同一个 `worker-common.env`，逐账号的重复云配置已移除。
`test2` 在没有个人云配置、没有既有云绑定的情况下，直接执行命令就完成首次 VM 创建、自动安装、
五库导入和 HTTPS 请求（HTTP 200），耗时约 96 秒；命令完成后 VM 已释放。
各账号原有 51 张卡片及 8 条连线保留，未覆盖用户配置或文件。新增 profile 自动继承公共环境文件、
并从可信身份派生独立云绑定的路径另有本地测试覆盖；新增账号仍需管理员按通用部署流程建号。

## 运维

通用安装、创建账号和发布模板见 [多用户托管说明](../../docs/contributing/hosting.zh-CN.md)。
当前目录为 `/opt/open-agent-world` 和 `/srv/oaw-hosting`，服务组为 `oaw-hosted.target`。

```sh
systemctl status oaw-hosted-gateway.service
systemctl list-units 'oaw-hosted-worker-*'
systemctl restart oaw-hosted.target
/opt/open-agent-world/backend/.venv/bin/python /opt/open-agent-world/scripts/hosted.py \
  user-list --directory /srv/oaw-hosting
```

账号/路由变更遵循通用说明：停止 target，使用 CLI 修改，重新生成并安装 units，再启动。
本次测试部署已启用公共模型：管理员通过 `/admin` 统一配置模型及默认值，`test1`、`test2`、`test3`
直接继承。管理员拥有独立工程工作区，可搭建和发布应用；测试账号和各自的画布数据保持原样。本机个人模型凭据不会随应用复制。
管理员配置说明见上面的通用托管文档。其他服务仍需分别接入，完整科研流程仍需要后续验收。

2026-10-20 公共模型实测：通过真实管理页面保存临时模型，三个测试账号均读取到同一默认模型；
管理端再次修改后，已打开的普通用户设置页自动刷新。普通账号的管理接口、模型写入、旧版密钥写入
及冒用内部工作进程接口均返回 403，页面不返回模型密钥。三个工作区各 51 张卡片保持不变。
临时模型在验证后清除，等待管理员填写真实服务配置；本项验证未调用真实模型服务。

公网 IP 使用短期 HTTPS 证书时，必须持续自动续期并在成功后 reload Nginx。
本次使用 `/opt/oaw-tools/bin/certbot` 和 `oaw-certbot-renew.timer` 每两小时检查，
保留 HTTP 的 `/.well-known/acme-challenge/` 路径供续期，其余 HTTP 请求跳转 HTTPS。
只公开 80/443，业务 worker 不监听公网端口。
