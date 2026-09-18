# Daily Banner

> 每天 `daily_banner.time`（默认 08:20）推早报：未来 N 天日程 + 未完成待办 + 昨天以来未读邮件。设计：`docs/superpowers/specs/2026-09-17-daily-banner-design.md`。

## 开启

- `config.json` → `daily_banner.enabled: true`。
- `data/members.json` 该成员加 `"banner": true`（opt-in；没加不推）。
- 邮件段需该成员 `mail` 块已配好（见 `Mail_Keeper/SKILL.md`）；日程段需 `calendar.enabled` + 该成员 `sync` 块，否则只读本地库。

## 排障

- 当天已推：`data/.state/.daily_banner_state.json` 删该成员当天日期即可重推（窗口内下一拍，~20 秒）。
- 推送/成文失败：日志 `familyassist.banner`；进程内 600 秒后重试，`catchup_until` 后放弃当天。
