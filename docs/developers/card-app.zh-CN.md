# 构建并安装 Greeter Card App

做一张可以编辑问候语、供已连接 Agent 调用的卡片。从仓库中完整的 [Python Greeter](../../examples/plugins/greeter/README.md) 出发，添加 React 编辑界面，最后把同一个项目安装为 `.oawpack`。构建与直接工具测试不需要模型密钥或 Sandbox。

## 1. 准备可运行卡片

准备 Git、Python 3.11+、uv 和 Node.js 22+（CI 使用 Node 24），克隆 OAW 并切换到 `dev`。应用项目放在宿主仓库之外。SDK 与 Pack CLI 目前随仓库提供，不要假定已有可从公共 npm 或 PyPI 安装的版本。

在 OAW 仓库根目录执行。Windows PowerShell：

```powershell
$OAW = (Get-Location).Path
$PROJECT = Join-Path (Split-Path $OAW -Parent) 'my-greeter'
Copy-Item -Recurse "$OAW/examples/plugins/greeter" $PROJECT
$PYTHON = "$OAW/backend/.venv/Scripts/python.exe"
$PACK = "$OAW/scripts/oaw-pack.py"
```

Linux/macOS：

```sh
OAW="$PWD"
PROJECT="$(dirname "$OAW")/my-greeter"
cp -R "$OAW/examples/plugins/greeter" "$PROJECT"
PYTHON="$OAW/backend/.venv/bin/python"
PACK="$OAW/scripts/oaw-pack.py"
```

目标必须是新目录，避免复制进已有项目。复制后包含 `pyproject.toml`、`src/oaw_greeter_plugin/__init__.py` 和 `tests/`。Python 入口为 `oaw_greeter_plugin:create_plugin`。

新建 `$PROJECT/oaw.pack.toml`：

```toml
schema_version = 3

[distribution]
id = "community.greeter"
name = "Greeter"

[compatibility]
oaw = ">=0.1.0,<1"
plugin_api = "1.14"

[plugin]
owner = "community.greeter"
packs = ["community.greeter.default"]
node_types = ["community.greeter"]

[components.backend]
project = "pyproject.toml"
mode = "host-adapter"
```

版本来自 `pyproject.toml`，当前为 `0.1.0`。分发 ID 标识安装包，owner 标识运行时插件，Pack ID 标识 Library 收藏包，node type 标识保存的卡片实例；它们不必相同。用户保存卡片后应保持这些 ID 稳定。发布你自己的衍生项目之前，选择自己的命名空间，同时修改声明和 Python 注册。

从 OAW 根目录准备并启动独立的 `card-app` 开发档案。Windows：

```powershell
./scripts/setup.ps1 -Profile card-app -SkipBuild -PluginPath $PROJECT
./scripts/dev.ps1 -AgentRuntime mock -Profile card-app -PluginPath $PROJECT
```

Linux/macOS：

```sh
bash scripts/setup.sh --profile card-app --skip-build --plugin-path "$PROJECT"
bash scripts/dev.sh --agent-runtime core.mock --profile card-app --plugin-path "$PROJECT"
```

打开启动器打印的地址。在 **Pack & Card Library → Packs** 开启 **Greeter**，把卡片加入牌组并放置，打开卡片查看已保存的配置。此时只有 Python，默认界面只读；第 4 步会添加编辑界面。改 Python 或添加构建依赖前，用 Ctrl+C 停止启动器。setup 准备依赖，普通 dev 启动不会安装依赖。

## 2. 理解行为与数据

阅读项目的 `src/oaw_greeter_plugin/__init__.py`：

| 定义 | 负责什么 |
| --- | --- |
| `GreeterConfig` | 持久化的 greeting、punctuation、uppercase 字段与验证 |
| `NodeTypeDefinition` | 卡片名称、外观、配置模型和生命周期 |
| `GreeterRuntime.greet` | 根据配置生成问候语 |
| `GreeterLifecycle`、`GreeterMutation` | 启动时恢复每张卡片的数据，配置变更时提交或回滚 |
| `PackDefinition` | 把卡片加入 Library 中的收藏包 |

当 `greeting="Welcome"`、`punctuation="!"` 时，问候 Ada 应返回 `Welcome, Ada!`；`uppercase=True` 时返回 `WELCOME, ADA!`。下方集成测试通过宿主更新配置并检查运行时结果，第 4 步再添加编辑界面。修改 Python 默认值只影响新卡片，不会覆盖 World 中已保存的配置。

