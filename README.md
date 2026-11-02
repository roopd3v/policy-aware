# BEMIX Policy-Aware

A reference implementation for discussing how predictable bug bounty rewards can respect each program's published impact rules.

The proposal preserves the original BEMIX score, evaluates severity against a versioned program policy, and calculates placement inside its financial band.
It is offered as a contribution to review, adapt or reuse with BugEx and program teams.

## A Structural Limitation in BEMIX

For E=4, D=O=0, the original formula gives `S_B = 16 + X`. Even X=2 reaches only 18, while the BEMIX Critical band begins at 22. A maximum single-axis impact therefore cannot reach that numerical band, including an impact a program might classify as Critical under its own rules.

The proposed extension keeps that original score available and gives validated program severity a separate role in financial calculation.

## Submission and assessment workflow

1. The platform defines configuration limits and reviews the program's asset rules. The program chooses impact importance and context weights within those limits. Checks and platform approval are mandatory before the policy goes Live.
2. The researcher selects behaviours, fills the BEMIX factors and required profile inputs, and receives an **Estimated Bounty** immediately. A fully determined calculation has an exact amount even before evidence review.
3. The assigned triager confirms or corrects those inputs. The same engine recalculates the **Validated Bounty**, which can rise, fall or stay the same.
4. The triager forwards the report. Program security can reassess it and record the **Final Award**, with any permitted adjustment separated from the score and calculated bounty.

The responsible assessor can explain the decision through the existing discussion mechanism. Revisions preserve the previous assessment and its evidence trail.

The repository implements the backend catalog, estimates, validation and award APIs.
Missing values can yield a conditional range; an uncertainty that cannot change the relevant severity or amount does not suppress a determinate calculation.
The submission screen itself is an integration task for an existing platform; there is no browser UI in this repository. Weights need not clutter a form, but published scoring configuration is consultable by program members.

## Run

The calculation engine uses the standard library. The service uses FastAPI, Uvicorn and SQLite. The locked, tested runtime is **CPython 3.14 on Linux x86_64**.
Package metadata requires Python 3.14.x; other interpreter versions and platforms are outside this release's supported deployment profile.

```sh
python3.14 -m venv .venv
.venv/bin/python -m pip install --require-hashes -r requirements-test.lock
.venv/bin/python scripts/verify.py
.venv/bin/python -m bemix_pilot.demo
.venv/bin/python -m bemix_pilot.lifecycle_example
```

The example policy is `experimental: true`; it is not automatically publishable.
Publication requires an authorized program role and complete policy definitions.
Approval records who accepts a configuration; it does not prove calibration is fair.
Program publication requests remain pending. Only the platform operator can approve and activate the exact reviewed record. Changes need a new version and approval; ordinary report corrections remain with the assigned assessor.

## Read next

- [Design and formulas](docs/DESIGN.md)
- [Submission and triage flow](docs/WORKFLOW.md)
- [API contract](docs/API.md)
- [Platform limits, impact importance and publication approval](docs/PLATFORM_GOVERNANCE.md)
- [Operations and deployment](docs/OPERATIONS.md)
- [Security boundaries](docs/SECURITY.md)
- [Verification record](artifacts/verification.json)
- [Policy Aware Mathematical reference PDF](docs/BEMIX_Policy_Aware_1.3.0_Mathematical_Reference.pdf)

Core code lives in `bemix_pilot/`; the persistent service is in `bemix_service/`.
Tests cover the two independently. `fixtures/` and `artifacts/examples.json` contain reproducible synthetic examples. No real report, exploit, private program snapshot or production credentials are included.

## Scope

The service provides authenticated program-scoped roles, assignment and handoff, mandatory revision checks, exact arithmetic, immutable application history, authenticated audit records, concurrency/idempotency controls, backups and checkpoints. It has no integration with live BugEx accounts, no corporate SSO, no payment execution and no official policy calibration.

The deployment profile is one Linux host with local SQLite and one ASGI worker, behind TLS. Multi-host availability and production capacity need separate validation.
Version 1.3.0 retains database schema 3 but uses core contract 1.3 and mandatory platform approval. Old publications without that approval do not become live under the new contract. Existing installations need an explicit transition; historical decisions and policy snapshots are not silently rewritten.

Reward amounts use **two decimal places**: one API minor unit is 0.01 of the configured reward currency. Currency is a denomination label, not a precision lookup or exchange rate. Native token base units and currencies requiring other precision need an explicit extension before use.

License: [MIT](LICENSE). The source can be reviewed, adapted and reused under those terms; it is not an official BugEx or program release.
