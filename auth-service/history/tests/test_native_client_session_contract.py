import json
from pathlib import Path

from django.test import SimpleTestCase
from django.urls import reverse


class NativeClientSessionContractTests(SimpleTestCase):
    def test_fixture_matches_native_session_routes_shapes_and_reasons(self):
        contract_path = (
            Path(__file__).resolve().parents[2]
            / "contracts"
            / "native-client-session-v1.json"
        )
        contract = json.loads(contract_path.read_text(encoding="utf-8"))

        self.assertEqual(contract["version"], 1)
        self.assertEqual(contract["routes"], {
            "session": reverse("native-client-session"),
            "current": reverse("native-client-session-current"),
            "trace_token": reverse("native-trace-token"),
        })
        self.assertEqual(contract["headers"], {
            "authorization": "Authorization",
            "installation_id": "X-Ansatz-Installation-ID",
        })
        self.assertEqual(contract["explicit_revocations"], [
            "account_disabled",
            "account_revoked",
            "session_revoked",
        ])
        self.assertEqual(contract["transient_codes"], ["invalid_session_credential"])
        self.assertEqual(contract["issue_request_keys"], [
            "client_version",
            "installation_id",
        ])
        self.assertEqual(contract["issue_response_keys"], [
            "account_id",
            "installation_id",
            "issued_at",
            "session_id",
            "session_token",
            "username",
        ])
        self.assertEqual(contract["active_status_keys"], [
            "account_id",
            "installation_id",
            "server_time",
            "session_id",
            "state",
            "username",
        ])