配置由宿主持久化，运行时缓存由配置重建。不要再建一套配置数据库。大段可编辑内容使用[文档接口](../reference/plugin-documents.md)，会话范围数据使用[卡片状态](../card-state.md)。注册过程不要启动工作进程、访问网络或修改用户文件。

## 3. 连接 Agent 并验证授权

同一份 Python 实现注册了 `community.greeter.greet` capability（工具名为 `greet`，必填参数 `name`）、卡片 trait `community.greeting-target`，以及把 `core.agent` 来源匹配到该 trait 的 `community.greet` 关系。

放置 Agent，拖动连接到 Greeter，选择 **Greet with**。处理器使用宿主授权的 `capability.target_id`，不能让模型自己传入任意卡片 ID。trait 用来匹配端点，本身不授予权限。关系开放工具调用，不是自动调度或类型化数据管道。

从 OAW 根目录运行现有集成测试。Windows：

```powershell
uv run --project backend --with-editable $PROJECT python -m pytest -p no:cacheprovider "$PROJECT/tests"
```

Linux/macOS：

```sh
uv run --project backend --with-editable "$PROJECT" python -m pytest -p no:cacheprovider "$PROJECT/tests"
```

测试覆盖 entry point 发现、真实图连接派生的工具调用、配置变化、插件实例隔离、Legion 克隆、非法输入和目标删除后的权限撤销。直接工具测试成功不等于模型一定选择正确工具。单独检查真实模型时，选好模型，用 **Participate** 连接 Agent 与 Conversation，再让它调用已连接 Greeter 问候 Ada。mock 是确定性调试运行时，不是语言模型。

准确的匹配规则见[关系与 capability](../reference/plugin-capabilities.md)。

## 4. 添加 React 界面

在现有 `NodeTypeDefinition` 调用中的 `config_model=GreeterConfig,` 后加一行：

```python
frontend={"body": "greeting"},
```

向 `oaw.pack.toml` 追加：

```toml
[components.frontend]
entry = "frontend/index.tsx"
project = "package.json"
mode = "pack-build"
```

在项目根目录新建 `package.json`：

```json
{
  "name": "my-greeter",
  "private": true,
  "type": "module",
  "scripts": { "build": "node build.mjs" },
  "devDependencies": {
    "react": "18.3.1",
    "react-dom": "18.3.1",
    "@types/react": "^18.3.0"
  }
}
```

新建 `frontend/index.tsx`：

```tsx
import { useState } from 'react';
import type { FrontendPlugin, PluginViewProps } from '@oaw/plugin-api';

function Greeting({ card, host }: PluginViewProps) {
  const [greeting, setGreeting] = useState(String(card.config.greeting ?? 'Hello'));
  const [status, setStatus] = useState('');
  const [saving, setSaving] = useState(false);
  async function save() {
    setSaving(true);
    try {
      await host.updateConfig({ greeting });
      setStatus('Saved');
    } catch (error) {
      setStatus(String(error));
    } finally {
      setSaving(false);
    }
  }
  return <div className="nodrag nopan" style={{ padding: 16 }}>
    <label>Greeting<input aria-label="Greeting" value={greeting}
      onChange={event => setGreeting(event.target.value)} /></label>
    <button disabled={saving} onClick={() => void save()}>Save greeting</button>
    <p role="status">{status}</p>
  </div>;
}

export default { apiVersion: 1, views: { greeting: Greeting } } satisfies FrontendPlugin;
```

新建 `build.mjs`：

```js
import { buildPackFrontend } from '@oaw/plugin-api/build';
await buildPackFrontend({ entryPoint: 'frontend/index.tsx', outfile: 'dist/frontend/index.js' });
```

构建宿主 SDK 并打成 tarball，再安装到外部项目。Windows：

```powershell
npm.cmd --prefix "$OAW/frontend" run build:pack-sdk
npm.cmd pack "$OAW/frontend/pack-sdk" --pack-destination $PROJECT
npm.cmd --prefix $PROJECT install --save-dev "$PROJECT/oaw-plugin-api-1.0.0.tgz"
npm.cmd --prefix $PROJECT run build
```

Linux/macOS：

```sh
npm --prefix "$OAW/frontend" run build:pack-sdk
npm pack "$OAW/frontend/pack-sdk" --pack-destination "$PROJECT"
npm --prefix "$PROJECT" install --save-dev "$PROJECT/oaw-plugin-api-1.0.0.tgz"
npm --prefix "$PROJECT" run build
```

