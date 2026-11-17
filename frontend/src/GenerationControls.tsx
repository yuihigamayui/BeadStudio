export type Strategy={algorithm:'V3';fit:string;subject_scale:number};
export const defaults:Strategy={algorithm:'V3',fit:'pad',subject_scale:1};
export function GenerationControls({value,onChange}:{value:Strategy;onChange:(v:Strategy)=>void}){
 return <div className="toolbar"><strong>V3 · PIA 作者源码语义移植</strong><label>构图<select value={value.fit} onChange={e=>onChange({...value,fit:e.target.value})}><option value="pad">等比留白</option><option value="crop">等比裁剪</option></select></label><label>主体缩放 {value.subject_scale.toFixed(2)}<input type="range" min="0.5" max="2" step="0.05" value={value.subject_scale} onChange={e=>onChange({...value,subject_scale:+e.target.value})}/></label><p>超像素与 MCDA 调色板交替优化，再以 CIEDE2000 匹配 MARD 社区色卡。主体缩放围绕图片中心，大于 1 可能裁去边缘。CPU 生成时请稍候。</p></div>
}
