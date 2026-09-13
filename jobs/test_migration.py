from pathlib import Path
from contextlib import closing
import sqlite3
import unittest


class MigrationTests(unittest.TestCase):
    def test_version_upgrade_preserves_sent_report_and_foreign_keys(self):
        with closing(sqlite3.connect(':memory:')) as db:
            db.execute('PRAGMA foreign_keys = ON')
            for path in sorted(Path('migrations').glob('000[1-5]*.sql')):
                db.executescript(path.read_text(encoding='utf-8'))
            db.execute("INSERT INTO daily_runs VALUES ('test-2026-09-13', '2026-09-13', 'test', '123', 'ready', '{}', '2026-09-13', NULL)")
            db.execute("INSERT INTO reports VALUES ('test-2026-09-13', '{\"title\":\"original\"}', '2026-09-13')")
            db.execute("INSERT INTO deliveries VALUES ('test-2026-09-13', 'smtp_accepted', NULL, '2026-09-13')")
            db.commit()
            db.executescript('BEGIN;\n' + Path('migrations/0006_report_versions.sql').read_text(encoding='utf-8') + '\nCOMMIT;')
            self.assertEqual(db.execute('SELECT version FROM daily_runs').fetchone(), (1,))
            self.assertEqual(db.execute('SELECT result_json FROM reports').fetchone(), ('{"title":"original"}',))
            self.assertEqual(db.execute('SELECT state FROM deliveries').fetchone(), ('smtp_accepted',))
            self.assertEqual(db.execute('PRAGMA foreign_key_check').fetchall(), [])


if __name__ == '__main__':
    unittest.main()
