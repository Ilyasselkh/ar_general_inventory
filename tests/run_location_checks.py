import os
import subprocess
import sys
from pathlib import Path
import psycopg2

database = 'codex_ar_locations_20261019'
connection = dict(host=os.environ.get('HOST', 'db'), port=os.environ.get('PORT', '5432'),
                  user=os.environ.get('USER', 'odoo'), password=os.environ.get('PASSWORD', 'odoo'))
db = psycopg2.connect(dbname='postgres', **connection)
db.autocommit = True
try:
    with db.cursor() as cursor:
        if '--cleanup' in sys.argv:
            cursor.execute('DROP DATABASE IF EXISTS codex_ar_locations_20261019 WITH (FORCE)')
            print('Base temporaire supprimée.')
            raise SystemExit(0)
        cursor.execute('SELECT 1 FROM pg_database WHERE datname = %s', [database])
        exists = bool(cursor.fetchone())
        if not exists:
            cursor.execute('CREATE DATABASE codex_ar_locations_20261019')
finally:
    db.close()
config = Path('/tmp/codex_ar_locations.conf')
config.write_text('[options]\naddons_path = /mnt/extra-addons\n' +
                  ''.join('db_%s = %s\n' % (key, value) for key, value in connection.items()))
config.chmod(0o600)
log = '/tmp/codex_ar_locations.log'
result = subprocess.run(['odoo', '-c', str(config), '-d', database,
                         '-u' if exists else '-i', 'ar_general_inventory', '--without-demo', '--http-port=18079',
                         '--test-enable', '--test-tags', '/ar_general_inventory:TestFullFlow',
                         '--stop-after-init', '--no-http', '--max-cron-threads=0', '--logfile', log])
lines = Path(log).read_text().splitlines()
start = next((i for i in range(len(lines) - 1, -1, -1) if 'Starting post tests' in lines[i]), max(0, len(lines) - 70))
print('\n'.join(lines[start:]))
config.unlink(missing_ok=True)
raise SystemExit(result.returncode)
