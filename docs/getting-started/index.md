# Getting Started

Perago 的上手路径分两步：先写一个 single-task Python module，再用 CLI 校验、导出 Conductor TaskDef，并在准备好 task 类型需要的外部服务配置后启动 worker。

本页只作为入口。具体规则分散在子页面中，避免把 workspace、Pydantic contract、guardrail、TaskDef、CLI 和失败语义混在同一个长页面里。

## 最短阅读路径

1. 如果 task 需要读取或发布 LakeFS workspace，先读 {doc}`workspace-task`。
2. 如果 task 不接收 workspace，只处理 typed params/result，先读 {doc}`workspace-free-task`。
3. 用 {doc}`commands` 跑 `perago check` 和 `perago extract`；准备好外部服务配置后再跑 `perago start`。
4. 需要收紧 contract、文件检查、TaskDef 控制字段或失败语义时，再进入对应专题页。

## 页面导览

| 你要解决的问题 | 阅读页面 |
| --- | --- |
| 写一个读写 LakeFS workspace 的 task | {doc}`workspace-task` |
| 写一个不依赖 workspace 的 task | {doc}`workspace-free-task` |
| 理解 `params` / `result` 的 Pydantic 规则 | {doc}`pydantic-contracts` |
| 声明输入输出文件检查 | {doc}`guardrails` |
| 配置 retry、timeout、execution limit、publish budget | {doc}`controls-and-taskdef` |
| 本地校验、导出 TaskDef、启动 worker | {doc}`commands` |
| 区分业务分支、retryable failure 和 terminal failure | {doc}`failure-signaling` |
| 直接看完整正例和反例 | {doc}`examples` |

```{toctree}
:maxdepth: 1

workspace-task
workspace-free-task
pydantic-contracts
guardrails
controls-and-taskdef
commands
failure-signaling
examples
```
