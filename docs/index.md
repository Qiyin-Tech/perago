# Perago

Perago 是一个面向 Conductor worker 的 typed Python 运行时层。它把 task module、Pydantic 输入输出契约、Conductor TaskDef、LakeFS workspace 下载与按需发布，以及运行时 guardrail 校验收敛到同一套模型中。

## 适用范围

Perago 适合下面这类任务：

- 用 Python 函数实现 Conductor task。
- 需要把输入输出约束建模为 Pydantic schema。
- 需要在版本化 LakeFS workspace 上读取，或读取、修改并发布结果。
- 需要把本地校验、TaskDef 生成和 worker 启动放进一条稳定流程。

## 核心模型

- `task module`：一个 Python module 暴露一个 Perago task。
- `workspace task`：函数签名是 `(workspace: Path, params: ParamsModel) -> OutputModel`，用于读取 LakeFS workspace，并在可写模式下按需发布变更。
- `workspace-free task`：函数签名是 `(params: ParamsModel) -> OutputModel`，不涉及 workspace publication。
- 三个核心命令：`perago check`、`perago extract`、`perago start`。

## 阅读路径

- {doc}`getting-started/index`：先跑通一个最小 task，理解 `check`、`extract`、`start`、TaskDef、controls、workspace 和 guardrail 的基本分工。
- {doc}`lakefs-publication-protocol`：理解 workspace task 成功、失败、retry 和 abandoned publication 的协议边界。
- {doc}`development`：维护 runtime、reference、architecture、concepts 和 API 文档。

## 目录

```{toctree}
:maxdepth: 2

getting-started/index
lakefs-publication-protocol
development
```
