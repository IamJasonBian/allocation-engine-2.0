import base64
import os
import unittest
from unittest import mock

import gcp_secrets


class GcpSecretsTests(unittest.TestCase):
    def test_add_secret_version_uses_add_version_endpoint(self):
        resp = mock.Mock()
        resp.raise_for_status = mock.Mock()
        resp.json.return_value = {"name": "projects/p/secrets/s/versions/9"}
        with mock.patch.dict(os.environ, {"GOOGLE_ACCESS_TOKEN": "tok"}, clear=False), \
             mock.patch("gcp_secrets.requests.post", return_value=resp) as post:
            name = gcp_secrets.add_secret_version("my-secret", "hello", "my-project")
        self.assertEqual(name, "projects/p/secrets/s/versions/9")
        url = post.call_args.args[0]
        self.assertIn(":addVersion", url)
        self.assertNotIn(":addSecretVersion", url)
        body = post.call_args.kwargs["json"]
        self.assertEqual(
            base64.b64decode(body["payload"]["data"]).decode(),
            "hello",
        )


if __name__ == "__main__":
    unittest.main()
