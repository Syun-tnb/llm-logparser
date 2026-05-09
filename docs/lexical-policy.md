# Lexical Policy and Observed Token Artifacts

This document defines the current terminology boundary for lexical data. It is
intended to keep future CLI, Core API, and GUI operations aligned without
changing analyzer behavior.

## Current Artifacts

`l3/token-dictionary/observed_tokens.json`

- primary observed-token artifact
- generated from a provider root by `analyze token-dictionary`
- contains corpus/provider-root token statistics
- deterministic and rebuildable
- not active semantic policy

`l3/token-dictionary/dictionary.json`

- legacy alias for `observed_tokens.json`
- still readable for backward compatibility
- should not be used by new documentation or new workflows

`l3/token-dictionary/bundles.json`

- corpus cooccurrence evidence derived from observed tokens
- deterministic and rebuildable
- supporting evidence only
- not active semantic policy

`l3/token-dictionary/lexical_rules.json`

- generated seed / legacy policy-like sidecar
- not reviewed user/project policy
- should not be edited as the active policy surface

`l3/lexical-rules/candidates.jsonl`

- inactive lexical-rule suggestions
- generated from observed corpus facts and optional supporting evidence
- requires human review before use
- never activates rules automatically

## Read-Only Inspection Commands

Observed token facts can be listed and inspected without applying policy:

```bash
llm-logparser lexical observed list --input artifacts/openai
llm-logparser lexical observed inspect --input artifacts/openai --token "DALL-E"
```

These commands read `observed_tokens.json`, `bundles.json`, and
`provenance.json` when available. They fall back to legacy `dictionary.json` for
older artifact sets. Output is corpus-fact oriented: counts, conversation spread,
cooccurrence bundle evidence, and provenance/source summaries. It does not
classify, score, activate, or promote tokens.

Inactive lexical-rule candidates can also be listed and inspected:

```bash
llm-logparser lexical candidates list --input artifacts/openai
llm-logparser lexical candidates inspect --input artifacts/openai --candidate-id candidate_xxx
```

These commands read `l3/lexical-rules/candidates.jsonl` plus compact diagnostics
when present. They show candidate type, value, suggested rule path, review score,
evidence summary, and diagnostics context. They do not promote, reject, edit, or
write reviewed policy.

Reviewed project/user lexical YAML

- active editable lexical policy surface
- loaded only when explicitly supplied
- human-reviewed and additive above built-in resources
- not generated or promoted automatically

## Future Contract Stub: `user_lexical_profile`

`user_lexical_profile` is reserved for a future provider-crossing user-level
lexical memory. It is not implemented and is not wired into scoring.

Intended purpose:

- store reviewed user-level lexical preferences across provider roots
- preserve user-level lexical memory separately from corpus facts
- support future GUI/Core API operations without treating observed tokens as
  policy

Non-goals for the current implementation:

- no automatic promotion from observed tokens or candidates
- no scoring integration
- no GUI write workflow
- no replacement of reviewed project/user lexical YAML

Draft shape, for future discussion only:

```yaml
schema_version: "0.1"
profile_type: "user_lexical_profile"
owner_scope: "user"
rules:
  topic_summary:
    scoring:
      generic_tokens: []
      short_specific_tokens: []
      distinctive_allow_tokens: []
      distinctive_block_tokens: []
      weak_distinctive_tokens: []
      persona_weak_tokens: []
      tool_residue_patterns: []
      citation_residue_patterns: []
      ritual_title_phrases: []
```

The future profile must remain reviewed and explicit. Generated candidates must
continue to follow candidate -> diagnostics -> review -> promote; no automatic
adoption is allowed.
