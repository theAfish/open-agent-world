# 单人云端验证：不依赖 CIAM

单人使用可以先不引入 CIAM，也不实现自助注册。保留通用 OIDC 身份接口，后续切换
身份服务时继续使用。已选择继续验证 **AgentCore 托管 + 阿里 Agent Sandbox**，
不切换为普通 ECS 个人版。当前尚未完成真实云部署或 MatCreator 云端验收。

## 登录选择

优先复用现有 RAM 用户，通过阿里云 OIDC 登录 OAW。RAM 不是面向公众的注册系统，
但可以作为你本人登录测试应用的身份来源，不需要新建 CIAM 实例。
应用仅请求 `openid profile`，验证身份并限定到你的 `issuer + sub`；
登录凭证不等于允许 Agent 操作你的云资源。

官方依据：[通过 OIDC 获取用户信息](https://help.aliyun.com/zh/ram/obtain-user-information-through-oidc)。
另外，IDaaS EIAM 文档也提供[免费创建实例](https://help.aliyun.com/zh/idaas/eiam/user-guide/eiam-sso-quick-start)
的方式，可作为内部账号管理的备选；其具体功能限制按当前实例和计费规则确认，不能当作
CIAM 自助注册的等价替代。

### 运营方配置 RAM 登录

1. 由具有相应权限的管理员在 RAM 控制台的“集成管理 > OAuth 应用”中创建企业 Web 应用。
   **当前 OAW 客户端要求客户端密钥，因此选择 OAuth 2.0。**
   阿里 OAuth 2.1 应用不支持固定密钥验证，不能直接套用当前配置。
2. 仅添加 `openid`、`profile` 范围。按实际入口登记完整回调
   `<OAW_PUBLIC_ORIGIN>/auth/callback`，创建并保存应用密钥。
3. 配置 `OAW_OIDC_ISSUER=https://oauth.aliyun.com`、
   `OAW_OIDC_DISCOVERY_URL=https://oauth.aliyun.com/.well-known/openid-configuration`、
   `OAW_OIDC_SCOPES=openid profile`，以及该应用的客户端 ID 和密钥。
   客户端 ID 取应用基本信息中的 **应用 ID（AppId）**；客户端密钥取 **AppSecretValue**，
   不要将密钥列表中的 **AppSecretId** 填为客户端 ID。
   令牌端点的客户端认证方式须按实际应用联调确认；现有实现支持 basic 和 post。
4. 经过真实登录和令牌校验后，将该用户真实 `sub` 绑定为工作区所有者。
   `sub` 不是 RAM 用户名、邮箱或猜测的 UID；不得直接信任浏览器提供的用户标识。

参见[创建应用](https://help.aliyun.com/zh/ram/create-an-application)及
[创建应用密钥](https://help.aliyun.com/zh/ram/create-an-application-secret)。
密码始终输入阿里登录页；应用密钥保存在仓库外的服务端配置文件中。
这些步骤准备的是身份接入，不会自动部署应用。

### 先在本机验证登录并获取真实 sub

提供了 `backend.cloud.aliyun.identity_probe`，只验证 OIDC 登录并显示已验证的
`issuer`、`subject`，不会打开个人 profile、绑定假所有者或创建任何云资源。
RAM 登录可直接使用仓库外的私密文件 `C:/private/aliyun-oauth.env`：

```dotenv
ALIYUN_OAUTH_CLIENT_ID=<应用基本信息中的 AppId>
ALIYUN_OAUTH_CLIENT_SECRET=<对应应用密钥的 AppSecretValue>
ALIYUN_OAUTH_REDIRECT_URI=http://localhost:8080/auth/callback
```

在 RAM 应用中登记完全相同的回调地址。文件按 UTF-8 读取（也支持 BOM），不进行环境变量插值，
因此密钥中的 `${...}` 会保留原样。探针每次启动生成临时会话密钥，不修改这个私密文件。
安装 hosting 依赖后从仓库根目录运行（Windows Python 路径示例）：

```powershell
backend/.venv/Scripts/python.exe -m backend.cloud.aliyun.identity_probe --env-file C:/private/aliyun-oauth.env --identity-output C:/private/aliyun-verified-identity.json
```

探针仅监听 `127.0.0.1`，关闭访问日志，避免把回调授权码写入日志。浏览器打开
`http://localhost:8080`，完成阿里登录后显示经过校验的身份。可选的 `--identity-output` 文件
仅保存 `issuer` 和 `subject`，不保存令牌或 Cookie；该文件可能包含上次的结果，必须确认本次
登录成功后再使用。将真实 `subject` 用作正式部署的 `OAW_OWNER_SUBJECT`。
测试结束关闭探针；之后本机网关可复用相同端口和回调。
不要在未验证签名的情况下从任意 ID Token 文本中提取并信任身份。

如果授权页返回 `invalid_client` / `App not exists`，先核对应用 ID 是否误用了 AppSecretId。
阿里[官方配置说明](https://help.aliyun.com/zh/pds/drive-and-photo-service-dev/user-guide/enable-logon-with-ram-oauth)
也特别指出这个区别。ID 的格式只是排查线索，不能仅凭格式判断应用有效性。
修改环境文件后必须重启探针，并重新打开本机首页发起登录；旧的阿里授权链接仍携带旧 ID。

已有通用 `OAW_OIDC_*` 配置时仍可使用 `identity_probe:create_app --factory`，需要同时提供
`OAW_PUBLIC_ORIGIN=http://localhost:8080`、`OAW_ALLOW_LOCAL_HTTP=1` 和至少 32 字符的
`OAW_SESSION_SECRET`；启动 uvicorn 时使用 `--host 127.0.0.1 --port 8080 --no-access-log`。
正式云服务使用 `OAW_OIDC_*` 配置和持久会话密钥，不使用探针的临时会话密钥。

## 已选择的部署路径

AgentCore 托管 OAW 前后端与受信任的编排逻辑，阿里 Agent Sandbox 执行生成命令。
无需另外购买 ECS 来运行管理节点；但仍需确认目标 AgentCore 支持完整网页路由、
WebSocket 及适合 OAW 数据库的持久存储，才能完成这一部署。

当前云入口 `backend.cloud.aliyun.app:create_app` 允许内化 MatCreator 作为受信任编排代码运行，
使用环境变量允许列表和现有授权工具桥；生成命令只进入托管 Sandbox。
这不是把整个 Agent 搬进根 Sandbox，仍不能仅凭本地协议测试宣称云上 Legion 已验收。
个人版保留现有入口、安装流程与本地 Sandbox，云端入口需要显式选择。

没有域名时，单人测试可在本机运行固定上游网关，浏览器访问 localhost；
网关保存 AgentCore Endpoint 的服务认证凭据，转发到托管后端，不在本机执行 Agent。
现有 `nginx.conf.template` 可作为配置起点，容器端口只发布到 `127.0.0.1:8080`；
只替换 `OAW_*`，保留 nginx 的请求变量。该网关仍需对真实 Endpoint 验证。
若已有私有网关服务器，也可以使用回环监听 + SSH 隧道。
RAM 官方[接入示例](https://help.aliyun.com/zh/agentidentity/alibaba-cloud)包含 localhost 回调。
实际回调需与 OAW 路径完全一致。使用这种私有访问方式时不开放公网 Web 端口；
不要将“公网 IP”视为备案要求的替代方案。

## 杭州资源准备与本机 CLI

地域统一使用 `cn-hangzhou`。不安装 CLI 也能先在控制台准备：

1. 在 [AgentCore 控制台](https://agentcore.console.aliyun.com)左上角查看已有 Workspace。
   开通时通常已创建一个，可直接使用；确实没有时再创建 `oaw-poc`。
   VPC 绑定按部署需要选择，开启时要求至少两个不同可用区的交换机。
   参见[管理 Workspace](https://help.aliyun.com/zh/agentcore/manage-workspace)。
2. 在 Agent Sandbox 控制台选择杭州、极速版，通过 Team 选择器查看或创建用于本次 POC 的 Team。
   选择已授权的资源组，核对所选 QoS/订阅的能力与费用。记录 Team ID；它与 AgentCore 的 Team
   是不同资源。参见[创建 Team](https://help.aliyun.com/zh/agent-sandbox/create-team)。
3. 在极速版的 API Keys 中选择该资源组和 Team，创建测试 Key，将完整值保存在仓库外，
   用于 `OAW_ALIYUN_SANDBOX_API_KEY`。需要的是以 `e2b_` 开头的完整 `apiKeyValue`，
   不是 UUID 格式的 `apiKeyID`，也不是列表里的掩码。
   参见[API Key 管理](https://help.aliyun.com/zh/agent-sandbox/user-guide/api-key-management)。

这些步骤不会准备好运行所需的全部资源。仍需在同一 Team 中配置兼容模板和工作卷，另行验证
OAW profile 的数据库持久存储，再配置 AgentCore 与镜像仓库。

要从本机管理资源，优先使用阿里 CLI 的浏览器 OAuth 登录；这是部署者的管理凭据，
与 OAW 用户登录的 Web OAuth 应用分开配置：

1. 按[官方安装说明](https://help.aliyun.com/zh/cli/install-update-alibaba-cloud-cli)
   安装当前受支持的 Windows AMD64 版 CLI，将 `aliyun.exe` 所在目录加入 PATH，
   新开 PowerShell 执行 `aliyun version` 确认。OAuth 模式要求 CLI 至少 3.3.0。
2. 由管理员在 RAM → 集成管理 → OAuth 应用 → 第三方应用中安装官方 CLI（`official-cli`）；
   在“允许访问的身份”中添加你的 RAM 用户或角色。已有应用和授权时直接复用。
3. 执行以下交互式命令，选择中国站，在浏览器完成授权，并将默认地域填写为 `cn-hangzhou`：

   ```powershell
   aliyun configure --mode OAuth --profile oaw-poc
   aliyun sts get-caller-identity --profile oaw-poc
   ```

最后一条仅验证身份，不会创建资源；资源操作还取决于 RAM 权限。CLI 会保存并续期临时凭据，
Windows 默认配置位于 `%USERPROFILE%/.aliyun/config.json`，只需提供配置路径和 profile 名称，
不要粘贴文件内容或令牌。`configure set` 不能代替浏览器授权。
参见[CLI OAuth 凭证](https://help.aliyun.com/zh/cli/oauth-credentials)及
[凭证管理](https://help.aliyun.com/zh/cli/configure-credentials)。

## 下一步所需信息

- 已选择 AgentCore 托管；需要已开通的 AgentCore Workspace、Sandbox 版本/地域及
  持久存储权限信息。AgentCore profile 存储与 Sandbox 工作卷是两份独立资源，
  不能直接把 SQLite WAL 放到未经验证的网络文件系统。
- 要验证阿里登录，需要管理员创建上述 RAM OAuth 应用，提供客户端 ID 和私密配置文件路径。
- 镜像上传还需要 AgentCore 支持的 ACR 仓库；按高代码部署文档核对仓库类型与拉取权限。
  镜像包含项目源代码，确认可见范围后再上传。

未来恢复“别人自行注册”测试时，再选择用户注册服务及自动工作区开通方案，
参考 [多用户内部测试准备](INTERNAL_TEST.md)。
