# Probably Stolen: save editing and Wild's favor

Checked November 21, 2026. The [official Steam Workshop](https://steamcommunity.com/workshop/browse/?appid=4348910) links to `dragonpan2/probably-stolen-modding` via its Learn More button (`instructionsURL`). The guide says the developers reviewed it and checked its examples and API names against the game source. The links below pin the GitHub evidence to commit `f1ec77b3021af7124d01478aae58e7a754d43609`. [Guide provenance](https://github.com/dragonpan2/probably-stolen-modding/blob/f1ec77b3021af7124d01478aae58e7a754d43609/ModdingGuide.md#L1-L7)

## Save editing

- **Player-owned data.** The guide tells mod authors not to delete saves or silently corrupt them, to disclose save-affecting behavior before installation, and to document safe removal steps. The supported `ModHelper.GetModData*` / `SetModData*` APIs store a mod's own namespaced data with a run; the example `reputation` key is mod-owned, not the game's favor balance. [Save File Responsibility and support policy](https://github.com/dragonpan2/probably-stolen-modding/blob/f1ec77b3021af7124d01478aae58e7a754d43609/ModdingGuide.md#L229-L247) · [Saving Mod Data](https://github.com/dragonpan2/probably-stolen-modding/blob/f1ec77b3021af7124d01478aae58e7a754d43609/ModdingGuide.md#L718-L739)
- **In-game tools, not a disk editor.** The guide warns that F9 developer buttons can permanently change money, reputation and other state; use a disposable save for testing. The September 14 demo notes describe **Discover**, which loads saves manually added from another player, not editing their contents. [Dev Menu](https://github.com/dragonpan2/probably-stolen-modding/blob/f1ec77b3021af7124d01478aae58e7a754d43609/ModdingGuide.md#L168-L175) · [Update 049, Quality of Life](https://steamstore-a.akamaihd.net/news/externalpost/steam_community_announcements/1843481262702145)
- **Mod file access.** Path-taking Easy Save (ES3) operations count toward the in-game `files` capability. The guide and changelog say mod calls or patches to `SaveFiles` will also count as `files` from **December 10, 2026**; the changelog names restore, quarantine, integrity check and import helpers. This is the announced mod-scanner policy, not an external editor API or evidence of enforcement in the installed build. [Capability table](https://github.com/dragonpan2/probably-stolen-modding/blob/f1ec77b3021af7124d01478aae58e7a754d43609/ModdingGuide.md#L1450-L1453) · [Guide: SaveFiles](https://github.com/dragonpan2/probably-stolen-modding/blob/f1ec77b3021af7124d01478aae58e7a754d43609/ModdingGuide.md#L1485-L1486) · [API changelog](https://github.com/dragonpan2/probably-stolen-modding/blob/f1ec77b3021af7124d01478aae58e7a754d43609/APIChanges.md#L25-L39) · [Steam: 100-Rev13 Hotfix](https://store.steampowered.com/news/app/4348910/view/676262871030563152)

## Wild's favor

- **Spending:** The guide calls it **Wild's favor** (Chinese guide: **Wild 的人情**). Wild's Network spends favor before cash. `ModHelper.AddNetworkUpgrade` registers upgrades whose purchase, cooldown and cost state persist by ID; the documented getters read upgrade state, not the player's favor balance. [Network Upgrades](https://github.com/dragonpan2/probably-stolen-modding/blob/f1ec77b3021af7124d01478aae58e7a754d43609/ModdingGuide.md#L988-L1041) · [Chinese guide](https://github.com/dragonpan2/probably-stolen-modding/blob/f1ec77b3021af7124d01478aae58e7a754d43609/ModdingGuide.zh-CN.md#L1033-L1039)
- **Earning:** The difficulty API example sets `DifficultyRules.tradeFavorRate` to `0.02f` for a custom difficulty. This illustrates changing the trade favor rate, not a vanilla default or a setter for the accumulated balance. [Difficulties](https://github.com/dragonpan2/probably-stolen-modding/blob/f1ec77b3021af7124d01478aae58e7a754d43609/ModdingGuide.md#L1135-L1175)

## 本机版本核对

- 原版 `UI_zh` 本地化表中的 `network_ui_wild_favor` 将 `wildFavor` 显示为「王尔德的恩惠」；`ui_wild_favor` 说明下层区声望和乔·王尔德解锁的夜间服务是获取渠道。`WildNetworkUI` 购买升级时先扣恩惠、不足再扣现金。旧标签「荒野人情值」是管理器误译。
- `PlayerStore` 与 `StoreStation` 是当前槽位的两条主要记录。管理器的 [字段清单](../core/save_fields.py) 仅开放在本机游戏读写逻辑中核实过的字段；兼容未知字段时保留原始字节。编辑器范围用于限制输入，不等同于游戏硬上限。
- `StoreClientManager.GetNormalCustomerCount` 根据吸引力、声望福利、天赋等生成普通顾客人数。顾客购买预算分别由顾客生成逻辑赋值；`StoreClientListWanted` 使用 `PlayerStore.odinBudget` 生成奥丁的购买预算，不影响其他顾客。
- 不直接写天数、剧情日程、物品及唯一 ID、阵营福利和一次性事件标记、鉴定证书和晋升状态，以及模组自有数据。极高风险违禁品加价在这个游戏构建中读档会重置，也没有开放编辑。

## 阵营声望：本机存档与游戏 DLL

通过 `ilspycmd` 核对安装目录 `Probably Stolen_Data/Managed/Assembly-CSharp.dll`（SHA-256 `725cb999d019dff1ff31bed353c7f9bb8d81a7605a15de1f80f5a3a6cf7b1d05`），并只读比对 `%USERPROFILE%/AppData/LocalLow/Questing Goose Studio/Probably Stolen/` 下的 `save_0.es3`、`save_1.es3` 及其日备份。以下是这个构建和这些存档的结论，不代表其他版本或模组。

`playerStore.value.storeReputations[]` 是每局当前声望的来源。每项的 `factionId` 确定身份；`amount` 是 `double`，不是整型，也不是累计获得量。`StoreReputation.GetReputation` 把分数向零截断给界面与部分玩法使用；`GetReputationExact` 保留小数。`CheckReputation` 将正常调整的结果限制在 `[-200, 200]`。编辑器沿用该范围，并按 ID 而不是数组索引修改 `amount`。

| `factionId` | 阵营 | 证据及编辑策略 |
| --- | --- | --- |
| `FACTION_LOWER_LEVEL` | 下层区 | 两个槽位均存在，可编辑。 |
| `FACTION_UPPER_LEVEL` | 上层区 | 两个槽位均存在，可编辑。 |
| `FACTION_SECURITY` | 治安部 | 两个槽位均存在，可编辑。 |
| `FACTION_BLACK_MARKET` | 黑市 | 两个槽位均存在，可编辑。 |
| `FACTION_REVOLUTION` | 革命军 | 两个槽位均存在，可编辑。 |
| `FACTION_CARTEL` | 卡特尔 | `InitReputation` 仅为药剂师开局创建；`PlayerStore` 读档时会按 `isCartelExited` 移除它。两份实档均无此项；仅对有该记录的档位开放，不创建或恢复阵营。 |
| `FACTION_TOURIST` / `FACTION_CHURCH` | 游客 / 教会 | DLL 中有 ID 或引用，但 `InitReputation` 不为它们建项，这些实档也没有；不创建、不开放写入。 |
| `BE_FACTION_OUTER_RING` | 外环区 | 两个槽位均存在，不是原版初始化项。模组联动未验证，和其他未知阵营一样只读显示。 |

- **福利与负面效果是联动状态。** `PlayerStore` 读档调用 `StoreReputation.AddMissingPerksAfterLoad`，后者补原版缺项再调用 `UpdateReputation`：负门槛比较 `amount <= threshold`，零或正门槛比较 `amount >= threshold`。`IsPerkUnlocked` 使用 `isUnlocked || isAlwaysActive`；常驻福利不因普通门槛变化而停用。编辑器不手写 `perks[].{perkId,perkThreshold,perkType,isUnlocked,isAlwaysActive,iconName}`。新门槛可能改变开店后的顾客、事件或福利；不撤销之前已经发生的结果。
- **一次性事件不由分数反推。** `perks[].firstTimeUnlockActionDone` 是单独保存的历史标记。`StoreShutterButton` 还检查市场请求升级和 `REV_REQUEST` 条件，在首次触发时放置口令并设置该标记。实档 0 的 `REV_REQUEST` 已记录。编辑器保留该标记，不补发、取消或直接触发事件。
- **王尔德的恩惠是独立余额。** `playerStore.value.wildFavor` 另存；正常 `StoreReputation.ModReputation(string, int, …)` 在下层区正增量时按传入增量的 40 倍增加恩惠，正常交易还有难度配置的独立恩惠渠道。直接设档位分数不会运行这些奖励、通知或成就检查，也不改变恩惠。
- **其他阵营字段不是声望。** `factionDisplay` 与 `factionColor1/2/3` 是显示数据；`strength`、`influence`、`maxInfluence`、`actionCooldown` 属于另一套状态（`StoreStation` 推进时增加影响力、递减冷却）；`currentReportPenalty` 在举报时按 `ReportedClient` 使用并递降至 −20；`canVandalized`、`repLossMultiplier` 也是独立存档字段，未作为分数联动写入。`storeStation.value.storeOperationManager` 下的站区友好度和势力，以及 `playerStore.value.wrongfulReportCount`，也都不是声望值。

写入仍走现有的关游戏、版本校验、原档备份、限位替换流程。阵营列表重复或格式无法识别时关闭此项编辑；其他既有字段不受影响。只读实档核对与夹具写回验证不等于进游戏验收。

**范围：** 工坊指南、变更日志和已核对的 Steam 公告中，未找到官方支持的外部存档编辑流程，也没有可穷举全部可写字段的稳定规范。上述可编辑项基于本机游戏构建，不保证未来版本的格式与机制不变。
