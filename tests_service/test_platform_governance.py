"""A program cannot widen platform limits or activate its own policy."""

from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from dataclasses import replace
from fractions import Fraction as F
import uuid

from fastapi.testclient import TestClient

from bemix_pilot.engine import EvidenceError, canonical, fingerprint
from bemix_pilot.examples import bind
from bemix_pilot.platform_example import example_guardrails
from bemix_service.api import create_app
from bemix_service.guardrails import validate_template
from bemix_service.service import DecisionService, ServiceError
from bemix_service.storage import IntegrityFailure, Store
from support import Harness


class PlatformGovernanceTests(Harness):
    def request_review(self, policy=None, governance=None):
        draft = self.command("publisher", "draft_policy", {"policy": canonical(policy or self.policy),
            "governance": governance if governance is not None else self.governance()})
        request = self.command("publisher", "publish_policy", {"expected_policy_record_hash": draft["hash"],
            "reason": "Program submits bounded configuration for platform review"}, draft["id"])
        return draft, request

    def changed_policy(self, importance=9, version="PROPOSED-2"):
        profile = replace(self.policy.profiles[0], importance=importance)
        return replace(self.policy, version=version, profiles=(profile, *self.policy.profiles[1:]))

    def test_platform_must_define_limits_before_a_program_can_configure(self):
        service = DecisionService(Store.initialize(self.directory / "unconfigured.sqlite", self.key))
        service.add_program("unconfigured")
        person = service.add_principal("program author")
        service.grant(person, "unconfigured", "publisher")
        token = service.issue_token(person)["token"]
        error = self.assert_error(422, lambda: service.command(token, "unconfigured", "draft_policy", "",
            {"policy": canonical(self.policy), "governance": self.governance()}, str(uuid.uuid4())))
        self.assertEqual(error.code, "platform_configuration_required")

    def test_program_cannot_supply_or_override_its_platform_template(self):
        self.assert_error(422, lambda: self.command("publisher", "draft_policy", {
            "policy": canonical(self.policy), "governance": self.governance(),
            "platform_guardrails": example_guardrails(self.policy, self.governance())}))
        for operation in ("install_guardrails", "approve_policy"):
            self.assert_error(404, lambda: self.command("publisher", operation, {}))

    def test_critical_importance_zero_or_outside_critical_is_rejected(self):
        for importance in (0, 2, 7, 11):
            with self.subTest(importance=importance), self.assertRaises(ValueError):
                self.command("publisher", "draft_policy", {"policy": canonical(self.changed_policy(importance)), "governance": self.governance()})

    def test_program_cannot_widen_the_importance_interval_or_its_influence(self):
        candidates = [replace(self.changed_policy(), importance_ranges={**self.policy.importance_ranges, "Critical": (8, 11)}),
                      replace(self.changed_policy(), importance_influence=F(1))]
        for policy in candidates:
            with self.subTest(policy=policy.version), self.assertRaises(EvidenceError):
                self.command("publisher", "draft_policy", {"policy": canonical(policy), "governance": self.governance()})

    def test_context_weights_cannot_be_disabled_or_exceed_platform_bounds(self):
        for value in (F(0), F(3)):
            profile = replace(self.policy.profiles[0], weights={"extent": value, "reliability": F(1)})
            policy = replace(self.policy, profiles=(profile, *self.policy.profiles[1:]))
            with self.subTest(value=value), self.assertRaisesRegex(EvidenceError, "Context weight"):
                self.command("publisher", "draft_policy", {"policy": canonical(policy), "governance": self.governance()})

    def test_relative_context_shares_are_bounded_even_when_raw_weights_are_in_range(self):
        profile = replace(self.policy.profiles[0], weights={"extent": F(1, 2), "reliability": F(2)})
        policy = replace(self.policy, profiles=(profile, *self.policy.profiles[1:]))
        with self.assertRaisesRegex(EvidenceError, "Context share"):
            self.command("publisher", "draft_policy", {"policy": canonical(policy), "governance": self.governance()})

    def test_program_can_choose_approved_importance_and_context_weights(self):
        profile = replace(self.policy.profiles[0], importance=9, weights={"extent": F(2), "reliability": F(1)})
        policy = replace(self.policy, profiles=(profile, *self.policy.profiles[1:]))
        draft, request = self.request_review(policy)
        self.assertEqual(request["body"]["status"], "pending_platform_review")
        self.platform_approve(draft)
        case = self.command("triager", "create_case", {"researcher": self.people["researcher"], "policy_id": draft["id"], "reference": "BOUNDED-IMPORTANCE"})
        assessed = self.command("triager", "initial", {"expected_version": 0, "report": canonical(bind(self.report, policy)), "reason": "Validated report"}, case["id"])
        result = assessed["body"]["result"]
        self.assertEqual(result["reward"]["amount"], "32892.00")
        self.assertEqual(result["bemix"]["score"], 17)
        self.assertEqual(result["classification"]["severity"], "Critical")

    def test_program_cannot_change_financial_curves_or_bands(self):
        first = self.policy.profiles[0]
        profile = replace(first, anchors=(first.anchors[0], (F(1, 2), 450000), first.anchors[-1]))
        policy = replace(self.policy, profiles=(profile, *self.policy.profiles[1:]))
        with self.assertRaisesRegex(EvidenceError, "platform-approved"):
            self.command("publisher", "draft_policy", {"policy": canonical(policy), "governance": self.governance()})

    def test_program_cannot_relabel_a_rule_to_escape_its_severity_limits(self):
        rule = replace(self.policy.rules[0], severity="High")
        profile = replace(self.policy.profiles[0], severity="High", importance=5,
            anchors=((F(0), self.policy.bands["High"][0]), (F(1), self.policy.bands["High"][1])))
        policy = replace(self.policy, rules=(rule, *self.policy.rules[1:]), profiles=(profile, *self.policy.profiles[1:]), caps=())
        with self.assertRaisesRegex(EvidenceError, "platform-approved"):
            self.command("publisher", "draft_policy", {"policy": canonical(policy), "governance": self.governance()})

    def test_program_cannot_replace_criterion_definitions_or_award_exceptions(self):
        for key in ("rubrics", "award_adjustments"):
            governance = self.governance()
            if key == "rubrics":
                governance[key]["primary"]["extent"] = "Different meaning chosen by program"
            else:
                governance[key][0]["max_delta_minor_units"] = 100000000
            draft = self.command("publisher", "draft_policy", {"policy": canonical(self.policy), "governance": governance})
            with self.assertRaisesRegex(EvidenceError, "platform-approved"):
                self.command("publisher", "publish_policy", {"expected_policy_record_hash": draft["hash"], "reason": "Attempted change"}, draft["id"])
            self.assert_error(404, lambda: self.service.read_active_policy(self.tokens["researcher"]["token"], "alpha", self.policy.asset))
            if key == "rubrics":
                self.policy = replace(self.policy, version="TEST-APPROVED-NEXT")

    def test_program_request_never_activates_a_policy_without_platform_approval(self):
        draft, request = self.request_review()
        self.assertEqual(request["kind"], "policy_review_request")
        self.assertEqual(request["body"]["platform_validation"]["status"], "passed")
        self.assert_error(404, lambda: self.service.read_active_policy(self.tokens["researcher"]["token"], "alpha", self.policy.asset))
        self.assert_error(422, lambda: self.command("triager", "create_case", {
            "researcher": self.people["researcher"], "policy_id": draft["id"], "reference": "NOT-LIVE"}))

    def test_platform_approval_requires_the_exact_record_hash(self):
        draft, _ = self.request_review()
        error = self.assert_error(409, lambda: self.service.approve_policy("alpha", draft["id"], "0" * 64, "platform", "Review"))
        self.assertEqual(error.code, "policy_changed")

    def test_platform_cannot_approve_a_draft_that_has_not_been_submitted_for_review(self):
        draft = self.command("publisher", "draft_policy", {"policy": canonical(self.policy), "governance": self.governance()})
        self.assertEqual(self.assert_error(409, lambda: self.platform_approve(draft)).code, "review_request_required")

    def test_platform_approval_is_program_scoped(self):
        draft, _ = self.request_review()
        self.assert_error(404, lambda: self.platform_approve(draft, program="beta"))

    def test_live_publication_is_bound_to_platform_approval_and_configuration_tests(self):
        draft, _ = self.request_review()
        approved = self.platform_approve(draft)
        publication, approval = approved["publication"], approved["approval"]
        self.assertEqual(publication["body"]["platform_approval_hash"], approval["hash"])
        self.assertEqual(approval["body"]["policy_record_hash"], draft["hash"])
        certificate = approval["body"]["platform_validation"]
        self.assertEqual(certificate["policy_sha256"], fingerprint(self.policy))
        self.assertGreater(certificate["financial_grid_points"], 0)
        self.assertGreater(certificate["input_transition_checks"], 0)
        self.assertEqual(len(certificate["profile_scenarios"]), len(self.policy.profiles))

    def test_later_settings_require_new_platform_review_and_old_reports_keep_the_snapshot(self):
        case = self.case()
        old_id = case["body"]["policy_id"]
        policy = self.changed_policy()
        draft, _ = self.request_review(policy)
        token = self.tokens["researcher"]["token"]
        self.assertEqual(self.service.read_active_policy(token, "alpha", self.policy.asset)["policy"]["id"], old_id)
        self.platform_approve(draft)
        self.assertEqual(self.service.read_active_policy(token, "alpha", self.policy.asset)["policy"]["id"], draft["id"])
        self.assertEqual(self.state(case)["case"]["body"]["policy_id"], old_id)
        self.assertEqual(self.state(case)["validated_bounty"]["amount"], "31800.00")

    def test_new_platform_limits_invalidate_pending_drafts_and_review_certificates(self):
        draft, _ = self.request_review()
        policy = replace(self.policy, importance_influence=F(1, 4))
        self.trust_guardrails(policy, self.governance(policy))
        self.assertEqual(self.assert_error(409, lambda: self.platform_approve(draft)).code, "platform_configuration_changed")

    def test_platform_template_versions_are_immutable(self):
        template = example_guardrails(self.policy, self.governance())
        changed = deepcopy(template)
        changed["base_governance"]["source_reference"] = "Another source under the same version"
        self.assert_error(409, lambda: self.service.install_guardrails("alpha", changed, "Changed without a new version"))

    def test_platform_requires_valid_bounds_and_a_scenario_for_every_profile(self):
        template = example_guardrails(self.policy, self.governance())
        for key in ("context_weight_bounds", "context_share_bounds", "profile_scenarios"):
            changed = deepcopy(template); changed[key].pop("primary")
            with self.subTest(key=key), self.assertRaises(EvidenceError):
                validate_template(changed)

    def test_even_platform_setup_rejects_a_broken_acceptance_scenario(self):
        template = example_guardrails(self.policy, self.governance())
        template["profile_scenarios"]["primary"]["facts"]["unauthorized_fund_effect"]["value"] = False
        with self.assertRaisesRegex(EvidenceError, "acceptance scenario"):
            self.service.install_guardrails("alpha", template, "Deliberately broken synthetic case")

    def test_concurrent_platform_approvals_only_activate_once(self):
        draft, _ = self.request_review()
        def approve(_):
            try:
                return self.platform_approve(draft)["publication"]["kind"]
            except ServiceError as error:
                return error.status
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(approve, range(2)))
        self.assertCountEqual(results, ["publication", 409])
        with self.store.connection() as conn:
            self.assertEqual(len(self.store.children(conn, "platform_approval", "alpha", draft["id"])), 1)

    def test_limits_are_consultable_but_no_http_route_can_approve_or_replace_them(self):
        with TestClient(create_app(self.service, ["testserver"])) as client:
            headers = {"Authorization": "Bearer " + self.tokens["publisher"]["token"]}
            base = "/v1/programs/alpha"
            result = client.get(base + "/platform-guardrails", params={"asset": self.policy.asset}, headers=headers)
            self.assertEqual(result.status_code, 200)
            self.assertEqual(result.json()["body"]["template"]["base_policy"]["importance_ranges"]["Critical"], [8, 10])
            self.assertEqual(client.post(base + "/platform-guardrails", json={}, headers=headers).status_code, 405)
            self.assertEqual(client.post(base + "/policies/unknown/approve", json={}, headers=headers).status_code, 404)

    def test_platform_can_return_a_mathematically_valid_configuration_for_changes(self):
        draft, _ = self.request_review()
        feedback = self.service.request_policy_changes("alpha", draft["id"], draft["hash"], "platform-reviewer",
            "The weights satisfy the limits but the program must justify these relative priorities")
        self.assertEqual(feedback["body"]["status"], "changes_requested")
        self.assertEqual(self.assert_error(409, lambda: self.platform_approve(draft)).code, "policy_changes_requested")
        state = self.service.read_policy(self.tokens["publisher"]["token"], "alpha", draft["id"])
        self.assertEqual(len(state["platform_feedback"]), 1)
        self.assertFalse(state["publication"])

    def test_platform_feedback_cannot_rewrite_an_already_live_policy(self):
        draft = self.publish()
        self.assert_error(409, lambda: self.service.request_policy_changes("alpha", draft["id"], draft["hash"], "platform", "Too late"))

    def test_a_legacy_publication_record_without_platform_approval_is_not_live(self):
        draft = self.command("publisher", "draft_policy", {"policy": canonical(self.policy), "governance": self.governance()})
        with self.store.connection(write=True) as conn:
            self.store.insert(conn, "publication", "alpha", "synthetic-legacy-import",
                {"policy_id": draft["id"], "policy_record_hash": draft["hash"], "reason": "Legacy record without approval"}, parent=draft["id"])
        with self.assertRaisesRegex(IntegrityFailure, "platform approval"):
            self.service.read_active_policy(self.tokens["researcher"]["token"], "alpha", self.policy.asset)


if __name__ == "__main__":
    import unittest
    unittest.main()
