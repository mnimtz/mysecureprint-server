"""
.mobileconfig Generator für iOS AirPrint (v0.8.0)
===================================================
Erzeugt Apple Configuration Profiles die einen "MySecurePrint"-Drucker
über AirPrint (IPP over HTTPS) auf iOS-Geräten registrieren.

Format: Property-List XML mit optionaler PKCS7-Signatur.
MIME: application/x-apple-aspen-config
Payload-Type: com.apple.airprint

Referenz:
  https://developer.apple.com/documentation/devicemanagement/airprint
"""

from __future__ import annotations

import logging
import plistlib
import uuid as _uuid
from urllib.parse import urlparse

logger = logging.getLogger("printix.airprint.profile")


def build_mobileconfig(server_url: str,
                       profile_token: str,
                       queue_display_name: str,
                       organization: str = "MySecurePrint",
                       server_hostname: str = "") -> bytes:
    """Baut das .mobileconfig als Property-List-XML.

    Args:
        server_url: Basis-URL des Servers, z.B. "https://printix-sp.azurewebsites.net"
        profile_token: Token aus airprint_profiles.create_profile()
        queue_display_name: Anzeige im iOS-Print-Dialog, z.B. "SecurePrint DE"
        organization: Für Profil-Metadaten, default "MySecurePrint"
        server_hostname: Wenn leer, aus server_url abgeleitet

    Returns:
        bytes des unsignierten .mobileconfig
    """
    parsed = urlparse(server_url)
    host = server_hostname or parsed.hostname or "localhost"
    # v0.7.230 — Port aus URL ableiten (nicht hardcoded 443).
    # HTTPS → 443, aber Kunden könnten ihren Server auf 8443/etc. haben.
    if parsed.port:
        derived_port = parsed.port
    elif parsed.scheme == "http":
        derived_port = 80
    else:
        derived_port = 443
    force_tls = parsed.scheme != "http"

    # UUIDs müssen deterministisch aus dem Token abgeleitet sein — sonst
    # gäbe es bei Re-Download desselben Profils immer eine neue "Profil-ID"
    # und iOS würde bei Update-Installation nach expliziter Bestätigung
    # fragen als wäre es ein neues Profil.
    profile_uuid = str(_uuid.uuid5(_uuid.NAMESPACE_URL,
                                    f"mysecureprint/profile/{profile_token}"))
    payload_uuid = str(_uuid.uuid5(_uuid.NAMESPACE_URL,
                                    f"mysecureprint/payload/{profile_token}"))

    airprint_entry = {
        "ForceTLS": force_tls,
        "Port": derived_port,
        "ResourcePath": f"/airprint/{profile_token}",
        "IPAddress": host,
    }

    airprint_payload = {
        "PayloadType":         "com.apple.airprint",
        "PayloadUUID":         payload_uuid,
        "PayloadIdentifier":   f"com.mysecureprint.airprint.{profile_token}",
        "PayloadDisplayName":  f"MySecurePrint — {queue_display_name}",
        "PayloadDescription":  f"AirPrint über MySecurePrint auf {queue_display_name}",
        "PayloadVersion":      1,
        "PayloadOrganization": organization,
        "AirPrint": [airprint_entry],
    }

    # WICHTIG: Kein `TargetDeviceType` gesetzt — dieses Profil funktioniert
    # dadurch auf **iPhone, iPad UND macOS** (Sequoia+) gleichermaßen. Apple
    # behandelt das dann als "Universal Profile":
    # - iOS/iPadOS: Installation via Anhang/Safari, Drucker taucht in jedem
    #   "Drucken"-Dialog auf
    # - macOS: Installation via Systemeinstellungen → Profile, Drucker
    #   erscheint in "Systemeinstellungen → Drucker & Scanner"
    # `PayloadScope: User` heißt: für den installierenden Benutzer, nicht
    # für alle User des Devices — bei privaten iPhones/Macs sinnvoller
    # Default. Firmen mit MDM können später `System` setzen.
    top_level = {
        "PayloadType":         "Configuration",
        "PayloadUUID":         profile_uuid,
        "PayloadIdentifier":   f"com.mysecureprint.profile.{profile_token}",
        "PayloadDisplayName":  f"MySecurePrint — {queue_display_name}",
        "PayloadDescription":  (
            "Registriert einen nativen Drucker über MySecurePrint an deine "
            "SecurePrint-Queue. Funktioniert auf iPhone, iPad und Mac — "
            "danach kannst du aus jeder App direkt drucken (Safari, Mail, "
            "Fotos, Dateien, Vorschau, Pages, …)."
        ),
        "PayloadVersion":      1,
        "PayloadOrganization": organization,
        "PayloadScope":        "User",
        "PayloadRemovalDisallowed": False,
        "PayloadContent":      [airprint_payload],
    }

    return plistlib.dumps(top_level, fmt=plistlib.FMT_XML, sort_keys=False)


def maybe_sign_mobileconfig(unsigned: bytes,
                             cert_pem: str = "",
                             key_pem: str = "") -> bytes:
    """Signiert das .mobileconfig via PKCS#7 wenn Cert+Key vorhanden.

    Wenn cert_pem oder key_pem leer sind: gibt das unsignierte Profil
    zurück. iOS zeigt dann "Nicht überprüft"-Warnung, Installation
    funktioniert trotzdem.

    Für signierte Profile empfehlen wir ein Apple Developer ID Certificate
    (kostenpflichtig) — dann zeigt iOS "Überprüft" mit grünem Häkchen.
    """
    if not cert_pem or not key_pem:
        return unsigned
    try:
        from cryptography.hazmat.primitives.serialization import (
            load_pem_private_key,
        )
        from cryptography.hazmat.primitives.serialization import pkcs7
        from cryptography.hazmat.primitives import serialization, hashes
        from cryptography import x509

        key = load_pem_private_key(key_pem.encode("utf-8"), password=None)
        cert = x509.load_pem_x509_certificate(cert_pem.encode("utf-8"))

        signed = (
            pkcs7.PKCS7SignatureBuilder()
            .set_data(unsigned)
            .add_signer(cert, key, hashes.SHA256())
            .sign(serialization.Encoding.DER, [pkcs7.PKCS7Options.Binary])
        )
        return signed
    except ImportError:
        logger.warning(
            "AirPrint: cryptography.pkcs7 nicht verfügbar — sende unsigned"
        )
        return unsigned
    except Exception as e:
        logger.error("AirPrint: PKCS7-Signing fehlgeschlagen: %s", e)
        return unsigned


def generate_mobileconfig_for_profile(profile: dict,
                                      server_url: str,
                                      organization: str = "MySecurePrint",
                                      cert_pem: str = "",
                                      key_pem: str = "") -> tuple[bytes, str]:
    """High-Level-Wrapper: aus einem Profil-Dict die fertige Datei erzeugen.

    Returns (bytes, mime_type). Der Content-Type ist entweder
    application/x-apple-aspen-config (immer) — signiert oder nicht.
    """
    unsigned = build_mobileconfig(
        server_url=server_url,
        profile_token=profile["profile_token"],
        queue_display_name=profile.get("queue_display_name") or "SecurePrint",
        organization=organization,
    )
    payload = maybe_sign_mobileconfig(unsigned, cert_pem, key_pem)
    return payload, "application/x-apple-aspen-config"


def suggest_filename(profile: dict, organization: str = "MySecurePrint") -> str:
    """Sinnvoller Dateiname für den Download / Email-Anhang."""
    queue = (profile.get("queue_display_name") or "SecurePrint").replace(" ", "_")
    return f"{organization}-{queue}.mobileconfig"
