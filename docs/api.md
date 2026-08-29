# Essex & Suffolk Water (NWG) API — reverse-engineered spec

> Captured 22 Jul 2026 from the web portal at `https://www.eswater.co.uk`
> (logged-in "My account" → My Usage → Smart Meter). All personal values below
> are **redacted** with `<PLACEHOLDERS>`.

Essex & Suffolk Water is part of **Northumbrian Water Group (NWG)**. The portal
is a same-origin ASP.NET Core app fronted by **Cloudflare**; the JSON API lives
under `https://www.eswater.co.uk/api/`. Identity is handled by **LoginRadius**
(CIAM); smart-usage endpoints are gated by a LoginRadius-issued JWT.

## Auth model (two layers)

> **Verified end-to-end against the live API (22 Jul 2026).** The notes below
> are what actually works, not just what the browser appeared to do.

1. **Portal session** — `POST /api/Auth/Login` with `{email, password}` returns
   the profile **in the response body** and sets cookies:
   - `.AspNetCore.Session` — **the only cookie that matters for auth**; the
     usage endpoints authorise off this server-side session (confirmed by
     leave-one-out cookie bisection).
   - `.AspNetCore.Antiforgery.*`, `ARRAffinity*`, `EPiStateMarker` (not needed).
   - The browser *also* sets `userProfile` / `smartTokenInfo` /
     `smartUserTokenInfo` cookies **client-side via JavaScript** — these are
     **not** required by the API and the library does not set them.

   The login **response body** carries the LoginRadius tokens the server
   obtained on our behalf (this is the source of truth, not the cookie):
   ```json
   {
     "RestException": null, "OtherException": null,
     "Response": {
       "access_token": "<uuid>",
       "refresh_token": "<uuid>",           // seed for GetSmartAuthToken
       "expires_in": "2026-07-22T16:02:45Z",  // ~1 hour
       "Profile": { "CustomFields": { "PersonId": "PersonId" } }
       /* + Email, FirstName, Surname, Roles … (PII — do not log/persist) */
     }
   }
   ```
   A cookie jar is mandatory (carries `.AspNetCore.Session`).

2. **Smart JWT** — usage endpoints require a short-lived JWT from
   `POST /api/Customer/GetSmartAuthToken`, sent in the usage request **body** as
   `Authorization`. **But the JWT alone is not sufficient** — see the required
   sequence below.

### Required login sequence (order matters!)
The usage endpoints return **401** unless the session is set up in this exact
order *before* fetching the smart token:

1. `POST /api/Auth/Login {email, password}` → read `Response.refresh_token`.
2. `POST /api/Auth/SaveUserProfile` with the whole `Response` object — registers
   the profile in the session. Without it, `GetAccountSummary` returns
   `{"statusField": {"codeField": 400, "messageField": "PersonId is required"}}`.
3. `GET /api/Customer/GetAccountSummary?personId=PersonId` — **binds the account
   to the session**. This is the non-obvious step: skipping it makes usage 401
   even with a valid JWT (`AddOrUpdateCustomerSession` does *not* substitute).
4. `POST /api/Customer/GetSmartAuthToken {refresh_Token}` → `Id_token` (JWT).
5. Usage calls now succeed.

No LoginRadius apikey and **no call to `api.loginradius.com`** is needed — the
server does that internally. (The JWT is LoginRadius-issued:
`iss = cloud-api.loginradius.com/sso/oidc/northumbrianwater`,
`aud = f62c1509-d0c0-4ebf-8f0d-ed2e71d5c3a3`, `PersonId`, **~10 min** lifetime.)

### Token refresh & rotation (verified)
- The `GetSmartAuthToken` **refresh token is single-use** and **rotates**: each
  call returns a new `Refresh_token`; reusing a spent one returns `null`. Store
  the returned `Refresh_token` for the next refresh.
- Within a live session, a repeat `GetSmartAuthToken` (using the rotated token)
  works **without** re-running steps 1–3, and its JWT authorises usage.
