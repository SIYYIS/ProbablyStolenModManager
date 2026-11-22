import os
import stat
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from zipfile import ZipFile, ZipInfo

from py7zr import SevenZipFile

import core.archive_install as installer
from core.archive_install import ArchiveError, extract_archive, plan_install, publish
from core.paths import GamePaths


class ArchiveInstallTests(unittest.TestCase):
    def test_nested_package_publishes_only_after_preview_and_does_not_replace(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            archive = root / 'mod.zip'
            with ZipFile(archive, 'w') as z:
                z.writestr('Release-v2/Actual/MANIFEST.XML', '<Mod><ID>Demo.Mod</ID><Name>Demo</Name></Mod>')
                z.writestr('Release-v2/Actual/Textures/a.png', b'png')
                z.writestr('Release-v2/readme.txt', b'wrapper')
            payload = root / 'payload'
            mods_dir = root / 'Mods'
            modset = extract_archive(archive, '.zip', payload)
            paths = GamePaths(local_mods_dir=mods_dir)
            plan = plan_install(paths, modset)
            self.assertEqual(plan.rows[0].id, 'Demo.Mod')
            self.assertFalse(mods_dir.exists())
            publish(paths, modset, plan, game_running=lambda: False)
            self.assertEqual((mods_dir / 'Demo.Mod/Textures/a.png').read_bytes(), b'png')
            self.assertTrue((mods_dir / 'Demo.Mod/manifest.xml').is_file())
            self.assertFalse((mods_dir / 'Demo.Mod/readme.txt').exists())
            self.assertTrue(plan_install(paths, modset).blockers)

    def test_real_7z_with_empty_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            archive = root / 'mod.7z'
            with SevenZipFile(archive, 'w') as z:
                z.writestr('<Manifest><ID>Seven.Mod</ID></Manifest>', 'Outer/Inner/manifest.xml')
                z.writestr(b'', 'Outer/Inner/empty.txt')
            payload = root / 'payload'
            paths = GamePaths(local_mods_dir=root / 'Mods')
            mods = extract_archive(archive, '.7z', payload)
            publish(paths, mods, plan_install(paths, mods), game_running=lambda: False)
            self.assertEqual((root / 'Mods/Seven.Mod/empty.txt').read_bytes(), b'')

    def test_windows_aliases_conflicting_paths_and_links_are_rejected(self):
        cases = [('../outside.txt',), ('/absolute',), ('inside\\escape',), ('Base/config:stream',),
                 ('Base/COM¹.txt',), ('Base/com1 .json',), ('Base/FILE~1.TXT',), ('Base/x.',),
                 ('Base/Foo/a.txt', 'Base/fOO/b.txt'), ('Base/Thing', 'Base/Thing/child'),
                 ('Base/Ａ.txt', 'Base/A.txt')]
        for case in cases:
            with self.subTest(case=case), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                archive = root / 'invalid.zip'
                with ZipFile(archive, 'w') as z:
                    z.writestr('Base/manifest.xml', '<Manifest><ID>Safe</ID></Manifest>')
                    for member in case:
                        entry = ZipInfo(member)
                        entry.filename = member  # Keep raw Windows separators in the fixture.
                        z.writestr(entry, b'x')
                with self.assertRaises(ArchiveError):
                    extract_archive(archive, '.zip', root / 'payload')
                self.assertFalse((root / 'outside.txt').exists())
        for attrs in ((stat.S_IFLNK | 0o777) << 16, 0x400):
            with tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                with ZipFile(root / 'links.zip', 'w') as z:
                    z.writestr('manifest.xml', '<Manifest><ID>Safe</ID></Manifest>')
                    link = ZipInfo('linked')
                    link.external_attr = attrs
                    z.writestr(link, 'target')
                with self.assertRaises(ArchiveError):
                    extract_archive(root / 'links.zip', '.zip', root / 'stage')

    def test_destination_hardlink_manifest_is_not_followed(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            mods_root = root / 'Mods'
            (mods_root / 'DifferentFolder').mkdir(parents=True)
            manifest = root / 'outside.xml'
            manifest.write_text('<Manifest><ID>Hidden</ID></Manifest>')
            os.link(manifest, mods_root / 'DifferentFolder/manifest.xml')
            with self.assertRaises(ArchiveError):
                plan_install(GamePaths(local_mods_dir=mods_root), ())

    def test_second_publish_failure_rolls_back_first_without_touching_existing(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            existing = root / 'Mods' / 'Keep'
            existing.mkdir(parents=True)
            (existing / 'keep.txt').write_bytes(b'old')
            archive = root / 'multi.zip'
            with ZipFile(archive, 'w') as z:
                for mod in ('One', 'Two'):
                    z.writestr(f'{mod}/manifest.xml', f'<Manifest><ID>{mod}</ID></Manifest>')
            mods = extract_archive(archive, '.zip', root / 'payload')
            paths = GamePaths(local_mods_dir=root / 'Mods')
            plan = plan_install(paths, mods)
            rename = installer._rename_new

            def fail_second(source, target):
                if target == root / 'Mods' / 'Two':
                    raise OSError('Simulated sharing failure')
                return rename(source, target)

            with patch.object(installer, '_rename_new', side_effect=fail_second):
                with self.assertRaises(OSError):
                    publish(paths, mods, plan, game_running=lambda: False)
            self.assertEqual((existing / 'keep.txt').read_bytes(), b'old')
            self.assertFalse((root / 'Mods/One').exists())
            self.assertFalse((root / 'Mods/Two').exists())
