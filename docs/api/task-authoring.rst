Task Authoring API
==================

这些 API 是任务作者日常使用的入口，用于声明 task、workspace guardrail 和 Conductor TaskDef。

.. currentmodule:: perago

.. autosummary::
   :toctree: generated/

   task
   TaskDefinition
   load_module_task
   WorkspaceSpec
   require_file
   require_dir
   require_glob
   forbid_glob
   check_guardrails
   build_taskdef
   write_taskdef