- Lifetimes: login `refresh_token` ~1 hour (re-login after); JWT ~10 min
  (re-fetch before expiry).

## Common request conventions
- `Content-Type: application/json`, `Accept: */*`
- `X-Requested-With: XMLHttpRequest` (send this — likely enforced)
- Cookies from step 1 on every call
- Bodies are JSON; some responses are `text/plain` (e.g. token)

---

## Endpoints

### `POST /api/Auth/Login`
Request:
```json
{ "email": "<EMAIL>", "password": "<PASSWORD>" }
```
Response: empty body; sets session cookies.

### `GET /api/Customer/GetAccountSummary?personId=<PERSON_ID>&_=<ts>`
Account/premise discovery entry point.
```json
{
  "Message": "",
  "Accounts": [
    {
      "AccountID": "<ACCOUNT_ID>",
      "PremiseID": "<PREMISE_ID>",
      "PropertyAddress": "<ADDRESS>",
      "AccountType": 1,
      "AccountBalance": -27.19,
      "MultiplePremises": false
    }
  ],
  "Status": { "Code": "0", "Message": "<p>Success</p>" }
}
```
> Note: the portal often sends `personId=PersonId` literally (a placeholder);
> the server resolves the person from the session cookie.

### `POST /api/Customer/AddOrUpdateCustomerSession`  ← selects the account for GetAccountDetails
Request:
```json
["PersonId:PersonId", "AccountId:<ACCOUNT_ID>"]
```
Response: `true`.

> The portal session holds exactly one "current" account at a time (bound to
> whichever account `GetAccountSummary` last saw, i.e. the first one after
> login). For a person with multiple accounts, `GetAccountDetails` scopes its
> `Account`/`Meters` fields to whatever account is currently selected — call
> this immediately before each `GetAccountDetails` to re-bind first, or every
> account after the first comes back with those fields `null`.

### `POST /api/Customer/GetAccountDetails`  ← meter discovery
Request:
```json
{ "AccountId": "<ACCOUNT_ID>", "PremiseId": "<PREMISE_ID>", "PersonId": "PersonId" }
```
Response (trimmed to the useful bits): `AccountDetail.Meters[]` holds the meter,
where **`BadgeNumber` is the `MeterSerial`** used by the usage calls:
```json
{
  "AccountDetail": {
    "Account": { "SmartMeter": true, "StartDate": "2017-01-30T00:00:00",
                 "NumberOfOccupiers": 4, "LastBillAmount": 254.81 },
    "Meters": [
      {
        "BadgeNumber": "<METER_SERIAL>",
        "smartPoint": "<SMART_POINT>",
        "smartStatus": "COMM",
        "meterInstalledDate": "2024-03-20T00:00:00",
        "LastRead": "231.000000",
        "LastReadDate": "2026-07-14T00:00:00",
        "NumberDials": 5
      }
    ],
    "Premise": { "PropertyAddress": "<ADDRESS>", "Measured": true },
    "AccountId": "<ACCOUNT_ID>", "PremiseId": "<PREMISE_ID>", "PersonId": "PersonId"
  }
}
```
> Also contains `Person` (name, DOB, email, phone) and `Payment` blocks — ignore
> for usage; do not persist PII.

### `POST /api/Customer/GetSmartAuthToken`  ← smart JWT
Request:
```json
{ "refresh_Token": "<REFRESH_TOKEN>" }
```
Response (`text/plain` JSON):
```json
{
  "Access_token": "<uuid>",
  "Token_type": "Bearer",
  "Refresh_token": "<uuid>",
  "Expires_in": 3598,
  "Id_token": "<JWT>",
  "Status": null
}
```
`Id_token` (the JWT) is what usage calls put in their `Authorization` field.
Cache it and re-fetch when within ~30s of the 10-min expiry.

### Usage — `POST /api/Customer/Get{Hourly,Daily,Weekly,Monthly,Yearly}WaterUsage`
All five share the same request/response contract; only aggregation differs.

