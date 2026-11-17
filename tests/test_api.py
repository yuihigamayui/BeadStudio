import io
import numpy as np
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
