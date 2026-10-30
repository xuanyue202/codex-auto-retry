# Codex Auto Retry for macOS

[![Tests](https://github.com/xuanyue202/codex-auto-retry/actions/workflows/test.yml/badge.svg)](https://github.com/xuanyue202/codex-auto-retry/actions/workflows/test.yml)
[![MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)

[English](README.md) · [简体中文](README.zh-CN.md)

在 macOS 上自动重试 Codex 桌面版的模型满载错误，沿用原会话的模型、工作目录和权限。

任务遇到 “Selected model is at capacity” 而停止时，工具会等待片刻，再请求原会话继续。它作为 macOS 用户后台服务运行，只依赖 Python 标准库。

## 运行要求

- macOS 已登录桌面，安装了 Python 3.10+、git 和 curl。
- 同一台 Mac 上的 Codex 桌面应用正在运行，并持有目标会话。仅运行命令行客户端不够。
- 能读取本机的生命周期日志和 IPC socket，默认位于 `~/.codex`。首次安装支持 `CODEX_HOME`。

## 安装

在目标 Mac 上，把下面这句话发给 Codex：

```text
请读取并按 https://raw.githubusercontent.com/xuanyue202/codex-auto-retry/v0.1.1/INSTALL.md 安装 Codex Auto Retry。
```

也可以在终端运行固定版本的安装器：

```sh
curl -fsSL https://raw.githubusercontent.com/xuanyue202/codex-auto-retry/v0.1.1/install.sh | sh
```

运行前可以先[查看脚本](install.sh)。安装器会检查桌面连接，安装用户 LaunchAgent，并核对进程和心跳。无需 sudo 或 API Key，也不修改 shell 配置。安装过程不会提交模型请求。

## 重试规则

- 只处理结构化的 `server_overloaded` 和 `serverOverloaded` 错误。登录、配额、计费、政策限制和未知错误交由用户处理。
- 连续失败时，依次等待 1、2、4、8、16 秒，15 分钟内最多尝试 5 次。日志每秒检查一次，扫描和桌面请求还会增加耗时。
- 用户发来新消息、取消任务，或已有新回合开始时，撤销等待中的重试。仍处于 active 状态的原生 Goal 可以重试；已暂停或完成的 Goal 不会被重新激活，预算也不会重置。
- 检查范围包括跨天会话，以及上下文压缩后产生的日志文件。首次安装和重新启用从日志当前位置开始，不补跑旧错误。
- 发送前先保存请求意图。接收结果不确定时，观察是否出现新回合，不重复发送同一个续接请求。30 秒内未观察到新回合，会记录 `start_not_observed`。

工具通过已有的桌面会话持有者发起续接，不切换模型、不重放原始提示词，也不使用 CLI resume。[实现与保护规则](docs/SAFETY.md)。

## 命令

```sh
"$HOME/.local/bin/codex-retry" status      # 查看服务、心跳和待重试任务
"$HOME/.local/bin/codex-retry" doctor      # 只读检查桌面连接
"$HOME/.local/bin/codex-retry" disable     # 暂停自动重试
"$HOME/.local/bin/codex-retry" enable      # 监听未来的错误
"$HOME/.local/bin/codex-retry" stop        # 暂停并卸载后台服务
"$HOME/.local/bin/codex-retry" start       # 加载服务，保留启用偏好
"$HOME/.local/bin/codex-retry" uninstall   # 移除服务和启动器
```

如果 PATH 已包含 `~/.local/bin`，可以直接使用 `codex-retry`。服务在登录后启动，覆盖本机 Codex 桌面持有的符合条件的会话，跨工作目录生效。任务运行在另一台 Mac 时，需要在那台机器上另行安装。

更新时，运行目标版本的安装器。已有配置、日志读取位置和发送去重记录都会保留，也支持从早期私人版迁移。存在待发送或结果不明的请求时，需要先核对结果再更新。安装器会备份原服务；新版健康检查失败时，会恢复原服务。[安装细节](INSTALL.md)。

## 限制与测试结果

工具使用 Codex 桌面的内部 IPC 接口，应用更新后可能需要适配。任务 API 能访问一个会话，并不代表桌面仍持有它。可以用 `doctor --thread-id <id>` 只读检查指定会话的持有者。

受控测试中，一台 Mac 在续接后产生了模型输出；另一台 Mac 虽然按原设置启动了新回合，却返回空消息，也没有执行待续的测试动作，因此尚未证实它能继续完成工作。0.1.1 会将这种情况记录为 `same_thread_started_without_assistant_output`。

这些测试在隔离镜像中注入错误记录，再发出一次真实续接请求，尚未验证真实上游满载后的恢复效果。出现新回合或模型输出，也不能单独证明任务已经完成。[验证方法](docs/VERIFICATION.md)。

桌面接口不支持原子化的“检查状态并启动回合”，因此取消操作与发送之间仍可能发生竞态。串行 IPC 查询可能推迟其他会话的重试。恢复后的模型仍需核对结果不确定的外部操作。

## 隐私与本机文件

工具不发送遥测。它在本机读取生命周期记录，丢弃对话和工具文本，只保留 ID、路径、时间、计数、结果分类和 Goal 版本哈希；不保留提示词、凭据或模型原文。

运行文件、配置、状态和回执存放在 `~/Library/Application Support/CodexAutoRetryMac/`，用户 LaunchAgent 名为 `com.local.codex-capacity-retry`。卸载会移除服务和启动器，保留本机数据供核对；不会删除 Codex 历史，也不修改全局 Codex 设置。

## 开发

```sh
git clone https://github.com/xuanyue202/codex-auto-retry.git
cd codex-auto-retry
python3 -m unittest discover -s tests -v
```

CI 使用合成日志和假 socket，不调用模型。参见[贡献指南](CONTRIBUTING.md)、[发布审查](docs/REVIEW.md)和[问题反馈](https://github.com/xuanyue202/codex-auto-retry/issues)。

## 许可与致谢

采用 MIT 许可。恢复架构和桌面 IPC 帧协议改编自 [sybxxx/codex-auto-retry](https://github.com/sybxxx/codex-auto-retry)，[LICENSE.upstream](LICENSE.upstream) 保留其版权声明。本项目独立于 OpenAI。
