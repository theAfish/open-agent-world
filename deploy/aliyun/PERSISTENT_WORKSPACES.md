# AgenticFS 长期文件工作区

当前状态（2026-10-30）：白名单续期后，K 区创建已恢复。admin 和 test1～test3 的当前登录入口已各自分配独立空间、访问点、受限角色和卷，并安装了运营方存储配置。四个卷均通过 OAW 文件写入、VM 销毁重建后的读取及路径越界拒绝验证。

## 范围

将 Sandbox 的 `/workspace` 从 ECS 快照改为 AgenticFS 卷。计算 VM 可以销毁、重建，文件继续保留，不再受 ECS 快照的 32 MiB 总量限制。已有单次上传、预览、下载的 API 限制仍适用。

每个 OAW profile 对应独立的 AgenticSpace、Access Point、RAM 角色/策略和 `oaw-<profile UUID>` Volume。用户继续使用原来的文件浏览器和 Sandbox 卡片，无需另配界面。管理员批量准备云资源；此轮没有实现新账号登录时自动创建云资源。重新发布并部署应用可能生成新的 profile UUID，需要按新的登录映射准备资源，不能复用旧入口的分配文件。

这一步只迁移 Sandbox 文件。账号数据库、OAW 卡片/知识库数据、模型配置、Pack 和 worker 状态仍在 ECS；并非整个服务已脱离 ECS，也未部署 AgentCore 代码包。

## 实现

- `scripts/aliyun_workspaces.py`：创建上述独立资源，保存每步请求和结果；先写预留记录再请求云端。超时、异常或响应丢失后停止，必须核对资源才能继续，不盲目重发创建。
- `backend/cloud/aliyun/workspace.py`：从 profile 的 `data` 同级读取运营方文件 `aliyun-workspace.json`。Linux 下必须为 root 所有、不可由组或其他用户写入、不可为链接，且通过 profile/Volume/验证状态校验。
- `backend/cloud/aliyun/hosted.py`：存在有效配置时使用独立卷；配置不存在时维持原部署设置。错误配置直接拒绝启动，不退回另一用户或其他存储。
- `backend/cloud/aliyun/sandbox.py`：存储访问点只允许精确的私网 IPv4 `/32`；公网开关不再同时切断 NFS。其他私网和云元数据地址继续被拒绝。地址变化会触发已有 binding 不匹配，需要运营方核对，不能自动扩大放行范围。
- 开启联网时额外放行阿里 VPC 默认 DNS `100.100.2.136/32`、`100.100.2.138/32`。挂载 AgenticFS 后沙盒使用 VPC DNS，原 CGNAT 拒绝规则也会覆盖解析器；只补充这两个固定地址，不扩大私网范围。关闭联网时不放行 DNS，仍只保留专属存储端点。
- `scripts/aliyun_workspace_probe.py`：通过真实 OAW 文件协议验证写入、VM 销毁、后端重建、读取、路径越界拒绝与清理。不会安装 hosted 配置，也不会自动将任何卷标记为已验收。

个人部署入口没有改动。

## 2026-10-26 单卷实测

使用既有独立测试 Volume `oaw-poc-workspace`，没有挂载线上用户数据。

| 检查 | 结果 |
| --- | --- |
| 第二代 Sandbox 模板预装存储客户端 | 模板 `6n8ju67xhuxuaa0yk4cf` ready；`installStorageClients=true` |
| 关闭公网，只允许访问点精确 IP | NFS 读写成功；测试时访问点解析为 `172.28.1.39` |
| 写入 45 MiB、fsync、销毁 VM、新 VM 读取 | 字节数及 SHA256 一致 |
| 使用 OAW 文件接口，重建 VM 和后端对象 | 写入和预览内容一致 |
| OAW 文件接口访问 `../etc/passwd` | 被拒绝 |
| Volume 自带 RAM/VPC 配置，不额外覆盖 metadata | OAW 探针成功 |
| 不同用户卷之间的云端隔离 | 当时未执行；见下节 2026-10-30 验收 |
| 线上迁移、真实用户浏览器验收 | 当时未执行；见下节 2026-10-30 验收 |

45 MiB 探针回执：ECS 私密维护目录 `persistent-20261026/aliyun-persistent-probe-c6325a35bb2f4e66a553006b323c5c8e.json`。
OAW 探针回执：`persistent-20261026/backend-probes/c8f9ebc710d34b01ad1d81f4503f8bdf/result.json`。
这些文件均位于 `/opt/oaw-maintenance/` 下，不属于线上应用安装。

