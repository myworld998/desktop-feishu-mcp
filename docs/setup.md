# 飞书应用、授权与本地安装

本文面向第一次使用飞书开放平台的用户。后台标签可能因租户/语言而不同，以下同时写出功能名与协议标识，最终以后台实际显示为准。不要在 issue、截图或项目配置中贴应用凭据。

## 1. 安装运行环境

安装 Python 3.10+ 与 Node.js LTS，在项目目录运行：

```sh
python --version
node --version
npm install --prefix .local/deps @larksuite/cli
node .local/deps/node_modules/@larksuite/cli/scripts/run.js --version
```

本地安装后，把下文所有 `lark-cli` 替换成 `node .local/deps/node_modules/@larksuite/cli/scripts/run.js`。已经安装官方 CLI 的用户可直接使用 `lark-cli`。官方也提供 `npx @larksuite/cli@latest install` 安装向导，可能修改 CLI 自己的用户安装目录，由用户自行选择。本项目 setup 只检查依赖并给出指引，不自动写全局安装目录。

如果找不到 Python，Windows 安装时启用 Python 的 PATH 选项，或用 `py -3`。macOS/Linux 通常使用 `python3`。如果 npm 安装成功但 CLI 无法运行，检查原生二进制下载是否失败，参考 [上游安装说明](https://github.com/larksuite/cli#installation--quick-start)。

## 2. 创建独立的应用机器人

1. 打开 [飞书开发者后台](https://open.feishu.cn/app)，用目标租户的飞书账户扫码登录。
2. 点击 **创建企业自建应用**，填写自己的名称、描述和图标，完成创建。
3. 进入新应用，左侧 **添加应用能力**，选择 **机器人** 并添加/启用。
4. 左侧 **凭证与基础信息** 是应用凭据的管理页面。项目不会读取该页面或 CLI 的存储；如 CLI 自己的交互向导要求导入应用，由你直接在 CLI 中完成。不要放到 `config.json`，不要粘贴到聊天或命令参数里。
5. 使用独立于其他机器人桥接的应用和 CLI profile，避免不同监听者分流同一个回调。

也可以使用 CLI 的交互式 `config init` 浏览器建应用流程，随后回后台检查机器人、权限、回调及可用范围。如果 CLI 向导创建了新应用，请确保后台配置的正是它，避免后台配置和本地 profile 指向不同应用。

## 3. 勾选权限 scopes

进入应用左侧 **权限管理 → 开通权限**，按权限标识搜索并勾选。默认 `message_identity=bot`：

| 身份与场景 | scope | 用途 |
| --- | --- | --- |
| 应用身份，必需 | `im:message:send_as_bot` | 以机器人身份发送卡片和文字消息 |
| 应用身份，必需 | `im:message:readonly` | 读取消息及 CLI 获取卡片原内容 |
| 应用身份，使用群聊时 | `im:message.group_msg` | 读取机器人所在群聊的历史消息 |
| 应用身份，使用会话列表时 | `im:chat:read` | 列出机器人加入的群聊 |
| 用户身份，可选 OAuth | `im:chat:read` | 枚举用户会话，取得机器人私聊的 chat ID |
| 用户身份，仅使用 user 消息模式时 | `im:message:readonly`、`im:message`、`im:message.send_as_user` | 读取及代表用户发送消息 |
| 用户身份，仅使用 user 消息模式时 | 私聊 `im:message.p2p_msg:get_as_user`；群聊 `im:message.group_msg:get_as_user` | 目标类型对应的读取权限 |

发送与历史消息的权限组合来自 [飞书发送消息 API](https://open.feishu.cn/document/server-docs/im-v1/message/create) 和 [获取历史消息 API](https://open.feishu.cn/document/server-docs/im-v1/message/list)。只配置实际使用的身份和场景；不要为本工具申请全部业务权限。默认 bot 模式只把消息发给应用自己的会话，不需要代表用户发送消息权限。

权限变更可能需要管理员批准和发布版本。点击添加权限后检查待发布状态，不要以“勾选成功”判断已经生效。CLI 的缺权限错误如指出其他必要 scope，核对目标调用和身份后再申请。

## 4. 配置卡片回调（不是普通消息事件）

1. 左侧 **事件与回调**，进入 **回调配置** 标签。
2. 订阅方式选择 **使用长连接接收回调**（部分后台显示“接收事件”），保存。
3. 点击 **添加回调**，搜索并添加 **卡片回传交互**，标识必须是 **`card.action.trigger`**。
4. 不要选择旧版回调；不需要为此填写公网回调 URL。
5. 如果后台提示“未建立长连接”，先完成第 6 节 CLI profile 绑定，在另一个终端启动 `python listener.py`，看到 `consume ready` 后返回保存，再完成发布。

本项目通过 API 轮询文字回退，不依赖 `im.message.receive_v1` 普通消息事件；仅添加“接收消息”不能替代卡片回调。官方 [接收回调说明](https://open.feishu.cn/document/event-subscription-guide/callback-subscription/receive-and-handle-callbacks) 说明长连接不需要额外公网地址。

## 5. 发布与可用范围

1. 左侧 **版本管理与发布 → 创建版本**。
2. 填写版本号和更新说明，检查本版本启用的机器人、权限、事件/回调。
3. 设置 **可用范围**，至少包含你自己；仅需个人测试时不要默认选择整个组织。
4. **保存 / 申请发布**，按租户规则由管理员审核。确认版本状态已发布；新增权限或回调后重新发布。
5. 在飞书客户端找到这个应用的机器人，打开单聊并发送一条普通消息。若用群聊，在 **群设置 → 群机器人 → 添加机器人** 中加入该应用，确认机器人有发言权限。

## 6. 隔离 CLI profile，再扫码授权

每个用于本项目的终端先设置相同 profile。以下路径全是项目内目录：

PowerShell：

```powershell
$env:LARKSUITE_CLI_CONFIG_DIR = Join-Path (Get-Location) '.lark-cli'
```

macOS/Linux：

```sh
export LARKSUITE_CLI_CONFIG_DIR="$PWD/.lark-cli"
```

新用户运行 `lark-cli config init`，按 CLI 本身的交互提示完成已有应用绑定或浏览器创建流程，凭据由 CLI 自己存储。已有专用 profile 则跳过初始化，配置 `cli_config_dir` 指向已有目录，环境变量也要一致；不必复制任何认证文件。

**默认 bot 模式无需 OAuth。** 如果需要用用户身份列出私聊，在后台开通用户身份 `im:chat:read` 后运行：

```sh
lark-cli auth login --scope "im:chat:read"
```

1. 在自己的终端中打开 CLI 显示的授权 URL；如 CLI 显示二维码，可直接扫码。
2. 使用目标租户的飞书手机 App 扫码，确认应用名称和授权范围，然后点击同意。
3. 返回原终端等待 CLI 显示成功；不要在项目里保存授权页面链接、设备码或完整认证输出。
4. 用 `lark-cli auth check --scope "im:chat:read" --json` 检查 scope。该命令检查用户授权，不检查 bot 的后台权限。不要把完整 `auth status` 输出贴到公开 issue。

只有选用 `message_identity=user` 才额外申请上一节表中的用户消息权限，再运行带所需 scope 的 `auth login`。以 bot 为基础的回调仍要求同一应用的 bot profile，OAuth 不能代替后台发布。

## 7. 获取用户与会话标识

open ID 与应用相关，同一个人在不同应用的值可能不同。可在飞书 API 调试台的用户选择器或应用对应的回调操作者信息中获得；只在本地填写，建议默认私聊加允许用户限制。

取得机器人私聊 chat ID 最方便的方式是完成上节可选 OAuth，然后：

```sh
python setup.py chats --identity user
```

这个命令用用户身份列出第一页（最多 100 个）会话，显示会话名称；选择你与目标机器人的私聊后将 chat ID 写入本地 `config.json`。不要选普通人与人的私聊。已有文件会先要求确认，使用配置路径/chat ID 环境覆盖时自动写入会拒绝执行。

bot 身份只能枚举群聊，使用 `python setup.py chats --identity bot`。列表没有目标会话时，可依 [CLI chat-list 文档](https://github.com/larksuite/cli/blob/main/skills/lark-im/references/lark-im-chat-list.md) 用分页参数寻找，或在飞书开发者后台 API 调试台取得目标会话标识并手动填写。本项目不通过不相关应用的 profile 推测标识。

接着运行：

```sh
python setup.py configure
python setup.py doctor
```

已有配置要改用户限制时用 `python setup.py configure --edit`；Enter 保留原值。要清空已有用户限制，直接在本地 JSON 中把 `user_open_id` 设为 `""`。先前运行 `chats` 生成的配置会保留，使用 `--edit` 补齐其他字段。

## 8. 验证与注册

```sh
python setup.py verify
python setup.py verify --interactive
python setup.py registration
```

`verify` 读取一条消息但不显示内容，随后限时启动 consume，检查 stderr ready 标志；不发消息、不自动启用任何后台设置。`verify --interactive` 需要先停止已有 listener，程序临时启动一个 listener，收到其 ready 标志后才发送已确认的测试卡。手机在 60 秒内选择“连接正常”，看到回复回传，并人工检查卡片变灰禁用。

**ready 不证明回调开关打开**。这是 [官方 CLI 明确说明的限制](https://github.com/larksuite/cli/blob/main/skills/lark-im/references/lark-im-card-action-reply.md)：后台没启用回调时 consume 也能启动。只有实际点击和收到对应回复才验证完整链路。

注册生成器只写 `.local/claude-desktop.local.json` 与 `.local/claude-code.local.txt`。按 README 合并 Desktop 配置，或执行生成的 Claude Code 命令；它们使用当前 Python 和脚本绝对路径，不写入任何应用凭据。更改注册前可检查生成文件。

## 后台与开机自启

先注册 MCP，再保持一个 listener 运行。它和 server 是不同进程；关闭 listener 后卡片依然能发送，但点击将无法被本项目处理。

### 前台与临时后台

```sh
python setup.py listener --mode foreground
python setup.py listener --mode background
```

两条是二选一。前台用 Ctrl+C 停止。后台 PID 写在 `.local/listener.pid`，先核对进程确实为本项目 listener，再用系统进程工具停止该 PID（Windows 可用任务管理器，macOS/Linux 可用 `ps -p PID -o args=` 检查后 `kill PID`）。PID 文件可能过期，不要盲目照着文件杀进程。日志在 `listener.log`，出现 `consume ready` 才表示连接就绪。

后台模式不是服务，不提供自动重启保证；不要重复运行造成多个消费者。

### Windows 登录自启

```sh
python setup.py listener --mode startup
```

只生成 `.local/listener-task.local.xml` 和 `.local/start-listener.local.ps1`。

1. Windows 搜索并打开 **任务计划程序**。
2. 右侧 **导入任务**，选择生成的 XML。
3. 在 **常规** 中选择自己的用户，使用“只在用户登录时运行”，无需勾选最高权限。
4. 在 **触发器** 中核对登录触发；在 **操作** 中检查项目与脚本路径；保存。
5. 停止手动 listener，再右键任务 **运行**，检查项目日志。注销再登录验证自启。
6. 撤销时在任务计划程序禁用/删除该任务；此操作由你执行，setup 不修改注册表或任务库。

如本机策略禁止运行 PowerShell 脚本，请按组织政策处理，不要全局放宽执行策略。`run-listener.bat` 是无需个人绝对路径的手动替代启动器，优先用 `py -3`，否则用 PATH 的 Python，日志写项目目录；它本身不会安装自启。

### macOS LaunchAgent

同一命令生成 `.local/org.desktop-feishu.listener.plist`。检查文件后由用户执行：

```sh
mkdir -p "$HOME/Library/LaunchAgents"
cp .local/org.desktop-feishu.listener.plist "$HOME/Library/LaunchAgents/"
launchctl bootstrap "gui/$(id -u)" "$HOME/Library/LaunchAgents/org.desktop-feishu.listener.plist"
```

撤销：`launchctl bootout "gui/$(id -u)" "$HOME/Library/LaunchAgents/org.desktop-feishu.listener.plist"`，然后由用户移除对应 plist。此流程在登录会话启动；改文件后需重新加载。

### Linux systemd 用户服务

同一命令生成 `.local/desktop-feishu-listener.service`。检查后由用户执行：

```sh
mkdir -p "$HOME/.config/systemd/user"
cp .local/desktop-feishu-listener.service "$HOME/.config/systemd/user/"
systemctl --user daemon-reload
systemctl --user enable --now desktop-feishu-listener.service
journalctl --user -u desktop-feishu-listener.service -f
```

撤销：`systemctl --user disable --now desktop-feishu-listener.service`，自行移除用户服务文件后再 `daemon-reload`。默认随用户登录启动；无登录启动需要管理员按本机政策配置，本项目不自动开启 linger。

自启环境通常不会加载交互 shell PATH；把 Node 与 JS runner 或原生 CLI 路径明确写到**本地**配置，避免终端里正常而自启找不到命令。更换目录或 Python 后重新生成文件，并重新导入/加载。

## 排障顺序

1. `python setup.py doctor`：依赖能否启动，配置能否解析？
2. 两进程的 profile、chat ID、允许用户和存储目录是否一致？环境变量是否覆盖 JSON？
3. 后台是否给正确应用开权限、启用新版卡片回调并发布？
4. 机器人是否在目标群内，使用者是否在可用范围内？
5. `verify` 是否能读取消息并收到 ready？失败检查本机网络、CLI 配置或权限。
6. 停止已有 listener 后运行 `verify --interactive`，手机实际点击；检查卡片是否灰色禁用。

本项目只输出最少诊断，不回显原始事件/认证响应。需调试 CLI 时在自己终端执行最小读取命令，检查 `--help`，对外求助前人工脱敏。
