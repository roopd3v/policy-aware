"""Selections and explicit fields must form one consistent declaration."""

from copy import deepcopy
import uuid

from fastapi.testclient import TestClient

from bemix_pilot.engine import EvidenceError
from bemix_pilot.examples import example_inputs
from bemix_service.api import create_app
from bemix_service.estimates import submitted_report
from support import Harness


class SubmissionConsistencyTests(Harness):
    def claims(self, option="withdrawal_manipulation"):
        return [{"option_id": option, "evidence": {"proof": "Synthetic audit evidence"}}]

    def payload(self, inputs=None, option="withdrawal_manipulation"):
        return {"asset": self.policy.asset, "summary": "Synthetic consistency test",
                "claims": self.claims(option), "inputs": inputs if inputs is not None else example_inputs()}

    def test_explicit_fact_cannot_negate_or_erase_a_selected_true_condition(self):
        for value in (False, None):
            with self.subTest(value=value):
                inputs = example_inputs()
                inputs["facts"] = {"unauthorized_fund_effect": value}
                with self.assertRaisesRegex(EvidenceError, "unauthorized_fund_effect.*selected behaviour"):
                    submitted_report(self.policy, self.governance(), self.claims(), inputs)

    def test_explicit_fact_cannot_negate_or_erase_a_selected_false_condition(self):
        for value in (True, None):
            with self.subTest(value=value):
                inputs = example_inputs()
                inputs["facts"] = {"restricted_path": value}
                with self.assertRaisesRegex(EvidenceError, "restricted_path.*selected behaviour"):
                    submitted_report(self.policy, self.governance(), self.claims(), inputs)

    def test_matching_explicit_conditions_are_accepted_and_remain_provisional(self):
        self.publish()
        inputs = example_inputs()
        inputs["facts"] = {"unauthorized_fund_effect": True, "restricted_path": False}
        result = self.service.preview_estimate(self.tokens["researcher"]["token"], "alpha",
            {k: v for k, v in self.payload(inputs).items() if k != "summary"})
        self.assertEqual(result["amount"], "36350.00")
        self.assertFalse(result["facts_validated"])

    def test_values_for_other_conditions_can_still_override_published_form_defaults(self):
        inputs = example_inputs()
        inputs["facts"] = {"no_user_interaction": True, "no_effective_mitigation": None}
        report = submitted_report(self.policy, self.governance(), self.claims(), inputs)
        self.assertIs(report.facts["no_user_interaction"].value, True)
        self.assertIsNone(report.facts["no_effective_mitigation"].value)

    def test_custom_published_option_conditions_cannot_be_overridden_either(self):
        governance = self.governance()
        governance["estimation"]["option_facts"] = {"withdrawal_manipulation": {"silent_signing": True}}
        inputs = example_inputs()
        inputs["facts"] = {"silent_signing": False}
        with self.assertRaisesRegex(EvidenceError, "silent_signing.*selected behaviour"):
            submitted_report(self.policy, governance, self.claims(), inputs)

    def test_http_estimate_and_submission_reject_contradictions_without_storing_a_report(self):
        self.publish()
        inputs = example_inputs()
        inputs["facts"] = {"unauthorized_fund_effect": False}
        with TestClient(create_app(self.service, ["testserver"])) as client:
            for endpoint in ("estimates", "submissions"):
                with self.subTest(endpoint=endpoint):
                    body = self.payload(inputs)
                    if endpoint == "estimates":
                        body.pop("summary")
                    response = client.post("/v1/programs/alpha/" + endpoint, json=body,
                        headers={"Authorization": "Bearer " + self.tokens["researcher"]["token"],
                                 "Idempotency-Key": str(uuid.uuid4())})
                    self.assertEqual(response.status_code, 422, response.text)
                    self.assertIn("selected behaviour", response.json()["message"])
        with self.store.connection() as conn:
            self.assertEqual(conn.execute("SELECT count(*) FROM records WHERE kind IN ('submission','claim','decision')").fetchone()[0], 0)

    def test_rejected_edit_preserves_the_previous_estimate_and_revision(self):
        self.publish()
        submission = self.command("researcher", "submit", self.payload())
        identifier = submission["submission"]["id"]
        inputs = deepcopy(example_inputs())
        inputs["facts"] = {"restricted_path": True}
        with self.assertRaisesRegex(EvidenceError, "selected behaviour"):
            self.command("researcher", "change_claims", {"expected_version": 1, "inputs": inputs}, identifier)
        with self.store.connection() as conn:
            history = self.store.children(conn, "claim", "alpha", identifier)
        self.assertEqual(len(history), 1)
        self.assertEqual(history[0]["revision"], 1)
        self.assertEqual(history[0]["body"]["estimate"]["amount"], "36350.00")

    def test_researcher_can_change_selection_and_matching_inputs_together(self):
        self.publish()
        submission = self.command("researcher", "submit", self.payload())
        inputs = {"core": [4, 0, 0, 1], "facts": {"unauthorized_fund_effect": True, "restricted_path": True},
                  "metrics": {"restricted": {"extent": "0.6", "reliability": "0.6"}}}
        changed = self.command("researcher", "change_claims", {"expected_version": 1,
            "claims": self.claims("restricted_withdrawal"), "inputs": inputs}, submission["submission"]["id"])
        self.assertEqual(changed["body"]["estimate"]["severity"], "High")
        self.assertEqual(changed["body"]["estimate"]["amount"], "3500.00")


if __name__ == "__main__":
    import unittest
    unittest.main()
