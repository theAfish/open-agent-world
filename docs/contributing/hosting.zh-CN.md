# 多用户托管：统一登录与独立工作区

[中文文档目录](../README.zh-CN.md) · [工作区发布与部署](../deployment.zh-CN.md)

托管模式支持管理员创建账号、账号密码登录、标准 OIDC，以及登录后直接进入画布或发布后的 Workspace 页面。每个用户对应一个独立后端实例、数据库和文件目录。个人桌面/源码启动仍然默认免登录；原有共享密码部署仍可使用。

首版面向一台 ECS 上几个用户。每个账号配置一个默认应用实例；不提供公开注册、跨用户共享工作区、自动扩容或自动升级发布版本。账号管理使用服务器 CLI，不向浏览器开放管理员 API。

## 准备

ECS 推荐 Ubuntu 24.04 LTS；需要 Linux、正在运行的 systemd 249+、Python 3.11+ 和源码安装所需的 Node/uv。当前托管方案不需要 Docker。个人桌面/源码启动方式不变。

将应用安装在管理员维护的 `/opt/open-agent-world`，托管数据放在独立的 `/srv/oaw-hosting`。不要把部署应用或 Python 放在 `/home`，服务的 `ProtectHome` 会屏蔽该目录。若使用 uv 管理 Python，应在安装前设置 `UV_PYTHON_INSTALL_DIR=/opt/oaw-python`；也可以使用系统 Python。设置 `UV_LINK_MODE=copy`，避免共享依赖缓存的硬链接权限相互影响。

先按[源码安装说明](../getting-started.zh-CN.md)完成宿主和插件准备，然后在应用目录执行以下一次性安装步骤。`--inexact` 保留已有插件依赖；`prepare-runtime` 为所有经过管理员审核的源码 Pack 准备共享运行环境，不接触个人工作区：

```bash
cd /opt/open-agent-world
umask 022
uv sync --project backend --locked --inexact --extra hosting --extra adk --extra litellm
npm --prefix frontend run build
backend/.venv/bin/python scripts/hosted.py prepare-runtime
```

最终应用、依赖、Python 解释器及其父目录必须归 root 所有，且组和其他用户不可写。若先使用运维账号完成安装，在确认以下路径是本次专用安装目录后设置：

```bash
sudo chown -R root:root /opt/open-agent-world
sudo chmod -R go-w /opt/open-agent-world
sudo install -d -m 700 -o root -g root /srv/oaw-hosting
```

若使用 `/opt/oaw-python`，也需为该专用解释器目录设置同样的所有权和写权限。安装器检查共享应用及解释器的权限，不会下载依赖或自动修改应用代码所有权。以下账号管理命令使用 `sudo`，服务本身使用独立的非 root 系统账号。

## 初始化账号与实例

```bash
sudo backend/.venv/bin/python scripts/hosted.py init \
  --directory /srv/oaw-hosting --origin https://oaw.example.com

sudo backend/.venv/bin/python scripts/hosted.py user-add \
  --directory /srv/oaw-hosting --user alice --display-name Alice

sudo backend/.venv/bin/python scripts/hosted.py profile-add \
  --directory /srv/oaw-hosting --user alice --mode builder --name 'Alice 的工作区'
```

`user-add` 在终端私密询问密码及确认，要求至少 12 个字符。不要把密码放进命令行、URL 或源码。账号也可以用 `--oidc-only` 创建，随后绑定外部身份。创建用户会分配稳定的内部 UUID。

### 登录后直接进入发布页面

先在工程工作区发布一个 Legion，停止工程后端，然后导出托管模板：

```bash
sudo backend/.venv/bin/python scripts/hosted.py template-export \
  --source /srv/oaw-engineering --release RELEASE_ID --output /srv/oaw-hosting/research-template.json

sudo backend/.venv/bin/python scripts/hosted.py user-add \
  --directory /srv/oaw-hosting --user bob

sudo backend/.venv/bin/python scripts/hosted.py profile-add \
  --directory /srv/oaw-hosting --user bob --mode runtime \
  --application research --name '研究助手' --template /srv/oaw-hosting/research-template.json
```

模板通过已有 Legion 可移植协议导出选定工作区，用户实例会生成新的卡片 ID 和数据库。工程端的会话历史、运行记录、附件目录、凭证库和无关卡片不会被复制。模板包含选定卡片的当前文档、指令和初始资源；这些是发布者主动共享的内容，导出前应确认其中没有私人文本。自定义插件的可移植文档投影由其 `document.capture` / template handler 定义。已知内嵌凭证和宿主目录字段会被拒绝。

