# Security and trust boundaries

Remote clients are untrusted. Individual bearer tokens identify principals; roles and program membership are loaded on every operation. Researchers declare facts, BEMIX values and profile inputs to obtain an estimate. Their declarations cannot assign validated status, financial weights or a trusted prior assessment.

During triage, only the assigned triager can modify the assessment. Handoff transfers write authority to the program security role. The authorized assessor can correct a result with evidence and a reason. This is deliberate organizational authority, not a peer-approval queue. The audit trail makes those actions visible; it does not prove that the human interpretation is correct.

History is append-only through the API and ordinary database operations, protected by SQL triggers and authenticated records/events. HMAC keys live outside the database. Changes without the key are detectable; rollback of a complete valid database requires an external checkpoint. HMAC is shared-key authentication, not public non-repudiation or proof against a host administrator controlling the application, database, key and backups.

Operator-provisioned identities and permissions are trusted. The service does not provide corporate SSO/MFA or prove that two accounts are different people.
The organization can integrate existing identity while retaining program scopes and the assigned triager/security-team responsibilities.

Program `publisher` and `security` roles can request publication but cannot activate a policy. The platform's local operator defines immutable limits and acceptance cases, and approves exact record hashes after review. Approval and activation are atomic, authenticated history records. Active-policy reads verify the exact approval linkage; client-supplied approval fields grant no authority.
The operator interface and its database/key access must belong to the platform.
Programs must not receive that host access. A future platform panel must preserve this separation rather than expose operator methods to program roles.

The template bounds impact importance, raw weights and normalized shares, and protects rule mappings, criterion definitions, financial curves, caps and award exceptions. It prevents out-of-range configuration and self-relabeling to evade those limits. A numerically valid priority can still be inappropriate; mandatory platform review can return it for changes. Review quality and fair calibration are human responsibilities. This gate does not require another triager for an ordinary report correction.

Program security can confirm an award from the latest resolved calculation.
Adjustments require a rule from the pinned policy, remain bounded by that rule, and include a reason. This authority does not let researchers or triagers confirm awards, and it does not change scores to justify a chosen payout. Subsequent assessment changes invalidate the current award while retaining its history.

Input is bounded JSON with exact numbers and schema validation. No evidence URL, template, script or payload is executed. No payment execution endpoint or permissive CORS is provided. TLS, disk/backup protection, operator segregation and retention are deployment responsibilities.

The copied dependency lock was audited after updating Starlette to 1.3.1 for [GHSA-82w8-qh3p-5jfq](https://github.com/Kludex/starlette/security/advisories/GHSA-82w8-qh3p-5jfq)
and [GHSA-jp82-jpqv-5vv3](https://github.com/Kludex/starlette/security/advisories/GHSA-jp82-jpqv-5vv3).
The report is date-specific, covers the Python lock rather than the operating system, and is not a security certification. Re-audit dependencies when deploying.

Report a code concern with a minimal synthetic reproduction, expected behaviour and affected version. Do not include private bounty reports, production credentials or live exploitable details in public issues.
