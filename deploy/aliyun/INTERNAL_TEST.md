# 内部自助注册与 MatCreator Legion 测试

本轮验收要求是真实用户在阿里托管页面自行注册，然后从统一入口进入自己的 OAW，
运行仓库内化的 MatCreator Legion。管理员预建账号、每用户手工部署、只验证 Sandbox
命令或只展示 Legion 页面，都不满足这一要求。

当前仍处于部署准备阶段，不能按下面的身份配置直接得到完整多用户服务。
统一入口、自动工作区开通和 MatCreator 云端执行尚未完成。

## 身份服务选型

采用阿里 **IDaaS CIAM 用户身份服务**提供的注册/登录页面。RAM 子账号用于管理云资源，
不是终端用户注册系统；Agent Identity 用户池的手工建号/JIT 也不能替代自助注册验收。
CIAM 的[快速入门](https://help.aliyun.com/zh/idaas/ciam/getting-started/ciam-quick-start)
明确支持用户通过门户自行注册。

OAW 只消费经过验证的 OIDC 身份，以 `(issuer, subject)` 绑定工作区，不收集或存储
注册密码、短信验证码。后续如需 Agent Identity 的智能体权限能力，再核实和接入其联邦链路。
现有通用 OIDC 接口仍可用于其他符合要求的身份源。

## 需要运营方先完成的操作

1. 请阿里账号管理员确认当前 RAM 子账号可访问 IDaaS **CIAM**，以及是否有可用实例。
   CIAM 是单独的产品，开通 AgentCore 不表示已开通 CIAM；实例规格和费用由运营方确认。
2. 在 CIAM 实例中创建网站应用，开启用户自行注册，选定注册方式。
   按实例要求配置短信或邮件网关。官方[账密注册说明](https://help.aliyun.com/zh/idaas/ciam/user-guide/account-password-registration-login-1)
   要求验证手机号并配置短信网关；不要假定打开注册开关就能发送验证码。
3. 从应用配置获取应用 ID、客户端 ID、客户端密钥、实例登录域名及 OIDC 信息。
   应用 ID 与客户端 ID 按控制台字段分别记录，不能互相替代。
4. 告知 AgentCore Workspace 所在地域、Agent Sandbox 的版本和开通情况，以及 Volume
   权限状态。现有适配使用极速版；[Volume 文档](https://help.aliyun.com/zh/agent-sandbox/create-an-agenticfs-volume)
   当前说明其需要申请邀测。已有 AgentCore 权限不能证明已有 Sandbox 和存储权限。
5. 提供可用模型连接的类型和配置文件路径，供实际 MatCreator 工作流测试使用。

密钥保存在仓库外的配置文件中，只提供文件路径给部署人员，不贴到聊天、Git 或网页前端。
以上操作不要求测试用户购买云资源，也不要求他们安装 OAW。

## OIDC 配置对照

CIAM 的[授权信息文档](https://help.aliyun.com/zh/idaas/ciam/developer-reference/authorization-information)
提供服务发现、授权码模式和客户端认证说明。实际值以目标实例的元数据为准：

| OAW 配置 | CIAM 侧来源 |
| --- | --- |
| `OAW_OIDC_ISSUER` | OIDC 元数据中的 `issuer`，包含该应用的路径 |
| `OAW_OIDC_DISCOVERY_URL` | `https://<实例域名>/api/bff/v1.2/developer/ciam/oidc/<应用ID>/.well-known/openid-configuration` |
| `OAW_OIDC_CLIENT_ID` | 客户端 ID / appKey |
| `OAW_OIDC_CLIENT_SECRET` | 对应客户端密钥 |
| `OAW_OIDC_SCOPES` | `openid profile USER_API`，在应用侧允许这些范围 |
| `OAW_OIDC_TOKEN_AUTH_METHOD` | `client_secret_post` |
| `OAW_PUBLIC_ORIGIN` | 测试者实际访问 OAW 的固定入口，不含路径和查询参数 |

回调地址为 `<OAW_PUBLIC_ORIGIN>/auth/callback`，必须在 CIAM 应用中准确登记。
现有客户端保留 S256 PKCE、state、nonce 和签名校验；CIAM 实例对该完整流程的兼容性
必须实测，不能用关闭校验来代替联调。自助注册入口应由 CIAM 登录页提供，不能凭空
拼接一个 `/register` 地址。

## 未备案时的访问与服务器选择

AgentCore 可以托管自定义 HTTP 容器，因此新增 ECS 不是它的部署前提。
但尚未验证它能否完整提供本项目所需的网页资源、回调、Cookie、WebSocket 和持久化数据库。
先核对已有资源，再决定是否添加一台服务器承载统一入口及管理数据；服务器不会替代
阿里托管的用户执行隔离。若执行托管最终改用其他阿里服务，应明确记录，不能称为已完成
AgentCore 部署。

大陆 ECS 不能因为改成公网 IP 就视为免备案；阿里[排障文档](https://help.aliyun.com/zh/ecs/support/what-do-i-do-if-i-cannot-access-a-website-hosted-on-an-instance)
明确同时提到 IP 和域名访问。短期内部验证优先使用已有内网通道，或将仅监听回环地址
的入口通过 SSH 隧道转发到测试机：

```powershell
ssh -N -L 127.0.0.1:8080:127.0.0.1:8080 <已授权的SSH连接名>
```

这只是后续入口就绪后的访问方式，不会启动或部署 OAW。此模式可使用
`OAW_PUBLIC_ORIGIN=http://localhost:8080` 和 `OAW_ALLOW_LOCAL_HTTP=1`；
浏览器访问 `http://localhost:8080`，CIAM 回调登记为
`http://localhost:8080/auth/callback`。**目标 CIAM 实例是否允许该回调仍需控制台和实际登录验证**。
不要将允许本地 HTTP 的配置推广到公网 IP。若必须做到仅浏览器访问且无需隧道，需确定
允许使用的 HTTPS 测试入口，再登记对应回调。

## MatCreator 与完整验收

这里的 MatCreator 始终是仓库内的 `plugins/matcreator/oaw_matcreator_engine`。
`native_runtime.py` 启动 worker，并通过 OAW 工具接口执行任务；“原生”指这条内化引擎路径，
不代表另一个云产品。目前云入口在 `backend/cloud/aliyun/matcreator.py` 主动阻止执行，
在完成隔离和工具桥接前不能移除这道限制。

交付前必须在真实环境中完成：

1. 两位新用户通过同一个入口跳转到 CIAM，自行注册并返回 OAW。
2. 首次登录自动开通不同工作区；后续登录恢复自己的会话、数据库和文件。
3. 两人能运行内化 MatCreator Legion 的实际任务；验证模型调用、工具回传、结果与任务记录。
4. 跨用户访问 API、WebSocket、文件、任务均被拒绝，执行进程无法获得管理凭据和其他用户数据。
5. 重连、服务重启和资源创建结果不确定时保留绑定与任务标识，不重复提交科学计算任务。

目前本地的签名令牌和模拟身份服务测试只证明客户端配置及认证边界，不能证明 CIAM
注册成功、云端隔离成立或 MatCreator 科学工作流已可用。
