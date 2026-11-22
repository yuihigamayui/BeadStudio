import {FormEvent, useEffect, useState} from 'react';
import {api} from './api';
import type {Review, Run} from './api';

type Mode = 'design_then_convert' | 'convert_only';
const sampleImage = '/tabby-example.png';
const boardSizes = [29, 48, 58, 100];

const progress: Record<string, string> = {
  analysis: '正在理解输入，确定关键特征…',
  artwork: '正在绘制设计底稿…',
  conversion: '正在转换成拼豆图纸…',
  review: '图纸已生成，正在打开审图…',
};

export function DesignFlow({onReady}: {onReady: (review: Review) => Promise<void>}) {
  const [mode, setMode] = useState<Mode>('design_then_convert');
  const [file, setFile] = useState<File>();
  const [exampleSelected, setExampleSelected] = useState(false);
  const [preview, setPreview] = useState('');
  const [dragging, setDragging] = useState(false);
  const [sampling, setSampling] = useState(false);
  const [intent, setIntent] = useState('');
  const [size, setSize] = useState(48);
  const [colors, setColors] = useState(16);
  const [unlimited, setUnlimited] = useState(false);
  const [configured, setConfigured] = useState<boolean>();
  const [runId, setRunId] = useState(() => sessionStorage.getItem('beadstudio.run'));
  const [run, setRun] = useState<Run>();
  const [answer, setAnswer] = useState('');
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState('');
  const running = !!runId && (!run || run.status === 'queued' || run.status === 'running' || run.status === 'ready');
  const locked = submitting || sampling || running || run?.status === 'needs_input';

  useEffect(() => {
    if (!file) {
      setPreview('');
      return;
    }
    const url = URL.createObjectURL(file);
    setPreview(url);
    return () => URL.revokeObjectURL(url);
  }, [file]);

  useEffect(() => {
    api<{model_configured: boolean}>('/v1/capabilities')
      .then(value => setConfigured(value.model_configured))
      .catch(() => {});
  }, []);

  useEffect(() => {
    if (!runId || run?.status === 'needs_input' || run?.status === 'failed' || run?.status === 'ready') return;
    let timer: number | undefined;
    let canceled = false;

    async function poll() {
      try {
        const current = await api<Run>('/v1/runs/' + runId);
        if (canceled) return;
        setRun(current);
        if (current.status === 'queued' || current.status === 'running') {
          timer = window.setTimeout(poll, 900);
        }
        if (current.status === 'failed') sessionStorage.removeItem('beadstudio.run');
      } catch {
        if (!canceled) {
          sessionStorage.removeItem('beadstudio.run');
          setRunId(null);
          setError('暂时无法查询生成任务。');
        }
      }
    }

    void poll();
    return () => {
      canceled = true;
      window.clearTimeout(timer);
    };
  }, [runId, run?.status]);

  useEffect(() => {
    if (run?.status !== 'ready' || !run.pattern_version_id) return;
    const review = {pattern_version_id: run.pattern_version_id, issues: run.issues || []};
    onReady(review).then(() => sessionStorage.removeItem('beadstudio.run')).catch(() => {
      sessionStorage.removeItem('beadstudio.run');
      setRunId(null);
      setError('图纸已生成，可以在审图页的项目与版本中打开。');
    });
  }, [run?.status, run?.pattern_version_id]);

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setSubmitting(true);
    setError('');
    setRun(undefined);
    try {
      const body = new FormData();
      if (file) body.set('image', file);
      if (mode === 'design_then_convert' && intent.trim()) body.set('intent', intent.trim());
      const project = await api<{id: string; image_id: string | null}>('/v1/projects', body);
      const current = await api<Run>('/v1/projects/' + project.id + '/runs', {
        mode,
        board: {cols: size, rows: size, margin_cells: 1},
        palette: {id: 'mard', max_colors: unlimited ? null : colors},
        artwork_id: mode === 'convert_only' ? project.image_id : null,
      });
      sessionStorage.setItem('beadstudio.run', current.id);
      setRun(current);
      setRunId(current.id);
    } catch (cause) {
      setError((cause as Error).message);
    } finally {
      setSubmitting(false);
    }
  }

  async function clarify(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!run || !answer.trim()) return;
    setSubmitting(true);
    setError('');
    try {
      const current = await api<Run>('/v1/runs/' + run.id + '/answers', {
        question_id: run.question_id, answer: answer.trim(),
      });
      setAnswer('');
      setRun(current);
    } catch (cause) {
      setError((cause as Error).message);
    } finally {
      setSubmitting(false);
    }
  }

  function chooseFile(next?: File, example = false) {
    if (!next) return;
    if (next.size > 20_000_000) {
      setError('图片超过 20 MB，请压缩后重试。');
      return;
    }
    if (!['image/png', 'image/jpeg', 'image/webp'].includes(next.type)) {
      setError('请选择 PNG、JPG 或 WebP 图片。');
      return;
    }
    setError('');
    setFile(next);
    setExampleSelected(example);
  }

  async function selectExample() {
    setSampling(true);
    setError('');
    try {
      const response = await fetch(sampleImage);
      if (!response.ok) throw new Error();
      chooseFile(new File([await response.blob()], '虎斑猫示例.png', {type: 'image/png'}), true);
      setMode('convert_only');
      setSize(29);
    } catch {
      setError('示例图片加载失败，请重试。');
    } finally {
      setSampling(false);
    }
  }

  return <main className="design-main">
    <div className="page-heading">
      <h1>新建拼豆图纸<span aria-hidden="true" className="heading-period">.</span></h1>
      <p>上传图片或描述想拼的图案，选好规格就可以开始。</p>
    </div>

    <div className="design-layout">
      <section className="design-card pixel-panel" aria-label="新建设置">
        <div className="mode-switch" aria-label="生成方式">
          <button type="button" aria-pressed={mode === 'design_then_convert'} disabled={locked}
            onClick={() => setMode('design_then_convert')}>
            <span className="pixel-icon icon-spark" aria-hidden="true" />AI 设计
          </button>
          <button type="button" aria-pressed={mode === 'convert_only'} disabled={locked}
            onClick={() => setMode('convert_only')}>
            <span className="pixel-icon icon-grid" aria-hidden="true" />底稿直转
          </button>
        </div>
        <form className="design-form" onSubmit={submit}>
          <div className="source-field">
            <label className="field-label" htmlFor="source-file">{mode === 'convert_only' ? '上传底稿' : '参考图片'}
              {mode === 'design_then_convert' && <small>可选</small>}
            </label>
            <label className={`upload-zone${dragging ? ' is-dragging' : ''}${locked ? ' is-locked' : ''}`}
              onDragOver={event => {
                event.preventDefault();
                if (!locked) setDragging(true);
              }}
              onDragLeave={() => setDragging(false)}
              onDrop={event => {
                event.preventDefault();
                setDragging(false);
                if (!locked) chooseFile(event.dataTransfer.files[0]);
              }}>
              <input id="source-file" type="file" accept="image/png,image/jpeg,image/webp" disabled={locked}
                onChange={event => {
                  chooseFile(event.target.files?.[0]);
                  event.target.value = '';
                }} />
              <span className="upload-icon" aria-hidden="true"><span className="pixel-icon icon-upload" /></span>
              <span className="upload-copy">
                <strong>{file?.name || '点击上传，或把图片拖到这里'}</strong>
                <small>{file ? '点击或拖入图片以更换' : 'PNG、JPG、WebP · 最大 20 MB'}</small>
              </span>
            </label>
            {file && <button type="button" className="text-button remove-file" disabled={locked}
              onClick={() => setFile(undefined)}>移除图片</button>}
          </div>
          {mode === 'design_then_convert' &&
            <label className="stacked-field">想拼什么
              <textarea rows={2} maxLength={2000} value={intent} disabled={locked}
                placeholder="例如：只保留猫头，突出黄绿色的眼睛。"
                onChange={event => setIntent(event.target.value)} />
            </label>}

          <div className="design-options">
            <div className="board-field">
              <label className="field-label" htmlFor="board-side">方板边长<small>单位：格</small></label>
              <div className="board-sizes" aria-label="常用尺寸">
                {boardSizes.map(value => <button type="button" key={value} disabled={locked}
                  aria-pressed={size === value} onClick={() => setSize(value)}>{value}<span>×{value}</span></button>)}
              </div>
              <label className="inline-field" htmlFor="board-side">
                自定义<input id="board-side" type="number" min={10} max={100} required value={size || ''} disabled={locked}
                  onChange={event => setSize(+event.target.value)} />
              </label>
            </div>
            <div className="color-field">
              <label className="stacked-field">MARD 色数上限
                <input type="number" min={2} max={32} required={!unlimited} value={colors || ''} disabled={locked || unlimited}
                  onChange={event => setColors(+event.target.value)} />
              </label>
              <label className="inline-field"><input type="checkbox" checked={unlimited} disabled={locked}
                onChange={event => setUnlimited(event.target.checked)} />不限色</label>
            </div>
          </div>

          {configured === false && mode === 'design_then_convert' &&
            <p className="mode-note">AI 服务尚未配置。可以先用<strong>底稿直转</strong>。</p>}
          <div className="submit-row">
            <button className="primary-button design-submit" type="submit" disabled={locked || (mode === 'convert_only'
              ? !file : configured === false || (!file && !intent.trim()))}>
              <span className="pixel-icon icon-spark" aria-hidden="true" />生成图纸<span aria-hidden="true">→</span>
            </button>
            <span role="status">{running ? progress[run?.stage || 'analysis'] : submitting ? '正在提交…' : ''}</span>
          </div>
        </form>
        {run?.status === 'needs_input' && <form className="design-form clarification" onSubmit={clarify}>
          <label className="stacked-field">{run.question}<input value={answer} maxLength={2000} required
            onChange={event => setAnswer(event.target.value)} /></label>
          <button type="submit" disabled={submitting || !answer.trim()}>继续生成</button>
        </form>}
        {(error || run?.error) && <p role="alert" className="warning">{error || run?.error?.message}</p>}
      </section>

      <aside className="design-preview pixel-panel">
        <div className="preview-header">
          <h2>{preview ? '输入预览' : '图纸示例'}</h2>
          <span className="preview-dots" aria-hidden="true"><i /><i /><i /></span>
        </div>
        <div className={`preview-stage${preview ? ' has-image' : ''}`}>
          {preview
            ? <img src={preview} className={exampleSelected ? 'pixel-preview' : undefined} alt={file ? '已上传的 ' + file.name : '输入预览'} />
            : <div className="sample-board">
              <div className="board-ruler ruler-top" aria-hidden="true"><span>01</span><span>29</span></div>
              <div className="board-ruler ruler-side" aria-hidden="true"><span>01</span><span>29</span></div>
              <img src={sampleImage} alt="29×29 的虎斑猫像素图示例" />
            </div>}
        </div>
        <div className="preview-footer">
          <div>
            <strong>{preview ? file?.name : '虎斑猫'}</strong>
            {!preview && <small>29 × 29 格 · 8 色 · 454 颗</small>}
          </div>
          {!preview && <button type="button" className="sample-button" onClick={selectExample} disabled={locked}>
            {sampling ? '载入中…' : '试转示例'} <span aria-hidden="true">↗</span>
          </button>}
        </div>
      </aside>
    </div>
  </main>;
}
