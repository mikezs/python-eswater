# Changelog

All notable changes to this project are documented here. Format loosely follows
[Keep a Changelog](https://keepachangelog.com/); versioning is [SemVer](https://semver.org/).

## [0.1.3] - 2026-09-22

### Changed
- Replaced the sample account/premise/meter identifiers in test fixtures, docs,
  and CLI examples with obviously-fake placeholders, and stopped shipping the
  `tests/` directory in the sdist. (No functional/API changes.) Git history was
  also rewritten to purge the earlier sample values; releases 0.1.0–0.1.2 were
  removed from PyPI as part of this cleanup — use 0.1.3+.

## [0.1.2] - 2026-09-22

### Fixed
- **Multi-account logins crashed** with `Could not parse account response:
  'NoneType' object is not iterable`. The portal binds one account at a time, so
  `GetAccountDetails` returned null `Account`/`Meters` for every account except
  the one bound at login. Now re-bind via `AddOrUpdateCustomerSession` before
  each `GetAccountDetails`, and tolerate explicit JSON `null` in account parsing.
  (#3, thanks @corautem)

### Added
- **Auth-ladder fallback:** if a smart-token refresh fails with `ApiError` /
  `InvalidAuth` / `NotAuthenticated`, fall back to a full re-login instead of
  surfacing the error; transient `ServiceUnavailable` still propagates to the
  caller's retry/backoff. (#4, thanks @corautem)

## [0.1.1] - 2026-09-22

### Fixed
- **Authentication broke against the live portal.** ESW changed the
  `GetSmartAuthToken` contract: it now seeds the smart JWT from the login
  response's `access_token` (sent as `access_Token`), and the old
  `refresh_token`/`refresh_Token` pairing returns `null`. The smart response's
  rotated `Refresh_token` is also no longer accepted for a subsequent refresh,
  so each JWT refresh now re-seeds from the (session-reusable) login access
  token. Verified end-to-end against the live API. Updated `docs/api.md`.

## [0.1.0] - 2026-07-22

### Added
- Initial project scaffold: `ESWaterClient`, data models, typed exceptions, CLI,
  test harness, and CI.
- Reverse-engineered the ESW/NWG portal API and documented it in `docs/api.md`.
- Implemented cookie-based auth (`/api/Auth/Login` → `userProfile` cookie →
  `GetSmartAuthToken` JWT), account/meter discovery, and usage retrieval
  (`get_usage`/`get_meter_usage`) with litres + cost parsing and automatic JWT
  refresh.
- Tests parsing captured (redacted) fixtures; 100% line coverage, coverage
  gate in CI.
- **Verified end-to-end against the live API.** Corrected auth to read tokens
  from the login response *body* (not a cookie), and to run the required
  `SaveUserProfile` → `GetAccountSummary` sequence before `GetSmartAuthToken`
  (usage endpoints 401 otherwise). Smart-auth refresh token now rotates
  (single-use) on each refresh. CLI auto-loads `.env`.

### TODO
- Confirm `ReadingType` 0 vs 1 semantics and whether usage accepts date ranges.
- Tariff endpoint (`get_tariff`) not yet captured/implemented.
