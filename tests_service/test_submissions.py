from dataclasses import replace

from bemix_pilot.engine import EvidenceError, canonical
from support import Harness
from bemix_pilot.examples import example_inputs


class SubmissionTests(Harness):
    def claims(self, *options):
        return [{"option_id": option, "evidence": {"proof": "Synthetic report section with evidence"}} for option in options]

    def submit(self, *options):
        policy = self.publish()
        response = self.command("researcher", "submit", {"asset": self.policy.asset, "claims": self.claims(*(options or ("withdrawal_manipulation",))), "summary": "Synthetic submission", "inputs": example_inputs()})
        return policy, response

    def assign(self, policy, response):
        return self.command("triager", "create_case", {"researcher": self.people["researcher"], "policy_id": policy["id"],
            "submission_id": response["submission"]["id"], "reference": "SUBMISSION-CASE"})

    def review(self, *options, outcome="confirmed", replacements=None):
        return {"claims": [{"option_id": option, "outcome": outcome,
                            "confirmed_option_ids": replacements if replacements is not None else [option],
                            "reason": "Responsible assessor checks the evidence"} for option in options]}

    def test_researcher_gets_program_options_and_consultable_scoring(self):
        self.publish()
        options = self.service.read_options(self.tokens["researcher"]["token"], "alpha", self.policy.asset)
        self.assertIn("withdrawal_manipulation", [o["id"] for o in options["options"]])
        self.assertIn("weights", options["scoring_policy"]["profiles"][0])

    def test_complete_declared_inputs_produce_money_without_validated_status(self):
        _, response = self.submit()
        preview = response["declaration"]["body"]["preview"]
        self.assertEqual(preview["status"], "awaiting_validation")
        self.assertFalse(preview["trusted_facts_assigned"])
        self.assertEqual(preview["financial_amount"], "36350.00")
        self.assertFalse(response["estimated_bounty"]["facts_validated"])
        self.assertEqual(preview["options"][0]["conditional_severities"], ["Critical"])

    def test_researcher_can_declare_multiple_behaviours(self):
        _, response = self.submit("withdrawal_manipulation", "key_exfiltration")
        self.assertEqual(len(response["declaration"]["body"]["claims"]), 2)

    def test_unknown_duplicate_or_empty_selection_is_rejected(self):
        self.publish()
        for claims in ([], self.claims("missing"), self.claims("silent_signing", "silent_signing")):
            with self.assertRaises(EvidenceError):
                self.command("researcher", "submit", {"asset": self.policy.asset, "claims": claims, "summary": "x", "inputs": example_inputs()})

    def test_researcher_cannot_inject_severity_weights_or_trusted_fact_values(self):
        self.publish()
        claims = self.claims("withdrawal_manipulation")
        claims[0]["severity"] = "Critical"
        with self.assertRaises(EvidenceError):
            self.command("researcher", "submit", {"asset": self.policy.asset, "claims": claims, "summary": "x", "inputs": example_inputs()})

    def test_program_evidence_prompts_are_enforced(self):
        self.publish()
        with self.assertRaises(EvidenceError):
            self.command("researcher", "submit", {"asset": self.policy.asset,
                "claims": [{"option_id": "withdrawal_manipulation", "evidence": {}}], "summary": "x", "inputs": example_inputs()})

    def test_assessor_must_confirm_or_correct_each_declaration(self):
        policy, response = self.submit(); case = self.assign(policy, response)
        self.assert_error(422, lambda: self.command("triager", "initial", {
            "report": canonical(self.report), "expected_version": 0, "reason": "x"}, case["id"]))
        decision = self.command("triager", "initial", {"report": canonical(self.report), "expected_version": 0,
            "reason": "Reviewed selection and evidence", "claim_review": self.review("withdrawal_manipulation")}, case["id"])
        self.assertEqual(decision["body"]["result"]["reward"]["amount"], "31800.00")
        self.assertEqual(decision["body"]["claim_source"]["claim_version"], 1)

    def test_wrong_declaration_can_be_changed_by_the_same_assigned_triager(self):
        policy, response = self.submit(); case = self.assign(policy, response)
        decision = self.command("triager", "initial", {"report": canonical(self.reduced_report()), "expected_version": 0,
            "reason": "Actual prerequisites support a different rule", "claim_review": self.review("withdrawal_manipulation", outcome="changed", replacements=["restricted_withdrawal"])}, case["id"])
        self.assertEqual(decision["body"]["result"]["classification"]["severity"], "High")
        self.assertEqual(decision["body"]["result"]["reward"]["amount"], "3500.00")

    def test_confirmed_label_must_agree_with_the_assessed_facts(self):
        policy, response = self.submit(); case = self.assign(policy, response)
        with self.assertRaises(EvidenceError):
            self.command("triager", "initial", {"report": canonical(self.reduced_report()), "expected_version": 0,
                "reason": "Inconsistent confirmation", "claim_review": self.review("withdrawal_manipulation")}, case["id"])

    def test_unsupported_claim_can_be_rejected_without_creating_another_review(self):
        policy, response = self.submit(); case = self.assign(policy, response)
        report = replace(self.report, eligibility="rejected")
        decision = self.command("triager", "initial", {"report": canonical(report), "expected_version": 0, "reason": "Not demonstrated",
            "claim_review": self.review("withdrawal_manipulation", outcome="not_demonstrated", replacements=[])}, case["id"])
        self.assertEqual(decision["body"]["result"]["reward"]["status"], "ineligible")

    def test_declaration_edits_are_versioned_and_stop_after_assessment(self):
        policy, response = self.submit(); sid = response["submission"]["id"]
        changed = self.command("researcher", "change_claims", {"expected_version": 1, "claims": self.claims("induced_signature")}, sid)
        self.assertEqual(changed["revision"], 2)
        self.assert_error(409, lambda: self.command("researcher", "change_claims", {"expected_version": 1, "claims": self.claims("withdrawal_manipulation")}, sid))
        case = self.assign(policy, response)
        self.command("triager", "initial", {"report": canonical(self.report), "expected_version": 0, "reason": "Updated claim evaluated",
            "claim_review": self.review("induced_signature", outcome="changed", replacements=["withdrawal_manipulation"])}, case["id"])
        self.assert_error(409, lambda: self.command("researcher", "change_claims", {"expected_version": 2, "claims": self.claims("silent_signing")}, sid))

    def test_submission_snapshot_survives_publication_of_a_later_policy(self):
        policy, response = self.submit()
        self.publish(replace(self.policy, version="TEST-APPROVED-2"))
        case = self.assign(policy, response)
        self.assertEqual(case["body"]["policy_id"], policy["id"])

    def test_submission_cannot_be_reassigned_to_a_different_researcher(self):
        policy, response = self.submit()
        self.assert_error(422, lambda: self.command("triager", "create_case", {"researcher": self.people["other"], "policy_id": policy["id"],
            "submission_id": response["submission"]["id"], "reference": "wrong-owner"}))

    def test_other_researcher_or_publisher_cannot_read_private_declarations(self):
        _, response = self.submit(); sid = response["submission"]["id"]
        for who in ("other", "publisher"):
            self.assert_error(404, lambda: self.service.read_submission(self.tokens[who]["token"], "alpha", sid))
        self.assertEqual(self.service.read_submission(self.tokens["researcher"]["token"], "alpha", sid)["declaration"]["revision"], 1)

    def test_other_option_is_not_assigned_an_automatic_category(self):
        _, response = self.submit("other")
        self.assertEqual(response["declaration"]["body"]["preview"]["options"][0]["conditional_severities"], [])

    def test_claim_review_can_be_retained_for_compatible_financial_updates(self):
        policy, response = self.submit(); case = self.assign(policy, response)
        self.command("triager", "initial", {"report": canonical(self.report), "expected_version": 0, "reason": "x",
            "claim_review": self.review("withdrawal_manipulation")}, case["id"])
        updated = self.command("triager", "revise", {"report": canonical(self.report), "expected_version": 1, "kind": "correction", "reason": "Clarified evidence reference"}, case["id"])
        self.assertEqual(updated["body"]["claim_review"], self.review("withdrawal_manipulation"))

    def test_conflicting_reclassification_requires_updated_claim_review(self):
        policy, response = self.submit(); case = self.assign(policy, response)
        self.command("triager", "initial", {"report": canonical(self.report), "expected_version": 0, "reason": "x",
            "claim_review": self.review("withdrawal_manipulation")}, case["id"])
        with self.assertRaises(EvidenceError):
            self.command("triager", "revise", {"report": canonical(self.reduced_report()), "expected_version": 1, "kind": "correction", "reason": "Prerequisites changed"}, case["id"])
