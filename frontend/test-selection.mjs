import assert from 'node:assert/strict';
import {world,cells,applySelection,flood} from './.tests/selection.js';
for(const zoom of [.3,1,1.75,3])for(const pan of [{x:0,y:0},{x:87,y:-34}]){
 const screen=p=>({x:p.x*zoom+pan.x,y:p.y*zoom+pan.y});
 const points=[{x:16,y:16},{x:48,y:48}].map(screen).map(p=>world(p,zoom,pan));
 assert.deepEqual(cells(points,'rectangle',5,5,16),['1,1','2,1','1,2','2,2']);
 const polygon=[{x:16,y:16},{x:48,y:16},{x:48,y:48},{x:16,y:48}].map(screen).map(p=>world(p,zoom,pan));
 assert.deepEqual(cells(polygon,'lasso',5,5,16),['1,1','2,1','1,2','2,2']);
}
const m=[['A','A','B'],['A','A',null],['A','B','B']];
assert.deepEqual(flood(m,0,0,'C',['0,0','1,0']),[['C','C','B'],['A','A',null],['A','B','B']]);
assert.deepEqual(flood(m,2,0,'C',['0,0']),m);
assert.deepEqual(applySelection(m,['1,1','2,1'],null),[['A','A','B'],['A',null,null],['A','B','B']]);
assert.deepEqual(applySelection(m,['0,0','2,0'],'C','A'),[['C','A','B'],['A','A',null],['A','B','B']]);
assert.equal(m[0][0],'A');
console.log('Selection geometry: 16 transformed cases; flood, clear, replace and immutability passed.');
