# Glossary

本页定义 Perago 文档中的固定术语。`避免使用` 表示容易混淆、在 Perago 语境中不宜替代固定术语的说法。

## Task language

### Task Module

一个 Python module，且只声明一个 Perago task worker。

避免使用：app、registry module、multi-task worker file。

### Task Worker

由 typed Python function 和 Perago task metadata 共同定义的一个 Conductor task type。

避免使用：route、endpoint、handler app。

### Task Contract

从 Python 函数签名推导出的输入和输出结构。

避免使用：duplicated params declaration、duplicated output declaration。

### Task Controls

映射到 Conductor TaskDef retry、timeout 和 execution-limit 字段的 Perago 配置对象。

避免使用：task contract、business params、workflow input。

### Task Function Signature

Task worker 必须使用的 Python callable 签名：workspace task 接收 `workspace` 和 `params`，workspace-free task 只接收 `params`，并返回 typed Pydantic model。

避免使用：arbitrary callable、variadic args、keyword-only contract。

## Contract fields

### Workspace Input

Conductor input 中标识 LakeFS repository、writable branch、immutable commit ref 的字段。Perago 会把它暴露成本地路径。

避免使用：business parameter、params field、workspace prefix。

### Workspace Output

Conductor output 中携带成功提交后的 LakeFS repository、writable branch、immutable commit ref 的字段。

避免使用：business result、params field、workspace prefix。

### Params Input

Conductor input 中承载业务 payload 的字段。

避免使用：top-level business fields、workspace metadata。

### Result Output

Conductor output 中承载业务返回值的字段。

避免使用：workspace metadata、top-level business fields。

## Workspace language

### Workspace Prefix

task 在代码里声明的 LakeFS path prefix。Perago 把这个 prefix 暴露成本地 workspace root。

避免使用：workflow input、workflow output、runtime credential。

### Workspace Check

task 声明的本地 workspace 文件检查。源码和公开 API 中仍使用 `guardrail` 命名，例如 `require_file`、`require_glob` 和 `check_guardrails`。

避免使用：business validator、schema validator、data transformer。

### Workspace Path

相对于本地 workspace root 的逻辑路径。

避免使用：absolute path、process working directory path、host-specific storage path。

### Attempt Workspace

一次 task attempt 执行 workspace task worker 时使用的本地文件系统目录。

避免使用：shared worker workspace、task-type workspace、process workspace。

### Workspace Branch

workspace task worker 成功时写入的 LakeFS branch。

避免使用：immutable input version、temporary branch。

### Protected Workspace Branch

只接受 Perago 通过 LakeFS merge 更新的 workspace branch。

避免使用：direct object writes、direct branch commit、business lock。

### Workspace Ref

workspace task worker 读取的不可变 LakeFS commit ref。

避免使用：mutable branch name、latest branch head。

## Runtime language

### Task Attempt

Conductor 对一个 task worker 的一次执行 attempt。

避免使用：logical task、worker process。

### Logical Task Key

workflow step 跨重试保持稳定的身份。

避免使用：task id、worker id、process id。

### Workspace Transaction

Perago 管理的发布边界，用于把本地 workspace 结果变成 committed workspace output。

避免使用：direct branch write、business transaction。

### Serial Workspace Workflow

所有写入同一个 workspace branch 的 workspace task worker 都按顺序执行，不能并行。

避免使用：parallel workspace writers、fan-out writers。

### Single Active Workspace Workflow

同一时刻只有一个 workflow instance 可以写入某个 workspace branch。

避免使用：duplicate workspace workflow、concurrent rerun。

### Staging Branch

发布前隔离一次 workspace transaction 的短生命周期 LakeFS branch。

避免使用：target branch、workflow branch、permanent branch。

### Attempt Fence

Perago 在发布 workspace transaction 前检查当前 task attempt 是否仍然是 Conductor 的当前 attempt。

避免使用：retry policy、timeout setting。

### Publish Fence

Perago 在发布 workspace transaction 前检查目标 workspace branch 是否仍处于可发布状态。

避免使用：merge strategy、schema validation。

### Publish Budget

Perago 为 LakeFS merge、Conductor completion、heartbeat 和 worker shutdown 预留的操作性时间边界。

避免使用：hard transaction guarantee、exact worst-case duration。

### Workspace Task Worker

接收并提交 versioned workspace 的 task worker。

避免使用：task with optional workspace。

### Workspace-Free Task Worker

只接收 business params 并只返回 business result 的 task worker。

避免使用：empty workspace、fake workspace。

### Worker Process

加载一个 task module 并独立 poll Conductor 的操作系统进程。

避免使用：internal worker slot、process-pool worker。

### Worker ID

运行时给一个 worker process 分配的身份，用于日志和 Conductor polling。

避免使用：task id、process id、logical task key。

### Worker ID Prefix

部署时配置的前缀，worker supervisor 用它生成 worker IDs。

避免使用：worker id、task id、task name。

### Worker Supervisor

`perago start -j` 启动的父进程，负责启动和重启 worker processes。

避免使用：internal task scheduler、shared task pool。

### Module Target

用于定位一个 task module 的 Python import path。

避免使用：file path、object path、module:app target。