首版要求 Legion 的依赖自包含：连接到组外的卡片时会拒绝导出，需要先把依赖纳入组内。源工作区配置改变后需要重新发布。模板不复制模型 API Key；管理员应单独为每个实例配置模型凭证。公开的模型目录会保留，使用 `environment` 认证的模型可以在实例的 `worker.env` 中配置相应变量。

环境变量认证的模型连接由管理员通过模板配置，托管画布的浏览器不能新增或修改此类连接，也不能用它发现模型，避免把 Sandbox 等服务的环境凭证发送到用户指定的地址。画布用户仍可配置自己的 API Key。

发布可直接运行的部署模板前，请在工程端把需要的模型连接设置为 `environment` 认证，并指定变量名；创建实例后，将相应密钥写入该实例的 `worker.env` 再启动。仍处于 `api_key` 模式的连接不会因为设置了环境变量而自动切换认证方式，而部署页面不开放模型设置。

同一个模板可以给不同用户创建实例，用户后续的聊天和文件彼此独立。`profile-add` 拒绝覆盖已有账号实例。服务重启不会重新导入模板或清空数据。发布新版本的自动数据迁移尚未实现，请保留现有实例并安排显式迁移。

## ECS 上启动

生成配置、检查后安装 systemd units，再启动整组服务：

```bash
sudo backend/.venv/bin/python scripts/hosted.py systemd --directory /srv/oaw-hosting
sudo cat /srv/oaw-hosting/systemd/oaw-hosted/*.service
sudo backend/.venv/bin/python scripts/hosted.py install-systemd --directory /srv/oaw-hosting
sudo systemctl enable --now oaw-hosted.target
sudo systemctl status oaw-hosted-gateway.service
```

`systemd` 只生成文件；`install-systemd` 要求相关服务已停止，验证配置和目录、创建专用系统账号、配置权限、安装 units 并执行 daemon-reload，不会自行启动。已生成的配置发生变化时必须重新生成。默认网关端口是 `127.0.0.1:8080`，可在生成时使用 `--port` 更改。`--name` 可区分多套安装，生成和安装时必须一致。

每个用户都有稳定且独立的 Linux UID、数据目录和服务。systemd 为 worker 创建权限为 `0600`、只有网关账号可连接的 Unix socket，并通过继承的文件描述符交给 worker；worker 不监听 TCP 端口。网关向 worker 签发的短期身份断言仍需验证。画布和部署页面共用这一边界，部署模式登录后直接进入已发布页面。

