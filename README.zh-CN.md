# Codex Auto Retry · macOS

**离开电脑之后，让 Codex 在满载中断后继续干活。**

[![Tests](https://github.com/xuanyue202/codex-auto-retry/actions/workflows/test.yml/badge.svg)](https://github.com/xuanyue202/codex-auto-retry/actions/workflows/test.yml)
[![MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)
[English](README.md) · [简体中文](README.zh-CN.md)

让 Codex 跑一个长任务，回来却发现它早已停在：
**“Selected model is at capacity.”**，一直等你手动重试。

这个小工具在后台识别这类错误，稍等片刻后，让**原来的 Codex 桌面会话继续**。
模型、工作目录和权限沿用该会话的设置。无需 API Key、新账号或额外 Python 依赖。

```text
模型满载 → 等 1 秒 → 原会话继续
再次满载 → 等 2 秒 → 4 秒 → 8 秒 → 16 秒 → 到上限就停
你发消息或取消 → 撤掉等待中的重试
```

*以上为行为示意。等待从检测到错误时算起，扫描和桌面路由另有耗时；它不能绕过上游容量限制，也不保证重试一定成功。*

## 一句话安装

在**需要安装的那台 Mac 上**，把这句话发给 Codex：

```text
请读取并按 https://raw.githubusercontent.com/xuanyue202/codex-auto-retry/v0.1.0/INSTALL.md 安装 Codex Auto Retry。
```

Codex 会检查兼容性、安装后台服务并核对健康状态。保持 Codex 桌面应用运行。
安装本身只做只读兼容检查，不提交模型请求，不重放过去的失败任务。

也可以用终端安装，先满足[运行要求](#运行要求)：

```sh
curl -fsSL https://raw.githubusercontent.com/xuanyue202/codex-auto-retry/v0.1.0/install.sh | sh
```

安装器固定到版本标签；可先下载或[阅读脚本](install.sh)再运行。无需 `sudo`，不修改 shell 配置。

## 它帮你省掉什么？

| 情况 | 工具会怎么做 |
| --- | --- |
| 长任务以结构化满载错误结束 | 短暂等待后续接原会话 |
| 连续满载 | 15 分钟内最多重试 5 次 |
| 你发了新消息，或按了取消 | 撤掉等待中的重试 |
| 会话跨天、上下文压缩后分文件 | 合并检查仍在使用的生命周期记录 |
| 桌面已接收请求，但结果不确定 | 观察是否出现新回合，不盲目补发 |
| 想暂停或卸载 | 都有对应命令 |

支持普通会话和仍处于 active 状态的原生 Goal；不会重新激活已暂停或完成的 Goal，
不会重置目标和预算。旧 Goal 之后由你发起的新普通请求会单独处理。

## 随时控制

```sh
"$HOME/.local/bin/codex-retry" status      # 启用、进程、心跳、待处理项
"$HOME/.local/bin/codex-retry" doctor      # 只读检查桌面接口
"$HOME/.local/bin/codex-retry" disable     # 暂停，重启后仍有效
"$HOME/.local/bin/codex-retry" enable      # 监听未来错误，不补跑旧任务
"$HOME/.local/bin/codex-retry" stop        # 暂停并卸载后台服务
"$HOME/.local/bin/codex-retry" start       # 加载服务，保留启用/暂停偏好
"$HOME/.local/bin/codex-retry" uninstall   # 移除服务与启动器，保留本机数据
```

若 PATH 已包含 `~/.local/bin`，也可以直接运行 `codex-retry status`。

服务在登录后自动启动，覆盖本机 Codex 桌面持有的符合条件的会话，跨工作目录生效。
更新时重新运行目标版本的安装器。已有设置、扫描位置和去重记录会保留，支持从之前的私人版原位迁移。
存在待重试任务或结果不明的发送时，升级会停止，等核对清楚再继续。[安装细节](INSTALL.md)。

## 运行要求

- macOS，已登录图形界面；Python **3.10+**、git、curl。
- 同一台 Mac 上运行的 Codex **桌面应用**，且桌面仍持有目标会话。
- 能读取本机生命周期日志和 IPC socket，默认在 `~/.codex`；首次安装支持 `CODEX_HOME`。

它不是 Codex CLI 插件。任务运行在哪台 Mac，工具就需要装在哪台 Mac。
IPC 是内部接口，桌面应用更新后可能变化。`doctor --thread-id <id>` 可只读检查特定会话的持有者。

## 隐私与边界

完全本机运行，无遥测。程序临时读取生命周期记录，丢弃对话和工具文本；
本机私有状态只保存 ID、路径、时间、计数、结果分类和 Goal 版本哈希。
不保存提示词、凭据或模型原文。[数据流与保护规则](docs/SAFETY.md)。

- 仅自动处理结构化 `server_overloaded` / `serverOverloaded`。配额、计费、登录、权限、政策和未知错误不自动处理。
- 不换模型、不重放提示词、不用 CLI resume、不改全局 Codex 配置。
- 首次安装与重新启用，从日志当前位置开始，不自动续跑旧错误。
- 收到 IPC 回执不代表已开始执行。30 秒未观察到新回合，会记录 `start_not_observed`，不会补发。
- 桌面失去会话持有权、内部接口变化，以及最后检查到启动之间的竞态仍可能发生。
  串行查询会带来等待；恢复后的模型仍需核对结果不确定的外部操作。

## 原理

```text
本机生命周期日志 → 精确识别满载 → 有上限的重试队列
                               ↓
                    复核当前回合和 Goal 状态
                               ↓
                   先记录意图 → 原桌面会话续接
                               ↓
                    观察新回合，或报告结果未知
```

运行时只依赖 Python 标准库。版本文件、配置、状态、回执存放在
`~/Library/Application Support/CodexAutoRetryMac/`；唯一的用户 LaunchAgent 为
`com.local.codex-capacity-retry`。卸载后保留这些本机数据，方便核对；不用时可自行移除。

## 验证与贡献

```sh
git clone https://github.com/xuanyue202/codex-auto-retry.git
cd codex-auto-retry
python3 -m unittest discover -s tests -v
```

CI 使用合成日志和假 socket，不调用模型。可选的[同会话集成测试](docs/VERIFICATION.md)
通过隔离镜像注入错误，再做一次真实续接；这与真实上游满载恢复是两种证据。
[贡献指南](CONTRIBUTING.md) · [反馈问题](https://github.com/xuanyue202/codex-auto-retry/issues/new/choose)。

## 致谢

恢复架构和桌面 IPC 帧协议改编自 [sybxxx/codex-auto-retry](https://github.com/sybxxx/codex-auto-retry)，
补充了 macOS 服务管理及生命周期保护。[MIT](LICENSE)，保留[上游许可](LICENSE.upstream)。
本项目独立开发，非 OpenAI 官方项目。
