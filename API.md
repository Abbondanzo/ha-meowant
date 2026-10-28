# Meowant API

Reference for the cloud API behind the official **AIR PET** app (iOS id1643683280), which Meowant litter boxes (SC-01, SC-02) use. Meowant boxes are rebranded UBTech **UBPet / UPET / Airrobo** devices, so they share the UBTech "AirPet" cloud platform.

**Status:** login and the device list are verified against a live Meowant account (AIR PET 2.1.9 and this repo's client, 2026-10-21). Everything else comes from secondary sources (see [Sources](#sources)) and is marked by confidence. The IM/MQTT channel is verified against a live Meowant SC-01 (2026-10-28).

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

Actions (clean, level, pause/resume) and live work state do **not** go over REST. They go over UBTech's IM (instant messaging) channel, which runs on MQTT. The protocol comes from upet-hass (UPET brand, same platform). **Verified against a Meowant SC-01 on 2026-10-28** (this repo's dev console, from an account the box is shared to): connect, all-state, start/pause/resume clean and level, receipts, acks and state pushes all behave as described here.

### Getting MQTT credentials (REST, logged in)

- `POST /v1/ubtechinc-im-manager/im/login` with `{"type": 2}` returns the MQTT login: `clientId`, `mqttUserName`, `mqttPassword`, `pubTopic`, `subTopic`, and possibly the account's IM `uid`. The account's IM uid is the same as the REST `user.userId` (verified: box replies are addressed to it). Calling it may hand out a fresh session; whether that affects the phone app's own IM session is unknown.
- `GET /v1/ubtechinc-im-manager/im/friends` lists IM contacts. The box is the contact whose `userName` equals its serial number; its `userId` is the box's IM uid (the message target).

### Connection

- MQTT 3.1.1 over **TLS, port 21000**, on the REST base URL's host (`apis.airrobo-home.com`).
- CONNECT with `clientId` / `mqttUserName` / `mqttPassword`, clean session, keepalive 20 s. SUBSCRIBE to `subTopic` at QoS 1. PUBLISH requests to `pubTopic` at QoS 0.

### Message nesting

Every payload is protobuf (proto3: zero/empty fields omitted), nested five deep:

```
IM Message
  1 cid (string)          "<ms>+<6-digit random>"
  2 gid (string)          (ms << 20) + counter, as a decimal string
  3 createdAt (varint)    ms when we send; the box sends **seconds**
  4 from (string)         account IM uid
  5 to (string)           box IM uid
  6 type (varint)         seen on incoming messages
  7 content (bytes)  -->  MessageContent
                            1 type (string)  "custom"
                            2 body (bytes) -->  google.protobuf.Any
                                                  1 type_url  "type.googleapis.com/google.protobuf.BytesValue"
                                                  2 value -->  BytesValue
                                                                 1 value -->  CommandProto
                                                                                1 protype (varint)  2 = RISP
                                                                                2 frame (bytes) --> RISP frame
  8 version (string)      "1.0.1"
```

Delivery receipts come back from the server (not the box) for every message we publish, as `Any` with type_url `type.googleapis.com/rosa.im.protobuf.ReceiptNotification`: `1` status (`receipt/success`, `receipt/unreachable`), `2` cause (e.g. `send message success`). The receipt's outer `cid` and `createdAt` are those of the message it acknowledges; its MessageContent type is `receipt` and outer `type` is 2.

### RISP frame

```
0xDB | header length (1 byte) | header | CRC8(header) | body | CRC8(body)   (body + its CRC omitted when empty)

header (protobuf):  1 dev = 2 (AirPet)   2 cmd   4 body length   5 attr   7 seq (1..0x7FFF, wraps)
attr:               1 = request (ours)   2 = reply (echoes our seq)   6 = push from the box (its own seq counter)
CRC8: poly 0x07, init 0x00, final XOR 0x55
```

| cmd | Meaning | Body |
|---|---|---|
| `0x3FD` | request all state; the reply (attr 2) uses the same cmd | empty → all-state body below |
| `0x3FB` | operation; the box acks (attr 2, same seq) with an empty body | `1` op mode, `2` op state |
| `0x400` | **pushed by the box** (attr 6) on every work-state change | all-state body below |
| `0x3FF` | pushed by the box (attr 6) when a clean or level finished, right after the `0x400` | unknown: `2` = 2000 and `3` = 1 every time; `1` differs each time, e.g. 0xFFDF6FE9 (−2134039 as int32) |

The CRC model above checks out on frames from the box too.

Reference frames with seq 1: state request `db09080210fd0728013801f6`; start clean `db0b080210fb072004280138018b08011001de`.