服务使用 `ProtectSystem=strict`、`ProtectHome`、私有临时目录、空 Linux capabilities、禁止提权及进程可见性限制。每个 worker 只得到自己配置的只读映射和自己数据的可写映射，原始托管目录与其他 worker socket 不可见；配置和 `worker.env` 不能被 worker 改写。网关单独持有账户库和路由。默认限制网关 512 MiB、每 worker 2 GiB；可使用 `systemctl edit UNIT` 按实际负载设置 `MemoryMax`/`TasksMax`，这些是上限而非容量承诺。实现采用标准 [systemd 服务沙箱](https://www.freedesktop.org/software/systemd/man/latest/systemd.exec.html)和 [socket activation](https://www.freedesktop.org/software/systemd/man/latest/systemd.socket.html)。

使用 Caddy 或 Nginx 提供 HTTPS，例如：

```caddyfile
oaw.example.com {
    reverse_proxy 127.0.0.1:8080
}
```

DNS 和证书配置完成后通过该域名访问；ECS 安全组仅开放需要的 HTTPS/HTTP 和受限管理端口。代理需保留原始 Host；网关严格校验 Host/Origin，不信任浏览器转发头。登录限速按真实 socket peer 计算，单一反向代理后的用户共享保守的来源限额。

新增用户或修改部署配置时，先 `sudo systemctl stop oaw-hosted.target`，执行账号/实例命令，再重新执行上述生成、安装、启动步骤。这会为新用户分配独立 UID，并保持已有用户 UUID、密码、签名密钥、会话和文件。安装器可以把旧的 HTTP worker 地址转换为 Unix socket，原始路由保存在 `control/hosting.before-systemd.json`；不会重新初始化用户数据。旧部署若仍在运行，应先停止它，旧 Compose 文件不会再被使用。

```bash
sudo journalctl -u oaw-hosted-gateway.service -n 100
sudo systemctl list-units 'oaw-hosted-worker-*'
sudo systemctl restart oaw-hosted.target
```

应用和插件代码由管理员审核安装。浏览器不能安装插件、读取宿主目录、重定位存储、开启本地 Codex 或选择本机 Sandbox。
管理员可在 `/srv/oaw-hosting/worker-common.env` 一次性配置所有用户的执行平台、模板、基础环境及服务凭据；
该文件由 root 持有、权限为 0600。每个新生成的 worker 服务都会读取它，用户不需要重复配置。
文件不存在时保持原行为；存在时它在实例 `worker.env` 之后加载，同名公共值优先。
不要在公共文件中设置用户/profile 标识、共享数据目录或某个用户的专属 Volume；适配器应从已验证的实例身份派生独立资源。
该文件不映射给 worker 或浏览器，由 systemd 读取并注入运行环境。修改后需在任务空闲时滚动重启 worker；
新增用户仍按上述账号/实例和 systemd 安装流程操作，无需复制基础环境。

例如接入远程 Sandbox 时，在公共文件设置 `OAW_HOSTED_SERVICES_FACTORY=package.module:factory`；
工厂签名为 `factory(settings)`，返回 `ApplicationServices`。工厂代码应安装在共享应用环境，
继续使用实例数据目录和经过批准的插件，不能启用宿主命令执行。个人版不读取此托管部署文件。

systemd 的 `IPAddressDeny` 阻止链路本地地址和阿里云元数据地址 `100.100.100.200`。安装器会用临时回环监听器实际检查内核的 cgroup BPF 过滤能力，不支持时拒绝安装，不会访问真实元数据地址。仍需按实际内网拓扑阻断管理服务的出站访问。不应给应用附加可管理整台 ECS 的 RAM 角色；远程 Sandbox 凭证应只有适配器所需权限。此方案适用于几个用户使用受信任应用和远程 Sandbox，不能把服务进程当作运行任意不可信本地代码的隔离环境。

## 对接阿里云、AWS 等身份服务

在 `/srv/oaw-hosting/control/oidc.json` 写入 OIDC 配置，停止服务后重新生成并安装 systemd 配置，以设置网关专用读取权限。不要通过终端日志或版本控制传播其中的密钥：

```json
{
  "issuer": "https://YOUR_ISSUER",
  "discovery_url": "https://YOUR_DISCOVERY/.well-known/openid-configuration",
  "client_id": "YOUR_CLIENT_ID",
  "client_secret": "YOUR_CLIENT_SECRET",
  "session_secret": "REPLACE_WITH_AT_LEAST_32_RANDOM_CHARACTERS",
  "public_origin": "https://oaw.example.com",
  "scopes": ["openid", "profile"],
  "token_endpoint_auth_method": "client_secret_basic"
}
```

在身份服务配置回调地址 `https://oaw.example.com/auth/oidc/callback`。根据提供商文档调整 scopes 和 token endpoint authentication method，例如部分 CIAM 配置使用 `USER_API` scope 与 `client_secret_post`。协议采用 Authorization Code + PKCE，验证签名、issuer、audience、nonce、state 和有效期；需要目标提供商实际联调。

管理员将可信的 issuer/subject 绑定到内部账号：

```bash
sudo backend/.venv/bin/python scripts/hosted.py identity-link \
  --directory /srv/oaw-hosting --user bob \
  --issuer https://YOUR_ISSUER --subject VERIFIED_SUBJECT
sudo systemctl restart oaw-hosted-gateway.service
```

不会按照邮箱或显示名自动合并账号，不会给未知外部身份自动创建工作区。切换身份提供商时，可以显式把新身份绑定到同一个内部用户 UUID，数据归属保持不变。OIDC token 不发送给业务 worker，也不存入浏览器 localStorage；浏览器持有 HttpOnly 的随机会话 Cookie，服务器只保存其哈希。

## 管理和备份

### 管理员和公共模型

小规模托管可启用公共模型：管理员在 `/admin` 维护连接、密钥、可用模型及默认模型，
普通用户的设置页只显示公共模型，无需逐个填写 API Key。管理员账号可以没有业务工作区；
它不会因此获得其他用户的画布、数据库或文件访问权。

首次启用由部署人员离线执行（先停止已有 target）：

```bash
sudo systemctl stop oaw-hosted.target
sudo backend/.venv/bin/python scripts/hosted.py user-add --directory /srv/oaw-hosting --user admin --role admin
sudo backend/.venv/bin/python scripts/hosted.py shared-models-enable --directory /srv/oaw-hosting --origin http://127.0.0.1:8080
sudo backend/.venv/bin/python scripts/hosted.py systemd --directory /srv/oaw-hosting
sudo backend/.venv/bin/python scripts/hosted.py install-systemd --directory /srv/oaw-hosting
sudo systemctl start oaw-hosted.target
```

密码通过交互输入。已有账号可使用 `user-role --directory ... --user ... --role admin` 显式授权；
更改角色会撤销该账号的现有会话，普通网页没有角色提升接口。

管理员登录后添加连接，填写 Base URL 和 API Key，手动添加模型或读取服务端模型列表，选择默认模型后保存。
密钥留空表示保留原值；修改密钥可直接填写新值；停用服务/模型使用禁用开关，保留已保存的模型 ID。
模型列表查询不代表真实对话、工具调用或特定模型能力已验收。

公共配置及加密密钥只持久保存在 `control/services`。工作进程通过专用的短期身份断言读取自己的加密副本，
仅在内存中缓存两秒。浏览器、卡片、工作区数据库均不保存公共模型密钥；公共配置不能从任意环境变量取密钥。
普通用户的公共模型写入和发现接口在后端拒绝，个人版和未启用共享的托管部署仍使用原配置方式。

管理配置保存后无需重启。后续调用会读取新配置，已打开的画布每 15 秒或重新聚焦时刷新模型选择；
既有 Agent 显式选择的模型不被覆盖，选择 `oaw:default` 的 Agent 跟随公共默认模型。
此后通过 `profile-add` 创建的工作区自动继承。已有个人模型配置仍保留在原工作区，启用共享期间不参与模型解析。
其他服务（例如 DeepL、外部计算平台）目前仍遵循各自配置接口，不会因为添加公共模型而自动共享其凭据。

应用代码和 Pack 依赖属于运维管理的安装内容。安装和更新时执行完整校验；日常启动检查安装记录、版本兼容性和顶层加载路径，不再逐文件扫描全部依赖。生成的 systemd 服务保持应用文件系统只读，每个用户只得到自己的数据映射，普通用户不能调用 Pack 安装管理接口。不要把应用或依赖目录改为用户可写目录。

需要排查安装内容损坏时，使用后端解释器执行 `python -m open_agent_world.pack verify --data-root /path/to/profile`。该命令执行完整校验，不导入插件工厂、不安装或修复代码。登录密码哈希、会话认证和用户数据隔离不受这项启动优化影响。

```bash
sudo backend/.venv/bin/python scripts/hosted.py user-list --directory /srv/oaw-hosting
sudo backend/.venv/bin/python scripts/hosted.py user-disable --directory /srv/oaw-hosting --user bob
sudo backend/.venv/bin/python scripts/hosted.py user-enable --directory /srv/oaw-hosting --user bob
sudo backend/.venv/bin/python scripts/hosted.py password --directory /srv/oaw-hosting --user alice
```

退出登录、修改密码、禁用账号都会撤销对应会话；网关对已有 WebSocket 每秒检查一次会话有效性。已经提交的后台任务不会因退出自动撤销。默认会话有效期为一小时；OIDC 会话同时受身份 token 有效期限制。密码以带随机盐的 scrypt 哈希保存，错误登录有持久化限速。

备份前停止相关服务，备份整个 `control` 和 `instances` 目录以及匹配的代码、依赖版本和 systemd 配置。账号库、签名密钥、每用户数据和加密凭证都需要保持对应关系。不要把控制目录当作静态网站目录，也不要提交到 Git。仓库默认忽略 `.oaw-hosting/`；其他部署目录应放在仓库外。

## 本机集成调试

可以显式使用 `--allow-local-http` 和 `http://127.0.0.1:8080` 初始化，在 `profile-add` 指定 `--upstream http://127.0.0.1:8001`，分别运行：

```bash
backend/.venv/bin/python scripts/hosted.py worker \
  --config .oaw-hosting/instances/PROFILE_ID/worker.json --port 8001
backend/.venv/bin/python scripts/hosted.py gateway \
  --config .oaw-hosting/control/hosting.json --port 8080
```

这种同账号进程运行方式用于开发验证；ECS 多用户部署使用 systemd 独立服务账号和私有数据映射。所有 worker 请求仍必须携带网关签发的短期断言，即使请求来自回环地址也没有免认证通道。

测试覆盖账号存储、OIDC、用户到实例的路由、跨用户请求拒绝、模板数据隔离、部署接口边界和前端账号切换。真实 ECS 主机、第三方身份服务和远程 Sandbox 的联调需要在目标环境完成。

在已准备好的 Linux 测试安装中，可执行 `sudo backend/.venv/bin/python scripts/test-hosted-systemd.py`：它创建临时账号和 systemd 服务，验证真实用户路由、Unix socket、系统文件权限、会话撤销及重启后的数据保留，并清理自己的测试账号、服务和数据。不要将此命令当作云服务联调或负载测试。
