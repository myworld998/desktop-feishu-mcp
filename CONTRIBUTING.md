# 参与贡献

欢迎提交可复现的问题、文档修正和小范围改进。先搜索已有 issue；涉及架构或依赖的大改动请先讨论。

## 本地开发

1. 使用 Python 3.10+，不需要 Python 第三方依赖。
2. 按 README 配置独立测试用飞书应用。真实配置只写 `config.json` 或环境变量。
3. 运行 `python -m unittest discover -s tests -v`，测试使用临时目录和模拟 CLI，不联系飞书。
4. 运行 `python package.py --check`，确认待发布文件不包含真实标识和个人路径。
5. 涉及飞书的修改，在自己的测试会话执行 `python setup.py verify --interactive`，说明 CLI 版本、系统、Python 版本及验证结果。不要把真实会话、原始事件或令牌贴到 issue。

## 兼容性要求

- MCP stdin/stdout 只传逐行 JSON-RPC；诊断信息放 stderr。
- 保留 ask_user 参数、文本回复格式与现有通知处理。
- 卡片所有交互元素放在同一 form；按钮名称编码、`form_action_type`、不设置 input required、`update_multi`、灰色禁用状态都要回归。
- server 与 listener 使用相同配置优先级、profile 和结果目录。
- 不添加读取、保存、显示或上传应用认证信息的代码；认证仅由 lark-cli 完成。
- 路径使用 pathlib/os.path，调用外部命令使用参数数组，不拼接 shell 字符串执行。

## Pull request

写清楚触发条件、修改后的行为、验证命令和已知限制。附图时先遮盖用户、会话、应用标识和消息内容。文档链接优先使用上游官方来源；不要把本机路径放进示例。

贡献默认按 MIT 许可证发布。请保持讨论友善、具体、可复现。
