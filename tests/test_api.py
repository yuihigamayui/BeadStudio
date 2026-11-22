import io
import numpy as np
import pytest
from PIL import Image
from fastapi.testclient import TestClient
import backend.main as m

def test_full_flow(tmp_path,monkeypatch):
 monkeypatch.setattr(m,'DATA',tmp_path)
 c=TestClient(m.app)
 b=io.BytesIO();Image.new('RGBA',(80,60),(255,30,20,180)).save(b,format='PNG')
 v=c.post('/api/projects',files={'file':('test.png',b.getvalue(),'image/png')}).json()
 assert v['parent'] is None
 e=c.post('/api/images/'+v['id']+'/edit',json={'brightness':1.2,'contrast':1.1,'crop':[0,0,40,40]}).json()
 assert e['parent']==v['id'] and e['width']==40
 p=c.post('/api/images/'+e['id']+'/convert',json={'size':32,'colors':8}).json()
 a=np.array(p['matrix']);assert a.shape==(32,32) and len(np.unique(a))<=8
 assert p['strategy']['algorithm']=='V3'
 p['matrix'][0][0]=None;p['matrix'][0][1]='A2'
 s=c.post('/api/patterns/'+p['id']+'/save',json={'matrix':p['matrix']}).json()
 assert s['source']==e['id'] and s['parent']==p['id']
 assert s['strategy']==p['strategy'] and 'metrics' not in s
 assert c.get('/api/versions/'+s['id']).json()['matrix']==p['matrix']
 assert c.get('/api/versions/'+p['id']).json()['matrix'][0][0] is not None
 for k in ['png','numbered','materials']:
  r=c.get('/api/patterns/'+s['id']+'/export/'+k);assert r.status_code==200
  if k!='materials':Image.open(io.BytesIO(r.content)).load()
  else:
   import pandas as pd
   table=pd.read_csv(io.BytesIO(r.content));assert table['count'].sum()==1023
 assert c.post('/api/patterns/'+s['id']+'/save',json={'matrix':[['BAD']]}).status_code==422
 assert c.post('/api/images/'+v['id']+'/edit',json={'crop':[-1,0,3,4]}).status_code==422
 assert c.get('/api/images/'+v['id']).status_code==200
 assert len(c.get('/api/projects').json())==4

def test_invalid(tmp_path,monkeypatch):
 monkeypatch.setattr(m,'DATA',tmp_path);c=TestClient(m.app)
 assert c.post('/api/projects',files={'file':('x',b'bad')}).status_code==400
 assert c.get('/api/versions/missing').status_code==404

def test_legacy_palette_keeps_its_colors(tmp_path,monkeypatch):
 monkeypatch.setattr(m,'DATA',tmp_path);c=TestClient(m.app)
 old=m.put('legacy','pattern','old-source',{'source':'old-source','matrix':[['D15',None]]})
 assert c.post('/api/patterns/'+old['id']+'/save',json={'matrix':[['D15',None]]}).status_code==200
 legacy={p['id']:p for p in c.get('/api/palette?legacy=true').json()}
 current={p['id']:p for p in c.get('/api/palette').json()}
 assert legacy['D15']['hex']!=current['D15']['hex']
 result=c.get('/api/patterns/'+old['id']+'/export/png')
 image=Image.open(io.BytesIO(result.content)).convert('RGBA')
 color=legacy['D15']; assert image.getpixel((0,0))==(color['r'],color['g'],color['b'],255)
 assert image.getpixel((16,0))[3]==0


