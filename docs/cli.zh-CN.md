# 用 OAW 命令行操作卡片

安装后，在任意目录输入 `oaw`。CLI 与 OAW 界面使用同一份卡片、连接和聊天记录。

## 安装一次

Windows 源码目录中运行：

```powershell
.\scripts\install-cli.ps1
oaw --help
```

这会安装当前用户的命令，并更新 PATH。新的 Windows 桌面安装包会自动完成这一步。

Linux 或 macOS：

```sh
bash scripts/install-cli.sh
oaw --help
```

命令安装到 `~/.local/bin`。如果该目录尚未加入 PATH，按安装程序给出的一行命令设置即可。

打开平时使用的 OAW。也可以在终端运行 `oaw start` 启动正常应用，然后在另一个终端操作卡片。

## 找到卡片

```sh
oaw cards
oaw show agent
```

列表中首先是**名称**，后面是类型、状态和短 ID。后续命令可以用名称或短 ID。
只有一张对应类型的卡片时，也可以直接写类型，例如 `show agent`。

新建时可以直接起名：

```sh
oaw create agent 助手
oaw create text 笔记 --text "请整理这个项目"
oaw show 笔记
```

省略名称时，CLI 会使用 `agent`、`agent-2` 这样的名字。多个卡片重名时，使用列表里的短 ID。

`oaw types` 显示基础类型和卡包分组；用 `oaw types 分组ID` 展开一组，或用 `oaw types --all` 查看全部。

## 让 Agent 回答

先运行 `oaw models` 检查默认模型。如果尚未配置，在 OAW 的**设置 → 模型**中添加连接并选择默认模型。

```sh
oaw chat 助手 "你好，介绍一下你能做什么"
```

想连续交谈，运行 `oaw chat 助手`，逐句输入消息。输入 `/exit` 退出。

## 在 Conversation 卡片中聊天

Conversation 保存聊天记录，界面与 CLI 都能查看。把 Agent 连进去即可：

```sh
oaw create conversation 讨论
oaw connect 助手 讨论
oaw chat 讨论 "你好，我们开始讨论吧"
oaw chat 讨论 --history
```

如果有多个参与者，用 `oaw chat 讨论 "问题" --agent 助手` 选择回复者。
`oaw chat 讨论 --new` 开始新的会话；已有会话可用 `--session` 选择。

## 连接工具、查看执行

```sh
oaw connect 助手 笔记 read
oaw create sandbox 工作台
oaw connect 助手 工作台 execute_manage
oaw connections
```

没有写连接方式且有多种选择时，终端会列出选项。`execute_manage` 允许执行和启停 Sandbox。

较长的任务可以先提交，稍后查看：

```sh
oaw run 助手 "检查项目并给出改进建议"
oaw runs
oaw result RUN_ID
```

`runs` 会显示失败原因。`oaw wait RUN_ID` 等待结束，`oaw cancel RUN_ID` 取消执行。

## 给外部 agent 使用

```sh
oaw cards --json
oaw chat 讨论 --message-file task.txt --json
oaw create text --input card.json --json
```

`--json` 输出纯 JSON，失败时也返回 JSON 和非零退出码；不会弹出交互选择。
文件使用 UTF-8，文件参数中的 `-` 表示读取标准输入。

等待超时或断线后，先根据返回的 Run 或会话 ID 查询结果，避免重复提交同一任务。
更详细的参数可用 `oaw COMMAND --help` 查询。
