"""Editable ES3 leaves corroborated against the game's readers and save slots."""

from dataclasses import dataclass
from typing import Literal


LIMIT = 10_000_000
SlotValue = int | float | bool


@dataclass(frozen=True)
class SaveField:
    key: str
    record: bytes
    path: tuple[bytes, ...]
    label: str
    description: str
    group: str
    kind: Literal["integer", "number", "boolean"]
    minimum: int = 0
    maximum: int = LIMIT
    unit: str = ""
    faction_id: str | None = None

    def accepts(self, value: SlotValue) -> bool:
        if self.kind == "boolean":
            return type(value) is bool
        if self.kind == "number":
            return type(value) in (int, float) and self.minimum <= value <= self.maximum
        return type(value) is int and self.minimum <= value <= self.maximum

    def present(self, value: SlotValue) -> dict:
        data = {"key": self.key, "label": self.label, "description": self.description,
                "group": self.group, "kind": self.kind, "value": value}
        if self.kind in {"integer", "number"}:
            data.update(min=self.minimum, max=self.maximum)
        if self.unit:
            data["unit"] = self.unit
        return data


def field(key: str, path: str, label: str, description: str, group: str, maximum: int = LIMIT,
          *, minimum: int = 0, unit: str = "", toggle: bool = False, station: bool = False) -> SaveField:
    return SaveField(key, b"storeStation" if station else b"playerStore", tuple(path.encode("ascii").split(b".")),
                     label, description, group, "boolean" if toggle else "integer", minimum, maximum, unit)


FUNDS = "资金与账务"
SHOP = "商店经营"
OUTING = "外出与拾荒"
HEALTH = "健康与状态"
STATION = "站区局势"
REPUTATION = "阵营声望"


@dataclass(frozen=True)
class FactionReputation:
    faction_id: str
    label: str
    amount: float

    def present_read_only(self) -> dict:
        return {"key": f"reputation:{self.faction_id}", "label": f"{self.label}声望", "group": REPUTATION,
                "kind": "number", "value": self.amount, "read_only": True,
                "description": f"{self.faction_id} · 未核实该阵营的联动规则，仅供查看。"}


def reputation_field(key: str, faction_id: str, label: str, description: str) -> SaveField:
    return SaveField(key, b"playerStore", (b"amount",), label, description, REPUTATION, "number",
                     minimum=-200, maximum=200, faction_id=faction_id)


REPUTATION_FIELDS = {
    spec.faction_id: spec for spec in (
        reputation_field("lowerLevelReputation", "FACTION_LOWER_LEVEL", "下层区声望",
                         "影响下层区的转介绍、顾客与负面事件。直接调整不会发放王尔德的恩惠。"),
        reputation_field("upperLevelReputation", "FACTION_UPPER_LEVEL", "上层区声望",
                         "影响上层区顾客、评价与福利。与站区的「上层区友好度」是不同数据。"),
        reputation_field("securityReputation", "FACTION_SECURITY", "治安部声望",
                         "影响治安部的检查待遇、保护与相关福利，不改变治安部势力。"),
        reputation_field("blackMarketReputation", "FACTION_BLACK_MARKET", "黑市声望",
                         "影响黑市顾客与交易福利，不改变黑市势力。"),
        reputation_field("revolutionReputation", "FACTION_REVOLUTION", "革命军声望",
                         "影响革命军福利及事件条件，不清除已触发的一次性事件记录。"),
        reputation_field("cartelReputation", "FACTION_CARTEL", "卡特尔声望",
                         "只在档位已有该阵营时显示；不改变开局身份或退出状态。"),
    )
}

