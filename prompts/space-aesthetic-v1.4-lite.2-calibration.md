## V1.4 Lite.2 等级上限校准规则

本节优先级高于 `space_aesthetic_dimensions_v1.4-lite.1` 中所有冲突规则。

- `casual_snapshot.status=yes` 时，最终等级最高为 `L2`。
- `image_quality.quality_severity` 为 `slight|moderate|severe|unusable` 时，视为画质受损，最终等级最高为 `L2`。
- 严重或不可用画质仍可由服务端应用更严格的 `L1` 上限；多个限制同时命中时取最低等级。
- 在 `decision_rules.level_cap` 和 `level_cap_reasons` 中如实输出对应限制，但最终等级始终以服务端评分引擎为准。
- 输出中的 `prompt_version` 使用 `space_aesthetic_dimensions_v1.4-lite.2`。
