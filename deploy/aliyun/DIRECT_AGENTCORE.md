# AgentCore 直接托管与挂载验证

2026-10-16 起，下一轮云端验证优先采用 **AgentCore 高代码容器 + 直接挂载
AgenticFS**。不再以独立 Agent Sandbox Volume 挂载成功作为这条路径的前置条件。
已有 OAW 云入口仍使用独立 Sandbox 执行适配，尚未切换；个人版入口完全不变。
同日平台工程师反馈推荐 AgenticFS，并可通过 API 或控制台挂载；此前挂载问题仍在排查。
这一反馈支持继续采用直接挂载路径，但不能替代本账号实际读写和重建验收。

## 已完成与待完成

| 项目 | 当前结果 |
| --- | --- |
| 独立 AgentCore Workspace | 已创建 `oaw-poc`，状态 `Initialized` |
| 网络 | 已绑定专用 VPC，覆盖杭州 i、k 两个可用区 |
| AgenticFS | 复用已有文件系统、Space、RAM 认证 Access Point |
| AgentCore 挂载授权 | 实际查询为 `UNAUTHORIZED`，Workspace 角色尚未创建 |
| ACR | 杭州账号无实例，用户确认暂无可复用实例 |
| 探针镜像 | 本地 `linux/amd64` 构建及容器重建验证通过，未上传 |
| 云端高代码 Agent | 尚未创建，没有可访问的 OAW 云服务网址 |

Workspace：`ws-38d150ff974c4086adc24`。
VPC：`vpc-bp1vhx04x0l8rutsgfpga`。
交换机：`vsw-bp19ju0xdhet3bpg3wq1v`（k，172.28.1.0/24）、
`vsw-bp1kd1u12sm52bljbwv4b`（i，172.28.2.0/24）。
已有 `测试` Workspace 中存在一个托管 Agent，本轮没有更改它的网络或配置。
Workspace 返回的公网出口 IP 不是 OAW 的访问地址。

没有购买 ACR、ECS、NAT 或公网 IP，没有推送镜像，也没有创建持续运行的 Agent。
已保留的存储资源仍按各自计费规则计费；没有将它们删除。

## 直接路径的依据与边界

本机官方管理 SDK `alibabacloud-agentcore20260804==2.3.0` 的
`CreateCustomAgentRequestBody` 包含 `agenticFsMounts`，运行配置包含
`ISOLATED_HEADER_FIELD`。这证明存在相应配置接口，**不代表本账号已经完成挂载或
多用户隔离验收**。

- `server` 使用 NAS Access Point 的 DomainName，不包含协议、端口或 `:/`。
- `path` 的 SDK 说明要求访问点下已有的非空相对目录；不能填根目录、绝对路径或 `..`。
  目前还没有验证 `agentcore-probe` 子目录的创建和 UID 10001 写权限。
  独立 Sandbox 文档的自动建目录行为不能直接当作 AgentCore 已验证能力。
- `mountPath` 使用 `/mnt/agenticfs/probe`，`readOnly=false`。
- Header 隔离按 Session 调度运行环境，不会自动把同一 Access Point 划分为不同用户的
  安全存储边界。生产入口必须按验证过的用户身份生成/绑定 Session，不能信任浏览器自报身份。
- Workspace 的 `PLATFORM` 存储类型不等于为自定义容器提供了适合 SQLite WAL 的数据盘。
  文件跨重启保留也不证明数据库锁、WAL、并发或故障恢复正确，核心数据库暂不搬入此挂载。

完整 OAW 接入仍须解决可信编排、模型/OIDC 凭据与生成命令的 OS 隔离边界。
不能把原来的远端 Sandbox 调用直接改为 OAW Web 进程中的普通 `subprocess`，也不能仅凭
平台隔离不同 Session 就把应用管理凭据暴露给同一实例内的生成命令。

## 最小探针

代码位于 [agentcore_probe](agentcore_probe/)，只依赖 Python 标准库。
镜像只复制 `probe.py`，不包含 OAW、MatCreator、个人 profile、模型配置或云管理凭据。
这是部署验证工具，不是面向用户的 OAW 前端。

```sh
docker build --platform linux/amd64 -t oaw-agentcore-probe:mount-probe-20261016 deploy/aliyun/agentcore_probe
docker run --rm --network none --read-only --tmpfs /tmp \
  -v "$PWD/deploy/aliyun/agentcore_probe:/checks:ro" --entrypoint python \
  oaw-agentcore-probe:mount-probe-20261016 -m unittest discover -s /checks -p test_probe.py -v
python deploy/aliyun/agentcore_probe/verify_local.py
```

最后一条创建专用临时 Docker 卷及容器；使用 UID 10001、断网、只读根文件系统，验证后清理。
Windows PowerShell 的 Docker 绑定源路径请使用实际绝对路径。

接口：

| 请求 | 用途 |
| --- | --- |
| `GET /health/live` | 无需鉴权的进程存活检查 |
| `GET /health/ready` | 挂载点存在返回 200，缺失返回 503；不代表可写或远端持久性 |
| `GET /probe/status` | 返回本次启动 ID、UID、是否为挂载点 |
| `POST /probe/marker` | 排他创建本轮 UUID 对应的测试文件；已有文件只读取，不覆盖 |
| `GET /probe/marker` | 检查文件是否来自之前的进程启动 |

