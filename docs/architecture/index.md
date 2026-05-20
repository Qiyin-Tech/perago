# Architecture

Architecture 文档记录 Perago runtime 选择当前事务、fence 和故障恢复模型的原因。这里不重复 task authoring 和 runtime 页面的操作步骤，而是解释这些步骤为什么存在、它们能保证什么，以及哪些边界仍然是 MVP 的显式取舍。

```{toctree}
:maxdepth: 1

transaction-model
publish-fences
adr/index
```
