import io, json, sqlite3, uuid, os
from pathlib import Path
import numpy as np
from PIL import Image, ImageOps, ImageEnhance
from fastapi import FastAPI, UploadFile, HTTPException
from fastapi.responses import Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from backend.palette import load_palette
from backend.pattern_generator import render_pattern, png_bytes, usage_table
from backend.generation import MARD_ALGORITHMS, Strategy, generate
from backend.design_workflow import design_routes

DATA=Path(os.environ.get('BEAD_DATA', Path(__file__).parent.parent/'data'))
DATA.mkdir(parents=True, exist_ok=True)
def db():
    c=sqlite3.connect(DATA/'studio.db')
    c.execute('CREATE TABLE IF NOT EXISTS versions(id TEXT PRIMARY KEY, project TEXT, kind TEXT, parent TEXT, payload TEXT)')
    return c
def put(project,kind,parent,payload):
    ident=uuid.uuid4().hex
    with db() as c: c.execute('INSERT INTO versions VALUES(?,?,?,?,?)',(ident,project,kind,parent,json.dumps(payload)))
    return {'id':ident,'project':project,'kind':kind,'parent':parent,**payload}
def get(ident):
    with db() as c: row=c.execute('SELECT * FROM versions WHERE id=?',(ident,)).fetchone()
    if not row: raise HTTPException(404,'版本不存在')
    return dict(id=row[0],project=row[1],kind=row[2],parent=row[3],**json.loads(row[4]))
app=FastAPI(title='BeadStudio V3 本地 API',version='3.0.0')
@app.get('/api/palette')
def palette(legacy:bool=False): return load_palette(legacy=legacy).to_dict('records')
@app.post('/api/projects')
async def upload(file:UploadFile):
    raw=await file.read(20_000_001)
    if len(raw)>20_000_000: raise HTTPException(413,'图片上限 20MB')
    try:
        im=Image.open(io.BytesIO(raw))
        if im.width*im.height>25_000_000: raise ValueError()
        im=ImageOps.exif_transpose(im).convert('RGBA')
        if im.getchannel('A').getextrema() == (255, 255): im=im.convert('RGB')
    except Exception: raise HTTPException(400,'无效图片或像素过大')
    project=uuid.uuid4().hex
    name=project+'.png'; im.save(DATA/name)
    return put(project,'image',None,{'file':name,'width':im.width,'height':im.height})
@app.get('/api/versions/{ident}')
def version(ident:str): return get(ident)
@app.get('/api/projects')
def projects():
    with db() as c: rows=c.execute('SELECT id FROM versions ORDER BY rowid DESC').fetchall()
    return [get(r[0]) for r in rows]
@app.get('/api/images/{ident}')
def image(ident:str):
    v=get(ident)
    if v['kind']!='image': raise HTTPException(400,'非图片版本')
    return Response((DATA/v['file']).read_bytes(),media_type='image/png')
class Edit(BaseModel):
    brightness:float=Field(1,ge=0,le=3)
    contrast:float=Field(1,ge=0,le=3)
    crop:list[int]|None=None
@app.post('/api/images/{ident}/edit')
def edit(ident:str,body:Edit):
    v=get(ident)
    if v['kind']!='image': raise HTTPException(400,'非图片版本')
    im=Image.open(DATA/v['file'])
    if body.crop:
        if len(body.crop)!=4: raise HTTPException(422,'裁剪框需要四个坐标')
        x,y,r,b=body.crop
        if not 0<=x<r<=im.width or not 0<=y<b<=im.height: raise HTTPException(422,'裁剪框超出图片')
        im=im.crop((x,y,r,b))
    im=ImageEnhance.Contrast(ImageEnhance.Brightness(im).enhance(body.brightness)).enhance(body.contrast)
    name=uuid.uuid4().hex+'.png'; im.save(DATA/name)
    return put(v['project'],'image',ident,{'file':name,'width':im.width,'height':im.height,'operation':body.model_dump()})
class Convert(Strategy):
    pass
@app.post('/api/images/{ident}/convert')
def convert(ident:str,body:Convert):
    v=get(ident)
    if v['kind']!='image': raise HTTPException(400,'非图片版本')
    try:
        with Image.open(DATA/v['file']) as source:
            ids,_,_,metrics=generate(source,body)
    except (ValueError, RuntimeError) as exc:
        raise HTTPException(422,'图纸生成失败：'+str(exc)) from exc
    return put(v['project'],'pattern',ident,{'source':ident,'matrix':ids.tolist(),'strategy':body.model_dump(),'metrics':metrics})
class Save(BaseModel):
    matrix:list[list[str|None]]
@app.post('/api/patterns/{ident}/save')
def save(ident:str,body:Save):
    v=get(ident)
    if v['kind']!='pattern': raise HTTPException(400,'非图纸版本')
    m=body.matrix; old=v['matrix']; valid=set(load_palette(legacy=v.get('strategy',{}).get('algorithm') not in MARD_ALGORITHMS).id)
    if len(m)!=len(old) or any(len(row)!=len(old[0]) for row in m) or any(code is not None and code not in valid for row in m for code in row): raise HTTPException(422,'无效矩阵或色号')
    cap = v.get('palette', {}).get('max_colors')
    if cap is not None and len({code for row in m for code in row if code is not None}) > cap:
        raise HTTPException(422, f'图纸超过本次设定的 {cap} 色上限')
    payload={'source':v['source'],'matrix':m}
    for field in ('run_id', 'board', 'palette'):
        if field in v:
            payload[field] = v[field]
    if 'strategy' in v: payload['strategy']=v['strategy']
    return put(v['project'],'pattern',ident,payload)
@app.get('/api/patterns/{ident}/export/{kind}')
def export(ident:str,kind:str):
    v=get(ident)
    if v['kind']!='pattern': raise HTTPException(400,'非图纸版本')
    palette = load_palette(legacy=v.get('strategy', {}).get('algorithm') not in MARD_ALGORITHMS)
    matrix = np.asarray(v['matrix'], dtype=object)
    if kind == 'materials':
        table = usage_table(matrix, palette).drop(columns='ratio')
        return Response(table.to_csv(index=False).encode('utf-8-sig'), media_type='text/csv')
    if kind == 'numbered':
        image = render_pattern(matrix, palette)
    elif kind == 'png':
        occupied = matrix != None
        rgb = np.full((*matrix.shape, 3), 255, dtype=np.uint8)
        rgb[occupied] = palette.set_index('id').loc[matrix[occupied], ['r', 'g', 'b']].to_numpy()
        rgba = np.dstack([rgb, occupied.astype(np.uint8) * 255])
        height, width = matrix.shape
        image = Image.fromarray(rgba).resize((width * 16, height * 16), Image.Resampling.NEAREST)
    else:
        raise HTTPException(404, '未知导出类型')
    return Response(png_bytes(image), media_type='image/png')
app.include_router(design_routes(db, get, put, lambda: DATA))
DIST=Path(__file__).parent.parent/'frontend/dist'
if DIST.exists(): app.mount('/',StaticFiles(directory=DIST,html=True),name='frontend')
