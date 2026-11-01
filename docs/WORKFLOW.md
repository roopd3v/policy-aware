# Researcher selection, assigned triage and program assessment

## Program-owned catalog

The program's catalog and settings become usable only after platform validation and approval. Program-selected importance and criterion weights are restricted by platform intervals and share limits. A publication request is pending review; only the platform activates the exact approved record. New settings require a new version and approval. See [platform governance](PLATFORM_GOVERNANCE.md).

`governance.submission_options` belongs to the versioned policy record. Every option has an ID, a behaviour description, rule IDs and evidence prompts. The form can show descriptions without numerical coefficients. Members can consult the published rule, weight and curve configuration through the policy API.

The catalog supports several selected behaviours and an `other` option without an automatic rule mapping. It is not an exhaustive security taxonomy. A browser form is not implemented here; the backend is ready for an existing platform to render the catalog and collect the responses.

## Researcher declaration

`POST /submissions` records the researcher, asset, active policy snapshot, summary, claims and calculation inputs. It immediately returns an Estimated Bounty. Complete inputs give an exact provisional amount; the selected option and filled values are still declarations. Required evidence prompts are enforced, but the calculator does not independently verify the referenced PoC.

`POST /estimates` provides the same calculation while filling a form without creating a submission or assessment. Missing required values can produce a conditional range. Published defaults and bindings belong to the program, not the researcher's choice of weights, and remain consultable.

The researcher can revise selections or numerical values with version checks before the first assessment. Each declaration retains its own estimate and can be read through the declaration-version endpoint. Once assessed, the original declaration remains fixed in history; additional evidence can be handled through the existing discussion process.
Another researcher or a policy publisher cannot read the private declaration.

## Assignment and validation

The platform creates an assigned case linked to the submission. The submission's policy is retained even if another version was published before triage starts.
An unlinked case can also be imported from an existing report platform, using the currently active policy. Historical migrations require a reviewed procedure.

The assigned triager explicitly confirms the submitted inputs or supplies corrected values, and addresses each claim as confirmed, changed, not demonstrated or pending. The same engine calculates the Validated Bounty. A changed claim has replacement catalog IDs and a reason. Confirmations must agree with matched technical rule conditions. A chosen Critical option can guide a provisional estimate, but it does not validate the condition or decide the final award.

Compatible financial updates can retain a prior claim review. If corrected facts no longer support the confirmed classification, the assessor must update the claim review in that same revision. There is no new-person approval step.

## Corrections and handoff

The assigned triager can correct facts, classification and the resulting score with a recorded explanation. Other triagers cannot edit that assignment's assessment. Normal edits default to correction and recalculate immediately.
An explicitly false improvement label is rejected with `correction_required`; the same responsible assessor can record the justified correction.

The assigned triager forwards the case with `POST /cases/{id}/handoff`, fixing the triage version and hash. Edit authority then belongs to the program security role, which can raise or lower the assessment under the policy. The organization controls role assignments; the software does not require two different people to repeat each correction.

A researcher question/appeal records their disagreement without changing scores, reassigning a triager or automatically creating an escalation. The assessor responsible for the current stage can explain the decision. Any resulting change uses the same versioned revision calculation.

## Program confirmation

After handoff the program can correct the assessed inputs, or confirm an award from the latest resolved calculation. A policy-permitted adjustment is recorded separately, with its rule and reason. Final Award confirmation never silently changes a score. The service records the assigned amount; payment execution belongs to the platform's payment system.

An award is tied to the assessed version. A later correction preserves it in history but removes it as the current award until the program reconfirms. The researcher can inspect the original estimate, validated calculation, changes and final award separately.
