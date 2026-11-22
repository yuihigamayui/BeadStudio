import {useEffect, useRef, useState} from 'react';
import {Stage, Layer, Rect, Image as KImage} from 'react-konva';
import {GenerationControls, defaults} from './GenerationControls';
import type {Version, Strategy} from './api';

type Crop = [number, number, number, number];
type Point = [number, number];

export function SourceSettings({
  image, pattern, busy, onEdit, onConvert,
}: {
  image: Version;
  pattern?: Version;
  busy: boolean;
  onEdit: (settings: {brightness: number; contrast: number; crop?: number[]}) => void;
  onConvert: (strategy: Strategy & {size: number; colors: number | null}) => void;
}) {
  const viewport = useRef<HTMLDivElement>(null);
  const [width, setWidth] = useState(0);
  const [bitmap, setBitmap] = useState<HTMLImageElement>();
  const [brightness, setBrightness] = useState(1);
  const [contrast, setContrast] = useState(1);
  const [crop, setCrop] = useState<Crop>();
  const [start, setStart] = useState<Point>();
  const [strategy, setStrategy] = useState<Strategy>(defaults);
  const [size, setSize] = useState(pattern?.matrix?.length || 48);
  const [colors, setColors] = useState(12);
  const [unlimited, setUnlimited] = useState(false);

  useEffect(() => {
    if (!image) return;
    const next = new Image();
    next.onload = () => setBitmap(next);
    next.src = '/api/images/' + image.id;
    return () => { next.onload = null; };
  }, [image?.id]);

  useEffect(() => {
    const element = viewport.current;
    if (!element) return;
    const resize = new ResizeObserver(([entry]) => setWidth(Math.floor(entry.contentRect.width)));
    resize.observe(element);
    return () => resize.disconnect();
  }, []);

  const limited = strategy.algorithm === 'V3' || !unlimited;
  const canConvert = !!image && !busy && size >= 10 && size <= 100 && (!limited || (colors >= 2 && colors <= 32));
  const scale = bitmap && width ? Math.min(width / bitmap.width, 400 / bitmap.height, 1) : 1;
  return <div className="source-settings">
      <p className="mode-note">调整会应用到当前用于转图的图片。</p>
      <div className="toolbar source-tools">
        <label>亮度 {brightness.toFixed(2)}<input type="range" min="0" max="3" step="0.05" value={brightness}
          onChange={event => setBrightness(+event.target.value)} /></label>
        <label>对比度 {contrast.toFixed(2)}<input type="range" min="0" max="3" step="0.05" value={contrast}
          onChange={event => setContrast(+event.target.value)} /></label>
        <button disabled={!image || busy} onClick={() => onEdit({brightness, contrast})}>应用调整</button>
        <button disabled={!crop || busy}
          onClick={() => crop && onEdit({brightness, contrast, crop: crop.map(value => Math.round(value / scale))})}>裁剪选区</button>
        <button disabled={!crop} onClick={() => setCrop(undefined)}>清除选区</button>
      </div>
      <div className="source-canvas-scroll" ref={viewport}>
      {bitmap && width > 0 &&
        <div className="canvas" style={{filter: `brightness(${brightness}) contrast(${contrast})`}}>
          <Stage width={bitmap.width * scale} height={bitmap.height * scale}
            onMouseDown={event => {
              const point = event.target.getStage()?.getPointerPosition();
              if (point) setStart([point.x, point.y]);
              setCrop(undefined);
            }}
            onMouseMove={event => {
              const point = event.target.getStage()?.getPointerPosition();
              if (start && point) {
                setCrop([Math.min(start[0], point.x), Math.min(start[1], point.y),
                  Math.max(start[0], point.x), Math.max(start[1], point.y)]);
              }
            }}
            onMouseUp={() => setStart(undefined)} onMouseLeave={() => setStart(undefined)}>
            <Layer>
              <KImage image={bitmap} width={bitmap.width * scale} height={bitmap.height * scale} />
              {crop && <Rect x={crop[0]} y={crop[1]} width={crop[2] - crop[0]} height={crop[3] - crop[1]}
                stroke="#5c4dd9" dash={[5, 3]} listening={false} />}
            </Layer>
          </Stage>
        </div>}
      </div>
      <GenerationControls value={strategy} onChange={setStrategy} />
      <div className="toolbar">
        <label>方板边长 <input type="number" min={10} max={100} value={size}
          onChange={event => setSize(+event.target.value)} /></label>
        {strategy.algorithm !== 'V3' && <label className="inline-field"><input type="checkbox" checked={unlimited}
          onChange={event => setUnlimited(event.target.checked)} />不限色</label>}
        <label>颜色上限 <input type="number" min={2} max={32} value={colors}
          disabled={strategy.algorithm !== 'V3' && unlimited} onChange={event => setColors(+event.target.value)} /></label>
        <button disabled={!canConvert}
          onClick={() => onConvert({...strategy, size, colors: limited ? colors : null})}>生成新图纸</button>
      </div>
      {pattern && image?.id !== pattern.source && <p className="warning">当前图片已更新，重新转换将创建新图纸。</p>}
  </div>;
}

export function sourceChoices(image: Version, versions: Version[]) {
  const ancestors = new Map(versions.map(version => [version.id, version]));
  const choices: {version: Version; label: string}[] = [];
  let current: Version | undefined = image;
  while (current) {
    if (current.role === 'artwork') {
      if (!choices.length) choices.unshift({version: current, label: 'AI 底稿'});
    } else if (current.role === 'reference' || current.parent === null) {
      choices.unshift({version: current, label: '上传原图'});
      break;
    }
    current = current.parent ? ancestors.get(current.parent) : undefined;
  }
  if (!choices.some(choice => choice.version.id === image.id)) {
    choices.push({version: image, label: '当前底稿'});
  }
  return choices;
}
