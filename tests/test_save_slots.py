import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from fastapi.testclient import TestClient

from core.save_slots import SlotError, list_slots, parse_slot, read_slot, slot_revision, write_slot
from core.settings import AppSettings
from webui.server import create_app


def sample(slot_id=0):
    return (b'{"playerStore":{"__type":"PlayerStore,Assembly-CSharp","value":'
            b'{"playerCash":22,"saveSlotId":' + str(slot_id).encode() + b',"wildFavor":365,'
            b'"nested":{{"name":"a\\\xe2\x80\x9c} [{"}: {"playerCash":999}}}},'
            b'"storeStation":{"__type":"StoreStation,Assembly-CSharp","value":{"dayCounter":106}}}')


def expanded_sample():
    return (sample().replace(b'"wildFavor":365', b'"wildFavor":365,"rentValue":468,"garbageLevel":0,'
                             b'"tutorialEnable":false,"healthData":{"luck":-5,"isWoundStable":true,'
                             b'"sentinelPool":27,"other":{"luck":123}},"commissaryData":{"redeemedCredit":12}')
            .replace(b'"dayCounter":106', b'"dayCounter":106,"storeOperationManager":{"secPower":100}'))


def reputation_sample():
    # The amounts are double values, and list order is not an identity.
    return expanded_sample().replace(b'"wildFavor":365,', b'"wildFavor":365,"storeReputations":[ '
        b'{"amount":31.2,"factionId":"BE_FACTION_OUTER_RING","factionDisplay":"Outer Ring"},'
        b'{"amount":-1.125e1,"factionId":"FACTION_SECURITY","currentReportPenalty":-10},'
        b'{"amount":18.568749999999991,"factionId":"FACTION_LOWER_LEVEL","strength":3,"influence":29,'
        b'"perks":[{"perkId":"LLREFERAL","perkThreshold":20,"perkType":1,"isUnlocked":false,'
        b'"isAlwaysActive":false,"firstTimeUnlockActionDone":true}]},'
        b'{"amount":25,"factionId":"FACTION_CARTEL","perks":[]},'
        b'{"amount":0,"factionId":"FACTION_BLACK_MARKET","perks":[{"perkId":"BM_HONOR",'
        b'"isUnlocked":true,"isAlwaysActive":true,"firstTimeUnlockActionDone":false}]} ],')


