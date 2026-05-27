# ADR-0003: 使用 soft-fenced LakeFS 发布协议

**日期**: 2026-05-21
**Status**: accepted
**Deciders**: Perago maintainers

> 后续补充：read-only workspace task 和 no-op completion 的规则见 [ADR-0004](0004-add-read-only-workspace-and-no-op-completion.md)。

## 背景

Perago workspace task 在 Conductor retry 语义下发布 LakeFS workspace 更新。一次 task attempt 可能已经更新 LakeFS，但没有成功向 Conductor 回报 `COMPLETED`。这种情况下，Conductor 仍然是 task 成功事实来源；LakeFS 上那次更新是 abandoned publication。

旧设计把 retry publication 判断放进 LakeFS 元数据。这个协议对 Python、TS 和人工节点共享实现来说过重。

## 决策

Perago 使用 [LakeFS 发布协议](../../lakefs-publication-protocol.md)。

协议不需要任何 commit metadata。发布权限来自：

- Conductor attempt fence；
- target branch HEAD 状态。

如果 target `HEAD == input_ref`，runtime merge staging branch。如果 `parent(HEAD) == input_ref`，runtime 把 `HEAD` 当作 abandoned publication，并 hard-reset / relocate target branch 到当前 staged commit。其他 HEAD 状态 fail closed。

staging branch 在 task publish 路径中尽力删除。Perago 不直接删除 abandoned target commit；workflow-end 或运维侧可以按 LakeFS retention / GC 策略清理不再可达的物理对象。

## 影响

协议足够小，可以给 Python worker、TS 节点和人工节点工具共同遵守。

Perago 不再使用 LakeFS commit metadata 做 retry 判断或恢复。commit message 仍可写人类可读标识用于排查。

该模型仍然是 operational soft fence。它依赖串行 workspace 写入、同一 repository / branch 只有一个活跃 workflow instance，以及短 LakeFS publish 窗口。它不证明 exactly-once publication。
