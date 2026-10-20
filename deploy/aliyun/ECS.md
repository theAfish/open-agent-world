# ECS 多用户画布测试

本方案使用 ECS 承载 OAW HTTPS 网关及每用户独立后台，复用 `backend.hosting`
的账号、鉴权、systemd 隔离和持久目录。Agent 命令只允许交给阿里 Agent Sandbox；
不需要 ACR 或 AgentCore 托管 OAW 镜像。个人版入口和默认 Sandbox 保持不变。

## 运行结构

- Nginx 提供 HTTPS 和 WebSocket 转发，网关只监听 `127.0.0.1:8080`。
- 每个账号对应一个独立 Linux UID、Unix socket、OAW profile 和 SQLite 数据库。
- 账号由管理员预先创建，用户通过同一个地址登录；暂不开放自行注册。
- 当前使用 `builder` 画布模式。后续验证通过后，按通用托管文档发布 Legion、导出模板，
  为用户显式创建 `runtime` 实例；切换模式不得覆盖已有用户数据。
- 账户库和用户数据位于 ECS 本地持久磁盘。备份必须包含完整
  `/srv/oaw-hosting/control` 和 `/srv/oaw-hosting/instances`，并保持权限及凭据一致。
- 工作文件也保存在各自 ECS profile 的 `ecs-workspace` 目录。每次执行前恢复到该用户的
  阿里临时 Sandbox，执行结束后保存到 ECS，再释放 Sandbox。后续执行会使用新的 VM。
  账号库、OAW 数据库、登录凭据及云管理密钥不会传入 Sandbox。

## 阿里执行适配

每个实例的管理员私有 `worker.env` 配置：

```ini
OAW_HOSTED_SERVICES_FACTORY=backend.cloud.aliyun.hosted:create_hosted_services
OAW_ALIYUN_PROFILE_ID=<对应的 profile UUID>
OAW_ALIYUN_REGION=cn-hangzhou
OAW_ALIYUN_SANDBOX_API_KEY=<管理员配置，不能提交到仓库>
OAW_ALIYUN_SANDBOX_TEMPLATE=base
OAW_ALIYUN_WORKSPACE_STORE=ecs
OAW_ALIYUN_SANDBOX_LIFETIME=600
OAW_ALIYUN_SANDBOX_READY=0
```

工厂校验 profile 身份，只加载核心、MatCreator、结构查看器和 SQLite 这几组批准的代码。
它不注册本机执行后端。`ecs` 模式不需要 Volume、OSS 或 AgenticFS；管理员启用前必须验证
该部署的文件同步和用户隔离。远程命令以普通用户运行，文件传输助手在 VM 内以 root 运行。

默认 `READY=0`：前端会显示云 Sandbox 不可用，启动或重新连接 VM 均被拒绝。
只有为**该用户**配置独立存储，并验证写入、释放、重建、读取及跨用户访问拒绝后，
才能设置 `READY=1` 并重启其后台。更改这个标志本身不会完成验证。
本次测试实例验证通过后已设置为 `1`。

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
- 当前云命令关闭公网访问，不支持任意主机目录、私有运行时挂载或长期后台进程。
- MatCreator 代码和原生依赖已安装；这不代表完整科研流程已验证。模型凭据、科学运行环境、
  联网工具及耗时任务需要后续单独适配和验证。

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
模型 Key 由各测试用户在画布设置里配置；本机个人模型凭据不会随应用部署复制。

公网 IP 使用短期 HTTPS 证书时，必须持续自动续期并在成功后 reload Nginx。
本次使用 `/opt/oaw-tools/bin/certbot` 和 `oaw-certbot-renew.timer` 每两小时检查，
保留 HTTP 的 `/.well-known/acme-challenge/` 路径供续期，其余 HTTP 请求跳转 HTTPS。
只公开 80/443，业务 worker 不监听公网端口。
