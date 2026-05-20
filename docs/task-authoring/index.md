# Task Authoring

Task authoring 文档面向编写 Perago task module 的开发者。这里的页面按一个新任务从函数签名、workspace 声明、Pydantic 契约、guardrail 到 TaskDef 生成的顺序组织。

先读 workspace task。如果任务不需要 LakeFS workspace，后续再读 workspace-free task。API 精确签名见 [Task authoring API](../api/task-authoring)。

```{toctree}
:maxdepth: 1

workspace-task
workspace-free-task
pydantic-contracts
guardrails
controls-and-taskdef
```
