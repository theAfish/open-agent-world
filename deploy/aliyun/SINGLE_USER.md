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
   令牌端点的客户端认证方式须按实际应用联调确认；现有实现支持 basic 和 post。
4. 经过真实登录和令牌校验后，将该用户真实 `sub` 绑定为工作区所有者。
   `sub` 不是 RAM 用户名、邮箱或猜测的 UID；不得直接信任浏览器提供的用户标识。

参见[创建应用](https://help.aliyun.com/zh/ram/create-an-application)及
[创建应用密钥](https://help.aliyun.com/zh/ram/create-an-application-secret)。
密码始终输入阿里登录页；应用密钥保存在仓库外的服务端配置文件中。
这些步骤准备的是身份接入，不会自动部署应用。

### 先在本机验证登录并获取真实 sub

提供了 `backend.cloud.aliyun.identity_probe:create_app`，只验证 OIDC 登录并显示已验证的
`issuer`、`subject`，不会打开个人 profile、绑定假所有者或创建任何云资源。
在仓库外的私密环境文件中配置上述 `OAW_OIDC_*`，以及：

```dotenv
OAW_PUBLIC_ORIGIN=http://localhost:8080
OAW_ALLOW_LOCAL_HTTP=1
OAW_SESSION_SECRET=<至少32字符的随机会话密钥>
```

安装 hosting 依赖后从仓库根目录运行（Windows Python 路径示例）：

```powershell
backend/.venv/Scripts/python.exe -m uvicorn backend.cloud.aliyun.identity_probe:create_app --factory --host 127.0.0.1 --port 8080 --env-file C:/private/oaw-identity.env
```

浏览器打开 `http://localhost:8080`，完成阿里登录后，将返回的 `subject` 用作正式部署的
`OAW_OWNER_SUBJECT`。测试结束关闭此探针；之后本机网关可复用相同端口和回调。
不要在未验证签名的情况下从任意 ID Token 文本中提取并信任身份。

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

## 下一步所需信息

- 已选择 AgentCore 托管；需要已开通的 AgentCore Workspace、Sandbox 版本/地域及
  持久存储权限信息。AgentCore profile 存储与 Sandbox 工作卷是两份独立资源，
  不能直接把 SQLite WAL 放到未经验证的网络文件系统。
- 要验证阿里登录，需要管理员创建上述 RAM OAuth 应用，提供客户端 ID 和私密配置文件路径。
- 镜像上传还需要 AgentCore 支持的 ACR 仓库；按高代码部署文档核对仓库类型与拉取权限。
  镜像包含项目源代码，确认可见范围后再上传。

未来恢复“别人自行注册”测试时，再选择用户注册服务及自动工作区开通方案，
参考 [多用户内部测试准备](INTERNAL_TEST.md)。
