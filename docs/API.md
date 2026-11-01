# API contract

Base: `/v1/programs/{program}`. Domain endpoints require an individual bearer token. Writes require `Content-Type: application/json` and a unique 16-128 character `Idempotency-Key`, except the read-only `/estimates` calculator.
Tokens and roles are provisioned by the local operator.

Program accounts submit publication requests. Only the platform's local administration interface can approve and activate a version after reviewing the exact record hash and its configuration tests. This approval concerns program rules and is separate from ordinary report corrections.

| Endpoint | Role | Required body |
| --- | --- | --- |
| POST `/policies` | publisher | `policy`, `governance` |
| POST `/policies/{id}/publish` | publisher or security; requests review only | `expected_policy_record_hash`, `reason` |
| GET `/policies/{id}` | program member; drafts staff only | - |
| GET `/active-policy?asset=...` | member | - |
| GET `/platform-guardrails?asset=...` | member, read-only | - |
| GET `/submission-options?asset=...` | member | - |
| POST `/estimates` | program member | `asset`, `claims`, `inputs`; optional `expected_policy_record_hash` |
| POST `/submissions` | researcher | `asset`, `summary`, `claims`, `inputs`; optional `expected_policy_record_hash` |
| GET `/submissions/{id}` | owner, triager or security | - |
| POST `/submissions/{id}/claims` | owner before assessment | `expected_version` and changed `claims` and/or `inputs` |
| GET `/submissions/{id}/claims/{revision}` | owner or assessment staff | - |
| POST `/cases` | triager | `researcher`, `policy_id`, `reference`; optional `assigned_triager`, `submission_id` |
| GET `/cases/{id}?offset=0&limit=20` | owner, triager or security | - |
| GET `/cases/{id}/records/{record_id}` | same case access | - |
| POST `/cases/{id}/assessments` | assigned triager | `report` or `use_submission_inputs: true`, `expected_version: 0`, `reason`; `claim_review` for linked submissions |
| POST `/cases/{id}/revisions` | responsible assessor at current stage | `report`, `expected_version`, `reason`; optional `kind` defaults to `correction`; update `claim_review` when needed |
| POST `/cases/{id}/handoff` | assigned triager | `expected_version`, `reason` |
| POST `/cases/{id}/award` | program security after handoff | `expected_version`, `expected_award_version`, `reason`; optional `adjustment` |
| POST `/cases/{id}/appeals` | researcher owner | `decision_version`, `grounds` |
| POST `/appeals/{id}/resolve` | responsible assessor at current stage | `outcome`, `reason` |

The assigned triager owns corrections during `triage`. After handoff, program members with `security` own reassessment. Normal revisions default to `correction` and can raise or lower a result with a reason, without another triager's approval. `outcome` is `upheld` or `reassessment_required`; it does not itself modify a score or create another assignment.

## Catalog and researcher claims

`governance` contains `source_reference`, `calibration_reference`, `precedence_reference`, `rubrics`, and optional `submission_options`, `estimation` and `award_adjustments`. See [example-governance.json](../fixtures/example-governance.json) and [submission-options.json](../fixtures/submission-options.json). The catalog is part of the immutable policy record approved at publication.

The platform installs a template for each program/asset before drafts are accepted. Program settings may select each profile's integer `importance` within the template's severity interval and its criterion weights within both raw-value and normalized-share bounds. Intervals, importance influence, rules, criteria, curves, bands, caps and mappings cannot be redefined by program requests.

`POST /policies/{id}/publish` runs configuration checks and returns a `policy_review_request` with `status: pending_platform_review`. It does not make the draft active. Platform approval rechecks the exact policy record, guardrails and test certificate, then records approval and publication atomically.
`GET /policies/{id}` exposes requests, platform feedback, approvals and publication.
There is no program HTTP approval or guardrail-write endpoint. See [platform governance](PLATFORM_GOVERNANCE.md) for the operator interface.

A submission example is in [example-submission.json](../fixtures/example-submission.json).
`inputs` contains the four integers in `core`, optional declared boolean `facts`, and any required numeric `metrics`. A selected option supplies its published declaration conditions. Published defaults can fill non-selected paths; explicit input values remain provisional and override those defaults. An explicit field must agree with the conditions declared by a selected option. An opposite value or `null` cannot negate or erase that selection; the request returns 422 and a rejected edit preserves the previous version. To change the declaration, change the selection and its inputs together. This check does not restrict an assigned triager's reasoned corrections to the assessed facts.

The response includes `estimated_bounty`, with stage `estimated_bounty`, `facts_validated: false`, an exact `amount` when determined, or a conditional `range`/dependency when required inputs are missing. The classification and money are provisional until assessed. `POST /estimates` returns the same result without storing a report or validated assessment.

The triager confirms or changes every declaration through [example-claim-review.json](../fixtures/example-claim-review.json). Confirmation must agree with matched technical rule conditions. Outcomes are `confirmed`, `changed`, `not_demonstrated` or `pending`. A changed selection records replacement IDs and a reason. The optional `additional_options` list records behaviours identified during assessment. `governance.estimation.metric_bindings` can derive criteria from E/4, D/4, O/4 or X/2. Those bindings are used identically for staff and researcher calculations; criteria without a binding need their own declared values. Weights are always taken from the policy.

## Final Award

The program security team confirms the latest resolved calculated bounty through `/award`, with the current assessment and award versions. A permitted adjustment has `rule_id`, `delta_minor_units` and `reason`; the rule must be present in the case's pinned `governance.award_adjustments`. The program defines allowed signed delta limits. Published exceptions can explicitly replace the usual award bounds.
The score and calculated bounty are preserved alongside the adjustment.

A changed assessment makes the old award stale, not erased. Program confirmation must then refer to the new assessment. A Final Award is a recorded assignment of money, not an executed transfer. Ineligible findings can receive a confirmed zero award; unresolved material inputs cannot be silently finalized.

`policy` and `report` are the internal objects of the versioned JSON files in `fixtures/`, without the outer `schema_version` envelope. Numeric scores/weights are exact decimal or fraction strings; money is integer minor units. Extra fields, floats, duplicate JSON keys and oversized inputs are rejected.

All monetary fields, including bands, anchors, caps and award adjustments, use 100 minor units per one unit of the configured reward currency. Display amounts always have two decimal places. `currency` does not configure precision or convert amounts. Do not send satoshis or other native token units as if they were these API minor units; zero- or eight-decimal denominations are not supported here.

## Read authoritative state

`GET /cases/{id}` returns `estimated_bounty`, `validated_bounty`, `final_award`, `final_award_stale`, assessment and award histories, stage/handoff and questions.
The raw latest assessment remains in `accepted`. It can still be pending; the calculation status indicates whether its amount is determined. These fields must not be conflated with an executed payment.

History uses offset/limit and totals. Full earlier reports are loaded individually through `/records/{id}`. The service loads the previous assessment itself; a client cannot substitute `before`, change the pinned policy or bypass revisions by sending another initial assessment.

401 means invalid credentials; 403 insufficient authority; 404 not visible or not found; 409 stale version, duplicate or `correction_required`; 422 invalid input; 429 rate limit; 503 persistence/integrity failure. A correction request does not need another staff member simply because an improvement check failed.

Health routes expose no report data. There are no raw evaluator, payment, credential-administration or peer-correction-approval HTTP endpoints.
