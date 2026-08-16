# 3D/SU Source Identity Verification Contract v1

## Purpose and boundary

This contract defines the read-only evidence required before LabelLab may treat
an upstream 3D/SU source key as a verified canonical asset identity. It does not
grant DataWorks access, execute SQL, write production data, activate a field
contract, start a model, or publish label facts.

The authoritative candidate table is
`aliyun_3d66_dw.dim_res_info_union`. Its candidate identity key is
`res_type + ll_id`, where:

- `res_type=1` means a 3D asset;
- `res_type=6` means an SU asset;
- `res_id` is inspected only as an optional conflict signal and is not silently
  appended to the canonical key.

## Required read-only probe

For one explicitly signed data window, the evidence package must contain the
outputs or approved summaries of exactly these four `SELECT` checks:

1. scoped row counts grouped by `res_type` for 3D and SU;
2. null or blank counts for `ll_id` and `res_id`;
3. duplicate rows grouped by `res_type, ll_id`;
4. candidate keys that map to more than one distinct `res_id`.

The generator emits only SQL text plus a deterministic `probe_hash`. Executing
the SQL requires separate least-privilege, read-only authorization. No
`INSERT`, `UPDATE`, `DELETE`, table creation, permission application, or other
production mutation belongs to this probe.

## Verification decision

The result may be signed as `verified` only when both of these counts are zero
for the same signed data window:

- duplicate `res_type + ll_id` key count;
- multi-`res_id` conflict count for the same key.

Null or blank `ll_id` rows cannot receive a canonical `content_key` and must be
reported separately. Any duplicate or multi-resource mapping makes the result
`conflict`; the platform must fail closed and must not add a random suffix or
otherwise manufacture uniqueness.

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
