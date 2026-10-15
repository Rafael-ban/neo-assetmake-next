"""Cross-platform FIDO2/WebAuthn client for the asset store.

On Windows: prefers the native WindowsClient (if not running as admin).
On macOS/Linux: uses CtapHidDevice discovery + Fido2Client.

All operations use a synthetic origin derived from the configured RP ID
since this is a desktop application rather than a browser.
"""

from __future__ import annotations

import ctypes
import logging
import platform
import threading
from typing import Any, Optional

from PyQt6.QtCore import QObject, pyqtSignal as Signal

from _mext.core.config import Config, get_config

logger = logging.getLogger(__name__)


class Fido2UserInteraction(QObject):
    """User interaction handler that bridges FIDO2 prompts to Qt signals.

    Signals
    -------
    touch_required()
        Emitted when the authenticator needs a user touch/presence check.
    pin_required(int)
        Emitted when a PIN is needed. The int indicates remaining retries.
    pin_provided(str)
        Emitted internally when the UI provides a PIN.
    """

    touch_required = Signal()
    pin_required = Signal(int)
    pin_provided = Signal(str)

    # How long request_pin() blocks waiting for the UI to supply a PIN.
    _PIN_WAIT_TIMEOUT = 120.0

    def __init__(self, parent: Optional[QObject] = None) -> None:
        super().__init__(parent)
        self._pending_pin: Optional[str] = None
        # request_pin() runs inside the fido2 worker's QThread.run(), which has
        # no Qt event loop, so pin_provided cannot be delivered there. Block on a
        # threading.Event that provide_pin() sets from the GUI thread instead.
        self._pin_event = threading.Event()
        self.pin_provided.connect(self._on_pin_provided)

    def _on_pin_provided(self, pin: str) -> None:
        """Slot for the pin_provided signal (Qt path); delegates to provide_pin."""
        self.provide_pin(pin)

    def provide_pin(self, pin: Optional[str]) -> None:
        """Deliver a UI-entered PIN to the blocked request_pin() call.

        Thread-safe: sets the pending value and wakes request_pin() via the
        threading.Event, independent of any Qt event loop.
        """
        self._pending_pin = pin
        self._pin_event.set()

    def prompt_up(self) -> None:
        """Called by fido2 library when user presence is needed."""
        logger.info("FIDO2: Touch your security key")
        self.touch_required.emit()

    def request_pin(self, permissions: Any, rp_id: Optional[str] = None) -> Optional[str]:
        """Called by fido2 when a PIN is needed; blocks until the UI supplies one.

        Emits ``pin_required`` then blocks on a threading.Event until
        ``provide_pin`` is called (or the timeout elapses). Returning ``None``
        is fido2's documented "user cancelled" contract.
        """
        logger.info("FIDO2: PIN requested (rp_id=%s)", rp_id)
        self._pending_pin = None
        self._pin_event.clear()
        self.pin_required.emit(8)  # Default max retries as hint

        if not self._pin_event.wait(self._PIN_WAIT_TIMEOUT):
            logger.warning(
                "FIDO2: PIN entry timed out after %.0fs", self._PIN_WAIT_TIMEOUT
            )
            return None
        return self._pending_pin

    def request_uv(self, permissions: Any, rp_id: Optional[str] = None) -> bool:
        """Called when user verification is needed. We rely on PIN or touch."""
        self.prompt_up()
        return True


def _is_windows_admin() -> bool:
    """Check if the current process is running with admin/elevated privileges on Windows."""
    if platform.system() != "Windows":
        return False
    try:
        return ctypes.windll.shell32.IsUserAnAdmin() != 0
    except Exception:
        return False


