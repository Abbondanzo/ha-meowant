# Meowant API

Reference for the cloud API behind the official **AIR PET** app (iOS id1643683280), which Meowant litter boxes (SC-01, SC-02) use. Meowant boxes are rebranded UBTech **UBPet / UPET / Airrobo** devices, so they share the UBTech "AirPet" cloud platform.

**Status:** login and the device list are verified against a live Meowant account (AIR PET 2.1.9 and this repo's client, 2026-10-21). Everything else comes from secondary sources (see [Sources](#sources)) and is marked by confidence.

## Tenants

The same backend code serves several branded tenants. Each tenant has its own:

| Value     | Where it's used                         | Meowant value                                                 |
| --------- | --------------------------------------- | ------------------------------------------------------------- |
| Base URL  | every REST call, and the MQTT host      | `https://apis.airrobo-home.com`                               |
| `APP_ID`  | `x-ubt-appid` header                    | `970010016`                                                   |
| `APP_KEY` | the signing secret in `x-ubt-sign`      | see `DEFAULT_APP_KEY` in `custom_components/meowant/const.py` |
| `PRODUCT` | `product` header on authenticated calls | `97001`                                                       |

These are app-level constants baked into the app (like Hercules's bootstrap credentials), not per-user secrets. Known UPET (EU app) hosts: `https://apis-eu.airrobo-home.com`, `https://apis-ru.airrobo-home.com`. A separate North America UPET tenant also exists. The Meowant tenant is distinct from all of these: credentials from the UPET app do not work against it, and vice versa.

The Meowant values come from upet-hass PR #5's bundled profile and match what AIR PET 2.1.9 sends, including its `X-UBT-Sign`. The Android app is `com.yibainetwork.airpet` (AIR PET, signed by "CN=AIRPET YibaiNetwork"). Its QR-code landing page lives on `airpet.ubtrobot.com`, which is a different host from the API base URL.

## REST

### Common request headers

As sent by AIR PET 2.1.9 (Android) when logging in:

```
Content-Type: application/json; charset=UTF-8
User-Agent: okhttp/4.10.0
X-UBT-AppId: <APP_ID>
X-UBT-DeviceId: <32 hex chars, stable per install>
X-UBT-Sign: <signature, see below>
# logged-in requests only:
authorization: <token>          # raw token, no "Bearer " prefix
X-UBT-Language: en              # localizes some fields, e.g. cat breed names (Chinese without it)
# authenticated calls only:
authorization: <token>          # raw token, no "Bearer " prefix
product: <PRODUCT>
```

### Signing (`x-ubt-sign`) (confirmed against the AIR PET Android app)

```
x-ubt-sign = "<md5hex> <unix_ts_ms> <nonce> v2"
md5hex     = md5(ts + APP_KEY + nonce + x_ubt_deviceid)
nonce      = 10 random chars from [0-9A-Za-z]
```

The Android app sends the timestamp in **milliseconds** (13 digits). The UPET iOS app and upet-hass use seconds. Use milliseconds to match the AIR PET app.

The request body is **not** part of the signature.

### Response envelope

`{"code": 0, "message": "...", "data": ...}`. A non-zero `code` means an error, even with HTTP 200. Observed error codes (messages are in Chinese). A successful login response, by contrast, has no envelope (see below):

| code | meaning                                                                                                                                                                                          |
| ---- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| 2002 | wrong account or password (用户或密码错误)                                                                                                                                                       |
| 2004 | user does not exist (用户不存在), often a wrong tenant/region                                                                                                                                    |
| 1009 | parameter error (参数错误)                                                                                                                                                                       |
| 2342 | too many wrong account/password attempts; try again later (账号密码错误次数过多，请稍后再试). The account is temporarily locked. Every failed attempt counts, so make one login attempt per try. |

HTTP 401 means the token expired. Log in again once and retry.

### Login: `PUT /user-service-rest/v2/user/login` (confirmed against the AIR PET app)

```json
{
  "account": "<email>",
  "accountType": 1,
  "areaCode": "US",
  "password": "<md5hex(password)>"
}
```

- `accountType` is a JSON **number**: `1` for email, `0` for phone.
- `areaCode` is the account's ISO country code, chosen at signup and returned as `user.countryCode`. **It's required.** An empty or wrong area code gives `2004` (user does not exist), even with the correct email.
- The app sends fields in the order shown.

A successful response is **bare JSON with no `{code, data}` envelope**:

```json
{
  "token": {"token": "<url-encoded token>", "expireAt": 1793163661144, "refreshToken": "<32 hex>"},
  "user": {"userId": 123456, "countryCode": "US", "countryName": "United States of America", "userEmail": "<encrypted>", "nickName": "...", ...}
}
```

`expireAt` is in ms, about 7 days out. `userEmail`/`userName` come back encrypted (base64). The token contains `%2B`/`%2F` escapes. Send it back exactly as received; whether it gets URL-decoded first is still unverified.

### Read endpoints (authenticated)

| Method + path                                                 | Returns                                                                                                          |
| ------------------------------------------------------------- | ---------------------------------------------------------------------------------------------------------------- |
| `GET /user-service-rest/v2/robot/common/device/list`          | list of devices; key on `serialNumber` (verified, see below)                                                     |
| `GET /catbox-server/box/config/allConfig?serialNumber=`       | all box settings                                                                                                 |
| `GET /catbox-server/box/config/box-use-times/?serialNumber=`  | waste-bin use counter                                                                                            |
| `GET /catbox-server/app/deodorant-block/status?serialNumber=` | deodorant block status                                                                                           |
| `GET /v1/ubtechinc-im-manager/im/online/device/?sn=`          | online status                                                                                                    |
| `GET /catbox-server/web/cat/info`                             | cat profiles (weight, etc.)                                                                                      |
| `POST /catbox-server/web/box/record/new`                      | event records: `{"eventTime":"0","serialNumber":…,"size":N,"messageType":0}` (0 = cat visits, 1 = device events) |

Device list response (`data`), from a Meowant SC-01 shared to the logged-in account:

```json
[
  {
    "serialNumber": "FAJ003UBT6000…",
    "deviceStatus": 1,
    "deviceName": "Smart Cat Litter Box",
    "adminUserNickName": "…",
    "shareFlag": 1,
    "defaultFlag": 0,
    "boxColor": 0
  }
]
```

`shareFlag: 1` means the box is shared to this account rather than owned by it. Read access works for a shared account.

### Box state (observed on a Meowant SC-01, firmware `main_v1.0.0.43`)

Response shapes as returned in `data`. Values are examples; personal fields are elided.

**`allConfig`**:

```json
{
  "configId": 266300,
  "serialNumber": "…",
  "name": "",
  "wifiName": "…",
  "gmtTimeZone": "GMT-04:00",
  "offsetSeconds": -14400,
  "dst": 0,
  "sensorSwitch": 0,
  "cameraSwitch": 1,
  "lightSwitch": null,
  "lazyTime": 60,
  "controlBoardSwitch": 0,
  "uplogSwitch": 0,
  "uplogLevel": 1,
  "videoPushFlag": 1,
  "forcedUpgradeSwitch": 0,
  "silenceUpgradeSwitch": 1,
  "weighUnit": 1,
  "litterType": 1,
  "boxUseTimes": 0,
  "boxFullAlertSwitch": 0,
  "boxColor": 0,
  "taskConfigList": [
    {
      "timeTaskConfigId": 99473,
      "openSwitch": 1,
      "timeHour": 10,
      "timeMinute": 0
    }
  ],
  "disturbTimeConfigList": [
    {
      "disturbTimeConfigId": 21146,
      "openSwitch": 1,
      "startTimeHour": 23,
      "startTimeMinute": 0,
      "endTimeHour": 9,
      "endTimeMinute": 0
    }
  ],
  "lightTimeConfigList": [],
  "boxFunctionVOList": [
    { "firmwareVersion": "main_v1.0.0.43", "type": 1, "openSwitch": 1 }
  ],
  "notifyConfigList": [
    {
      "notifyConfigId": null,
      "type": 4,
      "openSwitch": 0,
      "timeHour": 19,
      "timeMinute": 0,
      "period": null
    }
  ]
}
```

- `taskConfigList`: scheduled cleans (local time, one config ID each). `disturbTimeConfigList`: do-not-disturb windows. These are what `timePoint/update` / `timePeriod/update` edit, via `configId`.
- `lazyTime`: auto-clean delay after a visit, most likely in **seconds** (60 = 1 min).
- `controlBoardSwitch`: child lock (read-only per upet-hass). `boxFunctionVOList[].firmwareVersion` is the firmware version. Its `type` values (1, 3, 4, 5 here) are unknown.

**`box-use-times`**: `{"boxUseTimes": 0, "boxFullMax": 30, "boxFullAlert": 20, "percentage": 0, "alertType": null, "boxUseTimesResetTime": null}`. Waste-bin uses since emptied, capacity, alert threshold, fill %.

**`deodorant-block/status`**: `{"alertSwitch": 0, "startTime": null, "remainingDays": 20, "expired": false}`.

**`im/online/device`**: `{"sn": "…", "onlineTime": "Sep 16, 2026 11:27:08 PM", "ip": "…", "online": true}`. `onlineTime` is when the current connection started.

**`record/new`**: paged: `{"records": [...], "total": 69, "size": 5, "current": 1, "pages": 14}`. Each record:

```json
{
  "recordId": 1010643733,
  "userId": 0,
  "catInfoId": 0,
  "catNickname": null,
  "icon": null,
  "timeStr": "2026-10-21 10:46:09",
  "eventTime": 1792550769000,
  "videoPath": "",
  "type": 0,
  "eventType": 21,
  "status": 0,
  "content": null,
  "weight": 3.4,
  "costTime": 67,
  "serialNumber": "…",
  "confirmed": 0
}
```

- **`timeStr` is China time (UTC+8), not the box's time zone.** Use `eventTime` (epoch ms).
- `eventType` 21 = cat visit (`weight` kg, `costTime` s; `catInfoId` 0 = not matched to a cat). 33 = scheduled clean (times match `taskConfigList`). 47 = probably the auto clean after a visit (seen about 5 min after visits). Other codes are unknown.
- `messageType` 0 returns cat visits and 1 returns device events.

**`cat/info`**: list of cat profiles: `catInfoId`, `nickname`, `gender`, `birthday`, `weight`/`baseWeight` (kg), `icon` (image URL), `catStatus`, `catBreed` `{id, breed, icon}`, `healthAlertConfig`, plus daily counters `number`/`costTime`/`subNumber`/`subCostTime`/`subWeight` and `lastWeigthTimestamp` (sic). `catBreed.breed` is in Chinese unless the request sends `X-UBT-Language: en`.

### Write endpoints (authenticated)

| Method + path                                       | Body                                                                                                                                   |
| --------------------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------- |
| `PUT /catbox-server/box/config/switch/update`       | `{serialNumber, functionType, controlSwitch: 0/1, optionInt}`. `functionType` 2 = auto clean, `optionInt` = delay in seconds           |
| `PUT /catbox-server/box/config/timePeriod/update`   | `{serialNumber, configId, functionType, openSwitch, startTimeHour, startTimeMinute, endTimeHour, endTimeMinute}` (e.g. do not disturb) |
| `PUT /catbox-server/box/config/timePoint/update`    | `{serialNumber, configId, functionType, openSwitch, timeHour, timeMinute, optionInt}`                                                  |
| `PUT /catbox-server/box/config/box-use-times/reset` | `{"boxUseTimes": 0, serialNumber}` (waste bin emptied)                                                                                 |
| `POST /catbox-server/app/deodorant-block/switch`    | `{serialNumber, alertSwitch: 0/1}`                                                                                                     |
| `POST /catbox-server/app/deodorant-block/replaced`  | `{serialNumber}` (medium confidence)                                                                                                   |

## Commands and live state: IM/MQTT

Actions (clean, level, pause) and live work state do **not** go over REST. They go over UBTech's IM channel:

- `POST /v1/ubtechinc-im-manager/im/login` with `{"type": 2}` returns MQTT credentials (`mqttUserName`, `mqttPassword`, `pubTopic`, sender UID).
- `GET /v1/ubtechinc-im-manager/im/friends` lists contacts. The box is the contact whose `userName` equals its serial number, and its `userId` is the target UID.
- MQTT over **TLS, port 21000**, on the same host as the REST base URL.
- Payload nesting: IM `Message` → `MessageContent` (custom) → protobuf `Any(BytesValue)` → `CommandProto(protype=2)` → RISP frame → AirPet protobuf body.
- RISP command `0x3fd` requests state, and `0x3fb` sends an operation.
- Operation bodies: clean `08011001`, level `08031001`, rise `08071001`, drop `08081001`. The last byte selects start (1), pause (2) or resume (3), e.g. pause clean = `08011002`.
- Work modes: 1 = CLEAN, 3 = SMOOTHING, 7 = RISE, 8 = DROP. Quirk: SC-01 firmware can report mode IDLE (0) with state RUNNING while it's actually ready. Treat IDLE mode as idle regardless of state.

Implementing this needs an MQTT client plus hand-rolled protobuf encoding. That's the bulk of the work beyond REST.

## Sources

- [CrazzyBerg/upet-hass](https://github.com/CrazzyBerg/upet-hass): working UPET integration (REST + MQTT)
  - [PR #5 "Add Meowant support"](https://github.com/CrazzyBerg/upet-hass/pull/5): Meowant tenant profile, verified on an SC-01.
  - [PR #4 "Add support for North America"](https://github.com/CrazzyBerg/upet-hass/pull/4)
- [HA forum: Meowant Litter Box](https://community.home-assistant.io/t/meowant-litter-box/592177): hardware teardown (ESP32-C3), the AIR PET / UBTech connection.