### Operations (what you send)

| Op mode | | Op state | |
|---|---|---|---|
| 1 | clean | 1 | start |
| 3 | level (smooth) litter | 2 | pause |
| 7 | rise | 3 | resume |
| 8 | drop | | |

Rise/drop are only known to take "start". Bodies: start clean `08011001`, pause clean `08011002`, resume clean `08011003`, start level `08031001`. Start, pause and resume of both clean and level are verified on an SC-01; within ~150 ms the box acks and pushes its new state. Start rise (`08071001`) is acked but produced no state push and no visible state change.

### All-state body (what the box reports, in `0x3FD` replies and `0x400` pushes)

Body fields (all varints): `1` work mode, `2` work state, `3` cause, `4` toilet, `5` foreign object, `6` weigh, `7` lid, `8` motor, `9` key, `10` object, `11` camera, `12` image ctrl, `13` picture ctrl, `14` update, `15` hall, `16` infrared, `17` box, `18` timestamp (Unix **seconds**), `19` weigh sensor, `20` LED. Most sub-state values aren't decoded yet; on an SC-01 only fields 1, 2, 3 and 18 have been non-zero so far.

Observed on an SC-01 (2026-10-28), operations started from the app:

| Moment | Mode | State | Cause |
|---|---|---|---|
| At rest | 0 | 1 | 90 |
| After start clean / resume clean | 2 | 1 | 50 |
| After pause clean | 2 | 3 | 60 |
| Clean finished by itself (~90 s of cleaning) | 0 | 1 | 90 |
| After start level / resume level | 3 | 1 | 100 |
| After pause level | 3 | 3 | 110 |
| Level finished by itself | 0 | 1 | 140 |

So `cause` looks like the reason for the current state, specific to the operation: clean runs/pauses/finishes as 50/60/90 and level as 100/110/140. Inferred from these runs only.

| Work mode | | Work state | |
|---|---|---|---|
| 0 | idle | 0 | idle |
| 1 | resetting | 1 | running |
| 2 | cleaning | 2 | blocked |
| 3 | levelling | 3 | paused |
| 4 | replacing litter | | |
| 5 | raking up | | |

Note the work-mode numbers differ from the op-mode numbers above (cleaning is op mode 1 but work mode 2). Quirk (SC-01, per upet-hass PR #5, and confirmed: it's how an idle SC-01 always reports): work mode 0 with state 1 (running). Treat mode 0 as idle regardless of state.

Because proto3 omits zero fields, a box that's idle with every sub-state at 0 should reply with an **empty** body (`dataLen` 0, no body CRC). So recognize a reply by its sender (the box's uid) and cmd, not by whether it has a body. Our own request has an empty body too, but comes from the account uid.

### How this repo uses it

`im_proto.py` (encoding), `mqtt.py` (a minimal MQTT 3.1.1 client: no paho-mqtt dependency) and `im.py` (the session) implement the above. The HA integration's `live.py` keeps **one persistent connection per config entry**, not one per command as upet-hass does. So `im/login` runs once per connection, not on every poll. State comes mainly from the box's `0x400` pushes. An all-state request every 60 s is a backstop that also gets the state right after connecting. It reconnects with backoff (30 s doubling to 15 min) and stops for good on auth/lockout errors, so it never adds password logins toward the lockout.

### Open questions

Answered on 2026-10-28: the Meowant tenant accepts all of this as-is; replies use attr 2 and pushes attr 6; the box pushes state on every change, including a clean finishing by itself; a **shared** account can send operations; receipts carry the acknowledged message's `cid`.

Still open:

- Does holding an IM connection (or calling `im/login`) disconnect the phone app's own IM session?
- Does the box push for cleans it starts itself (scheduled, or after a visit), as it did for one finishing by itself?
- What do rise (7) and drop (8) physically do? Rise is acked but changes no reported state; drop is untested.
- Sub-state values (`toilet`, `lid`, `weigh`, ...) and the full set of `cause` codes. What is the `0x3FF` push?
- What does `receipt/unreachable` look like when the box is offline?

## Sources

- [CrazzyBerg/upet-hass](https://github.com/CrazzyBerg/upet-hass): working UPET integration (REST + MQTT)
  - [PR #5 "Add Meowant support"](https://github.com/CrazzyBerg/upet-hass/pull/5): Meowant tenant profile, verified on an SC-01.
  - [PR #4 "Add support for North America"](https://github.com/CrazzyBerg/upet-hass/pull/4)
- [HA forum: Meowant Litter Box](https://community.home-assistant.io/t/meowant-litter-box/592177): hardware teardown (ESP32-C3), the AIR PET / UBTech connection.
