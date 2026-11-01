# Platform limits, program choices and mandatory approval

The platform owns the permitted mathematical configuration. Programs supply their asset-specific impact rules and priorities; the platform reviews that rulebook and installs an immutable template before accepting the bounded settings.
A program can choose impact importance and criterion weights only within that template. It cannot widen its own limits or activate its own policy.

## Configuration ownership

| Setting | Authority |
| --- | --- |
| Original BEMIX formula | Platform implementation |
| Importance intervals by severity and influence coefficient | Platform |
| Impact taxonomy and severity mapping per asset | Proposed by program; reviewed in platform template |
| Importance of each impact | Program, inside its severity interval |
| Applicable criteria, definitions, raw-weight and percentage-share bounds | Platform-approved profile |
| Criterion weights | Program, inside both sets of bounds |
| Bands, currency, curves, caps, mappings and award exceptions | Program terms reviewed and fixed in platform template |
| Configuration validation | Automatic engine |
| Final configuration approval and activation | Platform operator |
| Evidence and corrections in an individual report | Assigned triager, then program security |

The synthetic integer importance scale is Low 1–2, Medium 3–4, High 5–7, Critical 8–10. It is not calibrated or an official BugEx policy. Other valid integer intervals can be defined by the platform; they must be positive, strictly ordered and disjoint. A Critical profile cannot use a Low importance. 
Equal importance values within one severity are allowed.

Criterion weights are relative. For weights `w_j`, the actual share is `w_j / sum(w)`. Templates constrain both the raw value and that share, and require the share intervals to permit a total of 100%. Applicable criteria have positive weight minima. Only an explicitly platform-fixed zero criterion may be disabled; non-applicable criteria can instead be omitted in the approved profile.

## Business Risk Scoring and completeness

Fund impact, reputation, compliance, reliability, interaction, coverage and effective mitigations can enter `Q` through the criteria applicable to that approved impact profile. Their raw weights and normalized shares are bounded by the platform. Rubrics define what each score means and which evidence supports it. Compliance is not compulsory in an unrelated fund-loss profile. The fixtures use extent and reliability to exercise the mechanism; they do not claim to be a complete official Bitget configuration of all these factors.

Impact importance expresses the program's relative priority for the type.
Contextual fund impact describes the reach actually demonstrated. The platform reviews definitions to avoid counting one effect twice. A profile can derive a criterion from E/D/O/X, but the raw BEMIX aggregate is not forced into every calculation alongside duplicate penalties.

Mandatory top-of-band conditions remain explicit eligibility/cap rules rather than disappearing inside a weighted average. If the program requires full reliability, no interaction, all-protocol coverage and no effective mitigation, the approved configuration must define their financial consequence. Neither the program nor the calculator silently invents an unpublished percentage discount.

## Formula and guarantees

```text
Q_k = sum(w_kj * q_kj) / sum(w_kj)
i_k = (I_k - a_s) / (b_s - a_s)
R_k = Q_k + alpha * i_k * Q_k * (1 - Q_k)
p_k = min(C_k, F_k(R_k))
P_calc = min(C_global, max_k(p_k))
```

`I` is the program's bounded importance; `[a_s,b_s]` is the platform's interval for that severity. Normalized `i=0` represents the permitted minimum, not an importance of zero. The template fixes `alpha` in `(0,1]`; the fixtures use 1/5.
This coefficient is experimental, not a percentage bonus to the payment.

For `Q,i,alpha` in `[0,1]`, `R` stays in `[0,1]`, increases with importance, and `dR/dQ = 1 + alpha*i*(1-2Q) >= 0`. Both endpoints remain reachable regardless of importance. Non-negative fixed context weights and monotone financial curves preserve these properties. Explicit eligibility conditions and caps still apply.
The automatic checks also run an exact-number financial grid and actual criterion/BEMIX input transitions for every platform-provided profile scenario.

Importance is never added to BEMIX and does not choose severity. A higher-priority High remains High. A validated Critical does not need a BEMIX score of 22.
At Q=0.6 in the synthetic Critical band, importance 8/9/10 yields 31,800/32,892/33,984 USDC. See [importance-example.json](../artifacts/importance-example.json).

## Draft → Validation → Platform Review → Live

1. The platform operator installs a versioned template using local administration. A synthetic example is [platform-guardrails.json](../fixtures/platform-guardrails.json).
2. The program creates a draft. Out-of-range importance, weights or shares and changes to protected rule/financial structure are rejected.
3. The program calls the publication-request endpoint. Complete configuration validation and financial scenarios must pass. This creates `policy_review_request` with `pending_platform_review`, never a live policy.
4. A platform reviewer examines the exact record, program justification and validation results. They can request changes even when the mathematics passes.
5. Platform approval repeats the checks, requires the exact record hash and current template, then atomically records approval and activates that version.

Only the local platform administration interface can approve/activate or return a proposal for changes. Those operations are absent from program HTTP routes.
The reviewer and reason are recorded with the request, template, certificate and policy hashes. A forged `approved` field or a program role cannot replace this.
Active-policy reads verify the publication's exact platform-approval linkage.

Example commands, executed by the platform operator with the correct database and key paths (never by program accounts):

```sh
python -m bemix_service --db decisions.sqlite --key-file audit.key guardrails PROGRAM APPROVED_TEMPLATE.json --reason "Platform-reviewed limits and scenarios"
python -m bemix_service --db decisions.sqlite --key-file audit.key approve-policy PROGRAM POLICY_ID --expected-record-hash REVIEWED_HASH --reviewer PLATFORM_REVIEWER --reason "Reviewed priorities and configuration tests"
python -m bemix_service --db decisions.sqlite --key-file audit.key return-policy PROGRAM POLICY_ID --expected-record-hash REVIEWED_HASH --reviewer PLATFORM_REVIEWER --reason "Explain the required changes"
```

The current implementation uses trusted local administration; a platform panel or SSO integration is not supplied. Deployment must keep that interface, database and key under platform control. Programs receive scoped API tokens, not host access.

## Versioning and boundaries

A changed policy needs a new version, checks and platform approval. A returned proposal needs a new version too. Installing a new template invalidates pending drafts based on the older template. It does not rewrite approved snapshots.
New settings become active only on approval; submitted reports retain their snapshot and ordinary corrections do not request another policy approval.

This is a core-contract 1.3 change. Older publications without platform approval cannot be treated as newly approved live policies. An existing installation requires an explicit transition preserving historical reports; no approval is manufactured for an old record.

The validator proves structural and arithmetic properties under the configured rules. It cannot prove that a program's taxonomy, rubric or permitted priority is economically fair. Platform review is mandatory for that reason; its quality still depends on the actual review and calibration.
