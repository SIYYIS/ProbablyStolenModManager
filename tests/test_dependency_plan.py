"""Backend tests for the read-only prerequisite planner."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from core.dependency_plan import plan_dependencies
from core.manifest import ModManifest
from core.paths import GamePaths
from core.scanner import InstalledMod, scan_installed_mods


def mod(mid: str, *prereqs: str, error: str | None = None) -> InstalledMod:
    return InstalledMod(ModManifest(mid, mid, prerequisites=list(prereqs), parse_error=error), Path(mid), "local")


def write_manifest(folder: Path, mid: str, *prereqs: str) -> None:
    folder.mkdir(parents=True, exist_ok=True)
    entries = "".join(f"<Mod>{pre}</Mod>" for pre in prereqs)
    (folder / "manifest.xml").write_text(
        f"<Manifest><ID>{mid}</ID><Prerequisites>{entries}</Prerequisites></Manifest>", encoding="utf-8"
    )


class TestDependencyPlan(unittest.TestCase):
    def test_recursive_additions_preserve_unrelated_draft_order(self) -> None:
        draft = ["first", "A", "last"]
        installed = {m.mod_id: m for m in (mod("A", "B"), mod("B", "C"), mod("C"), mod("first"), mod("last"))}

        plan = plan_dependencies(draft, installed)

        self.assertEqual(plan["draft"], ["first", "C", "B", "A", "last"])
        self.assertEqual(plan["added"], ["C", "B"])
        self.assertEqual(plan["status"], "review_ready")
        self.assertTrue(plan["review_ready"])
        self.assertFalse(plan["limited"])
        self.assertEqual(plan["cycles"], [])
        items = {item["id"]: item for item in plan["items"]}
        self.assertEqual(items["A"]["state"], "installed")
        self.assertEqual(items["B"]["state"], "inactive")
        self.assertEqual(items["B"]["required_by"], ["A"])
        self.assertEqual(items["C"]["required_by"], ["B"])
        self.assertEqual(draft, ["first", "A", "last"])
        self.assertEqual(installed["A"].manifest.prerequisites, ["B"])
        self.assertEqual(plan, json.loads(json.dumps(plan)))

    def test_shared_prerequisite_and_installed_reordering_are_stable(self) -> None:
        mods = (mod("A", "B", "C", "B"), mod("C", "D"), mod("B", "D", "D"), mod("D"), mod("outside", "missing"))
        installed = {m.mod_id: m for m in mods}
        draft = ["A", "C", "D", "A"]

        plan = plan_dependencies(draft, installed)

        self.assertEqual(plan["draft"], ["D", "B", "C", "A"])
        self.assertEqual(plan["added"], ["B"])
        self.assertEqual(plan["items"][0]["required_by"], ["C", "B"])
        self.assertEqual(plan["items"][1]["required_by"], ["A"])
        self.assertEqual(plan, plan_dependencies(draft, dict(reversed(list(installed.items())))))
        reviewed = plan_dependencies(plan["draft"], installed)
        self.assertEqual(reviewed["draft"], plan["draft"])
        self.assertEqual(reviewed["added"], [])

    def test_missing_ids_are_only_from_exact_source_mappings(self) -> None:
        installed = {"A": mod("A", "share", "catalog", "agreed", "Unknown", "1234567890", "bad", "zero")}
        installed["A"].workshop_id = "1234567890"
        hints = {"share": "1111111111", "agreed": "0003333333333", "unknown": "4444444444", "bad": "id=5555555555", "zero": "0"}
        catalog = {"catalog": "2222222222", "agreed": "3333333333"}

        plan = plan_dependencies(["A"], installed, share_hints=hints, verified_catalog=catalog)

        self.assertEqual(plan["draft"], ["A"])
        self.assertEqual(plan["status"], "blocked")
        self.assertFalse(plan["review_ready"])
        items = {item["id"]: item for item in plan["items"]}
        for mid, item_id, source in [
            ("share", "1111111111", "share_hint"),
            ("catalog", "2222222222", "verified_catalog"),
            ("agreed", "3333333333", "verified_catalog"),
            ("Unknown", None, None), ("1234567890", None, None), ("bad", None, None), ("zero", None, None),
        ]:
            with self.subTest(mid=mid):
                self.assertEqual(items[mid]["state"], "missing")
                self.assertEqual(items[mid]["required_by"], ["A"])
                self.assertEqual((items[mid]["workshop_id"], items[mid]["workshop_source"]), (item_id, source))
        self.assertIsNone(items["A"]["workshop_id"])

    def test_ambiguous_hints_do_not_offer_download_ids(self) -> None:
        plan = plan_dependencies(
            ["disagreement", "reused"], {},
            share_hints={"disagreement": "101", "reused": "201", "other-mod": "0201"},
            verified_catalog={"disagreement": "102"},
        )

        self.assertEqual(plan["draft"], ["disagreement", "reused"])
        self.assertFalse(plan["review_ready"])
        for item in plan["items"]:
            self.assertEqual(item["state"], "missing")
            self.assertEqual(item["workshop_source"], "ambiguous")
            self.assertIsNone(item["workshop_id"])

    def test_invalid_manifests_are_blockers_and_no_edges_are_trusted(self) -> None:
        installed = {
            "A": mod("A", "bad-dep"),
            "bad-dep": mod("bad-dep", "untrusted", error="cannot parse"),
            "bad-root": mod("bad-root", error="cannot read"),
        }

        plan = plan_dependencies(["A", "bad-root"], installed)

        self.assertFalse(plan["review_ready"])
        self.assertEqual(plan["draft"], ["A", "bad-root"])
        self.assertEqual(plan["added"], [])
        items = {item["id"]: item for item in plan["items"]}
        self.assertEqual(set(items), set(installed))
        self.assertEqual(items["bad-dep"]["required_by"], ["A"])
        self.assertEqual(items["bad-dep"]["state"], "invalid_manifest")
        self.assertEqual(items["bad-root"]["state"], "invalid_manifest")
        self.assertEqual(items["bad-root"]["manifest_error"], "cannot read")

    def test_cycle_members_exclude_downstream_and_include_self_edges(self) -> None:
        mods = (mod("A", "B"), mod("B", "C", "D"), mod("C", "B"), mod("D", "C", "base"), mod("base"), mod("self", "self"))

        plan = plan_dependencies(["A", "self"], {m.mod_id: m for m in mods})

        self.assertEqual(plan["cycles"], [["self"], ["B", "C", "D"]])
        self.assertFalse(plan["review_ready"])
        self.assertEqual(plan["draft"], ["base", "B", "C", "D", "A", "self"])

    def test_long_prerequisite_chain_does_not_require_python_recursion(self) -> None:
        mids = [f"mod{i}" for i in range(1500)]
        installed = {left: mod(left, right) for left, right in zip(mids, mids[1:])}
        installed[mids[-1]] = mod(mids[-1])

        plan = plan_dependencies(mids[:1], installed)

        self.assertTrue(plan["review_ready"])
        self.assertEqual(plan["draft"], mids[::-1])

    def test_invalid_discovered_ids_are_not_exposed_or_added(self) -> None:
        longest_valid = "x" * 512
        invalid_ids = ("y" * 513, "nul\0here", " \t", "")
        mods = (mod("A", "B"), mod("B", longest_valid, *invalid_ids), mod(longest_valid))

        plan = plan_dependencies(["A"], {m.mod_id: m for m in mods})

        self.assertFalse(plan["review_ready"])
        self.assertFalse(plan["limited"])
        self.assertEqual(plan["draft"], [longest_valid, "A"])
        self.assertEqual([item["id"] for item in plan["items"]], [longest_valid, "B", "A"])
        self.assertEqual(plan["items"][1]["state"], "invalid_manifest")
        self.assertIn("invalid prerequisite ID", plan["items"][1]["manifest_error"])

    def test_reachable_node_limit_counts_transitive_nodes(self) -> None:
        mids = [f"mod{i}" for i in range(4097)]
        installed = {left: mod(left, right) for left, right in zip(mids, mids[1:])}
        installed[mids[-1]] = mod(mids[-1])

        at_limit = plan_dependencies(mids[1:2], installed)
        self.assertFalse(at_limit["limited"])
        self.assertTrue(at_limit["review_ready"])
        self.assertEqual(len(at_limit["draft"]), 4096)

        over_limit = plan_dependencies(mids[:1], installed)
        self.assertTrue(over_limit["limited"])
        self.assertFalse(over_limit["review_ready"])
        self.assertEqual(over_limit["status"], "blocked")
        self.assertEqual(len(over_limit["items"]), 4096)
        self.assertEqual(len(over_limit["draft"]), 4096)
        self.assertNotIn(mids[-1], over_limit["draft"])

    def test_unique_edge_limit_ignores_duplicate_declarations(self) -> None:
        leaves = [f"leaf{i}" for i in range(128)]
        roots = [f"root{i}" for i in range(129)]
        installed = {mid: mod(mid) for mid in leaves}
        installed.update({mid: mod(mid, *leaves, leaves[0]) for mid in roots})

        at_limit = plan_dependencies(roots[:-1], installed)
        self.assertFalse(at_limit["limited"])
        self.assertTrue(at_limit["review_ready"])
        self.assertEqual(at_limit["items"][0]["required_by"], roots[:-1])

        over_limit = plan_dependencies(roots, installed)
        self.assertTrue(over_limit["limited"])
        self.assertFalse(over_limit["review_ready"])
        self.assertEqual(over_limit["status"], "blocked")
        self.assertEqual(sum(len(item["required_by"]) for item in over_limit["items"]), 16384)

    def test_each_reachable_manifest_is_enumerated_once(self) -> None:
        class SinglePass(list):
            visited = False

            def __iter__(self):
                if self.visited:
                    raise AssertionError("Manifest prereqs enumerated twice")
                self.visited = True
                return super().__iter__()

        mods = (mod("A", "B"), mod("X", "B"), mod("B", "C"), mod("C"), mod("unreachable", "A"))
        for entry in mods:
            entry.manifest.prerequisites = SinglePass(entry.manifest.prerequisites)

        plan = plan_dependencies(["A", "X"], {m.mod_id: m for m in mods})

        self.assertTrue(plan["review_ready"])
        self.assertEqual(plan["draft"], ["C", "B", "A", "X"])
        self.assertFalse(mods[-1].manifest.prerequisites.visited)

    def test_refresh_uses_effective_copy_and_leaves_files_unchanged(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            local, workshop = root / "Mods", root / "Workshop"
            save = root / "SaveFile.es3"
            save.write_bytes(b"unchanged")
            paths = GamePaths(local_mods_dir=local, workshop_dir=workshop, save_file=save)
            write_manifest(workshop / "1000000001", "A", "wrong-copy-dep")
            write_manifest(local / "override", "A", "B")
            hints = {"B": "1000000002"}

            before = plan_dependencies(["A"], scan_installed_mods(paths), share_hints=hints)
            self.assertFalse(before["review_ready"])
            self.assertEqual(before["items"][0]["id"], "B")
            self.assertEqual(before["items"][0]["workshop_id"], "1000000002")
            self.assertEqual(before["draft"], ["A"])

            write_manifest(workshop / "1000000002", "B", "C")
            broken = local / "C"
            broken.mkdir()
            (broken / "manifest.xml").write_text("<Manifest>", encoding="utf-8")
            partial = plan_dependencies(before["draft"], scan_installed_mods(paths), share_hints=hints)
            self.assertFalse(partial["review_ready"])
            self.assertEqual(partial["items"][0]["state"], "invalid_manifest")
            self.assertEqual(partial["draft"], ["B", "A"])

            write_manifest(broken, "C")
            snapshot = {p: p.read_bytes() for p in root.rglob("*") if p.is_file()}
            ready = plan_dependencies(before["draft"], scan_installed_mods(paths), share_hints=hints)

            self.assertTrue(ready["review_ready"])
            self.assertEqual(ready["draft"], ["C", "B", "A"])
            self.assertEqual({p: p.read_bytes() for p in root.rglob("*") if p.is_file()}, snapshot)

    def test_empty_draft_is_review_ready(self) -> None:
        self.assertEqual(plan_dependencies([], {"unrelated": mod("unrelated", "missing")}), {
            "draft": [], "added": [], "items": [], "cycles": [], "status": "review_ready", "review_ready": True,
            "limited": False,
        })


if __name__ == "__main__":
    unittest.main()
