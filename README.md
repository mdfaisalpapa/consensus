# Consensus

Consensus is a standalone Frappe application for configurable elections,
resolutions, referendums and opinion polls. It is independent of ERPNext and
contains no organisation-specific voter or scope assumptions.

## Integration boundary

Consensus obtains voter references, eligibility, voting weight, scope entities,
contact destinations and receipt identity through trusted, versioned providers
registered with the consensus_voter_providers Frappe hook. A deployment must
also register a SecretResolver through consensus_secret_resolvers for
versioned signing, encryption and HMAC key resolution. Provider and secret
resolver keys are selected in Voting Integration Settings; users cannot
configure arbitrary Python import paths or SQL.

See the provider contract at consensus/providers/README.md and the architecture
specification at ARCHITECTURE.md.

## Implemented capabilities

The core includes provider-driven eligibility snapshots, encrypted voter
references and frozen receipt identity, independent Poll Type and Voting Scope
configuration, OTP authorization, authenticated Frappe User allocation,
anonymous ballot casting, voter-bound idempotency recovery, signed PDF receipts,
QR claims, public anonymous register verification and CSV/PDF register exports.

Consensus Eligibility and Consensus Participation hold identity-side
allocation data. Consensus Ballot contains no voter, user, eligibility or
participation reference. Frappe ballot owner metadata is normalized to the
service account; transaction timing and privileged database access remain
limits on application-level anonymity.

The Society ERP provider and resident portal integration remain separate work.
Consensus core does not import Society ERP DocTypes or query its tables.

## Development checks

Run the Consensus tests on an enabled development site with:

    bench --site <site> run-tests --module consensus.tests.test_core_primitives

The PDF tests generate a temporary self-signed test certificate. Configure a
deployment-specific SecretResolver, signing certificate and private key
reference before enabling receipt signing in a real environment.
