# Changelog

All notable changes to this project are documented here. Format loosely follows
[Keep a Changelog](https://keepachangelog.com/); versioning is [SemVer](https://semver.org/).

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