保存生成的 lockfile，确保构建环境仍能取得 SDK tarball；只有确认你的 registry 发布了 SDK 才能改用 registry 版本。新增前端依赖后，使用相同档案和项目参数再运行 setup，再启动 dev。打开 Greeter，输入 `Welcome` 并点击 **Save greeting**，重新打开后检查值保留且工具使用新值。

构建 helper 共用宿主 React 和 SDK。不要打包第二份 React、挂载第二个应用根节点或引用宿主源码路径。`body` 替换普通卡片的详情内容；`settings` 用于共享 Agent 控件内的配置，不是普通卡片的编辑槽位。CSS、资源、Worker 和多文件构建见[前端 SDK](../../frontend/pack-sdk/README.md)。

## 5. 打包同一项目

先构建 Python wheel，再把它和已构建的前端打包。Windows：

```powershell
uv build $PROJECT --wheel --out-dir "$PROJECT/dist/backend"
& $PYTHON $PACK doctor $PROJECT
& $PYTHON $PACK build-project $PROJECT "$PROJECT/dist/greeter-0.1.0.oawpack" --backend-wheel "$PROJECT/dist/backend/open_agent_world_plugin_greeter-0.1.0-py3-none-any.whl" --frontend-dir "$PROJECT/dist/frontend"
& $PYTHON $PACK inspect "$PROJECT/dist/greeter-0.1.0.oawpack"
```

Linux/macOS：

```sh
uv build "$PROJECT" --wheel --out-dir "$PROJECT/dist/backend"
"$PYTHON" "$PACK" doctor "$PROJECT"
"$PYTHON" "$PACK" build-project "$PROJECT" "$PROJECT/dist/greeter-0.1.0.oawpack" --backend-wheel "$PROJECT/dist/backend/open_agent_world_plugin_greeter-0.1.0-py3-none-any.whl" --frontend-dir "$PROJECT/dist/frontend"
"$PYTHON" "$PACK" inspect "$PROJECT/dist/greeter-0.1.0.oawpack"
```

`doctor` 只检查声明，不执行卡片。`build-project` 收集已有产物，不替你运行 npm 或编译 Python，也不覆盖已有输出文件。本地重建可换输出路径；发布升级时同时提升项目和 descriptor 的版本。

Greeter 仅依赖宿主兼容的 Pydantic。额外 adapter 库需要用 `resolve-adapter` 解析并通过 `--dependency-wheels` 提供已验证 wheels；见 [Pack 依赖契约](../pack-distribution.md)。前端构建清单列出的所有产物都必须进入归档。

## 6. 在宿主安装与检查

使用干净数据目录，避免源码或 editable 副本掩盖归档问题。从 OAW 根目录运行 `npm --prefix frontend run build` 构建宿主前端，然后用新目录和空闲端口启动。Windows：

```powershell
$env:OPEN_AGENT_WORLD_DATA_ROOT = "$PROJECT/host-test"
& $PYTHON -m backend.launcher --mode production --port 38583 --strict-port --frontend "$OAW/frontend/dist"
```

Linux/macOS：

```sh
OPEN_AGENT_WORLD_DATA_ROOT="$PROJECT/host-test" "$PYTHON" -m backend.launcher --mode production --port 38583 --strict-port --frontend "$OAW/frontend/dist"
```

在该宿主的 **Library → Packs → Install Pack from File...** 中选择 `dist/greeter-0.1.0.oawpack`，审阅安装计划后确认。Python 代码需要重启：停止，再以相同目录和命令启动。

开启 Greeter，把卡片加入牌组并放置，打开卡片，保存问候语，刷新并检查它是否保留。用 **Greet with** 连接 Agent，再用前述集成测试方式或真实模型检查工具；删除连接后，Agent 不应再有该访问权限。构建或归档检查成功不等于宿主运行验证成功。

可复现的安装测试见[测试与分发](testing.md)。宿主浏览器 CI 使用另一个[已打包 Greeter 示例](../../examples/packs/greeter/README.md)，其 ID 不同，不能替代本教程的授权测试。

分享 `.oawpack` 时附带宿主与依赖要求。Marketplace 投稿是另一个需要作者权限与审核的浏览器流程，构建不会发布卡包；见[投稿步骤](../creator-packs.md#submit-to-marketplace)。
