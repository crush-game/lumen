# M0 基线证据：Poirot

> 状态：已执行  
> 记录日期：2026-09-21  
> 目的：在改造 Lumen 前冻结可追溯基线；本文件不代表 Lumen 功能已经实现。

## 代码来源与归属

- 工作目录：`D:\桌面\北师大\秋招\秋招项目\poirot`
- Git 远程：`https://github.com/HezaoHezao/poirot.git`
- 基线 commit：`86bf279ad90c180f0ba696755620dd7d6661465e`
- commit 时间：`2026-07-29T01:05:47+08:00`
- commit 信息：`docs: update README hero and architecture images`
- 许可证：`LICENSE` 声明为 MIT License，Copyright (c) 2026 Poirot Authors。
- 改造开始前 `git status --short` 无输出；本次 M0 只新增本证据文件，不修改 Agent 逻辑。

## 环境

- Python：`3.13.7`
- 项目要求：`pyproject.toml` 声明 `requires-python = ">=3.12"`。
- 为运行开发测试，已执行：`python -m pip install -e '.[dev]'`。
- 安装后执行 `python -m pip check`：`No broken requirements found.`

## 已核验的调用链

| 关注点 | 源码位置 | 当前观察 |
| --- | --- | --- |
| 单次入口 | `poirot/backend/app/bootstrap.py::AppRuntime.run_question` | 创建 Run，调用 `LeaderAgent.run`，正常返回后直接调用 `mark_success` |
| Run 记录 | `poirot/backend/agents/runtime/run_manager.py` | `RunRecord` 写为 JSON，但 Manager 的记录/上下文主要保留在进程内字典 |
| 图状态 | `poirot/backend/agents/runtime/checkpointer.py` | 当前实现为 `InMemorySaver` |
| 工具事件 | `poirot/backend/agents/middlewares/run_journal_middleware.py` | 记录 Agent、LLM、工具调用与工具结果摘要 |
| 停滞处理 | `poirot/backend/agents/middlewares/stall_detection_middleware.py` | 连续工具失败后请求帮助并结束当前图，尚未形成子 Run 恢复闭环 |

这些观察是 M1--M4 的改造依据；后续代码改动前需重新核对相关测试。

## 基线测试

在 `poirot/` 下执行：

```powershell
python -m pytest poirot/backend/tests/v1/unit/runtime/test_run_manager.py `
  poirot/backend/tests/v1/unit/runtime/test_checkpointer.py `
  poirot/backend/tests/v1/unit/runtime/test_run_journal.py `
  poirot/backend/tests/v1/unit/middlewares/test_run_journal_status.py `
  poirot/backend/tests/v1/unit/observability/test_stall_tracker.py
```

实际结果：

```text
platform win32 -- Python 3.13.7, pytest-9.1.1
collected 34 items
34 passed in 1.24s
```

## 环境异常与处理

1. 受限沙箱内首次运行 pytest 时，系统临时目录不可写，pytest 在收集前报 `No usable temporary directory`。该错误没有进入项目测试用例，不计为项目测试失败。
2. 在允许系统临时目录的环境中，首次测试收集曾报缺少刚安装的 `langchain_core`/`langgraph`；随后通过 `pip show` 确认依赖已安装、`pip check` 无冲突，并以同一命令重跑获得 34/34 通过。

因此，M0 的结论仅限于：该最小相关测试集在上述 Python 与依赖环境中通过。未运行模型调用、Docker、MCP、多 Agent 或端到端开发任务。

## M0 完成判断

- [x] 已记录远程、commit、许可证与依赖边界。
- [x] 已核验目标调用链并记录代码位置。
- [x] 已运行最小相关原始测试并保存结果。
- [x] 未启用 V1 范围外的模型、Docker、MCP 或多 Agent 功能。
- [ ] 未开始 M1；持久化状态机、完成契约与恢复能力仍未实现。
