"""Regression tests for the FIDO2 client port to python-fido2 2.x (Cluster A).

These lock in the fido2 2.x contract so a future reversion to the pre-2.0 API
(or a fido2 major upgrade) fails loudly instead of crashing at runtime.
"""
import base64
import threading
import types
import unittest
from dataclasses import fields

from tests.qt_harness import ensure_app


def _b64(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode("ascii")


def setUpModule():
    ensure_app()


class Fido2ApiShapeTests(unittest.TestCase):
    """The installed fido2 must match the shapes the client relies on."""

    def test_client_second_arg_is_client_data_collector(self):
        import inspect
        from fido2.client import Fido2Client, DefaultClientDataCollector
        params = list(inspect.signature(Fido2Client.__init__).parameters)
        self.assertEqual(params[2], "client_data_collector")
        self.assertTrue(hasattr(DefaultClientDataCollector("https://x"), "collect_client_data"))

    def test_registration_response_shape(self):
        from fido2.webauthn import RegistrationResponse, AuthenticatorAttestationResponse
        top = {f.name for f in fields(RegistrationResponse)}
        self.assertNotIn("attestation_object", top)   # old code read this -> AttributeError
        self.assertIn("response", top)
        inner = {f.name for f in fields(AuthenticatorAttestationResponse)}
        self.assertEqual({"attestation_object", "client_data"}, inner)

    def test_assertion_response_shape(self):
        from fido2.webauthn import AuthenticationResponse, AuthenticatorAssertionResponse
        top = {f.name for f in fields(AuthenticationResponse)}
        self.assertNotIn("credential_id", top)        # never existed in 2.x
        self.assertNotIn("authenticator_data", top)   # lives on .response
        self.assertIn("raw_id", top)
        inner = {f.name for f in fields(AuthenticatorAssertionResponse)}
        self.assertTrue({"authenticator_data", "signature", "client_data"} <= inner)

    def test_windows_client_import_path(self):
        with self.assertRaises(ModuleNotFoundError):
            __import__("fido2.win_api")           # removed in 2.x
        from fido2.client.windows import WindowsClient  # noqa: F401


class Fido2WrapperTests(unittest.TestCase):
    """The wrapper must produce correct transport dicts from 2.x responses."""

    def _wrapper_with_fake_client(self):
        from _mext.services import fido2_client as fc
        import fido2.client as real_client
        from fido2.client import DefaultClientDataCollector

        captured = {}

        class _AttResp:
            attestation_object = b"ATTEST"; client_data = b"CDATA_REG"
        class _RegResp:
            response = _AttResp()
        class _AssResp:
            authenticator_data = b"AUTHDATA"; signature = b"SIG"; client_data = b"CDATA_AUTH"
        class _AuthResp:
            response = _AssResp(); raw_id = b"CREDID"
        class _Selection:
            def get_response(self, idx): return _AuthResp()

        class FakeClient:
            def __init__(self, device, cdc, user_interaction=None, **kw):
                captured["is_default_collector"] = isinstance(cdc, DefaultClientDataCollector)
            def make_credential(self, options, event=None): return _RegResp()
            def get_assertion(self, options, event=None): return _Selection()

        self.addCleanup(setattr, real_client, "Fido2Client", real_client.Fido2Client)
        real_client.Fido2Client = FakeClient
        self.addCleanup(setattr, fc, "_is_windows_admin", fc._is_windows_admin)
        fc._is_windows_admin = lambda: True        # force the HID path

        cfg = types.SimpleNamespace(fido2_origin="https://example.com", fido2_rp_id="example.com")
        w = fc.Fido2ClientWrapper(config=cfg)
        w._discover_devices = lambda: ["fake-hid-device"]
        return w, captured

    def test_make_credential_uses_response_fields(self):
        w, captured = self._wrapper_with_fake_client()
        opts = {
            "rp": {"id": "example.com", "name": "Example"},
            "user": {"id": _b64(b"user-1"), "name": "u", "displayName": "U"},
            "challenge": _b64(b"reg-challenge"),
            "pubKeyCredParams": [{"type": "public-key", "alg": -7}],
        }
        out = w.make_credential(opts)
        self.assertTrue(captured["is_default_collector"])
        self.assertEqual(out["attestationObject"], _b64(b"ATTEST"))
        self.assertEqual(out["clientDataJSON"], _b64(b"CDATA_REG"))

    def test_get_assertion_uses_response_fields_and_raw_id(self):
        w, _ = self._wrapper_with_fake_client()
        out = w.get_assertion({"challenge": _b64(b"auth-challenge"), "rpId": "example.com"})
        self.assertEqual(out["authenticatorData"], _b64(b"AUTHDATA"))
        self.assertEqual(out["signature"], _b64(b"SIG"))
        self.assertEqual(out["clientDataJSON"], _b64(b"CDATA_AUTH"))
        self.assertEqual(out["credentialId"], _b64(b"CREDID"))


class Fido2PinBridgeTests(unittest.TestCase):
    def test_request_pin_blocks_until_provided(self):
        from _mext.services.fido2_client import Fido2UserInteraction
        inter = Fido2UserInteraction()
        inter._PIN_WAIT_TIMEOUT = 2.0
        out = {}
        t = threading.Thread(target=lambda: out.__setitem__("pin", inter.request_pin(None, "rp")))
        t.start()
        time_slept = 0.0
        while "pin" in out:  # should not resolve before provide_pin
            break
        self.assertNotIn("pin", out)
        inter.provide_pin("123456")
        t.join(timeout=3)
        self.assertEqual(out.get("pin"), "123456")


if __name__ == "__main__":
    unittest.main()