`/probe/*` 同时要求 `X-OAW-Probe-Token` 和 `X-OAW-Session`。
Probe Token 是独立随机测试密钥，不复用 RAM、OIDC 或 AgentCore Endpoint Key。
不接受 URL Token、请求体、用户指定文件路径或任意执行命令。
若挂载缺失、文件为符号链接/FIFO、内容损坏或写权限不足，探针返回失败。
请求日志不记录路径或凭据。没有实现 WebSocket、SSE 或 Chat Completions/AG-UI，不能用来宣称这些能力通过。

## 真正部署前的条件

1. 提供杭州企业版 ACR 实例、命名空间和仓库，并确认 AgentCore 对该仓库的拉取方式。
   [当前高代码文档](https://help.aliyun.com/zh/agentcore/high-code-agent-deployment-guide)
   要求企业版 ACR、实例公开匿名拉取和公开仓库；个人免费仓库不能直接替代接口要求的实例。
   优先向平台工程师申请可复用或试用仓库。本轮未购买包年包月实例、未批准公开完整业务镜像。
   这个独立探针可用于先评估公开拉取要求；完整 OAW 镜像的发布需另外核对内容和权限。
2. 在 `oaw-poc` 的 AgenticFS 挂载授权入口创建/授权平台要求的 Workspace 角色。
   官方授权查询指向 `AliyunAgentCoreWorkspace-ws-38d150ff974c4086adc24`，
   策略模板 `NasFileSystemClientMount`，目标限定为本次文件系统和访问点。
   授权链接已保存在本机私密配置目录，不写入仓库。
   旧的 `OawPocAgenticFSRole` 属于独立 Sandbox 路径，不应假定它自动满足 AgentCore 授权。
3. 通过平台支持的方式准备访问点下的 `agentcore-probe` 子目录，确认 UID 10001 可写。
   若 AgentCore 实际支持自动创建，应取得平台确认并实测，之后再更新本说明和配置校验。
4. 确认 AgentCore/ACR 可以通过该 Workspace 的 VPC 拉取镜像；准备阶段不开启默认公网出网。

复制 `agentcore_probe/config.example.json` 到仓库外私密目录，填入已验证资源。
`probe_token` 使用密码学随机生成的 URL-safe 字符串（至少 32 字符），`probe_run_id` 使用 UUID。
`mount_subdirectory_verified` 只能在目录和写权限实际确认后设为 `true`。

```sh
python deploy/aliyun/agentcore_probe/prepare.py /secure/probe-config.json /secure/create-agent-body.json
```

该命令只准备 `CreateCustomAgentRequestBody`，输出文件含探针密钥，不能提交或粘贴聊天。
不会调用云 API、覆盖已有文件或推送镜像。Windows 文件 ACL 应由私密目录继承保证。
配置采用单实例、单 Session、300 秒 Session 闲置超时、`INTERACTIVE` 规格、Header 隔离、
VPC 开启/默认公网关闭。**最小实例数为 1，Session TTL 不是免费或自动停止计费承诺。**

提交时先持久保存创建请求和 client token；创建结果不确定时查询对账，禁止盲目重复创建。
取得 Endpoint 后，运营方使用平台实际要求的 Header 和 Endpoint Key，再加探针自己的 Header 调用。
这些调用凭据不进入公开前端。

云验收顺序：首次健康检查 → 认证和未认证调用 → 写入标记 → 明确销毁/重建运行实例 →
读取同一标记并核对启动 ID 已变化 → 记录回执 → 删除本轮标记并停止/删除测试 Agent。
标记为 `.oaw-agentcore-probe-<probe_run_id>.json`；只清理这一文件，不删除整个访问点目录。
相同运行实例内第二次读取不算跨实例持久性验证。

## 本轮验证记录

2026-10-16：9 项 Linux 单元/HTTP 测试通过，覆盖鉴权、缺失挂载、文件排他创建、损坏文件
保留、符号链接/FIFO 拒绝和配置输入约束。官方 SDK 校验通过且保留全部输入字段
（SDK 另补默认空 `ossMounts`）。

真实本地 Docker 检查通过：两次独立容器共享专用卷，启动 ID 不同，原标记保留；重复写入
不覆盖；第三个未挂卷容器返回 503；未认证请求返回 401；全部测试容器和卷已清理。
镜像为 `oaw-agentcore-probe:mount-probe-20261016`，本地 image ID：
`sha256:7e3348cf86fbdde6e4603a4e94bae59052fcc9d68c702909ec66e1ef4520a700`。

上述不包含阿里真实挂载、Session 隔离、WebSocket、RAM 登录回调、OAW 页面、
内化 MatCreator 或科学工作流验收。下一步先解锁 ACR/挂载条件，再通过平台实际部署结果
决定完整 OAW 的执行和数据库适配。

参考：[Workspace 双可用区要求](https://help.aliyun.com/zh/agentcore/manage-workspace)、
[高代码部署](https://help.aliyun.com/zh/agentcore/high-code-agent-deployment-guide)、
[ACR 计费](https://help.aliyun.com/zh/acr/product-overview/billing-description)。