Request:
```json
{
  "AccountId": "<ACCOUNT_ID>",
  "Authorization": "<JWT from GetSmartAuthToken>",
  "MeterSerial": "<METER_SERIAL>",
  "StartDate": "2026-07-18T00:00:00"
}
```
Response — array of readings:
```json
[
  { "Date": "2026-07-18T01:00:00", "LitreValue": 4, "MonetaryValue": 0.02,
    "Key": "12:00\nam|1:00\nam", "ReadingType": 1, "Status": null }
]
```
Field semantics:
- **`LitreValue`** — consumption in **litres** for the interval (integer).
- **`MonetaryValue`** — cost in **£** for the interval.
- **`Date`** — interval timestamp (local, no tz). For hourly, `Date` is the
  **end** of the hour (00:00→01:00 row has `Date=01:00`).
- **`Key`** — display label (`\n`-separated); ignore for data purposes.
- **`ReadingType`** — `1` vs `0`. Observed: older intervals = `1`, most recent
  1–3 days = `0`. Best guess: `1 = actual/confirmed`, `0 = provisional/estimated`.
  _TODO: confirm._
- **`Status`** — null in all samples.

Observed windowing behaviour:
- **Hourly**: `StartDate` = the target day → returns ~24 rows for that day.
- **Daily**: returned the most recent **7 days** (rolling), despite `StartDate`
  being the account start date. _TODO: confirm whether a narrower `StartDate`
  or an `EndDate` field narrows/extends it._
- **Weekly / Monthly / Yearly**: same shape, coarser buckets (not re-captured;
  assume identical contract). _TODO: confirm max range + whether `EndDate` is
  supported._

### Other endpoints seen (not needed for core usage)
- `POST /api/Customer/GetMeterReadHistory` — manual/actual meter reads
- `POST /api/Customer/GetWaterUsageEfficiency`
- `POST /api/Customer/GetSmartMeterAlertStatus` — leak/alert status
- `GET  /api/Customer/GetUsageComparison?noOfOccupiers=&lastYearAvgUsage=`
- `POST /api/Auth/SaveUserProfile` — empty response
- `POST /api/BillsPayments/GetBillsPayments`, `GET /api/BillsPayments/GetPaymentPlan?accountId=`

### Minimal library flow (verified)
1. `POST /api/Auth/Login` → read `Response.refresh_token` from the body
2. `POST /api/Auth/SaveUserProfile` (whole `Response` object)
3. `GET  /api/Customer/GetAccountSummary?personId=PersonId` (binds session)
4. `POST /api/Customer/GetSmartAuthToken {refresh_Token}` → JWT (+ rotated token)
5. For each account: `POST /api/Customer/AddOrUpdateCustomerSession` (re-binds
   the session to that account) → `POST /api/Customer/GetAccountDetails` →
   meter `BadgeNumber` (serial)
6. `POST /api/Customer/Get…WaterUsage` (JWT in body + serial) → readings

`AddOrUpdateCustomerSession`'s order relative to the token doesn't matter for
auth; only steps 2 and 3 must precede step 4. But it does matter relative to
`GetAccountDetails`: it must immediately precede each call, per account (see
above) — otherwise only the account bound by step 3 returns real data.

## Gotchas
- Cloudflare + reCAPTCHA sit on the **login page**; the LoginRadius API login
  may avoid the browser reCAPTCHA, but watch for challenges / rate limits.
- Send `X-Requested-With: XMLHttpRequest`.
- JWT lifetime ~10 min — refresh proactively; the portal session cookie lasts
  longer (re-login on 401).
- `LitreValue` is already litres — no unit conversion needed.

## Still TODO (minor; needs another short capture / test)
1. `ReadingType` 0 vs 1 meaning (guess: 1 = actual/confirmed, 0 = provisional).
2. Whether usage endpoints accept an `EndDate` / arbitrary historical ranges
   (matters for HA long-term-statistics backfill).
3. Weekly/Monthly/Yearly `StartDate` windowing (assumed same contract as daily).

_Resolved: auth needs no LoginRadius apikey — the seed refresh_token is in the
`userProfile` cookie from `/api/Auth/Login`._
