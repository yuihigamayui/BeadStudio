"""Install the bundled review sample as local, editable versions."""
import json
from pathlib import Path
from shutil import copyfile
from sqlite3 import Connection

from PIL import Image

EXAMPLES = Path(__file__).resolve().parent.parent / 'examples'
SAMPLE_PROJECT = 'sample-anime-avatar'
SAMPLE_PATTERN = 'sample-anime-avatar-pattern'


def seed_review_sample(conn: Connection, data: Path) -> None:
    """Seed one sample while the caller holds the startup transaction."""
    if conn.execute('SELECT 1 FROM versions WHERE id=?', (SAMPLE_PATTERN,)).fetchone():
        return

    reference_id = SAMPLE_PROJECT + '-reference'
    artwork_id = SAMPLE_PROJECT + '-artwork'
    assets = [
        (reference_id, None, 'reference', EXAMPLES / 'anime_avatar.png'),
        (artwork_id, reference_id, 'artwork', EXAMPLES / 'review-sample/artwork.png'),
    ]
    for ident, parent, role, path in assets:
        name = ident + '.png'
        copyfile(path, data / name)
        with Image.open(path) as image:
            payload = {'file': name, 'width': image.width, 'height': image.height, 'role': role}
        conn.execute('INSERT INTO versions VALUES(?,?,?,?,?)',
                     (ident, SAMPLE_PROJECT, 'image', parent, json.dumps(payload)))

    payload = json.loads((EXAMPLES / 'review-sample/pattern.json').read_text(encoding='utf-8'))
    payload.update(source=artwork_id, sample=True)
    conn.execute('INSERT INTO versions VALUES(?,?,?,?,?)',
                 (SAMPLE_PATTERN, SAMPLE_PROJECT, 'pattern', artwork_id, json.dumps(payload)))
    conn.execute('INSERT INTO design_projects VALUES(?,?,?)',
                 (SAMPLE_PROJECT, reference_id, '保留动漫头像和侧马尾，背景透明。'))
