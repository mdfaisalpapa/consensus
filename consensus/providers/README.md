# Voter providers and site-aware registration

Trusted installed apps register a provider import path through the
`consensus_voter_providers` Frappe hook. Providers implement the typed contract
in `protocol.py`, expose a unique key and exact `api_version = "1.0"`, and are
trusted server-side code. Provider selection in Voting Integration Settings is
a key looked up in this hook registry, never an arbitrary import path. Public
APIs do not accept provider keys. Scope discovery requires a Consensus poll
administrator.

Consensus reads hooks in the current Frappe site context without a process-
global `lru_cache`. This avoids one site's installed provider set leaking into
another site's requests in a shared Bench worker. If caching is introduced
later, it must be keyed by site and hook/app generation and invalidated after
site app changes. Provider instances must not retain mutable site-specific
configuration across calls.

## Provider capabilities

Provider metadata declares scope types and their hierarchy, multiple-entity,
include/exclude, and supported-combination capabilities. The provider lists
entities and resolves selected references. Resolution must return each
requested entity exactly once and no unexpected entity. Consensus rejects
missing, unexpected, or duplicate entity results.

The provider validates the complete scope query, conditions structure, and
operators, then returns a canonical query and provider API version. It also
validates rule configuration and returns canonical configuration plus rule
key/version. Poll Type defaults and poll-specific overrides use recursive
object merge; arrays replace; `null` removes a key; the provider validates
the resulting configuration. A different rule key requires a complete config
for that rule and does not inherit defaults for another rule.

## Paginated voter snapshots

`begin_voter_snapshot` returns a provider snapshot token/data version.
`get_voter_candidates` accepts this token, a stable cursor and page size. The
provider must return a consistent voter set across pages, stable pagination,
and a next cursor or completion. Candidate references are stable, opaque and
non-semantic. V1 authentication allocation is a Frappe User name in
`frappe_user`; external auth subjects are out of scope. Receipt identity and
contact details are returned through separate purpose-specific methods.

A Committee Election selecting a provider-defined Block entity and a
committee-membership condition is resolved wholly by the provider. Consensus
only freezes the canonical query, provider/rule versions, opaque voter
references and weights; it does not know the organisation's group model.
