export type Matrix=(string|null)[][];
export type Point={x:number;y:number};
export const key=(x:number,y:number)=>`${x},${y}`;
export function world(p:Point,zoom:number,pan:Point):Point{return {x:(p.x-pan.x)/zoom,y:(p.y-pan.y)/zoom}}
export function cells(points:Point[],kind:'rectangle'|'lasso',width:number,height:number,cell:number):string[]{
 if(points.length<2)return [];const xs=points.map(p=>p.x),ys=points.map(p=>p.y),out:string[]=[];
 for(let y=0;y<height;y++)for(let x=0;x<width;x++){
  const px=(x+.5)*cell,py=(y+.5)*cell;let inside=false;
  if(kind==='rectangle')inside=px>=Math.min(...xs)&&px<=Math.max(...xs)&&py>=Math.min(...ys)&&py<=Math.max(...ys);
  else for(let i=0,j=points.length-1;i<points.length;j=i++){const a=points[i],b=points[j];if((a.y>py)!==(b.y>py)&&px<(b.x-a.x)*(py-a.y)/(b.y-a.y)+a.x)inside=!inside;}
  if(inside)out.push(key(x,y));
 }return out;
}
export function applySelection(m:Matrix,selection:string[],color:string|null,replace?:string):Matrix{const selected=new Set(selection);return m.map((row,y)=>row.map((c,x)=>selected.has(key(x,y))&&(replace===undefined||c===replace)?color:c));}
export function flood(m:Matrix,x:number,y:number,color:string|null,selection:string[]):Matrix{
 const out=m.map(r=>[...r]),allow=new Set(selection);if(!out[y]||x<0||x>=out[y].length||allow.size&&!allow.has(key(x,y)))return out;
 const old=out[y][x];if(old===color)return out;const stack=[[x,y]];out[y][x]=color;
 while(stack.length){const[a,b]=stack.pop()!;for(const[c,d]of[[a-1,b],[a+1,b],[a,b-1],[a,b+1]])if(d>=0&&d<out.length&&c>=0&&c<out[0].length&&out[d][c]===old&&(!allow.size||allow.has(key(c,d)))){out[d][c]=color;stack.push([c,d])}}return out;
}