class SaveSlotsTests(unittest.TestCase):
    def setUp(self):
        tmp = TemporaryDirectory(prefix="psmm-slot-test-")
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.save = self.root / "saves"
        self.save.mkdir()
        self.path = self.save / "save_0.es3"
        self.path.write_bytes(sample())
        self.backups = self.root / "backups"

    def test_es3_only_patches_exact_schema_fields(self):
        with self.assertRaises(json.JSONDecodeError):
            json.loads(sample())
        slot = read_slot(self.save, 0)
        self.assertEqual(slot.values, {"cash": 22, "favor": 365})
        self.assertEqual(slot.day, 106)
        edited = slot.patch({"cash": 101, "favor": 9056})
        self.assertIn(b'"playerCash":999', edited)
        self.assertEqual(parse_slot(edited, 0).patch(slot.values), sample())
        for invalid in (sample(1), sample()[:-1], sample().replace(b'"playerCash":22', b'"playerCash":22,"playerCash":23')):
            with self.assertRaises(SlotError):
                parse_slot(invalid, 0)

    def test_nested_integer_and_toggle_edits_preserve_other_fields(self):
        before = expanded_sample()
        slot = parse_slot(before, 0)
        changes = {"luck": -20, "woundStable": False, "tutorialEnabled": True,
                   "commissaryCredit": 20, "securityPower": 50, "rentValue": 350}
        with patch("core.save_slots.is_game_running", return_value=False):
            self.path.write_bytes(before)
            write_slot(self.save, self.backups, 0, slot_revision(self.save, 0, slot), changes=changes)
        after = read_slot(self.save, 0)
        self.assertEqual(after.values, {**slot.values, **changes})
        self.assertIn(b'"other":{"luck":123}', after.data)
        self.assertEqual(after.values["sentinelPool"], 27)  # Existing out-of-range value is untouched.
        self.assertEqual(after.patch({key: slot.values[key] for key in changes}), before)

    def test_absent_or_unrecognized_optional_fields_are_not_writable(self):
        originals = (sample(), expanded_sample().replace(b'"luck":-5', b'"luck":"-5"'),
                     expanded_sample().replace(b'"luck":-5', b'"luck":-5,"luck":99'))
        for original in originals:
            self.path.write_bytes(original)
            slot = read_slot(self.save, 0)
            self.assertNotIn("luck", slot.values)
            for changes in ({"luck": 0}, {"cash": True}, {"favor": -1}, {"currentUniqueId": 3}):
                with self.subTest(changes=changes), self.assertRaises(SlotError), \
                        patch("core.save_slots.is_game_running", return_value=False):
                    write_slot(self.save, self.backups, 0, slot_revision(self.save, 0, slot), changes=changes)
            self.assertEqual(self.path.read_bytes(), original)
            self.assertFalse(self.backups.exists())

    def test_reputation_writes_only_amounts_by_id_then_restores_original_bytes(self):
        before = reputation_sample()
        self.path.write_bytes(before)
        slot = read_slot(self.save, 0)
        self.assertEqual(slot.values['lowerLevelReputation'], 18.568749999999991)
        self.assertEqual(slot.values['securityReputation'], -11.25)
        self.assertEqual(slot.values['cartelReputation'], 25)
        self.assertNotIn('upperLevelReputation', slot.values)
        self.assertEqual([(rep.faction_id, rep.amount) for rep in slot.read_only_reputations], [('BE_FACTION_OUTER_RING', 31.2)])
        changes = {'lowerLevelReputation': 20.125, 'blackMarketReputation': -200, 'securityReputation': 200}
        with patch('core.save_slots.is_game_running', return_value=False):
            backup = write_slot(self.save, self.backups, 0, slot_revision(self.save, 0, slot), changes=changes)
            after = read_slot(self.save, 0)
            expected = (before.replace(b'"amount":18.568749999999991,', b'"amount":20.125,')
                        .replace(b'"amount":-1.125e1,', b'"amount":200,')
                        .replace(b'"amount":0,"factionId":"FACTION_BLACK_MARKET"',
                                 b'"amount":-200,"factionId":"FACTION_BLACK_MARKET"'))
            self.assertEqual(after.data, expected)  # Perk flags, favor, report penalty and custom reputation stay unchanged.
            self.assertEqual(after.values, {**slot.values, **changes})
            self.assertEqual((self.backups / '0' / backup / 'original.es3').read_bytes(), before)
            write_slot(self.save, self.backups, 0, slot_revision(self.save, 0, after), restore=backup)
        self.assertEqual(self.path.read_bytes(), before)

    def test_malformed_or_ambiguous_reputations_leave_other_save_fields_available(self):
        before = reputation_sample()
        for changed in (
            before.replace(b'FACTION_CARTEL', b'FACTION_LOWER_LEVEL'),
            before.replace(b'"amount":25,', b'"amount":25,"amount":26,'),
            before.replace(b'"amount":25,', b'"amount":1e999,'),
            before.replace(b'"amount":25,', b'"amount":"25",'),
            before.replace(b'false}]} ],', b'false}]}, ],'),
        ):
            with self.subTest(size=len(changed)):
                slot = parse_slot(changed, 0)
                self.assertTrue(slot.reputation_issue)
                self.assertEqual(slot.values['cash'], 22)
                self.assertNotIn('lowerLevelReputation', slot.values)
                with self.assertRaises(SlotError):
                    slot.patch({'lowerLevelReputation': 20.125})

    def test_reputation_api_signed_decimals_and_read_only_rows(self):
        manager = self.root / 'manager'
        AppSettings(save_dir=str(self.save)).save(manager / 'settings.json')
        before = reputation_sample()
        self.path.write_bytes(before)
        with TestClient(create_app(manager), base_url='http://127.0.0.1', headers={'X-PSMM-Request': '1'}) as client, \
                patch('core.save_slots.is_game_running', return_value=False):
            path = '/api/save-slots/0'
            slot = client.get(path).json()
            fields = {row['key']: row for row in slot['fields']}
            spec = fields['lowerLevelReputation']
            self.assertEqual((spec['kind'], spec['min'], spec['max']), ('number', -200, 200))
            custom_key = 'reputation:BE_FACTION_OUTER_RING'
            self.assertTrue(fields[custom_key]['read_only'])
            self.assertEqual(fields[custom_key]['value'], 31.2)
            for change in ({custom_key: 120}, {'lowerLevelReputation': True}, {'lowerLevelReputation': '12.5'},
                           {'lowerLevelReputation': 200.01}, {'lowerLevelReputation': -200.01}, {'cash': 12.5}):
                with self.subTest(change=change):
                    self.assertEqual(client.put(path, json={'revision': slot['revision'], 'changes': change}).status_code, 422)
            self.assertEqual(client.put(path, json={'revision': slot['revision'],
                             'changes': {'upperLevelReputation': 10}}).status_code, 409)
            self.assertEqual(self.path.read_bytes(), before)
            result = client.put(path, json={'revision': slot['revision'], 'changes': {'lowerLevelReputation': -20.125}})
            self.assertEqual(result.status_code, 200, result.text)
            self.assertEqual(read_slot(self.save, 0).values['lowerLevelReputation'], -20.125)

    def test_backup_restore_and_stale_revision(self):
        (self.save / "save_0_day101.es3").write_bytes(sample())
        self.assertEqual(list_slots(self.save), [0])
        slot = read_slot(self.save, 0)
        revision = slot_revision(self.save, 0, slot)
        with patch("core.save_slots.is_game_running", return_value=False):
            name = write_slot(self.save, self.backups, 0, revision, changes={"cash": 3563})
            self.assertEqual((self.backups / "0" / name / "original.es3").read_bytes(), sample())
            with self.assertRaisesRegex(SlotError, "档位已变更"):
                write_slot(self.save, self.backups, 0, revision, changes={"cash": 123})
            current = read_slot(self.save, 0)
            self.assertEqual(current.values["cash"], 3563)
            write_slot(self.save, self.backups, 0, slot_revision(self.save, 0, current), restore=name)
            self.assertEqual(self.path.read_bytes(), sample())
        with patch("core.save_slots.is_game_running", return_value=True):
            with self.assertRaisesRegex(SlotError, "关闭游戏"):
                write_slot(self.save, self.backups, 0, revision, changes={"cash": 5})

    def test_api_round_trip_contract_and_rejects_wrong_directory(self):
        manager = self.root / "manager"
        AppSettings(save_dir=str(self.save)).save(manager / "settings.json")
        with TestClient(create_app(manager), base_url="http://127.0.0.1",
                        headers={"X-PSMM-Request": "1"}) as client, \
                patch("core.save_slots.is_game_running", return_value=False), \
                patch("webui.slot_routes.is_game_running", return_value=False):
            self.assertEqual(client.get("/api/save-slots").json()["slots"], [0])
            path = "/api/save-slots/0"
            slot = client.get(path).json()
            invalid = {"revision": slot["revision"], "changes": {"day": 2}}
            self.assertEqual(client.put(path, json=invalid).status_code, 422)
            body = {"revision": slot["revision"], "changes": {"favor": 500}}
            other = self.root / "other"
            other.mkdir()
            (other / "save_0.es3").write_bytes(sample())
            AppSettings(save_dir=str(other)).save(manager / "settings.json")
            self.assertEqual(client.put(path, json=body).status_code, 409)
            AppSettings(save_dir=str(self.save)).save(manager / "settings.json")
            self.assertEqual(client.put(path, json=body).status_code, 200)
            current = client.get(path).json()
            self.assertEqual(current["favor"], 500)
            body = {"revision": current["revision"], "backup": current["backups"][0], "confirm": True}
            self.assertEqual(client.post(path + "/restore", json=body).status_code, 200)
            self.assertEqual(self.path.read_bytes(), sample())

    def test_api_lists_descriptions_validates_ranges_and_strict_types(self):
        manager = self.root / "manager"
        AppSettings(save_dir=str(self.save)).save(manager / "settings.json")
        self.path.write_bytes(expanded_sample())
        with TestClient(create_app(manager), base_url="http://127.0.0.1",
                        headers={"X-PSMM-Request": "1"}) as client, \
                patch("core.save_slots.is_game_running", return_value=False):
            path = "/api/save-slots/0"
            slot = client.get(path).json()
            fields = {row["key"]: row for row in slot["fields"]}
            self.assertEqual(fields["favor"]["label"], "王尔德的恩惠")
            self.assertEqual(fields["luck"]["min"], -100)
            self.assertEqual(fields["woundStable"]["kind"], "boolean")
            self.assertTrue(all(row["description"] and row["group"] for row in fields.values()))
            for changes in ({"securityPower": 101}, {"woundStable": 0}, {"luck": "12"}, {"cash": True}, {"cash": 1.0}):
                with self.subTest(changes=changes):
                    self.assertEqual(client.put(path, json={"revision": slot["revision"], "changes": changes}).status_code, 422)
            absent = {"revision": slot["revision"], "changes": {"baseAttractiveness": 500}}
            self.assertEqual(client.put(path, json=absent).status_code, 409)
            changes = {"luck": -12, "woundStable": False, "securityPower": 40}
            result = client.put(path, json={"revision": slot["revision"], "changes": changes})
            self.assertEqual(result.status_code, 200, result.text)
            self.assertEqual({key: read_slot(self.save, 0).values[key] for key in changes}, changes)
