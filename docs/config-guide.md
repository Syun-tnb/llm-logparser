# Config Guide (YAML/JSON)

Define field mappings, fallbacks, split policies, and runtime defaults without code changes.

## Example (YAML)

```yaml
schema_version: 1
provider: openai
mapping:
  conversation_id: $.conversation_id
  message_id: $.message_id
  ts: $.create_time | epoch_ms
  author_role: $.author.role
  author_name: $.author.name | null
  model: $.metadata.model | null
  content: $.content
split:
  by: size
  size_mb: 20
locale:
  lang: en-US
  timezone: Asia/Tokyo
```

The CLI applies: CLI args > env > user profile > defaults.
