# Time & Count / 时间与计数

Seven preview-only instruments: an eight-tube fluorescent digital clock, a circular
analog clock, a Monday-first monthly calendar, countdown, stopwatch with 20 laps,
round Pomodoro timer and a compact counter. All controls live on the instrument.
Click a time or count readout to edit it in place. Duration editing uses separate
hours, minutes and seconds; apply with ✓ or Enter, cancel with × or Escape.
Clocks show a location chip that opens a searchable city list. The calendar
month/date are editable. Timer controls use circular play, pause, reset and lap
icons with accessible labels and hover hints. A cool blue palette, small unframed
transport controls and compact default dimensions keep the instruments light:
countdown 264×148, stopwatch 264×164, Pomodoro 224×224 and counter 256×132.
Pomodoro's progress ring and the countdown bar use recessed tracks with a blue
light core, soft glow and a highlighted rim. The glow brightens while running.
Existing manually resized instances retain their saved dimensions.
The host's management menu appears on hover/focus, just outside the upper-right arc of round cards,
and retains rename and delete. It contains no instrument settings.

包含电子时钟、圆盘时钟、月历、倒计时、秒表、番茄钟和计数器。仅使用第二级
显示，无需展开到详情或工作区。卡牌内可设置时间、时区、时长或计数。

Timers use persisted timestamps, so refresh, canvas culling and backend restarts
preserve elapsed time. Pausing freezes the value. Finishing stops at zero;
Start begins another cycle. Pomodoro offers manual 25-minute focus / 5-minute
break presets. Completion is visual; there are no background alarms, automatic
Agent runs or automatic phase changes. Clocks default to Shanghai. Choose a city
or the device's local city from the location chip; its IANA zone automatically
follows daylight-saving rules. The list shows city names, countries and local
times. Previously saved fixed offsets remain readable until a city is selected.
Setting a clock affects only that card. Clock edits are interpreted in the selected
city; nonexistent spring-forward times are rejected and repeated autumn hours use
the first occurrence. A manually adjusted clock can return to system time from
the inline time editor, independently of its city selection.
Round cards declare `ui.ellipse.v1`, so their visible shape, edge endpoints,
connection previews, hover hints and native drag handles share an ellipse boundary,
including when resized to a non-square ellipse.

Connect an Agent to an instrument and choose a function-specific relationship:
read, set time/zone/date/duration, start, pause, reset, lap, increment, decrement or
set count. Each relationship grants only its operation and read access. Removing
the connection revokes access immediately. Cards support only relevant operations;
these connections expose scoped Agent tools, not automatic signal propagation.
Count and timer mutations use the host's document revision checks. A conflict asks
the user to reload before retrying. Capturing a template preserves settings and
count but resets timer runs and laps.

Development uses the ordinary repository source-Pack discovery and SDK build:

```powershell
& backend/.venv/Scripts/python.exe .github/run-backend-tests.py backend/tests/test_time_pack.py
cd frontend
npx --no-install vitest run --config ../plugins/time/vitest.config.mts
```

Pack metadata: `python scripts/oaw-pack.py doctor plugins/time`.