@pytest.mark.parametrize('algorithm,color_limit', [('flat', None), ('pindou', None), ('pindou', 2), ('pindou_refine', None), ('pindou_refine', 2)])
def test_flat_conversion_keeps_empty_cells_and_opaque_white(tmp_path, monkeypatch, algorithm, color_limit):
    import csv
    from collections import Counter

    monkeypatch.setattr(m, 'DATA', tmp_path)
    client = TestClient(m.app)
    draft = Image.new('RGBA', (48, 48))
    draft.paste((255, 255, 255, 255), (8, 8, 24, 40))
    draft.paste((0, 39, 99, 255), (30, 22, 42, 40))
    buffer = io.BytesIO()
    draft.save(buffer, 'PNG')
    uploaded = client.post('/api/projects', files={'file': ('draft.png', buffer.getvalue(), 'image/png')})
    assert uploaded.status_code == 200
    version = uploaded.json()
    converted = client.post(
        f"/api/images/{version['id']}/convert", json={'algorithm': algorithm, 'size': 48, 'colors': color_limit}
    )
    assert converted.status_code == 200
    pattern = converted.json()
    assert pattern['strategy']['algorithm'] == algorithm
    matrix = pattern['matrix']
    assert len(matrix) == 48 and all(len(row) == 48 for row in matrix)
    assert matrix[0][0] is None and matrix[10][10] is not None
    counts = Counter(code for row in matrix for code in row if code is not None)
    assert len(counts) == 2
    assert sum(counts.values()) == 16 * 32 + 12 * 18 == pattern['metrics']['beads']

    saved = client.post(f"/api/patterns/{pattern['id']}/save", json={'matrix': matrix})
    assert saved.status_code == 200
    export = f"/api/patterns/{saved.json()['id']}/export"
    legend = client.get(f'{export}/materials')
    rows = csv.DictReader(io.StringIO(legend.content.decode('utf-8-sig')))
    assert {row['id']: int(row['count']) for row in rows} == counts
    pixels = Image.open(io.BytesIO(client.get(f'{export}/png').content)).convert('RGBA')
    assert pixels.getpixel((0, 0))[3] == 0
    assert min(pixels.getpixel((10 * 16, 10 * 16))) >= 250
    numbered = client.get(f'{export}/numbered')
    assert numbered.status_code == 200
    Image.open(io.BytesIO(numbered.content)).verify()


@pytest.mark.parametrize('white_patch', [True, False])
def test_detail_refine_recovers_small_highlight_from_pixels(tmp_path, monkeypatch, white_patch):
    from PIL import ImageDraw

    monkeypatch.setattr(m, 'DATA', tmp_path)
    client = TestClient(m.app)
    draft = Image.new('RGBA', (600, 600))
    draw = ImageDraw.Draw(draft)
    draw.ellipse((135, 145, 266, 274), fill=(191, 103, 41, 255))
    draw.ellipse((146, 156, 255, 263), fill=(25, 18, 16, 255))
    draw.ellipse((202, 193, 216, 207), fill=(252, 252, 252, 255))
    if white_patch:
        draw.rectangle((350, 240, 477, 370), fill=(252, 252, 252, 255))
    draw.rectangle((330, 100, 500, 180), fill=(38, 108, 133, 255))
    buffer = io.BytesIO()
    draft.save(buffer, 'PNG')
    source = client.post('/api/projects', files={'file': ('icon.png', buffer.getvalue(), 'image/png')})
    assert source.status_code == 200
    convert = f"/api/images/{source.json()['id']}/convert"
    patterns = {}
    for algorithm in ('pindou', 'pindou_refine'):
        converted = client.post(convert, json={'algorithm': algorithm, 'size': 30, 'colors': 12})
        assert converted.status_code == 200
        patterns[algorithm] = converted.json()

    baseline = np.asarray(patterns['pindou']['matrix'], dtype=object)
    result = np.asarray(patterns['pindou_refine']['matrix'], dtype=object)
    assert baseline[10, 10] == 'H16' and result[10, 10] == 'H2'
    assert np.array_equal(np.not_equal(baseline, None), np.not_equal(result, None))
    assert ('H2' in set(baseline.flat)) == white_patch
    assert set(result.flat) - set(baseline.flat) == (set() if white_patch else {'H2'})
    if not white_patch:
        at_limit = client.post(convert, json={'algorithm': 'pindou_refine', 'size': 30, 'colors': 3})
        assert at_limit.status_code == 200
        assert at_limit.json()['metrics']['colors'] == 3
        assert at_limit.json()['matrix'][10][10] == 'H16'
    assert patterns['pindou_refine']['metrics']['refine_rings'] == 1
