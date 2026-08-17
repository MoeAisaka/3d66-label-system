# 3D/SU Source Identity Verification Contract v1

## Purpose and boundary

This contract defines the read-only evidence required before LabelLab may treat
an upstream 3D/SU source key as a verified canonical asset identity. It does not
grant DataWorks access, execute SQL, write production data, activate a field
contract, start a model, or publish label facts.

The source identity contract is site-specific; domestic and overseas sources
must not be forced through one table or one asset-id field.

### Domestic binding

The authoritative candidate table is `aliyun_3d66_dw.dim_res_info_union`.
Its candidate identity key is `res_type + ll_id`, where:

- `res_type in (1, 6)` means a model-category asset;
- the authoritative asset ID is `ll_id`;
- `is_single` is read from the same table;
- `is_single=0` routes to whole and `is_single=1` routes to single;
- `res_id` remains an optional conflict/lineage field and is not silently
  appended to the canonical key.

### Overseas binding

The authoritative candidate table is `aliyun_3d66_dw.ods_ll_relebook_res`.
Its candidate identity key is `res_type + res_id`, where:

- `res_type=6` means a model-category asset;
- the authoritative asset ID is `res_id`;
- `is_single` is supplied by `aliyun_3d66_dw.ods_ll_relebook_res_su_extra`;
- the join key between the overseas main table and the extra table must be
  explicitly signed by the Data Owner before execution;
- `ll_id` may be retained as lineage when present, but is not assumed to be
  the overseas canonical asset ID.

## Required read-only probe

For each site-specific binding and one explicitly signed data window, the
evidence package must contain the outputs or approved summaries of these
read-only checks:

1. scoped row counts grouped by the binding's `res_type` filter;
2. null or blank counts for the binding's authoritative asset ID;
3. duplicate rows grouped by the binding's candidate key;
4. `is_single` null, invalid-value and whole/single distribution checks;
5. for overseas, main/extra-table join coverage and duplicate matches;
6. candidate keys that map to more than one secondary source ID, where such a
   secondary ID exists.

The generator emits only SQL text plus a deterministic `probe_hash`. Executing
the SQL requires separate least-privilege, read-only authorization. No
`INSERT`, `UPDATE`, `DELETE`, table creation, permission application, or other
production mutation belongs to this probe.

## Verification decision

The result may be signed as `verified` only when duplicate candidate keys,
invalid or missing `is_single` values, and (for overseas) unmatched or
multi-matched extra-table rows are zero for the same signed data window.

Null or blank authoritative asset IDs cannot receive a canonical `content_key`
and must be reported separately. Any duplicate or multi-resource mapping makes
the result `conflict`; the platform must fail closed and must not add a random
suffix or otherwise manufacture uniqueness.

Evidence persistence is limited to the probe hash, signed window, aggregate
counts, result, reviewer, and approval state. Credentials, tokens, full
sensitive result dumps, and unrelated source rows must not be stored in the
contract evidence.

## Stop conditions

Stop before approval when the table identifier or data window is ambiguous,
read-only authorization is missing, the generated `probe_hash` differs from the
reviewed package, or duplicate/conflict counts are non-zero. Until a matching
evidence record is manually approved, 3D/SU ingress remains unverified and
cannot enter label production.
