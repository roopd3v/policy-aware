# Design and formulas

This is a proposal to develop with platform and program teams. The formulas describe the implementation; the sample weights are fixtures, not an agreed financial policy.

## Original score

```text
S_B = 5A + 2B + C + X - 4
A, B, C = E, D, O sorted from highest to lowest
E, D, O in 0..4; X in 0..2
```

For E=4 and D=O=0, `S_B=16+X`, so even X=2 yields 18. The core keeps the original score and BEMIX label. It does not manufacture data exposure to raise a finding.

## Program classification

The program configures rules, exceptions and their precedence. A researcher selection plus the required values produces an immediate provisional calculation.
The same formulas are used after the assessor validates or corrects those values.
An exact calculation does not mean its factual inputs have already been validated.

Conflicting rules without published precedence leave the assessment unresolved.
The original numerical band and validated program severity are separate fields.
An informational all-zero impact vector remains subject to the core's no-impact rule; a contradictory eligible assessment is flagged for review.

## Financial placement

Each impact profile has fixed criteria and non-negative weights with a positive sum:

```text
Q_k = sum_j(w_kj * q_kj) / sum_j(w_kj)
0 <= q_kj <= 1
```

`q` is an evidence-scored positioning level, not automatically a probability.
`w` expresses relative influence within a profile. A weight of 2 contributes twice the influence of 1 to Q; it does not add BEMIX points or directly multiply money. The examples use extent and reliability with weights 1:1. Programs need to calibrate their own definitions, profiles and weights.

Impact importance `I_k` is separate from context weights. The program chooses it inside its severity's platform-defined interval `[a_s,b_s]`. The platform also fixes a bounded influence coefficient. Normalize importance and calculate position:

```text
i_k = (I_k - a_s) / (b_s - a_s)
R_k = Q_k + alpha * i_k * Q_k * (1-Q_k)
```

The fixtures use alpha=1/5 and integer intervals Low 1–2, Medium 3–4, High 5–7, Critical 8–10, solely as experimental calibration. Increasing importance or context cannot reduce placement; both financial endpoints remain reachable.
Importance never adds BEMIX points or changes a validated severity. Multiplying all context weights equally does not change their normalized average, which is why importance has its own bounded term.

The financial curve is monotone, with published endpoints and optional intermediate anchors:

```text
Linear reference: F_k(R) = L_k + (U_k - L_k) * R

Between anchors (a_i,p_i) and (a_(i+1),p_(i+1)):
F_k(R) = p_i + (p_(i+1)-p_i) * (R-a_i)/(a_(i+1)-a_i)
```

The raw aggregate `S_B/30` is not forced into every profile. Relevant E/D/O/X evidence can inform criteria through the program's definitions without counting the same effect twice by default. Non-applicability is fixed in profile design; researchers cannot remove low criteria from the denominator. Published form defaults and option conditions are declared assumptions. A criterion bound to E/4, D/4, O/4 or X/2 is derived identically for estimates and staff assessments.
Other positive-weight criteria require their declared numerical values.

## Several impacts and restrictions

```text
p_k = min(C_k, F_k(R_k))
P_calc = min(C_global, max_(k in K_applicable)(p_k))
```

Candidates belong to the same program snapshot, asset and currency. Caps apply only where the policy defines them. The highest resolved severity needs an eligible financial profile. No conversion between currencies occurs.

This release fixes reward precision at two decimal places: integer monetary fields are hundredths of the configured reward currency, and rounding is to the nearest such unit, half upward. This is a reward denomination, not native token precision. Other decimal scales require an explicit extension to transport, calculation, formatting and tests; changing only the currency label is insufficient.

A weaker additional profile does not reduce the first candidate. Combined impact can have a separately published profile, with no automatic sum of overlapping losses.
Different profiles still require comparative calibration; equal Q values alone do not prove equivalent economic impact.

## Estimated, validated and final

The workflow retains the original Estimated Bounty, the latest assessed inputs and Validated Bounty, and the program-confirmed Final Award. `P_calc` is the automatic calculation at the relevant stage; it is not an irreversible payment.
Normal staff edits default to correction and recalculate with a reason, allowing the value to rise or fall without another triager's approval.

The award is a separate record tied to an assessment version and hash. Optional adjustments must name a rule in the pinned policy, stay within its published delta and applicable payout bounds, and include a reason. An explicit exception can publish different award bounds. Adjustments never change E/D/O/X. A later assessment makes an earlier award stale until the program confirms a new one; all earlier records remain available.

## Missing inputs versus unverified evidence

Provided numerical values are sufficient for an estimate before PoC review.
For genuinely missing inputs, the engine computes conservative bounds using monotone profiles and boolean completions. If supported completions give the same severity and exact amount, it returns that amount with the uncertainty recorded. If they differ, a conditional range is returned when possible.
Conflicting policies or unmapped cases stay conditional rather than receiving invented classifications.

Boolean completion is limited to eight unresolved conditions to bound computation.
Larger unresolved sets ask for relevant inputs; that is a conservative limit, not proof that all missing facts affect the result. Published defaults, option conditions and filled form values avoid this path in ordinary complete submissions.
Hypothetical completions are never promoted to observed facts.

## Revisions

The revision checker records changes that do not satisfy the improvement contract, including removal of a profile by precedence. Normal edits default to a justified correction and recalculate immediately. A client that explicitly labels a regression as an improvement is asked to record it as a correction.
That same assigned triager can correct their assessment. After handoff, the program security team is responsible. There is no mandatory second-triager approval.

The service never retains an old amount solely because it was the largest in history.
It preserves the old record and recomputes the current one from the pinned policy.
Claims and their confirmations are recorded separately from validated fact inputs.

## Platform configuration control

Platform templates fix the permitted severity intervals, context weight/share bounds and approved rule/financial structure. Drafts cannot widen those limits.
Publication requests execute financial and input-transition checks, then await platform review. Only platform approval activates the exact reviewed record.
This configuration gate does not change report corrections. Details and the monotonicity argument are in [PLATFORM_GOVERNANCE.md](PLATFORM_GOVERNANCE.md).

All money uses integer minor units, with exact fractions internally and one final rounding step. Neither deterministic arithmetic nor passing tests proves that an uncalibrated set of weights produces fair real-world rewards.
