# LakeFS 发布协议

本协议定义 Perago worker 如何把 workspace 结果按需发布到 LakeFS。Python task、TS 节点和人工节点开发者在实现 workspace 读取、no-op completion 或发布前都应阅读本页。

## 核心规则

Conductor 负责判断 task 是否成功。LakeFS 只保存 workspace 状态。

如果 LakeFS 已经被更新，但 Conductor 没有接受该 task 为 `COMPLETED`，这次 LakeFS 更新就是废弃发布。Perago 不把它恢复成成功 task result。

LakeFS commit 只表达 workspace 内容变化。节点执行成功这个事实属于 Conductor result 和 worker 日志，不通过 empty commit 记录。

## 输入

每个 workspace task 都会收到：

| 字段 | 必填 | 含义 |
| --- | --- | --- |
| `repository` | 是 | 当前 workflow / song 对应的 LakeFS repo。 |
| `branch` | 是 | 目标 workspace branch，通常是 `main`。 |
| `ref_type` | 是 | 必须是 `commit`。 |
| `ref` | 是 | 不可变输入 commit，下文称为 `input_ref`。 |

`input_ref` 来自 Conductor input，不来自 LakeFS metadata。

`WorkspaceSpec(read_only=True)` 来自 task declaration，不来自 Conductor input。read-only task 成功时不检查 target branch HEAD、不创建 staging branch、不提交 LakeFS commit，output `workspace.ref` 保持为 input `ref`。这里跳过的是 LakeFS publication / no-op branch relocation 相关 fence；最终 task result 仍按普通 Conductor worker completion 回写，是否接受旧 `task_id` 的 completion 由 Conductor 服务端处理。

## 可写 no-op completion

`read_only=False` 的 workspace task 如果执行后没有 workspace diff，Perago 不会创建 LakeFS empty commit。此时 worker 仍要保持 output ref 与 target branch 可见 head 一致：

| LakeFS 状态 | 运行时操作 | Output ref |
| --- | --- | --- |
| `H == A` | 直接完成，不创建 staging branch。 | `A` |
| `parent(H) == A` | 把 `H` 当作废弃发布，并 hard-reset / relocate target branch 回 `A`。 | `A` |
| 其他状态 | fail closed。 | 不生成 workspace output |

不要把 abandoned publication `H` 包装成本次 no-op attempt 的成功 output。

## 发布步骤

设：

```text
A = input_ref
H = target branch 当前 head
C = 本次 attempt 的 staging commit
```

当 `read_only=False` 且 workspace diff 非空时，worker 必须：

1. 在 attempt-local workspace 中运行 task body。
2. 检查 Conductor attempt fence。
3. 从 `A` 创建 staging branch。
4. 把本机 workspace 投影同步到 staging branch，并 commit 成 `C`。
5. 再次检查 Conductor attempt fence。
6. 读取 target branch head `H`。
7. 按下面的目标分支状态发布。
8. 尽力删除 staging branch。
9. 回报 Conductor result。

## 目标分支状态

| LakeFS 状态 | 运行时操作 | 最终可见历史 |
| --- | --- | --- |
| `H == A` | 将 staging branch merge 到 target branch。 | `A -> published` |
| `parent(H) == A` | 把 `H` 当作废弃发布，并 hard-reset / relocate target branch 到 `C`。 | `A -> C` |
| 其他状态 | fail closed。 | 不发布 workspace output。 |

替换发布不能产生：

```text
A -> H -> C
```

必须产生：

```text
A -> C
```

## Soft Fence

采用 operational soft fence；不使用分布式锁。

Perago 先检查 Conductor，再检查 LakeFS HEAD 状态，然后执行一个很短的 LakeFS publish 操作。协议不要求 LakeFS 提供 target branch 的 server-side compare-and-swap。

协议依赖这些运营规则：

- 同一个 repository / branch 的 workspace 写入必须串行；
- 同一时间只能有一个活跃 workflow instance 写同一个 repository / branch；
- retry 必须通过 Conductor，不允许直接绕过 Conductor 写 LakeFS；
- 人工节点和 TS 节点也必须遵守同一套 Conductor 权限边界；
- 最终 LakeFS 操作必须保持短窗口。

## Metadata

不需要任何 commit metadata。

commit message 可以写 workflow 或 task 标识，方便人工排查；发布权限只来自 Conductor 和 HEAD 状态。

## Cleanup

staging branch cleanup 属于 task publish 路径。

无论 merge、reset、publish 失败还是 stale attempt，只要 staging branch 已创建，worker 都应该尽力删除自己的 staging branch。cleanup 失败只记录日志，不能覆盖原始 task result。

废弃 target commit 不直接删除。replacement publish 之后，废弃 commit 不应再从 target branch 可见。workflow-end 或运维侧可以按 LakeFS retention / GC 策略清理不再可达的物理对象；这不属于 Perago task protocol。

## 必须失败的情况

以下情况必须 fail closed：

- Conductor attempt 状态不等于 `IN_PROGRESS`；
- 当前 Conductor attempt identity 与 worker 持有的 attempt 不匹配；
- 可写 task 的 target `HEAD` 不等于 `input_ref`，并且 `parent(HEAD)` 不等于 `input_ref`；
- 生成 workspace output 前，LakeFS merge、hard reset 或必要前置条件失败。

不要把不确定的 LakeFS 状态包装成成功的 Conductor result。

## 验证入口

旧的 `scripts/real_conductor_lakefs_smoke.py` 只覆盖真实 Conductor + LakeFS 的 happy path。发布协议本身用专门的真实 LakeFS smoke 覆盖：

```bash
uv run python scripts/real_lakefs_publication_protocol_smoke.py
```

该脚本从 `.env` 读取 LakeFS 配置，每次创建独立 target branch，并验证：

- `H == A` 时通过 merge 发布 workspace output；
- `parent(H) == A` 时通过 hard reset 替换废弃发布，最终 target branch 指向本次 staging commit；
- `parent(H) != A` 时 fail closed，target branch head 不变，workspace output 不发布；
- no-op completion 中 Perago 不会创建 empty commit，并在 abandoned publication 场景把 target branch relocate 回 input ref；
- merge 或 hard reset 的必要 LakeFS 前置条件失败时不返回成功，target branch head 不变；
- 每条路径都尽力删除本次创建的 staging branch 和 target branch。

Conductor attempt fence、stale attempt 后 cleanup、cleanup 失败不覆盖原始结果由 `tests/test_execution.py` 覆盖。
