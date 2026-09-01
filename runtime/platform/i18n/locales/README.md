# Locale files

YAML key-value files. Keys are dot-namespaced.

- `en.yaml` -- English (default / fallback)
- `zh-CN.yaml` -- Simplified Chinese
- `ja.yaml` -- Japanese (initial = en, translate as needed)
- `ko.yaml` -- Korean (initial = en, translate as needed)

## Plural forms

Use `key_one`, `key_other`, `key_many` suffixes for plural-aware strings.
Example:
```yaml
cli.usage.count: "{n} tokens used"
cli.usage.count_other: "{n} tokens used"
cli.usage.count_many: "{n} tokens used"
```
Call with `t('cli.usage.count', count=n)` to get the right form.
