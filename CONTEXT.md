# Perago

Perago is an internal task runtime context for typed Python workers that execute Conductor tasks over versioned workspaces.

## Language

**Task Module**:
A Python module that declares exactly one Perago task worker.
_Avoid_: app, registry module, multi-task worker file

**Task Worker**:
The typed Python function plus Perago task metadata that defines one Conductor task type.
_Avoid_: route, endpoint, handler app

**Task Contract**:
The input and output shape of a Task Worker as derived from its Python function signature.
_Avoid_: duplicated params declaration, duplicated output declaration

**Task Controls**:
The Perago configuration object that maps Conductor TaskDef retry, timeout, and execution-limit controls.
_Avoid_: task contract, business params, workflow input

**Task Function Signature**:
The required Python callable shape for a Task Worker, either `workspace` plus `params` or `params` only, with a typed Pydantic return value.
_Avoid_: arbitrary callable, variadic args, keyword-only contract

**Workspace Input**:
The Conductor input field that identifies the LakeFS repository, writable branch, and immutable commit ref Perago should expose as a local path.
_Avoid_: business parameter, params field, workspace prefix

**Workspace Output**:
The Conductor output field that carries the LakeFS repository, writable branch, and immutable commit ref after a Workspace Task Worker completes successfully.
_Avoid_: business result, params field, workspace prefix

**Params Input**:
The Conductor input field that carries the business payload for a Task Worker.
_Avoid_: top-level business fields, workspace metadata

**Result Output**:
The Conductor output field that carries the business return value from a Task Worker.
_Avoid_: workspace metadata, top-level business fields

**Workspace Prefix**:
The task-declared LakeFS path prefix that Perago exposes as the local workspace root for one Task Worker.
_Avoid_: workflow input, workflow output, runtime credential

**Workspace Guardrail**:
A task-declared file-system expectation over the local workspace root exposed by a Workspace Prefix.
_Avoid_: business validator, schema validator, data transformer

**Workspace Access Mode**:
The task-declared workspace intent that says whether a Workspace Task Worker may publish workspace changes.
_Avoid_: inferred write mode, runtime guess

**Read-Only Workspace Task Worker**:
A Workspace Task Worker whose Workspace Access Mode forbids workspace publication.
_Avoid_: workspace-free task, fake workspace

**Workspace Path**:
A relative logical path inside the local workspace root exposed by a Workspace Prefix.
_Avoid_: absolute path, process working directory path, host-specific storage path

**Attempt Workspace**:
The local file-system directory used by one Task Attempt while executing a Workspace Task Worker.
_Avoid_: shared worker workspace, task-type workspace, process workspace

**Workspace Branch**:
The LakeFS branch a Workspace Task Worker writes to when it succeeds.
_Avoid_: immutable input version, temporary branch

**Protected Workspace Branch**:
A Workspace Branch that accepts Perago workspace updates only through LakeFS merge.
_Avoid_: direct object writes, direct branch commit, business lock

**Workspace Ref**:
The immutable LakeFS commit ref a Workspace Task Worker reads as its deterministic input version.
_Avoid_: mutable branch name, latest branch head

**Task Attempt**:
One Conductor execution attempt for a Task Worker within a workflow instance.
_Avoid_: logical task, worker process

**Logical Task Key**:
The stable identity of the workflow step a Task Attempt belongs to across retries.
_Avoid_: task id, worker id, process id

**Workspace Transaction**:
The Perago-managed publication boundary that turns a local workspace result into a committed Workspace Output.
_Avoid_: direct branch write, business transaction

**No-Op Workspace Completion**:
A successful Workspace Task Worker completion whose Workspace Output carries the same Workspace Ref as its Workspace Input.
_Avoid_: empty commit, fake success

**Abandoned Workspace Publication**:
A LakeFS workspace update left behind by a Task Attempt that Conductor did not accept as successful.
_Avoid_: recoverable success, durable task result

**Replacement Workspace Publication**:
A workspace publication that supersedes an Abandoned Workspace Publication while keeping the new visible commit parented by the original Workspace Ref.
_Avoid_: ordinary merge, recovery success

**Serial Workspace Workflow**:
A workflow shape where Workspace Task Workers that write the same Workspace Branch are ordered and never run in parallel.
_Avoid_: parallel workspace writers, fan-out writers

**Single Active Workspace Workflow**:
The invariant that only one workflow instance may write a given Workspace Branch at a time.
_Avoid_: duplicate workspace workflow, concurrent rerun