本地针对性验证：`test_aliyun_workspaces.py` 和 `test_aliyun_sandbox.py` 共 36 项；`test_aliyun_hosted.py` 和 `test_aliyun_ecs_workspace.py` 共 24 项通过。没有运行全项目回归。Windows pytest 缓存目录创建受限导致结束阶段挂起，诊断后使用 `-p no:cacheprovider` 完成；未跳过测试断言。

## 2026-10-30 独立资源验收

- 四个当前入口的 AgenticSpace 均创建成功，Access Point 为 `active`、Volume 为 `AVAILABLE`。
- 各卷均通过真实 OAW 文件协议的写入、销毁 VM、重建适配器与 VM、读取、拒绝 `../etc/passwd`、删除标记和释放探针 VM。
- test1/test2 的文件互不可见。相同显式角色挂载自己的卷成功；两个方向的交叉角色挂载均被平台拒绝，查询确认没有创建沙盒。平台返回通用 500，不能把它描述为具有明确授权错误码的响应。
- 四个卷在启用平台 DNS 例外后均实际访问 `https://mirrors.aliyun.com/pypi/simple/numpy/`，收到 HTTP 200；探针命令结束后 VM 已释放。
- 当前入口的 ECS 沙盒目录均为空，切换未复制或清理用户文档、会话、模型和环境变量。账号及数据库保留校验通过。
- admin 和三个普通账号的公网登录、页面加载、持久文件预览/下载、其他用户文件不可见及管理员权限边界均通过；test1 在 MatCreator Workspace 的 Files 面板实际显示持久文件。
- 新模板使用 Python 3.13.13，通过现有自动准备逻辑安装了 NumPy、SciPy、ASE、pymatgen、matplotlib、mp-api、emmet-core，并通过 pip 依赖检查。该项验证了冷环境安装，未执行完整科学计算任务。
- 验收期间 test1 有一次平台创建失败并留下未决回执。完整云列表确认没有对应实例、没有用户命令后，运营方记录并解除该回执；受控根目录读取和后续 MatCreator 文件面板验收通过。没有把失败的创建或用户命令自动重放。
- 回执位于 ECS 私有目录 `/opt/oaw-maintenance/persistent-20261030/`。`activation/receipt.json` 记录实际分配与配置保留结果；`persistence.json`、`isolation-*.json`、`acceptance-markers.json` 记录独立云验证及清理状态。

针对 DNS 修复运行 `test_aliyun_sandbox.py` 和 `test_aliyun_workspaces.py`：37 项通过。包括联网开启/关闭时 DNS 与专属存储路由的区别、私网与元数据拒绝规则、错误配置拒绝和未知创建结果不重放。未运行完整项目回归或付费模型调用。

## 已解除的创建阻塞

文件系统 `001zlkhc2ex3m28mg53` 中，已有 AgenticSpace 可读写，但新建空间失败。为首个 profile 的实际请求和 DryRun 均返回 `InvalidAzone.NotSupport`：

- 地域/可用区：`cn-hangzhou` / `cn-hangzhou-k`。
- 实际创建请求 ID：`01A177A0-2112-5C53-8FA9-274E2C5930E7`。
- K 区 DryRun 请求 ID：`01A177A1-7F3A-5741-B71B-5D8FCEC15235`。
- I 区 DryRun 返回 `InvaildZone.NotExist`，请求 ID：`01A177A1-7FCA-5063-8921-0E9CA34C386C`。

工程师确认：原开通白名单在 2026-10-22 过期，导致后续新建失败；杭州当前支持 K 区创建，无需更换可用区。白名单重新过审后，2026-10-30 的 K 区 DryRun 成功，请求 ID 为 `01A18BC3-C3E1-5B53-A0DF-2A61E4FA399A`，随后四个当前入口的独立资源均创建成功。

## 为新入口准备资源

