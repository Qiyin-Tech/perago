# Domain Docs

How the engineering skills should consume this repo's domain documentation when exploring the codebase.

## Before exploring, read these

- `CONTEXT.md` at the repo root for Perago's domain vocabulary.
- `docs/architecture/adr/` for accepted architecture decisions that touch the area being changed.

If any referenced file does not exist, proceed silently. Do not create domain docs upfront unless a skill explicitly resolves a new term or decision.

## Layout

This is a single-context repo. There is one root `CONTEXT.md`.

Architecture ADRs live under `docs/architecture/adr/` because that is the existing Sphinx documentation layout. Do not create a parallel `docs/adr/` tree for agent use.

`docs/agents/` contains agent configuration only. Do not add it to Sphinx toctrees or treat it as user-facing product documentation.

## Use the glossary's vocabulary

When output names a domain concept, use the term as defined in `CONTEXT.md`. Do not drift to synonyms the glossary explicitly avoids.

## Flag ADR conflicts

If output contradicts an existing ADR, surface it explicitly rather than silently overriding.
