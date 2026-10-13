# ADR-0007: Allow structured failure output on task exceptions

**Date**: 2026-10-13
**Status**: proposed
**Deciders**: Perago maintainers

## Context

ADR-0005 uses exceptions for execution failure and initially restricts failed results to status plus a string reason. A bounded reason cannot reliably carry task-owned error codes, original provider identities or structured diagnostic fields. Reimplementing native startup/IPC to bypass that restriction would duplicate runtime responsibilities.

## Decision

Keep task return values as successful Result Output and add optional keyword-only `output_data` to TaskFailed and TaskTerminalError. It is a finite JSON object copied at exception construction and transported by the existing execution classifier, RuntimeTaskResult, process IPC and SDK result mapping. It is written directly to Conductor outputData in the same update as the original failure status and reason.

Perago remains business-agnostic: it does not define error enums, inspect message text, infer retry permission or coerce metadata into the successful return model. reason stays required and bounded independently of metadata. None keeps the existing reason-only result; an empty object is preserved. Invalid Python values are rejected without echoing their contents.

Only the failed-output restriction in ADR-0005 is superseded. Exceptions still determine status, successful task schemas stay strict, and failed workspace attempts do not stage or publish changes. Native supervisor, fencing, cancellation and retry scheduling are unchanged. Conductor-owned failures or arbitrary runtime exceptions do not acquire fabricated metadata.

## Consequences

Task authors can publish stable failure information while using perago start unchanged. Consumers must use task status to distinguish failure metadata from successful Result Output, and authors must sanitize payloads. Output size remains subject to Conductor deployment limits; Perago's reason truncation does not truncate metadata.
