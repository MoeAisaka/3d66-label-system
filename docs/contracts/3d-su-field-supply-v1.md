# 3D/SU Field Supply Contract v1

This document records the currently implemented platform semantic fields for
the first 3D/SU slice. It is an implementation contract, not final downstream
sign-off. Adding a field requires a new versioned tag-demand contract; weakening
any quality gate requires explicit Owner approval.

| Field | Namespace | Whole | Single | Production method | Authority | Owner | SLA | Default gate | Rollback |
|---|---|---|---|---|---|---|---|---|---|
| `space` | semantic | required | not_applicable | hybrid | TPENG label platform | semantic Owner | 24h | P≥0.80/R≥0.70 | previous release |
| `object` | semantic | required | required | model | TPENG label platform | semantic Owner | 24h | P≥0.80/R≥0.70 | previous release |
| `style` | semantic | required | required | model | TPENG label platform | semantic Owner | 24h | P≥0.80/R≥0.70 | previous release |
| `material` | semantic | optional | optional | model | TPENG label platform | semantic Owner | 24h | P≥0.80/R≥0.70 | previous release |
| `structural_features` | semantic | optional | optional | model | TPENG label platform | semantic Owner | 24h | P≥0.80/R≥0.70 | previous release |
| `architectural_element` | semantic | optional | optional | model | TPENG label platform | semantic Owner | 24h | P≥0.80/R≥0.70 | previous release |
| `soft_decoration` | semantic | optional | optional | model | TPENG label platform | semantic Owner | 24h | P≥0.80/R≥0.70 | previous release |
| `hard_decoration` | semantic | optional | optional | model | TPENG label platform | semantic Owner | 24h | P≥0.80/R≥0.70 | previous release |
| `color` | semantic | optional | optional | model | TPENG label platform | semantic Owner | 24h | P≥0.80/R≥0.70 | previous release |
| `title` | semantic | optional | optional | source_direct | upstream source + label platform normalization | content data Owner | 24h | P≥0.80/R≥0.70 | previous release |

`whole` keeps the category default for `space`. The `single` execution variant
must explicitly override `space` to `not_applicable`; it must not emit a guessed
space value. The current seed's single source identity remains domestic-only
and `unverified`; it cannot be activated until a separately approved domestic
probe is appended and bound to a new candidate contract version. Overseas
`(res_type,res_id)` plus `su_extra.is_single` requires a later versioned source
binding and separate approved evidence; it is not silently inherited from the
domestic contract.

All projection targets remain `dry_run` in this contract. Seeding does not
execute a model, publish label facts, run DataWorks SQL, or write an external
database.