class Fido2ClientWrapper:
    """Wraps python-fido2 to provide a unified API across platforms.

    Parameters
    ----------
    config : Config, optional
        Application configuration (used for origin and rp_id).
    """

    def __init__(self, config: Optional[Config] = None) -> None:
        self._config = config or get_config()
        self._origin = self._config.fido2_origin
        self._rp_id = self._config.fido2_rp_id
        self._interaction = Fido2UserInteraction()

    @property
    def interaction(self) -> Fido2UserInteraction:
        """Return the user interaction handler for signal connections."""
        return self._interaction

    def _discover_devices(self) -> list[Any]:
        """Discover connected FIDO2 HID devices.

        Returns a list of CtapHidDevice instances, or an empty list if
        none are found or the library is unavailable.
        """
        try:
            from fido2.hid import CtapHidDevice

            devices = list(CtapHidDevice.list_devices())
            logger.info("Discovered %d FIDO2 HID device(s)", len(devices))
            return devices
        except Exception as exc:
            logger.warning("FIDO2 device discovery failed: %s", exc)
            return []

    def _get_client(self) -> Any:
        """Return an appropriate Fido2Client for the current platform.

        On Windows (non-admin): uses WindowsClient if available.
        Otherwise: uses Fido2Client with the first discovered HID device.
        """
        system = platform.system()

        # Try Windows native client first
        if system == "Windows" and not _is_windows_admin():
            try:
                # fido2 2.x moved WindowsClient to fido2.client.windows; the old
                # top-level fido2.win_api module no longer exists.
                from fido2.client.windows import WindowsClient
                from fido2.client import DefaultClientDataCollector

                if WindowsClient.is_available():
                    logger.info("Using WindowsClient (native Windows Hello)")
                    return WindowsClient(DefaultClientDataCollector(self._origin))
            except ImportError as exc:
                # Log at warning so a broken native path is not silently invisible.
                logger.warning("WindowsClient unavailable, falling back to HID: %s", exc)

        # HID-based client (macOS, Linux, or Windows admin/fallback)
        devices = self._discover_devices()
        if not devices:
            raise RuntimeError(
                "No FIDO2 security key detected. Please insert your security key and try again."
            )

        from fido2.client import Fido2Client, UserInteraction, DefaultClientDataCollector

        # Create a bridge adapter matching the fido2 UserInteraction protocol
        class _InteractionBridge(UserInteraction):
            def __init__(self, handler: Fido2UserInteraction) -> None:
                self._handler = handler

            def prompt_up(self) -> None:
                self._handler.prompt_up()

            def request_pin(self, permissions: Any, rp_id: Optional[str] = None) -> Optional[str]:
                return self._handler.request_pin(permissions, rp_id)

            def request_uv(self, permissions: Any, rp_id: Optional[str] = None) -> bool:
                return self._handler.request_uv(permissions, rp_id)

        device = devices[0]
        # fido2 2.x: the 2nd argument is a ClientDataCollector, not the origin
        # string. DefaultClientDataCollector wraps the origin and implements
        # collect_client_data(), which the client calls internally.
        client = Fido2Client(
            device,
            DefaultClientDataCollector(self._origin),
            user_interaction=_InteractionBridge(self._interaction),
        )
        return client

    def make_credential(self, options: dict[str, Any]) -> dict[str, Any]:
        """Create a new FIDO2 credential (registration).

        Parameters
        ----------
        options : dict
            PublicKeyCredentialCreationOptions from the server, typically
            containing ``rp``, ``user``, ``challenge``, ``pubKeyCredParams``,
            and optional ``excludeCredentials``, ``authenticatorSelection``.

        Returns
        -------
        dict
            Attestation response suitable for sending to the server, including
            ``attestationObject`` and ``clientDataJSON`` (base64url-encoded).
        """
        import base64

        from fido2.webauthn import (
            PublicKeyCredentialCreationOptions,
            PublicKeyCredentialParameters,
            PublicKeyCredentialRpEntity,
            PublicKeyCredentialType,
            PublicKeyCredentialUserEntity,
        )

        client = self._get_client()

        # Parse server options into fido2 objects
        rp = PublicKeyCredentialRpEntity(
            id=options.get("rp", {}).get("id", self._rp_id),
            name=options.get("rp", {}).get("name", "Asset Store"),
        )
        user = PublicKeyCredentialUserEntity(
            id=base64.urlsafe_b64decode(options["user"]["id"] + "=="),
            name=options["user"]["name"],
            display_name=options["user"].get("displayName", options["user"]["name"]),
        )

        challenge = base64.urlsafe_b64decode(options["challenge"] + "==")

        pub_key_cred_params = []
        for param in options.get("pubKeyCredParams", [{"type": "public-key", "alg": -7}]):
            pub_key_cred_params.append(
                PublicKeyCredentialParameters(
                    type=PublicKeyCredentialType(param["type"]),
                    alg=param["alg"],
                )
            )

        creation_options = PublicKeyCredentialCreationOptions(
            rp=rp,
            user=user,
            challenge=challenge,
            pub_key_cred_params=pub_key_cred_params,
        )

        result = client.make_credential(creation_options)

        # fido2 2.x: make_credential returns a RegistrationResponse; the
        # attestation payload lives on .response (AuthenticatorAttestationResponse).
        attestation = result.response

        # Serialize for transport back to server
        attestation_object = (
            base64.urlsafe_b64encode(attestation.attestation_object).rstrip(b"=").decode("ascii")
        )
        client_data = base64.urlsafe_b64encode(attestation.client_data).rstrip(b"=").decode("ascii")

        return {
            "attestationObject": attestation_object,
            "clientDataJSON": client_data,
            "type": "public-key",
        }

    def get_assertion(self, options: dict[str, Any]) -> dict[str, Any]:
        """Perform a FIDO2 assertion (authentication).

        Parameters
        ----------
        options : dict
            PublicKeyCredentialRequestOptions from the server, containing
            ``challenge``, ``rpId``, and optional ``allowCredentials``.

        Returns
        -------
        dict
            Assertion response suitable for sending to the server, including
            ``authenticatorData``, ``signature``, ``clientDataJSON``, and
            ``credentialId`` (all base64url-encoded).
        """
        import base64

        from fido2.webauthn import (
            PublicKeyCredentialDescriptor,
            PublicKeyCredentialRequestOptions,
            PublicKeyCredentialType,
        )

        client = self._get_client()

        challenge = base64.urlsafe_b64decode(options["challenge"] + "==")
        rp_id = options.get("rpId", self._rp_id)

        allow_credentials = None
        if "allowCredentials" in options:
            allow_credentials = []
            for cred in options["allowCredentials"]:
                cred_id = base64.urlsafe_b64decode(cred["id"] + "==")
                allow_credentials.append(
                    PublicKeyCredentialDescriptor(
                        type=PublicKeyCredentialType(cred.get("type", "public-key")),
                        id=cred_id,
                    )
                )

        request_options = PublicKeyCredentialRequestOptions(
            challenge=challenge,
            rp_id=rp_id,
            allow_credentials=allow_credentials,
        )

        result = client.get_assertion(request_options)
        assertion = result.get_response(0)

        # fido2 2.x: get_response returns an AuthenticationResponse; the assertion
        # payload lives on .response, and the credential id is raw_id (there is no
        # credential_id attribute).
        response = assertion.response
        authenticator_data = (
            base64.urlsafe_b64encode(response.authenticator_data).rstrip(b"=").decode("ascii")
        )
        signature = base64.urlsafe_b64encode(response.signature).rstrip(b"=").decode("ascii")
        client_data = base64.urlsafe_b64encode(response.client_data).rstrip(b"=").decode("ascii")
        credential_id = (
            base64.urlsafe_b64encode(assertion.raw_id).rstrip(b"=").decode("ascii")
        )

        return {
            "authenticatorData": authenticator_data,
            "signature": signature,
            "clientDataJSON": client_data,
            "credentialId": credential_id,
            "type": "public-key",
        }
