from dataclasses import replace
from pathlib import Path
import secrets
import tempfile
import unittest
import uuid
from bemix_pilot.engine import canonical
from bemix_pilot.examples import base_report, bind, change_facts, metrics, sample_policy, submission_options, estimation_config, adjustment_rules
from bemix_service.service import DecisionService, ServiceError
from bemix_service.storage import Store
from bemix_pilot.platform_example import example_guardrails

class Harness(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name)
        self.key = secrets.token_bytes(32)
        self.store = Store.initialize(self.directory / "decisions.sqlite", self.key)
        self.service = DecisionService(self.store, requests_per_minute=1000)
        self.service.add_program("alpha")
        self.service.add_program("beta")
        self.people, self.tokens = {}, {}
        for name, role in (("publisher", "publisher"), ("triager", "triager"),
                           ("security", "security"), ("security2", "security"),
                           ("researcher", "researcher"), ("other", "researcher"), ("other_triager", "triager")):
            subject = self.service.add_principal(name)
            self.people[name] = subject
            self.service.grant(subject, "alpha", role)
            self.tokens[name] = self.service.issue_token(subject)
        self.policy = replace(sample_policy(), version="TEST-APPROVED-1", experimental=False)
        self.report = bind(base_report(sample_policy()), self.policy)
        self.trust_guardrails(self.policy, self.governance())

    def trust_guardrails(self, policy, governance, program="alpha"):
        return self.service.install_guardrails(program, example_guardrails(policy, governance),
                                              "Trusted synthetic platform setup, not program-supplied limits")

    def platform_approve(self, draft, program="alpha"):
        return self.service.approve_policy(program, draft["id"], draft["hash"], "synthetic-platform-reviewer",
                                          "Platform reviewed the bounded settings and deterministic test results")

    def command(self, who, operation, body, resource="", key=None, program="alpha"):
        return self.service.command(self.tokens[who]["token"], program, operation, resource, body, key or str(uuid.uuid4()))

    def governance(self, policy=None):
        policy = policy or self.policy
        return {"source_reference": "SYNTHETIC test source", "calibration_reference": "SYNTHETIC test calibration",
                "precedence_reference": "SYNTHETIC reviewed precedence",
                "rubrics": {p.id: {m: "SYNTHETIC observable rubric for testing only" for m in p.weights} for p in policy.profiles},
                "submission_options": submission_options(), "estimation": estimation_config(), "award_adjustments": adjustment_rules()}

    def publish(self, policy=None):
        policy = policy or self.policy
        governance = self.governance(policy)
        self.trust_guardrails(policy, governance)
        draft = self.command("publisher", "draft_policy", {"policy": canonical(policy), "governance": governance})
        self.command("security", "publish_policy", {"expected_policy_record_hash": draft["hash"], "reason": "Independent synthetic review"}, draft["id"])
        self.platform_approve(draft)
        return draft

    def case(self, assessed=True):
        draft = self.publish()
        case = self.command("triager", "create_case", {"researcher": self.people["researcher"], "policy_id": draft["id"], "reference": "Synthetic external report"})
        if assessed:
            self.command("triager", "initial", {"report": canonical(self.report), "expected_version": 0, "reason": "Reviewed synthetic evidence"}, case["id"])
        return case

    def state(self, case, who="researcher"):
        return self.service.read_case(self.tokens[who]["token"], "alpha", case["id"])

    def reduced_report(self):
        return replace(change_facts(self.report, restricted_path=True), core=(4, 0, 0, 0), metrics={"restricted": metrics("0.6")})

    def correction(self, case):
        return self.command("triager", "revise", {"report": canonical(self.reduced_report()), "expected_version": 1,
                                                   "kind": "correction", "reason": "Previously omitted prerequisite with evidence"}, case["id"])

    def assert_error(self, status, function):
        with self.assertRaises(ServiceError) as error:
            function()
        self.assertEqual(error.exception.status, status, str(error.exception))
        return error.exception


