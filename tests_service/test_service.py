"""Service-level authorization, state-machine, persistence and API tests."""

from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from hashlib import sha256
import secrets
import sqlite3
import unittest
import uuid
from unittest.mock import patch

from fastapi.testclient import TestClient

from bemix_pilot.engine import canonical
from bemix_pilot.examples import bind, metrics, sample_policy
from bemix_service.api import MAX_BODY, create_app
from bemix_service.service import DecisionService, ServiceError
from bemix_service.storage import IntegrityFailure, Store


from support import Harness


class ServiceTests(Harness):
    def test_experimental_policy_cannot_be_published(self):
        policy = sample_policy()
        draft = self.command("publisher", "draft_policy", {"policy": canonical(policy), "governance": self.governance(policy)})
        self.assert_error(422, lambda: self.command("security", "publish_policy", {"expected_policy_record_hash": draft["hash"], "reason": "review"}, draft["id"]))

    def test_missing_rubrics_or_unknown_caps_cannot_be_published(self):
        governance = self.governance(); governance["rubrics"] = {}
        draft = self.command("publisher", "draft_policy", {"policy": canonical(self.policy), "governance": governance})
        self.assert_error(422, lambda: self.command("security", "publish_policy", {"expected_policy_record_hash": draft["hash"], "reason": "review"}, draft["id"]))

    def test_case_cannot_use_unpublished_policy(self):
        draft = self.command("publisher", "draft_policy", {"policy": canonical(self.policy), "governance": self.governance()})
        self.assert_error(422, lambda: self.command("triager", "create_case", {"researcher": self.people["researcher"], "policy_id": draft["id"], "reference": "x"}))

    def test_published_policy_and_initial_assessment(self):
        state = self.state(self.case())
        self.assertEqual(state["version"], 1)
        self.assertEqual(state["accepted"]["body"]["result"]["reward"]["amount"], "31800.00")
        self.assertEqual(state["accepted"]["body"]["result"]["classification"]["severity"], "Critical")

    def test_new_cases_use_active_policy_and_existing_cases_keep_snapshot(self):
        case = self.case()
        original_id = case["body"]["policy_id"]
        newer = self.publish(replace(self.policy, version="TEST-APPROVED-2"))
        active = self.service.read_active_policy(self.tokens["researcher"]["token"], "alpha", self.policy.asset)
        self.assertEqual(active["policy"]["id"], newer["id"])
        self.assert_error(409, lambda: self.command("triager", "create_case", {
            "researcher": self.people["researcher"], "policy_id": original_id, "reference": "obsolete snapshot"}))
        after = replace(self.report, metrics={"primary": metrics("0.7")})
        self.command("triager", "revise", {"report": canonical(after), "expected_version": 1,
                     "kind": "improvement", "reason": "Evidence under original snapshot"}, case["id"])
        state = self.state(case)
        self.assertEqual(state["case"]["body"]["policy_id"], original_id)
        self.assertEqual(state["accepted"]["body"]["report"]["policy_version"], "TEST-APPROVED-1")

    def test_policy_publisher_does_not_gain_access_to_private_reports(self):
        case = self.case()
        self.assert_error(404, lambda: self.state(case, "publisher"))

    def test_second_initial_assessment_cannot_bypass_revision(self):
        case = self.case()
        self.assert_error(409, lambda: self.command("triager", "initial", {"report": canonical(self.reduced_report()), "expected_version": 1, "reason": "bypass"}, case["id"]))
        self.assertEqual(self.state(case)["version"], 1)

    def test_duplicate_external_report_cannot_restart_initial_workflow(self):
        case = self.case()
        for reference in (case["body"]["reference"], "  " + case["body"]["reference"] + "  "):
            error = self.assert_error(409, lambda: self.command("triager", "create_case", {
                "researcher": self.people["researcher"], "policy_id": case["body"]["policy_id"], "reference": reference}))
            self.assertEqual(error.code, "report_already_registered")




    def test_positive_revision_can_apply_without_correction_permission(self):
        case = self.case()
        after = replace(self.report, metrics={"primary": metrics("0.7")})
        decision = self.command("triager", "revise", {"report": canonical(after), "expected_version": 1, "kind": "improvement", "reason": "More impact"}, case["id"])
        self.assertEqual(decision["kind"], "decision")
        self.assertEqual(decision["body"]["result"]["reward"]["amount"], "36350.00")

    def test_changed_engine_contract_cannot_silently_reinterpret_history(self):
        case = self.case()
        after = replace(self.report, metrics={"primary": metrics("0.7")})
        with patch("bemix_service.service.CORE_CONTRACT", "unreviewed-new-engine"):
            error = self.assert_error(409, lambda: self.command("triager", "revise", {
                "report": canonical(after), "expected_version": 1, "kind": "improvement", "reason": "x"}, case["id"]))
            self.assertEqual(error.code, "engine_migration_required")


    def test_client_cannot_supply_previous_report_or_actor(self):
        case = self.case()
        payload = {"report": canonical(self.report), "expected_version": 1, "kind": "improvement", "reason": "x", "before": canonical(self.reduced_report())}
        self.assert_error(422, lambda: self.command("triager", "revise", payload, case["id"]))


    def test_concurrent_revisions_have_one_winner(self):
        case = self.case()
        def update(q):
            after = replace(self.report, metrics={"primary": metrics(q)})
            try:
                return self.command("triager", "revise", {"report": canonical(after), "expected_version": 1, "kind": "improvement", "reason": "Concurrent improvement"}, case["id"])["kind"]
            except ServiceError as error:
                return error.status
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(update, ("0.7", "0.8")))
        self.assertCountEqual(results, ["decision", 409])
        self.assertEqual(self.state(case)["version"], 2)

    def test_idempotency_does_not_duplicate_a_decision(self):
        case = self.case(assessed=False)
        key = str(uuid.uuid4())
        body = {"report": canonical(self.report), "expected_version": 0, "reason": "same operation"}
        first = self.command("triager", "initial", body, case["id"], key)
        second = self.command("triager", "initial", body, case["id"], key)
        self.assertEqual(first, second)
        self.assertEqual(len(self.state(case)["history"]), 1)
        self.assert_error(409, lambda: self.command("triager", "initial", {**body, "reason": "different"}, case["id"], key))

    def test_idempotent_response_cannot_be_modified_without_detection(self):
        key = str(uuid.uuid4()); body = {"policy": canonical(self.policy), "governance": self.governance()}
        self.command("publisher", "draft_policy", body, key=key)
        with self.store.connection(write=True) as conn:
            conn.execute("DROP TRIGGER requests_no_update")
            conn.execute("UPDATE requests SET response='{}' WHERE key=?", (key,))
        with self.assertRaises(IntegrityFailure):
            self.command("publisher", "draft_policy", body, key=key)
        with self.assertRaises(IntegrityFailure):
            self.store.verify()

    def test_cached_request_does_not_restore_revoked_role(self):
        key = str(uuid.uuid4()); body = {"policy": canonical(self.policy), "governance": self.governance()}
        self.command("publisher", "draft_policy", body, key=key)
        self.service.grant(self.people["publisher"], "alpha", "researcher")
        self.service.revoke_membership(self.people["publisher"], "alpha", "publisher")
        self.assert_error(403, lambda: self.command("publisher", "draft_policy", body, key=key))

    def test_cross_program_and_other_researcher_are_isolated(self):
        case = self.case()
        self.assert_error(404, lambda: self.state(case, "other"))
        self.assert_error(404, lambda: self.service.read_case(self.tokens["triager"]["token"], "beta", case["id"]))
        self.service.grant(self.people["triager"], "beta", "triager")
        self.assert_error(404, lambda: self.service.read_case(self.tokens["triager"]["token"], "beta", case["id"]))

    def test_other_program_uses_its_own_asset_currency_and_financial_bands(self):
        for name, role in (("publisher", "publisher"), ("security", "security"),
                           ("triager", "triager"), ("researcher", "researcher")):
            self.service.grant(self.people[name], "beta", role)
        bands = {"Low": (100, 200), "Medium": (300, 1000), "High": (2000, 5000), "Critical": (10000, 100000)}
        profiles = tuple(replace(p, anchors=((p.anchors[0][0], bands[p.severity][0]),
                                             (p.anchors[-1][0], bands[p.severity][1]))) for p in self.policy.profiles)
        policy = replace(self.policy, version="OTHER-PROGRAM-1", asset="Web / different synthetic program",
                         currency="EUR", bands=bands, profiles=profiles, caps=())
        self.trust_guardrails(policy, self.governance(policy), program="beta")
        draft = self.command("publisher", "draft_policy", {"policy": canonical(policy), "governance": self.governance(policy)}, program="beta")
        self.command("security", "publish_policy", {"expected_policy_record_hash": draft["hash"], "reason": "Independent synthetic review"}, draft["id"], program="beta")
        self.platform_approve(draft, program="beta")
        case = self.command("triager", "create_case", {"researcher": self.people["researcher"], "policy_id": draft["id"], "reference": "OTHER-REPORT"}, program="beta")
        report = bind(self.report, policy)
        decision = self.command("triager", "initial", {"report": canonical(report), "expected_version": 0, "reason": "Program beta evidence"}, case["id"], program="beta")
        self.assertEqual(decision["body"]["result"]["policy"]["currency"], "EUR")
        self.assertEqual(decision["body"]["result"]["reward"]["amount"], "640.00")
        self.assert_error(404, lambda: self.service.read_case(self.tokens["researcher"]["token"], "alpha", case["id"]))

    def test_researcher_cannot_assign_or_correct_scores(self):
        case = self.case()
        self.assert_error(403, lambda: self.command("researcher", "revise", {"report": canonical(self.report), "expected_version": 1, "kind": "correction", "reason": "x"}, case["id"]))



    def test_revoked_token_and_disabled_principal_are_denied(self):
        case = self.case()
        self.service.revoke_token(self.tokens["researcher"]["id"])
        self.assert_error(401, lambda: self.state(case))
        self.service.disable_principal(self.people["triager"])
        self.assert_error(401, lambda: self.state(case, "triager"))

    def test_token_plaintext_is_not_stored(self):
        with self.store.connection() as conn:
            rows = list(conn.execute("SELECT digest FROM tokens"))
        for name, token in self.tokens.items():
            self.assertIn(sha256(token["token"].encode()).hexdigest(), [r[0] for r in rows])
            self.assertNotIn(token["token"].encode(), self.store.path.read_bytes())

    def test_rate_limit_is_enforced_and_committed(self):
        case = self.case()
        limited = DecisionService(self.store, requests_per_minute=1)
        limited.read_case(self.tokens["researcher"]["token"], "alpha", case["id"])
        self.assert_error(429, lambda: limited.read_case(self.tokens["researcher"]["token"], "alpha", case["id"]))

    def test_history_sql_updates_and_deletes_are_rejected(self):
        self.case()
        for statement in ("UPDATE records SET body='{}' WHERE kind='decision'", "DELETE FROM records WHERE kind='decision'", "DELETE FROM events"):
            with self.assertRaises(sqlite3.IntegrityError):
                with self.store.connection(write=True) as conn:
                    conn.execute(statement)
        self.store.verify()

    def test_tamper_is_detected_after_sql_protection_is_removed(self):
        case = self.case()
        with self.store.connection(write=True) as conn:
            conn.execute("DROP TRIGGER records_no_update")
            conn.execute("UPDATE records SET body='{}' WHERE kind='decision'")
        with self.assertRaises(IntegrityFailure):
            self.store.verify()
        with self.assertRaises(IntegrityFailure):
            self.state(case)

    def test_restore_and_external_checkpoint_detect_rollback(self):
        self.case()
        backup = self.directory / "backup.sqlite"
        self.store.backup(backup)
        before = self.store.verify()
        self.service.add_principal("after backup")
        after = self.store.verify()
        restored = Store(backup, self.key)
        self.assertEqual(restored.verify(before)["seq"], before["seq"])
        with self.assertRaises(IntegrityFailure):
            restored.verify(after)

    def test_restart_preserves_case_and_validates_key(self):
        case = self.case()
        restarted = DecisionService(Store(self.store.path, self.key))
        self.assertEqual(restarted.read_case(self.tokens["researcher"]["token"], "alpha", case["id"])["version"], 1)
        with self.assertRaises(IntegrityFailure):
            Store(self.store.path, secrets.token_bytes(32))

    def test_case_history_is_paginated_without_changing_current(self):
        case = self.case()
        after = replace(self.report, metrics={"primary": metrics("0.7")})
        self.command("triager", "revise", {"report": canonical(after), "expected_version": 1, "kind": "improvement", "reason": "x"}, case["id"])
        state = self.service.read_case(self.tokens["researcher"]["token"], "alpha", case["id"], offset=0, limit=1)
        self.assertEqual(len(state["history"]), 1)
        self.assertEqual(state["version"], 2)
        self.assertEqual(state["pagination"]["totals"]["decision"], 2)

    def test_case_record_details_require_same_case_ownership(self):
        case = self.case(); initial = self.state(case)["accepted"]
        self.assert_error(404, lambda: self.service.read_case_record(self.tokens["other"]["token"], "alpha", case["id"], initial["id"]))
        self.assert_error(404, lambda: self.service.read_case_record(self.tokens["researcher"]["token"], "alpha", "different-case", initial["id"]))
        self.assert_error(404, lambda: self.service.read_case_record(self.tokens["researcher"]["token"], "alpha", case["id"], case["body"]["policy_id"]))

    def test_http_has_no_raw_evaluator_or_public_docs(self):
        with TestClient(create_app(self.service, ["testserver"])) as client:
            for path in ("/evaluate", "/v1/evaluate", "/docs", "/openapi.json"):
                self.assertEqual(client.get(path).status_code, 404)
            self.assertEqual(client.get("/healthz").json(), {"status": "ok"})

    def test_http_auth_scope_headers_and_request_id(self):
        case = self.case(); path = f"/v1/programs/alpha/cases/{case['id']}"
        with TestClient(create_app(self.service, ["testserver"])) as client:
            self.assertEqual(client.get(path).status_code, 401)
            response = client.get(path, headers={"Authorization": "Bearer " + self.tokens["researcher"]["token"]})
            self.assertEqual(response.status_code, 200, response.text)
            self.assertEqual(response.headers["cache-control"], "no-store")
            self.assertIn("x-request-id", response.headers)
            self.assertEqual(client.get(path, headers={"Host": "evil.example"}).status_code, 400)

    def test_http_rejects_invalid_body_duplicate_keys_and_oversize(self):
        headers = {"Authorization": "Bearer " + self.tokens["publisher"]["token"], "Idempotency-Key": str(uuid.uuid4()), "Content-Type": "application/json"}
        with TestClient(create_app(self.service, ["testserver"])) as client:
            for body in ('{"x":1,"x":2}', '{"x":0.5}', '{"x":NaN}', 'null'):
                response = client.post("/v1/programs/alpha/policies", content=body, headers=headers)
                self.assertEqual(response.status_code, 422, response.text)
            self.assertEqual(client.post("/v1/programs/alpha/policies", content=b"x" * (MAX_BODY + 1), headers=headers).status_code, 413)
            self.assertEqual(client.post("/v1/programs/alpha/policies", content=b"{}", headers={**headers, "Content-Encoding": "gzip"}).status_code, 415)


if __name__ == "__main__":
    unittest.main()