# Editor input limits are not a promise that the game never stores other values.
FIELDS = {entry.key: entry for entry in (
    field("cash", "playerCash", "玩家现金", "目前可支配的信用点，用于采购、支付账单等。", FUNDS, unit="信用点"),
    field("favor", "wildFavor", "王尔德的恩惠", "下层区声望提升或使用乔·王尔德解锁的夜间服务可积累恩惠。购买王尔德的升级时先扣恩惠，不足再扣现金。", FUNDS),
    field("commissaryCredit", "commissaryData.redeemedCredit", "兑换票券", "在夜间票券兑换点兑换货品时使用，不是现金。", FUNDS, unit="票券"),
    field("rentValue", "rentValue", "基础租金", "下一次缴租的基础金额，不含欠缴租金；正常缴租后仍会按难度规则上涨。", FUNDS, unit="信用点"),
    field("temporaryRent", "temporaryRent", "欠缴租金", "因延期而累积、下次需与基础租金一起付的钱。设为 0 可清除这笔欠缴额。", FUNDS, unit="信用点"),
    field("dayUntilRent", "dayUntilRent", "账单倒计时", "距离下一期租金或房贷付款的天数。游戏每天推进时会扣减；不改变贷款本金。", FUNDS, 365, unit="天"),
    field("dayWithoutInsurance", "dayWithoutInsurance", "连续未投保天数", "连续未投保会提高夜间失窃风险。设为 0 可重置累计天数，但不会购买保险。", FUNDS, 365, unit="天"),
    field("baseAttractiveness", "baseStoreAttractiveness", "商店基础吸引力", "吸引力还要扣掉垃圾惩罚、加上投影广告。原版普通顾客数量在 200、250、500 吸引力处各有一个加成门槛；这不是固定客流。", SHOP, 10000),
    field("garbageLevel", "garbageLevel", "店门口垃圾等级", "0 表示干净；高于 2 时会降低商店吸引力。这个字段不删除垃圾桶里的物品。", SHOP, 10),
    field("retailMarkup", "retailMarkup", "零售加价", "符合原版零售加价条件的交易，会在估价时加上这个百分比。不是所有顾客或物品都适用。", SHOP, 500, unit="%"),
    field("contrabandMarkupLow", "contrabandMarkupLow", "低风险违禁品加价", "用于低风险违禁品相关的估价规则；不是顾客购买预算。", SHOP, 500, unit="%"),
    field("contrabandMarkupMid", "contrabandMarkupMid", "中风险违禁品加价", "用于中风险违禁品相关的估价规则；不是顾客购买预算。", SHOP, 500, unit="%"),
    field("contrabandMarkupHigh", "contrabandMarkupHigh", "高风险违禁品加价", "用于高风险违禁品相关的估价规则。极高风险档由本版游戏在读档时重置，不在此修改。", SHOP, 500, unit="%"),
    field("odinBudget", "odinBudget", "奥丁的购买预算", "原版特定顾客奥丁生成时使用的购买预算。不会改变其他顾客的金额。", SHOP, 1500, unit="信用点"),
    field("wrongfulReportCount", "wrongfulReportCount", "误报次数", "当前累计的错误举报次数。会影响误报警告及后续治安处理；不会清除已有案件。", SHOP, 100, unit="次"),
    field("tutorialEnabled", "tutorialEnable", "教程提示", "控制游戏是否显示教程及相关提示，不会重置已经完成的教程步骤。", SHOP, toggle=True),
    *REPUTATION_FIELDS.values(),
    field("visitLeftTonight", "visitLeftTonight", "今夜剩余外出次数", "当前夜晚还能离开店面外出的次数。重伤、宵禁等条件仍可能阻止外出；之后会按正常规则重置。", OUTING, 20, unit="次"),
    field("scavengingAttempts", "scavengingAttempts", "本轮已拾荒次数", "记录已经用掉的搜索次数。降低可增加剩余搜索机会，并降低随搜索次数增加的受伤风险。", OUTING, 30, unit="次"),
    field("minorWoundImmunity", "dumpingGroundMinorWoundImmunitySearchesLeft", "拾荒轻伤保护", "垃圾场搜索时免受轻伤的剩余次数，游戏会随搜索扣减。", OUTING, 100, unit="次"),
    field("majorWoundImmunity", "dumpingGroundMajorWoundImmunitySearchesLeft", "拾荒重伤保护", "垃圾场搜索时免受重伤的剩余次数，游戏会随搜索扣减。", OUTING, 100, unit="次"),
    field("woundState", "healthData.woundState", "伤势严重度", "0 表示无伤，超过 5 会被视为重伤并限制行动。夜间仍按原版规则推进伤势。", HEALTH, 10),
    field("woundStable", "healthData.isWoundStable", "伤势已稳定", "稳定的伤势会在夜间恢复；不稳定的伤势可能恶化。这个开关不直接改变伤势严重度。", HEALTH, toggle=True),
    field("luck", "healthData.luck", "基础运气", "参与好运或厄运判定。游戏还会叠加随身物品与液体好运的效果。", HEALTH, 100, minimum=-100),
    field("liquidLuckDaysLeft", "healthData.liquidLuckDaysLeft", "液体好运剩余天数", "大于 0 时保留液体好运加成，每天推进时扣减。设为 0 可结束这项效果。", HEALTH, 365, unit="天"),
    field("nicotineDays", "healthData.daysSinceNicotineConsumption", "距上次摄入尼古丁", "用于已有尼古丁成瘾天赋的戒断及康复判定，不会添加成瘾天赋。", HEALTH, 365, unit="天"),
    field("alcoholDays", "healthData.daysSinceAlcoholConsumption", "距上次饮酒", "用于已有酒精成瘾天赋的戒断及康复判定，不会添加成瘾天赋。", HEALTH, 365, unit="天"),
    field("narcoticDays", "healthData.daysSinceNarcoticConsumption", "距上次摄入麻醉药物", "用于已有药物成瘾天赋的戒断及康复判定，不会添加成瘾天赋。", HEALTH, 365, unit="天"),
    field("sentinelDays", "healthData.daysSinceSentinelInjection", "距上次哨兵注射", "用于哨兵免疫缺陷相关的逾期判定。降低可推迟逾期；不会改动累计病情。", HEALTH, 365, unit="天"),
    field("sentinelPool", "healthData.sentinelPool", "哨兵病情累计值", "疾病判定用的累计值，不是注射次数。原版达到 20 可能导致该病对应的结局。", HEALTH, 19),
    field("lowerLevelUnrest", "storeOperationManager.lowerLevelUnrest", "下层区动荡", "影响下层区的罢工、骚乱等事件能否进入候选。它不是你在下层区的声望。", STATION, 100, station=True),
    field("upperLevelFriendliness", "storeOperationManager.upperLevelFriendliness", "上层区友好度", "与下层区动荡一起影响上层区是否提出医疗援助或物资救济。它不是店铺声望。", STATION, 100, station=True),
    field("revolutionPower", "storeOperationManager.revPower", "革命军势力", "影响革命军的动员及突袭事件；只改变阵营局势，不改变你的声望。", STATION, 100, station=True),
    field("securityPower", "storeOperationManager.secPower", "治安部势力", "影响巡逻、镇压和戒严等事件的条件；之后仍会随游戏进程变动。", STATION, 100, station=True),
    field("blackMarketPower", "storeOperationManager.blackMarketPower", "黑市势力", "影响帮派冲突和治安部针对黑市行动等事件的条件。", STATION, 100, station=True),
    field("gutterflowPurity", "todayGutterflowPurity", "今日沟渠水纯度", "影响今天从水管取得的沟渠水。10000 表示 100%，次日重新生成。", STATION, 10000, unit="万分比", station=True),
    field("rustwaterPurity", "todayRustwaterPurity", "今日铁锈水纯度", "影响今天从水管取得的铁锈水。10000 表示 100%，次日重新生成。", STATION, 10000, unit="万分比", station=True),
)}
