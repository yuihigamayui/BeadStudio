import type {Strategy} from './api';

export const defaults: Strategy = { algorithm: 'pindou_refine', fit: 'pad', subject_scale: 1 };

export function GenerationControls({ value, onChange }: { value: Strategy; onChange: (value: Strategy) => void }) {
  return <div className="toolbar">
    <label>转换方式
      <select value={value.algorithm} onChange={e => onChange({ ...value, algorithm: e.target.value as Strategy['algorithm'] })}>
        <option value="pindou_refine">平涂底稿（细节优化）</option>
        <option value="pindou">平涂底稿（PinDou）</option>
        <option value="flat">平涂底稿（旧版）</option>
        <option value="V3">照片转换（PIA）</option>
      </select>
    </label>
    <label>构图
      <select value={value.fit} onChange={e => onChange({ ...value, fit: e.target.value as Strategy['fit'] })}>
        <option value="pad">等比留白</option>
        <option value="crop">等比裁剪</option>
      </select>
    </label>
    <label>主体缩放 {value.subject_scale.toFixed(2)}
      <input type="range" min="0.5" max="2" step="0.05" value={value.subject_scale}
        onChange={e => onChange({ ...value, subject_scale: +e.target.value })}/>
    </label>
    <p>{value.algorithm !== 'V3'
      ? '上传透明底 PNG。保留主体的主要色块和边缘，透明区域在图纸上留空。'
      : '用 PIA 将照片简化为色块，再匹配 MARD 社区色卡。放大可能裁去边缘。'}</p>
  </div>;
}