**Staging Branch**:
A short-lived LakeFS branch used to isolate one Workspace Transaction before it is published.
_Avoid_: target branch, workflow branch, permanent branch

**Attempt Fence**:
The check that a Task Attempt is still the current Conductor attempt before Perago may publish its Workspace Transaction.
_Avoid_: retry policy, timeout setting

**Publish Fence**:
The check that the target Workspace Branch is still in a state where a Workspace Transaction may be published.
_Avoid_: merge strategy, schema validation

**Operational Publish Window**:
The short interval after an Attempt Fence during which Perago assumes no other actor advances the same Workspace Branch.
_Avoid_: distributed lock, compare-and-swap guarantee

**Publish Budget**:
The operational time bound Perago assumes for confirming a Workspace Transaction through LakeFS and reporting completion to Conductor.
_Avoid_: hard transaction guarantee, exact worst-case duration

**Workspace Task Worker**:
A Task Worker that receives a versioned workspace and produces a Workspace Output.
_Avoid_: task with optional workspace

**Workspace-Free Task Worker**:
A Task Worker that only receives business params and produces business result.
_Avoid_: empty workspace, fake workspace

**Worker Process**:
An operating-system process running one Task Worker and polling Conductor independently.
_Avoid_: internal worker slot, process-pool worker

**Worker ID**:
The runtime identity assigned to one Worker Process for logging and Conductor polling.
_Avoid_: task id, process id, logical task key

**Worker ID Prefix**:
The deployment-configured prefix used by a Worker Supervisor when assigning Worker IDs.
_Avoid_: worker id, task id, task name

**Worker Supervisor**:
The parent process started by `perago start -j` that launches and restarts Worker Processes.
_Avoid_: internal task scheduler, shared task pool

**Module Target**:
A Python import path that identifies one Task Module.
_Avoid_: file path, object path, module:app target

## Relationships

- A **Task Module** contains exactly one **Task Worker**.
- A **Task Worker** exposes exactly one **Task Contract**.
- A **Task Worker** may declare **Task Controls**.
- A **Task Contract** is derived from the **Task Function Signature**.
- A **Workspace Task Worker** receives external Conductor input as one **Workspace Input** plus one **Params Input**.
- A **Workspace Task Worker** emits external Conductor output as one **Workspace Output** plus one **Result Output**.
- A **Workspace-Free Task Worker** receives external Conductor input as one **Params Input**.
- A **Workspace-Free Task Worker** emits external Conductor output as one **Result Output**.
- A **Workspace Prefix** belongs to a **Task Worker**, not to **Workspace Input** or **Workspace Output**.
- A **Workspace Guardrail** belongs to a **Workspace Task Worker** and is scoped to that worker's **Workspace Prefix**.
- A **Workspace Guardrail** is declared over one or more **Workspace Paths**.
- A **Workspace Guardrail** is checked against local workspace files, not against **Task Contract** schemas.
- A **Workspace Task Worker** has one **Workspace Access Mode**.
- A **Read-Only Workspace Task Worker** may read a **Workspace Input** but must not publish a new **Workspace Ref**.
- An **Attempt Workspace** belongs to exactly one **Task Attempt**.
- A **Workspace Input** carries one **Workspace Branch** and one **Workspace Ref**.
- A **Workspace Output** carries the same **Workspace Branch** and either the same or a new **Workspace Ref** after successful completion.
- A **Workspace Task Worker** may complete as a **No-Op Workspace Completion**.
- A **Protected Workspace Branch** is a **Workspace Branch** guarded against direct workspace writes.
- A **Task Attempt** belongs to one **Logical Task Key**.
- Multiple **Task Attempts** may exist for the same **Logical Task Key** when Conductor retries a task.
- A **Workspace Transaction** belongs to one **Task Attempt**.
- A **Workspace Transaction** uses one **Staging Branch** before it can update the target **Workspace Branch**.
- A **Workspace Transaction** must pass an **Attempt Fence** and a **Publish Fence** before producing a **Workspace Output**.
- A **Publish Fence** relies on an **Operational Publish Window** rather than a distributed lock.
- A **Task Attempt** may leave an **Abandoned Workspace Publication** if Conductor does not accept the attempt as successful.
- An **Abandoned Workspace Publication** is not a **Workspace Output**.
- A **Replacement Workspace Publication** may replace an **Abandoned Workspace Publication** without making the abandoned commit the parent of the new visible **Workspace Ref**.
- A **Publish Budget** sizes Conductor response timeout, LakeFS merge request timeout, Conductor completion budget reserve, heartbeat interval, and worker shutdown grace around a **Publish Fence**.
- A **Serial Workspace Workflow** orders all Workspace Task Workers that write the same **Workspace Branch**.
- A **Single Active Workspace Workflow** prevents duplicate workflow instances from writing the same **Workspace Branch**.
- A **Task Worker** is either a **Workspace Task Worker** or a **Workspace-Free Task Worker**.
- A **Worker Process** loads exactly one **Task Module**.
- A **Worker Process** has one **Worker ID**.
- A **Worker ID** starts with one **Worker ID Prefix**.
- A **Worker ID** belongs to one running **Worker Process** at a time.
- Multiple **Worker Processes** may load the same **Task Module**.
- Each **Worker Process** polls Conductor independently for the **Task Worker** it loaded.
- A **Worker Supervisor** manages Worker Process lifetime but does not assign tasks to Worker Processes.
- Perago CLI commands receive a **Module Target** to locate a **Task Module**.

