Runtime API
===========

这些 API 面向运行时集成和维护。普通任务作者通常不需要直接调用。

.. currentmodule:: perago

.. autosummary::
   :toctree: generated/

   run_workspace_task_attempt
   run_workspace_free_task_attempt
   invoke_workspace_task_body
   invoke_workspace_free_task
   build_workspace_task_output
   build_workspace_free_task_output
   StagedWorkspace
   WorkerRuntime
   prepare_worker_runtime
   WorkerChildSpec
   worker_child_specs
   restart_backoff_seconds
   assert_current_attempt_snapshot
