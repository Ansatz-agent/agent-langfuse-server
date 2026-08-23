from django.test import TestCase


class HealthCheckTests(TestCase):
    def test_healthz_returns_minimal_ok_payload(self):
        response = self.client.get("/healthz")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"status": "ok"})
        self.assertEqual(response.headers["Cache-Control"], "no-store")
