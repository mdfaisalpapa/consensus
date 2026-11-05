"""Fast unit checks for Consensus foundation and hash-compatibility primitives."""
from __future__ import annotations

import io
import json
from unittest.mock import patch

import frappe
from frappe.tests.utils import FrappeTestCase

from consensus.providers.registry import get_registered_provider, get_registered_providers
from consensus.providers.scope_resolution import resolve_scope_query
from consensus.security.crypto import (
    decrypt_protected_value,
    encrypt_protected_value,
    idempotency_digest,
    voter_reference_digest,
)
from consensus.security.secrets import SecretResolutionError
from consensus.services.hashes import ballot_hash, public_projection, register_hash

TEST_KEY = b"k" * 32


class TestCorePrimitives(FrappeTestCase):
    def test_aes_gcm_identity_round_trip_and_tamper_detection(self):
        with patch("consensus.security.crypto.resolve_secret", return_value=(TEST_KEY, "test-v1")), patch(
            "consensus.security.crypto._site", return_value="test.local"
        ):
            encrypted = encrypt_protected_value(
                b"member-opaque-ref", "provider_reference_encryption", "test-v1",
                poll_name="CNS-00001", provider_key="example", voter_ref_digest="a" * 64,
            )
            assert decrypt_protected_value(
                encrypted["ciphertext"], encrypted["nonce"], "provider_reference_encryption",
                encrypted["key_version"], poll_name="CNS-00001", provider_key="example",
                voter_ref_digest="a" * 64,
            ) == b"member-opaque-ref"
            ciphertext = encrypted["ciphertext"][:-2] + "AA"
            with self.assertRaises(SecretResolutionError):
                decrypt_protected_value(
                    ciphertext, encrypted["nonce"], "provider_reference_encryption", "test-v1",
                    poll_name="CNS-00001", provider_key="example", voter_ref_digest="a" * 64,
                )


    def test_voter_digest_is_stable_within_poll_and_domain_separated(self):
        with patch("consensus.security.crypto.resolve_secret", return_value=(TEST_KEY, "hmac-v2")), patch(
            "consensus.security.crypto._site", return_value="test.local"
        ):
            first = voter_reference_digest("example", "CNS-00001", "member-7", "hmac-v2")
            assert first == voter_reference_digest("example", "CNS-00001", "member-7", "hmac-v2")
            assert first != voter_reference_digest("example", "CNS-00002", "member-7", "hmac-v2")
            assert first != voter_reference_digest("other", "CNS-00001", "member-7", "hmac-v2")


    def test_idempotency_digest_is_voter_and_poll_bound(self):
        with patch("consensus.security.crypto.resolve_secret", return_value=(TEST_KEY, "idem-v1")), patch(
            "consensus.security.crypto._site", return_value="test.local"
        ):
            first = idempotency_digest("P1", "digest-A", "A" * 40, "idem-v1")
            assert first == idempotency_digest("P1", "digest-A", "A" * 40, "idem-v1")
            assert first != idempotency_digest("P1", "digest-B", "A" * 40, "idem-v1")
            assert first != idempotency_digest("P2", "digest-A", "A" * 40, "idem-v1")


    def test_provider_registry_is_reloaded_from_current_site_hooks(self):
        class Provider:
            key = "test-provider"
            api_version = "1.0"

            def __getattr__(self, name):
                return lambda *args, **kwargs: None

        with patch("consensus.providers.registry.frappe.get_hooks", return_value=["trusted.path"]), patch(
            "consensus.providers.registry.frappe.get_attr", return_value=Provider()
        ):
            assert get_registered_provider("test-provider").key == "test-provider"
        with patch("consensus.providers.registry.frappe.get_hooks", return_value=[]):
            assert get_registered_providers() == {}


    def test_test_only_provider_and_resolver_rejected_in_production(self):
        from consensus.providers.registry import get_registered_providers
        from consensus.security.secrets import get_registered_secret_resolvers

        class TestOnlyProvider:
            key = "mock-test-provider"
            api_version = "1.0"
            test_only = True

            def __getattr__(self, name):
                return lambda *args, **kwargs: None

        class TestOnlyResolver:
            key = "mock-test-resolver"
            api_version = "1.0"
            test_only = True

            def get_active_version(self, r, p):
                return "v1"

            def resolve_secret(self, r, p, v):
                return b"key"

        with patch.dict(frappe.conf, {"allow_tests": False}), patch.dict(frappe.flags, {"in_test": False}):
            with patch("consensus.providers.registry.frappe.get_hooks", return_value=["trusted.test_prov"]), patch(
                "consensus.providers.registry.frappe.get_attr", return_value=TestOnlyProvider
            ):
                self.assertNotIn("mock-test-provider", get_registered_providers())

            with patch("consensus.security.secrets.frappe.get_hooks", return_value=["trusted.test_res"]), patch(
                "consensus.security.secrets.frappe.get_attr", return_value=TestOnlyResolver
            ):
                self.assertNotIn("mock-test-resolver", get_registered_secret_resolvers())



    def test_scope_resolver_rejects_duplicate_and_unregistered_selections(self):
        class Provider:
            api_version = "1.0"

            def list_scope_types(self, context):
                return [{
                    "key": "group", "label": "Group", "hierarchical": True,
                    "multiple_entities": True, "supports_include_exclude": True,
                    "supports_any_selection": True, "supports_all_selection": True,
                }]

            def resolve_scope_entities(self, scope_type, refs, context):
                return [{"entity_ref": ref, "label": ref, "parent_ref": None, "hierarchy_path": []} for ref in refs]

            def validate_scope_query(self, query, context):
                canonical = json.dumps(query, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
                return {"query": query, "canonical_query_json": canonical, "provider_api_version": "1.0"}

        provider = Provider()
        with self.assertRaises(frappe.ValidationError):
            resolve_scope_query(provider, [
                {"scope_type": "group", "entity_ref": "g1"},
                {"scope_type": "group", "entity_ref": "g1"},
            ])
        with self.assertRaises(frappe.ValidationError):
            resolve_scope_query(provider, [{"scope_type": "flat", "entity_ref": "f1"}])


    def test_ballot_and_register_hash_golden_vectors_and_public_projection(self):
        value = ballot_hash(
            "CNS-00001", "rct_abc", "opt-opaque", "Yes — proceed", 1.25,
            "2026-10-20 12:34:56",
        )
        assert value == "624bedebf46a21a0daa0037829bb5c9cc566b9816b57ea0a7344f2f1772f2401"
        rows = [{
            "receipt_code": "rct_abc", "selected_option": "opt-opaque",
            "selected_option_label": "Yes — proceed", "voting_weight": 1.25,
            "ballot_hash": value,
        }]
        assert register_hash(rows) == "103f377252638ee09eb0d222800fa17feb240124b9c0a56ea419e940c1623a5f"
        public = public_projection(rows)[0]
        assert "selected_option" not in public
        assert public["selected_option_label"] == "Yes — proceed"


    def test_secret_resolver_resolves_pinned_versions(self):
        class Resolver:
            key = "test-secret-resolver"
            api_version = "1.0"

            def get_active_version(self, reference, purpose):
                return "active-7"

            def resolve_secret(self, reference, purpose, version):
                return (reference + ":" + purpose + ":" + version).encode()

        settings = frappe._dict({
            "secret_resolver_key": "test-secret-resolver",
            "voter_reference_hmac_key_reference": "voter-ref-secret",
            "idempotency_hmac_key_reference": "idem-secret",
            "identity_snapshot_hmac_key_reference": "identity-secret",
        })
        with patch("consensus.security.secrets.frappe.get_single", return_value=settings), patch(
            "consensus.security.secrets.get_registered_secret_resolvers",
            return_value={"test-secret-resolver": Resolver()},
        ):
            from consensus.security.secrets import resolve_secret
            active, version = resolve_secret("voter_reference_hmac")
            pinned, pinned_version = resolve_secret("voter_reference_hmac", "old-2")
        self.assertEqual((active, version), (b"voter-ref-secret:voter_reference_hmac:active-7", "active-7"))
        self.assertEqual((pinned, pinned_version), (b"voter-ref-secret:voter_reference_hmac:old-2", "old-2"))

    def test_identity_commitment_is_stable_across_randomized_aes_ciphertexts(self):
        fields = [{"key": "display", "label": "Member", "value": "Member Example", "display_order": 1, "required": True}]
        with patch("consensus.security.crypto.resolve_secret", return_value=(TEST_KEY, "identity-v1")), patch(
            "consensus.security.crypto._site", return_value="test.local"
        ):
            first = encrypt_protected_value(
                b"same identity", "identity_snapshot_encryption", "identity-v1",
                poll_name="P1", provider_key="p", voter_ref_digest="d" * 64,
            )
            second = encrypt_protected_value(
                b"same identity", "identity_snapshot_encryption", "identity-v1",
                poll_name="P1", provider_key="p", voter_ref_digest="d" * 64,
            )
            from consensus.security.crypto import identity_snapshot_commitment
            commitment1 = identity_snapshot_commitment("P1", "p", "d" * 64, "receipt", "1", fields, "identity-v1")
            commitment2 = identity_snapshot_commitment("P1", "p", "d" * 64, "receipt", "1", fields, "identity-v1")
        self.assertNotEqual(first["nonce"], second["nonce"])
        self.assertNotEqual(first["ciphertext"], second["ciphertext"])
        self.assertEqual(commitment1, commitment2)


    def test_identity_and_ballot_doc_types_remain_separated(self):
        eligibility_fields = {field.fieldname for field in frappe.get_meta("Consensus Eligibility").fields}
        participation_fields = {field.fieldname for field in frappe.get_meta("Consensus Participation").fields}
        ballot_fields = {field.fieldname for field in frappe.get_meta("Consensus Ballot").fields}
        self.assertIn("encrypted_identity_snapshot", eligibility_fields)
        self.assertIn("voter_ref_digest", eligibility_fields)
        self.assertIn("eligibility", participation_fields)
        self.assertIn("voter_user", participation_fields)
        self.assertNotIn("eligibility", ballot_fields)
        self.assertNotIn("participation", ballot_fields)
        self.assertNotIn("voter_ref_digest", ballot_fields)
        self.assertNotIn("voter_user", ballot_fields)
        self.assertNotIn("encrypted_identity_snapshot", ballot_fields)

    def test_database_uniqueness_indexes_exist(self):
        expected = {
            "Consensus Poll Option": ["uniq_cons_poll_option_key"],
            "Consensus Eligibility": ["uniq_cons_elig_voter_digest", "uniq_cons_elig_voter_user"],
            "Consensus Participation": ["uniq_cons_part_voter_digest"],
            "Consensus Ballot": ["uniq_cons_ballot_receipt", "uniq_cons_ballot_idem"],
            "Consensus Poll Scope": ["uniq_cons_poll_scope_entity"],
        }
        for doctype, indexes in expected.items():
            for index in indexes:
                self.assertTrue(
                    frappe.db.has_index("tab" + doctype, index),
                    f"Missing required unique index {index} on {doctype}",
                )

    def test_snapshot_and_atomic_anonymous_vote_flow(self):
        from datetime import timedelta

        from frappe.utils import now_datetime

        class Provider:
            key = "unit-provider"
            api_version = "1.0"

            def get_metadata(self):
                return {"label": "Unit Provider"}

            def list_scope_types(self, context):
                return [{
                    "key": "group", "label": "Group", "hierarchical": True,
                    "multiple_entities": True, "supports_include_exclude": True,
                    "supports_any_selection": True, "supports_all_selection": True,
                    "description": "Provider group",
                }]

            def list_scope_entities(self, scope_type, query=None, parent_ref=None, context=None):
                return [{"entity_ref": "group-a", "label": "Group A", "parent_ref": None, "hierarchy_path": ["Group A"]}]

            def resolve_scope_entities(self, scope_type, entity_refs, context):
                return [{"entity_ref": ref, "label": ref.upper(), "parent_ref": None,
                         "hierarchy_path": [ref.upper()]} for ref in entity_refs]

            def validate_scope_query(self, query, poll_context):
                from consensus.security.crypto import canonical_json_bytes
                return {"query": query, "canonical_query_json": canonical_json_bytes(query).decode(),
                        "provider_api_version": self.api_version}

            def validate_rule_configuration(self, rule_key, config, poll_type, poll_context):
                from consensus.security.crypto import canonical_json_bytes
                return {"rule_key": rule_key, "rule_version": "rule-v1", "config": config,
                        "canonical_config_json": canonical_json_bytes(config).decode()}

            def begin_voter_snapshot(self, poll_context):
                return {"token": "snapshot-token", "provider_data_version": "dataset-v1"}

            def get_voter_candidates(self, scope_query, poll_context, snapshot, cursor, page_size):
                candidates = [] if cursor else [{
                    "voter_ref": "opaque-member-1", "frappe_user": self.user,
                    "voter_category": "member", "context_ref": "group-a", "attributes": {},
                }]
                return {"candidates": candidates, "next_cursor": None, "snapshot": snapshot}

            def evaluate_eligibility(self, candidate, poll_context, rule_key, rule_config):
                return {"eligible": True, "reason_code": None, "voting_weight": "1.5",
                        "rule_version": "rule-v1", "voter_category": "member", "context_ref": "group-a"}

            def get_receipt_identity(self, voter_ref, purpose, schema_version=None):
                return {"schema_key": "member-receipt", "schema_version": "v1", "fields": [
                    {"key": "display_name", "label": "Member", "value": "Member One",
                     "display_order": 1, "required": True},
                ]}

            def get_contact_destination(self, voter_ref, channel):
                return {"channel": channel, "destination": "member@example.test"}

        user = "consensus-test-" + frappe.generate_hash(length=8) + "@example.test"
        frappe.get_doc({
            "doctype": "User", "email": user, "first_name": "Consensus Test",
            "enabled": 1, "send_welcome_email": 0,
        }).insert(ignore_permissions=True)
        provider = Provider()
        provider.user = user
        key_material = {
            "voter_reference_hmac": b"v" * 32,
            "provider_reference_encryption": b"r" * 32,
            "identity_snapshot_encryption": b"i" * 32,
            "identity_snapshot_commitment_hmac": b"c" * 32,
            "idempotency_hmac": b"d" * 32,
            "pdf_signing": b"not-a-real-signing-key",
            "otp_hmac": b"o" * 32,
        }

        def resolve(purpose, version=None):
            return key_material[purpose], version or "test-v1"

        now = now_datetime()
        from consensus.providers import registry
        from consensus.services import eligibility, voting
        with patch.object(registry, "get_configured_provider", return_value=provider), patch.object(
            registry, "get_registered_provider", return_value=provider
        ), patch.object(eligibility, "get_registered_provider", return_value=provider), patch.object(
            eligibility, "resolve_secret", side_effect=resolve
        ), patch.object(voting, "resolve_secret", side_effect=resolve), patch(
            "consensus.security.crypto.resolve_secret", side_effect=resolve
        ):
            settings = frappe.get_single("Voting Integration Settings")
            settings.otp_enabled = 1
            settings.save(ignore_permissions=True)
            poll_type = frappe.get_doc({
                "doctype": "Consensus Poll Type", "label": "Test Election",
                "type_key": "test-election", "enabled": 1, "configuration_version": "1",
                "default_rule_key": "member-rule", "default_rule_config": "{}",
            }).insert(ignore_permissions=True)
            options = []
            poll = frappe.get_doc({
                "doctype": "Consensus Poll", "title": "Unit Test Poll", "poll_type": poll_type.name,
                "starts_at": now - timedelta(minutes=1), "ends_at": now + timedelta(hours=1),
                "scope_selection_operator": "Any", "scope_conditions_operator": "All",
                "scope_conditions": "{}", "rule_config": "{}", "status": "Draft",
                "require_otp": 1, "result_visibility": "After Closing",
                "publish_anonymous_register": 1,
                "scope_selections": [{"scope_type": "group", "entity_ref": "group-a", "inclusion": "Include"}],
            }).insert(ignore_permissions=True)
            for label in ("Choice A", "Choice B"):
                options.append(frappe.get_doc({
                    "doctype": "Consensus Poll Option", "poll": poll.name,
                    "option_label": label, "display_order": len(options) + 1, "enabled": 1,
                }).insert(ignore_permissions=True))
            snapshot = eligibility.build_eligibility_snapshot(poll.name)
            self.assertEqual(snapshot["eligible_voter_count"], 1)
            frozen = frappe.get_doc("Consensus Poll", poll.name)
            self.assertEqual(frozen.snapshot_state, "Complete")
            from consensus.services.eligibility import verify_eligibility_snapshot
            self.assertTrue(verify_eligibility_snapshot(poll.name))
            original_option_label = frappe.db.get_value("Consensus Poll Option", options[0].name, "frozen_label")
            frappe.db.set_value("Consensus Poll Option", options[0].name, "frozen_label", "Tampered",
                               update_modified=False)
            with self.assertRaises(frappe.ValidationError):
                verify_eligibility_snapshot(poll.name)
            frappe.db.set_value("Consensus Poll Option", options[0].name, "frozen_label", original_option_label,
                               update_modified=False)
            frappe.set_user("Administrator")
            voting.open_poll(poll.name)
            frappe.set_user(user)
            with patch("consensus.services.voting.frappe.sendmail") as sendmail:
                voting.send_voting_otp(poll.name)
            import re
            code_match = re.search(r"\b(\d{6})\b", sendmail.call_args.kwargs["message"])
            self.assertIsNotNone(code_match)
            voting.verify_voting_otp(poll.name, code_match.group(1))
            idem = "test-idem-" + frappe.generate_hash(length=48)
            savepoint = "consensus_vote_failure"
            frappe.db.savepoint(savepoint)
            with patch.object(voting.frappe.db, "set_value", side_effect=RuntimeError("forced failure")):
                with self.assertRaises(RuntimeError):
                    voting.cast_vote(poll.name, options[0].option_key, idem)
            frappe.db.rollback(save_point=savepoint)
            self.assertEqual(frappe.db.count("Consensus Participation", {"poll": poll.name}), 0)
            self.assertEqual(frappe.db.count("Consensus Ballot", {"poll": poll.name}), 0)
            cast = voting.cast_vote(poll.name, options[0].option_key, idem)
            self.assertTrue(cast["success"])
            self.assertFalse(cast["recovered"])
            retry = voting.cast_vote(poll.name, options[0].option_key, idem)
            self.assertTrue(retry["recovered"])
            self.assertEqual(retry["receipt_code"], cast["receipt_code"])
            self.assertEqual(frappe.db.count("Consensus Participation", {"poll": poll.name}), 1)
            self.assertEqual(frappe.db.count("Consensus Ballot", {"poll": poll.name}), 1)
            self.assertEqual(frappe.db.get_value("Consensus Eligibility", {"poll": poll.name}, "status"), "Voted")
            ballot = frappe.db.get_value("Consensus Ballot", {"poll": poll.name, "receipt_code": cast["receipt_code"]}, ["owner", "modified_by"], as_dict=True)
            self.assertNotEqual(ballot.owner, user)
            eligibility_row = frappe.get_all("Consensus Eligibility", filters={"poll": poll.name},
                                             fields=["name"], limit=1)[0]
            from consensus.services.eligibility import get_frozen_identity
            with patch("consensus.security.crypto.resolve_secret", side_effect=resolve):
                frozen_identity = get_frozen_identity(eligibility_row.name)
            self.assertEqual(frozen_identity["fields"][0]["value"], "Member One")
            frappe.set_user("Administrator")
            voting.close_poll(poll.name)
            from consensus.services.receipts import (
                get_public_results,
                get_published_register,
                publish_register,
                verify_receipt_claims,
            )
            publish_register(poll.name)
            register = get_published_register(poll.name)
            self.assertEqual(len(register["ballots"]), 1)
            self.assertNotIn("selected_option", register["ballots"][0])
            self.assertNotIn("Member One", str(register))
            self.assertNotIn(options[0].name, str(register))
            option_claim = __import__("base64").urlsafe_b64encode(
                b"Choice A"
            ).decode("ascii").rstrip("=")
            verified = verify_receipt_claims(
                poll.name, cast["receipt_code"], cast["ballot_hash"], option_claim, "1.500"
            )
            self.assertTrue(verified["valid"])
            results = get_public_results(poll.name)
            self.assertEqual(sum(row["ballot_count"] for row in results["results"]), 1)
            from pypdf import PdfReader

            from consensus.services.receipts import (
                download_my_receipt,
                download_register_csv,
                download_register_pdf,
            )
            download_register_csv(poll.name)
            csv_bytes = frappe.local.response["filecontent"]
            self.assertIn(cast["receipt_code"], csv_bytes)
            self.assertNotIn(user, str(csv_bytes))
            download_register_pdf(poll.name)
            register_pdf = frappe.local.response["filecontent"]
            register_text = PdfReader(io.BytesIO(register_pdf)).pages[0].extract_text()
            self.assertIn(cast["receipt_code"], register_text)
            self.assertNotIn(user, register_text)
            frappe.set_user(user)
            with patch("consensus.services.receipts._signed_pdf", return_value=b"%PDF receipt") as sign:
                download_my_receipt(poll.name, idem)
            self.assertEqual(frappe.local.response["filecontent"], b"%PDF receipt")
            regenerated_identity = sign.call_args.args[2]
            self.assertEqual(regenerated_identity["fields"][0]["value"], "Member One")
            self.assertEqual(frappe.db.count("Consensus Ballot", {"poll": poll.name}), 1)
            outsider = "consensus-outsider-" + frappe.generate_hash(length=8) + "@example.test"
            frappe.get_doc({
                "doctype": "User", "email": outsider, "first_name": "Outsider",
                "enabled": 1, "send_welcome_email": 0,
            }).insert(ignore_permissions=True)
            frappe.set_user(outsider)
            with self.assertRaises(frappe.PermissionError):
                voting.get_my_vote(poll.name, idem)
            frappe.set_user("Administrator")

    def test_signed_receipt_contains_frozen_identity_and_qr_stays_anonymous(self):
        import tempfile
        from datetime import datetime
        from pathlib import Path
        from types import SimpleNamespace
        from urllib.parse import parse_qs, urlparse

        from cryptography import x509
        from cryptography.hazmat.primitives import hashes, serialization
        from cryptography.hazmat.primitives.asymmetric import rsa
        from cryptography.x509.oid import NameOID

        from consensus.services.receipts import _qr_url, _signed_pdf

        signing_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        subject = issuer = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "Consensus Test Signer")])
        certificate = (
            x509.CertificateBuilder()
            .subject_name(subject)
            .issuer_name(issuer)
            .public_key(signing_key.public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(datetime.utcnow())
            .not_valid_after(datetime.utcnow().replace(year=datetime.utcnow().year + 1))
            .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
            .sign(signing_key, hashes.SHA256())
        )
        key_pem = signing_key.private_bytes(
            serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()
        )
        certificate_pem = certificate.public_bytes(serialization.Encoding.PEM)
        with tempfile.TemporaryDirectory() as temp_dir:
            cert_path = Path(temp_dir) / "test-cert.pem"
            cert_path.write_bytes(certificate_pem)
            settings = frappe._dict({
                "signing_certificate": "/private/files/test-cert.pem",
                "organisation_label": "Example Organisation", "signature_reason": "Voting receipt",
                "signer_display_name": "Test Signer",
            })
            fake_file = SimpleNamespace(get_full_path=lambda: str(cert_path))
            poll = frappe._dict({
                "name": "CNS-TEST", "snapshot_signing_key_version": "sign-test-v1",
                "frozen_title": "Frozen Election Title", "title": "Changed Title",
            })
            ballot = frappe._dict({
                "receipt_code": "receipt_test_1", "selected_option_label": "Choice One",
                "voting_weight": 1.5, "cast_on": "2026-11-04 11:00:00",
                "ballot_hash": "a" * 64,
            })
            identity = {"schema_key": "member-v1", "schema_version": "1", "fields": [
                {"key": "name", "label": "Member", "value": "Frozen Member Name",
                 "display_order": 1, "required": True},
                {"key": "context", "label": "Membership reference", "value": "M-42",
                 "display_order": 2, "required": True},
            ]}
            with patch("consensus.services.receipts.resolve_secret", return_value=(key_pem, "sign-test-v1")), patch(
                "consensus.services.receipts.frappe.get_single", return_value=settings
            ), patch("consensus.services.receipts.frappe.get_doc", return_value=fake_file):
                pdf = _signed_pdf(poll, ballot, identity)
                claims = parse_qs(urlparse(_qr_url(poll, ballot)).query)
            self.assertTrue(pdf.startswith(b"%PDF"))
            from pypdf import PdfReader
            receipt_text = PdfReader(io.BytesIO(pdf)).pages[0].extract_text()
            for expected in ("Frozen Election Title", "CNS-TEST", "Frozen Member Name", "M-42",
                             "Choice One", "receipt_test_1", "1.5", "a" * 64):
                self.assertIn(expected, receipt_text)
            self.assertNotIn(b"Frozen Member Name", json.dumps(claims).encode())
            self.assertNotIn("option_id", claims)
            self.assertEqual(claims["option"][0], "Q2hvaWNlIE9uZQ")
            from asn1crypto import x509 as asn1_x509
            from pyhanko.pdf_utils.reader import PdfFileReader
            from pyhanko.sign.validation import validate_pdf_signature
            from pyhanko_certvalidator import ValidationContext
            reader = PdfFileReader(io.BytesIO(pdf))
            self.assertEqual(len(reader.embedded_signatures), 1)
            trusted_cert = asn1_x509.Certificate.load(certificate.public_bytes(serialization.Encoding.DER))
            status = validate_pdf_signature(
                reader.embedded_signatures[0],
                signer_validation_context=ValidationContext(trust_roots=[trusted_cert], allow_fetching=False),
            )
            self.assertTrue(status.intact)
            self.assertTrue(status.valid)
            marker = pdf.find(b"ReportLab PDF Library")
            self.assertGreaterEqual(marker, 0, "Expected a stable editable PDF info string for tamper testing")
            tampered = bytearray(pdf)
            tampered[marker] = ord("X")
            tampered_reader = PdfFileReader(io.BytesIO(bytes(tampered)))
            tampered_status = validate_pdf_signature(
                tampered_reader.embedded_signatures[0],
                signer_validation_context=ValidationContext(
                    trust_roots=[trusted_cert], allow_fetching=False
                ),
            )
            self.assertFalse(tampered_status.intact)


    def test_install_roles_and_doc_type_permissions_are_present(self):
        for role in ("Consensus Administrator", "Consensus Eligibility Officer"):
            self.assertTrue(frappe.db.exists("Role", role), f"Missing Consensus role {role}")
        poll_permissions = frappe.get_meta("Consensus Poll").permissions
        self.assertTrue(any(row.role == "Consensus Administrator" and row.create for row in poll_permissions))
        for doctype in ("Consensus Eligibility", "Consensus Participation", "Consensus Ballot"):
            permissions = frappe.get_meta(doctype).permissions
            self.assertFalse(any(row.read or row.report or row.export for row in permissions),
                             f"Raw {doctype} data must not be exposed through Desk permissions")