## Example dialogue

> **Dev:** "Can this file define both `features.build` and `model.train`?"
> **Domain expert:** "No — split them into separate **Task Modules** so each **Worker Process** has one **Task Worker** to run."

## Flagged ambiguities

- "app" and "registry" were used in earlier sketches, but the resolved model is a single-task **Task Module**, not a FastAPI-style multi-task application object.
- "worker" may mean either **Task Worker** or **Worker Process**; use the precise term when the distinction matters.
- CLI target syntax is a **Module Target** such as `app.workers.features_build`; file paths, object paths, and `module:app` targets are out of scope for the MVP.
- `params` and `output` must not be duplicated in task metadata; the **Task Contract** comes from the function signature.
- **Task Controls** configure Conductor execution behavior; they do not define the **Task Contract**.
- A **Task Worker** must use the standard **Task Function Signature**; alternate argument names and extra injected parameters are out of scope for the MVP.
- Business input belongs under **Params Input**; versioned workspace identity belongs under **Workspace Input**.
- Business output belongs under **Result Output**; committed workspace identity belongs under **Workspace Output**.
- **Workspace Input** and **Workspace Output** carry repository, **Workspace Branch**, and **Workspace Ref**; the **Workspace Prefix** is task metadata declared in code.
- **Workspace Task Worker** must not be read as a guaranteed workspace writer; it may complete without changing the **Workspace Ref**.
- A **Workspace Path** must stay inside the local workspace root exposed by a **Workspace Prefix**.
- An **Attempt Workspace** must not be reused across **Task Attempts**, **Task Workers**, or **Worker Processes**.
- A **Workspace Guardrail** is a local file-shape check; it is not a data transformation, TaskDef schema rule, or cross-repository scan.
- A **Read-Only Workspace Task Worker** is still a **Workspace Task Worker**, not a **Workspace-Free Task Worker**.
- A **Protected Workspace Branch** prevents direct workspace writes; it is not a business-level lock service.
- A **Workspace-Free Task Worker** must not receive fake workspace data.
- A **Worker Supervisor** may restart failed Worker Processes, but it must not become a task scheduler.
- In supervisor-managed runs, the **Worker Supervisor** concatenates a **Worker ID Prefix** and child slot index to assign **Worker IDs** to **Worker Processes**.
- A **Worker ID Prefix** must be alphanumeric and must not contain punctuation, separators, or whitespace.
- The default **Workspace Prefix** is `/`; prefixes must not escape the LakeFS repository.
- A Conductor `taskId` identifies a **Task Attempt**, not a **Logical Task Key**; retries may create new attempts for the same workflow step.
- A **Staging Branch** is not workflow data and must not be exposed as **Workspace Input** or **Workspace Output**.
- A **Workspace Transaction** is runtime publication control around workspace data; it does not make the business function implement TCC methods.
- A **Publish Budget** is an accepted MVP operating assumption, not proof that stale publication is impossible.
- An **Operational Publish Window** is an accepted operating assumption, not a compare-and-swap guarantee.
- Parallel Workspace Task Workers writing the same **Workspace Branch** are outside the Perago workflow model.
- Duplicate workflow instances writing the same **Workspace Branch** are outside the Perago workflow model.
