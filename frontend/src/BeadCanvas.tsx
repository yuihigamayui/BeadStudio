import {useState,useRef,useEffect} from 'react';
import {Stage,Layer,Rect,Text,Line} from 'react-konva';
import {Matrix,Point,world,cells,key,applySelection,flood} from './selection';
import type {Color} from './api';
type Snapshot={m:Matrix;selection:string[]};
type Preview = {matrix: Matrix; description: string};
export function BeadCanvas({matrix,palette,onChange,resetKey}:{matrix:Matrix;palette:Color[];onChange:(m:Matrix)=>void;resetKey:string}){
 const[selection,select]=useState<string[]>([]),[tool,T]=useState('画笔'),[color,C]=useState('A1'),[zoom,Z]=useState(1),[pan,P]=useState<Point>({x:0,y:0}),[grid,G]=useState(true),[labels,L]=useState(false),[path,Q]=useState<Point[]>([]),[add,A]=useState(false),[preview,V]=useState<Preview|null>(null),[past,H]=useState<Snapshot[]>([]),[future,F]=useState<Snapshot[]>([]);
 const drag=useRef<{start:Point;pan:Point;path:Point[];before:Snapshot;working:Matrix;selection:string[]}|null>(null);
 const [viewMode,setViewMode]=useState(true);
 const [replaceFrom, setReplaceFrom] = useState('');
 const [replaceWith, setReplaceWith] = useState('');
 const viewport=useRef<HTMLDivElement>(null);
 const [canvasWidth,setCanvasWidth]=useState(640);
 const cell=16,m=preview?.matrix||matrix,canvasHeight=Math.min(canvasWidth,510);
 const boardWidth=(m[0]?.length||1)*cell,boardHeight=(m.length||1)*cell;
 const fit=Math.min((canvasWidth-32)/boardWidth,(canvasHeight-32)/boardHeight,2);
 const displayZoom=fit*zoom;
 const origin={x:(canvasWidth-boardWidth*displayZoom)/2+pan.x,y:(canvasHeight-boardHeight*displayZoom)/2+pan.y};
 useEffect(()=>{
   const element=viewport.current;
   if(!element)return;
   const resize=new ResizeObserver(([entry])=>setCanvasWidth(Math.max(40,Math.floor(entry.contentRect.width))));
   resize.observe(element);
   return ()=>resize.disconnect();
 },[]);

 useEffect(()=>{
   drag.current = null;
   Q([]);
   select([]);
   H([]);
   F([]);
   V(null);
   setReplaceFrom('');
   setReplaceWith('');
   P({x:0,y:0});
   Z(1);
   setViewMode(true);
 },[resetKey]);
 function commit(next:Matrix,sel=selection,before:Snapshot={m:matrix,selection}){if(JSON.stringify(next)===JSON.stringify(before.m)&&JSON.stringify(sel)===JSON.stringify(before.selection))return;H(p=>[...p,before]);F([]);onChange(next);select(sel);V(null)}
 function restore(back:boolean){const from=back?past:future;if(!from.length)return;const v=from[from.length-1],current={m:matrix,selection};if(back){H(from.slice(0,-1));F(p=>[...p,current])}else{F(from.slice(0,-1));H(p=>[...p,current])}onChange(v.m);select(v.selection);V(null)}
 function position(e:any){return e.target.getStage().getPointerPosition() as Point}
 function paint(work:Matrix,p:Point,sel:string[]){const x=Math.floor(p.x/cell),y=Math.floor(p.y/cell);if(work[y]?.[x]===undefined)return work;if(tool==='取色'){if(work[y][x])C(work[y][x]!);return work}if(tool==='连通填充')return flood(work,x,y,color,sel);if(sel.length&&!sel.includes(key(x,y)))return work;const out=work.map(r=>[...r]);out[y][x]=tool==='橡皮擦'?null:color;return out}
 function finish(){const d=drag.current;if(!d)return;drag.current=null;Q([]);if(viewMode)return;if(tool==='矩形选区'||tool==='自由套索'){const chosen=cells(tool==='矩形选区'?[d.path[0],d.path.at(-1)!]:d.path,tool==='矩形选区'?'rectangle':'lasso',matrix[0]?.length||0,matrix.length,cell);commit(matrix,add?[...new Set([...selection,...chosen])]:chosen,d.before)}else if(tool!=='取色')commit(d.working,selection,d.before)}
 const lookup=Object.fromEntries(palette.map(p=>[p.id,p.hex]));
 const selected = new Set(selection);
 const counts = new Map<string, number>();
 matrix.forEach((row, y) => row.forEach((code, x) => {
   if (code && (!selected.size || selected.has(key(x, y)))) {
     counts.set(code, (counts.get(code) || 0) + 1);
   }
 }));
 const from = counts.has(replaceFrom) ? replaceFrom : '';
 const count = counts.get(from) || 0;
 const scope = selection.length ? '选区内' : '整张图纸';
 const canReplace = !!from && !!replaceWith && from !== replaceWith && !viewMode && !preview;

 function previewReplace() {
   if (!canReplace) return;
   const scopeCells = selection.length ? selection : matrix.flatMap((row, y) => row.map((_, x) => key(x, y)));
   V({
     matrix: applySelection(matrix, scopeCells, replaceWith, from),
     description: `${scope} ${count} 颗 · ${from} → ${replaceWith}`,
   });
 }
 function toggleEditMode() {
   finish();
   V(null);
   setViewMode(!viewMode);
 }

 return <>
 <div className="toolbar canvas-view-tools">
   <button type="button" className="canvas-mode-button" aria-expanded={!viewMode}
     aria-controls="canvas-edit-tools canvas-edit-actions" onClick={toggleEditMode}>
     {viewMode ? '编辑' : '完成编辑'}
   </button>
   <label className="inline-field">缩放 {Math.round(zoom * 100)}%
     <input type="range" min="0.3" max="3" step="0.1" value={zoom} onChange={event => Z(+event.target.value)} />
   </label>
   <button onClick={()=>{Z(1);P({x:0,y:0})}}>复位</button>
   <label className="inline-field"><input type="checkbox" checked={grid} onChange={event => G(event.target.checked)} />网格</label>
   <label className="inline-field"><input type="checkbox" checked={labels} onChange={event => L(event.target.checked)} />色号</label>
   <button onClick={()=>{G(false);L(false)}}>效果预览</button>
 </div>
 {!viewMode && <div className="toolbar canvas-edit-tools" id="canvas-edit-tools">
   {['画笔','橡皮擦','取色','连通填充','矩形选区','自由套索'].map(name =>
     <button className={tool === name ? 'active' : ''} key={name} onClick={() => T(name)}>{name}</button>)}
   <button disabled={!past.length || !!preview} onClick={() => restore(true)}>撤销</button>
   <button disabled={!future.length || !!preview} onClick={() => restore(false)}>重做</button>
   <label><input type="checkbox" checked={add} onChange={event => A(event.target.checked)} />累加选区</label>
   <button disabled={!selection.length || !!preview} onClick={() => commit(matrix, [])}>取消选区</button>
 </div>}
 <div className="canvas board-canvas" ref={viewport} style={{cursor:viewMode?'grab':'crosshair'}}><Stage width={canvasWidth} height={canvasHeight} onWheel={e=>{e.evt.preventDefault();if(drag.current)return;Z(z=>Math.max(.3,Math.min(3,z*(e.evt.deltaY<0?1.1:1/1.1))))}} onPointerDown={e=>{if((preview&&!viewMode)||!matrix.length)return;const p=position(e),w=world(p,displayZoom,origin);drag.current={start:p,pan,path:[w],before:{m:matrix,selection},working:matrix,selection};if(viewMode)return;if(tool==='矩形选区'||tool==='自由套索')Q([w]);else{drag.current.working=paint(matrix,w,selection);onChange(drag.current.working)}}} onPointerMove={e=>{const d=drag.current;if(!d)return;const p=position(e),w=world(p,displayZoom,origin);if(viewMode){P({x:d.pan.x+p.x-d.start.x,y:d.pan.y+p.y-d.start.y});return}if(tool==='矩形选区'||tool==='自由套索'){d.path=tool==='矩形选区'?[d.path[0],w]:[...d.path,w];Q([...d.path])}else if(tool==='画笔'||tool==='橡皮擦'){d.working=paint(d.working,w,d.selection);onChange(d.working)}}} onPointerUp={finish} onPointerLeave={finish} onPointerCancel={finish}><Layer x={origin.x} y={origin.y} scaleX={displayZoom} scaleY={displayZoom}>{m.map((row,y)=>row.map((c,x)=><Rect key={key(x,y)} x={x*cell} y={y*cell} width={cell} height={cell} fill={c?lookup[c]:'transparent'} stroke={grid?'#66578080':undefined} strokeWidth={.4}/>))}{labels&&m.map((row,y)=>row.map((c,x)=><Text key={key(x,y)} x={x*cell} y={y*cell+5} width={cell} fontSize={6} align="center" text={c||'·'} listening={false}/>))}{!viewMode&&selection.map(k=>{const[x,y]=k.split(',').map(Number);return <Rect key={k} x={x*cell} y={y*cell} width={cell} height={cell} fill="rgba(35,120,255,.3)" stroke="#2578ff" strokeWidth={.7} listening={false}/>})}{path.length>1&&(tool==='矩形选区'?<Rect x={Math.min(path[0].x,path.at(-1)!.x)} y={Math.min(path[0].y,path.at(-1)!.y)} width={Math.abs(path[0].x-path.at(-1)!.x)} height={Math.abs(path[0].y-path.at(-1)!.y)} stroke="#2578ff" listening={false}/>:<Line points={path.flatMap(p=>[p.x,p.y])} closed stroke="#2578ff" fill="rgba(35,120,255,.15)" listening={false}/>)}</Layer></Stage></div>
 {!viewMode && <div id="canvas-edit-actions">
 <details className="editor-colors"><summary><i className="swatch" style={{background:lookup[color]}}/> 画笔色号 {color} · 已选 {selection.length} 格</summary>
   <div className="palette">{palette.map(p=><button key={p.id} title={p.id+' '+p.name} aria-label={p.id+' '+p.name}
     aria-pressed={color===p.id} style={{background:p.hex}} onClick={()=>C(p.id)}/>)}</div>
 </details>
 {selection.length > 0 && <div className="toolbar">
   <button disabled={!!preview} onClick={() => V({
     matrix: applySelection(matrix, selection, color),
     description: `用 ${color} 填色 · ${selection.length} 格`,
   })}>预览选区填色</button>
   <button disabled={!!preview} onClick={() => V({
     matrix: applySelection(matrix, selection, null),
     description: `清空选区 · ${selection.length} 格`,
   })}>预览清空选区</button>
 </div>}
 <details className="editor-batch">
   <summary>批量换色</summary>
   <div className="batch-fields">
     <label>要换掉的色号
       <select value={from} disabled={!!preview}
         onChange={event => setReplaceFrom(event.target.value)}>
         <option value="">选择原色号</option>
         {palette.filter(entry => counts.has(entry.id)).map(entry =>
           <option key={entry.id} value={entry.id}>{entry.id} · {counts.get(entry.id)} 颗</option>)}
       </select>
     </label>
     <label>换成的色号
       <select value={replaceWith} disabled={!!preview}
         onChange={event => setReplaceWith(event.target.value)}>
         <option value="">选择新色号</option>
         {palette.map(entry => <option key={entry.id} value={entry.id}>{entry.id} · {entry.name}</option>)}
       </select>
     </label>
   </div>
   <div className="toolbar batch-summary">
     <span>{from && replaceWith && from === replaceWith
       ? '两个色号相同，请选一个不同的新色号。'
       : from ? `${scope}有 ${count} 颗 ${from}` : `替换范围 · ${scope}`}</span>
     <button disabled={!canReplace} onClick={previewReplace}>预览换色</button>
   </div>
 </details>
 {preview && <div className="toolbar edit-preview">
   <strong>预览中 · {preview.description}</strong>
   <button onClick={() => {commit(preview.matrix); V(null);}}>确认应用</button>
   <button onClick={() => V(null)}>取消预览</button>
 </div>}
 </div>}
 </>
}