1. 工程师修复后先运行 `CreateAgenticSpace` 的 `DryRun=true`。核对资源列表及本机私密 provisioning 回执，确认失败请求没有生成资源。保留错误历史后才能释放对应预留步骤；响应不明时不得删除预留并直接重试。
2. 在有阿里管理 SDK 的运营方 Python 环境中执行批量准备脚本。CLI OAuth/STS 凭据必须仍有效，可先使用 `aliyun sts GetCallerIdentity --profile oaw-poc` 刷新。不要把这些管理凭据发给 worker 或 Sandbox。

   ```powershell
   $env:PYTHONPATH = 'D:/AI/open-agent-world/.tmp/aliyun-admin-sdk'
   backend/.venv/Scripts/python.exe scripts/aliyun_workspaces.py `
     --config C:/Users/taoyu/.config/oaw/secrets/aliyun-persistent-config-20261026.json `
     --profiles C:/Users/taoyu/.config/oaw/secrets/aliyun-persistent-entry-profiles-20261030.json `
     --state-directory C:/Users/taoyu/.config/oaw/secrets/aliyun-persistent-profiles
   ```

   `--config` 包含 region、filesystem、zone、vpc、vswitch、security_group、team、quota_bytes、quota_files、credentials_file、cli_profile；`--profiles` 为含 `profile_id` 的 JSON 对象数组。当前试验配额为每 profile 10 GiB / 10,000 文件；资源计费按阿里规则执行。

3. 从被 Sandbox API Key 允许的出口解析访问点地址，核对确实属于该存储 VPC。不要扩大 API Key 来源范围。仅在独立维护进程中运行探针；以下命令要求代码及依赖可导入、密钥通过私密环境传入：

   ```sh
   python -m scripts.aliyun_workspace_probe \
     --region cn-hangzhou --template <storage-template-id> \
     --volume <dedicated-test-volume> --storage-address <verified-access-point-ip> \
     --state-directory <private-maintenance-directory>
   ```

4. 至少两个独立用户卷各自完成重建恢复；验证 A 的角色不能挂载 B 的访问点、A 的文件接口不能读取 B 的文件、两个 profile 的云资源和回执对应正确。通过前不得生成 `verified: true` 配置。

## 切换现有入口

2026-10-30 已获用户明确授权并切换四个当前入口；本节保留后续维护流程。不要把 provisioning 完成视为上线授权。

1. 用户确认测试员下线；再次核对当前登录映射、profile UUID、活动命令和云 VM，防止管理员重新发布后映射已经变化。
2. 暂停入口写入，停止相关 worker/socket，核对并结束对应沙盒；保留原 ECS 数据和必要的绑定、配置变更记录。数据备份按用户当轮要求处理，不默认保留额外数据副本。
3. 使用已有文件传输协议迁移每个工作区快照到其专属卷；比较完整目录清单、文件大小和摘要。原 ECS 数据暂不删除。
4. 安装经过验收的代码和每 profile 配置。示例（替换占位值，不能直接复制上线）：

   ```json
   {
     "version": 1,
     "profile_id": "<canonical-profile-uuid>",
     "region": "cn-hangzhou",
     "template": "<verified-storage-template>",
     "volume": "oaw-<canonical-profile-uuid>",
     "storage_addresses": ["<verified-private-ip>"],
     "verified": true
   }
   ```

   文件位置是该 worker `data_root` 的父目录内 `aliyun-workspace.json`，不是用户可写的 `data` 内。建议 root:worker-group、0640；父目录也必须由运营方控制。
5. 确认旧 binding 已停止且没有未决命令后保存旧绑定，再建立新存储绑定；不要直接篡改未决回执。保留原逻辑 Sandbox ID 及依赖配方，避免破坏已有卡片引用。
6. 启动后检查登录、文件列表/上传/预览、执行产物及重建恢复，然后恢复测试入口。数据库、Pack 和知识库仍沿用现有 ECS 部署。

回退：停止写入和 worker，保留新卷及变更记录，恢复原配置、绑定和版本。**若上线后已有新文件写入，必须先将增量安全复制回 ECS 并校验；不能只恢复旧快照，否则会丢失新数据。**

## 官方依据

- [创建 AgenticSpace](https://help.aliyun.com/zh/nas/developer-reference/api-nas-2017-06-26-createagenticspace)：单级目录、配额、DryRun 及幂等参数。
- [挂载 AgenticFS Volume](https://help.aliyun.com/zh/agent-sandbox/mount-agenticfs-volume-1)：存储客户端、Volume 的 RAM/VPC 配置及数据生命周期。
- [网络访问控制](https://help.aliyun.com/zh/agent-sandbox/network-access-control)：allow/deny 优先级与网络协议限制。本次 NFS 实测使用访问点精确 IP，不能仅依赖 HTTP/TLS 域名规则。
- [VPC DHCP 与 DNS](https://help.aliyun.com/en/vpc/dhcp-option-set-and-dns-hostname)：阿里 VPC 默认解析器的两个固定地址。
