import {useEffect, useState} from 'react';
import {api} from './api';
import type {Color, Review, Run, Version} from './api';
import {BeadCanvas} from './BeadCanvas';
import {DesignFlow} from './DesignFlow';
import {SourceSettings, sourceChoices} from './SourceSettings';
import type {Matrix} from './selection';

type ArtworkRevision = {
  before: Version;
  after: Version;
  beforeReview?: Review;
  afterReview: Review;
};

export function App() {
  const [page, setPage] = useState<'design' | 'review'>('design');
  const [palette, setPalette] = useState<Color[]>([]);
  const [image, setImage] = useState<Version>();
  const [pattern, setPattern] = useState<Version>();
  const [versions, setVersions] = useState<Version[]>([]);
  const [matrix, setMatrix] = useState<Matrix>([]);
  const [review, setReview] = useState<Review>();
  const [sourceChoice, setSourceChoice] = useState<string>();
  const [mobileView, setMobileView] = useState<'pattern' | 'source'>('pattern');
  const [busy, setBusy] = useState(false);
  const [editOpen, setEditOpen] = useState(false);
  const [editRequest, setEditRequest] = useState('');
  const [editProgress, setEditProgress] = useState('');
  const [editError, setEditError] = useState('');
  const [revision, setRevision] = useState<ArtworkRevision>();
  const [status, setStatus] = useState('');

  async function refresh() {
    setVersions(await api<Version[]>('/projects'));
  }

  useEffect(() => {
    api<Version[]>('/projects').then(async saved => {
      setVersions(saved);
      const sample = saved.find(version => version.sample);
      const onlySample = sample && saved.every(version =>
        version.project === sample.project && (version.kind === 'image' || version.sample),
      );
      if (onlySample && !sessionStorage.getItem('beadstudio.run')) {
        await loadPattern(sample);
        setPage('review');
      }
    }).catch(error => setStatus(error.message));
  }, []);

  async function run(action: () => Promise<void>) {
    setBusy(true);
    setStatus('');
    try {
      await action();
      await refresh();
    } catch (error) {
      setStatus((error as Error).message);
    } finally {
      setBusy(false);
    }
  }

  const dirty = !!pattern && JSON.stringify(pattern.matrix) !== JSON.stringify(matrix);
  const guard = () => !dirty || confirm('当前图纸有未保存修改，继续将丢弃修改。是否继续？');

  function applyPattern(version: Version) {
    setPattern(version);
    setMatrix(version.matrix || []);
  }

  async function loadPattern(version: Version) {
    const legacy = !['V3', 'flat', 'pindou', 'pindou_refine'].includes(version.strategy?.algorithm || '');
    const [colors, source] = await Promise.all([
      api<Color[]>('/palette?legacy=' + legacy),
      api<Version>('/versions/' + version.source),
    ]);
    setPalette(colors);
    setImage(source);
    setSourceChoice(undefined);
    setMobileView('pattern');
    applyPattern(version);
  }

  async function openReview(result: Review) {
    await loadPattern(await api<Version>('/versions/' + result.pattern_version_id));
    setReview(result);
    setPage('review');
    void refresh().catch(error => setStatus(error.message));
    window.scrollTo(0, 0);
  }

  function goToDesign() {
    if (page !== 'design' && !editProgress && !revision && guard()) {
      setPage('design');
      window.scrollTo(0, 0);
    }
  }

  async function editArtwork() {
    const request = editRequest.trim();
    if (!pattern || !request || editProgress || revision || !guard()) return;
    setEditError('');
    setEditProgress('正在提交修改…');
    try {
      let state = await api<Run>('/v1/patterns/' + pattern.id + '/artwork-edits', {request});
      while (state.status === 'queued' || state.status === 'running') {
        setEditProgress(state.stage === 'conversion' ? '正在重新转换图纸…' : 'AI 正在修改底稿…');
        await new Promise(resolve => window.setTimeout(resolve, 1200));
        state = await api<Run>('/v1/runs/' + state.id);
      }
      if (state.status !== 'ready' || !state.pattern_version_id) {
        throw new Error(state.error?.message || '这次修改没有生成图纸。');
      }
      const version = await api<Version>('/versions/' + state.pattern_version_id);
      const comparison = {
        before: pattern, after: version, beforeReview: review,
        afterReview: {pattern_version_id: version.id, issues: state.issues || []},
      };
      await loadPattern(version);
      setSourceChoice(version.source);
      setReview(comparison.afterReview);
      setRevision(comparison);
      setEditOpen(false);
      window.scrollTo(0, 0);
    } catch (error) {
      setEditError((error as Error).message);
    } finally {
      setEditProgress('');
    }
  }

  async function showRevision(after: boolean) {
    if (!revision) return;
    const version = after ? revision.after : revision.before;
    await loadPattern(version);
    setSourceChoice(version.source);
    setReview(after ? revision.afterReview : revision.beforeReview);
  }

  async function finishRevision(accept: boolean) {
    if (!revision) return;
    if (accept) {
      const saved = await api<Version>('/v1/patterns/' + revision.after.id + '/accept-artwork-edit', {});
      await loadPattern(saved);
      setSourceChoice(saved.source);
      setReview(revision.afterReview);
      setEditRequest('');
    } else {
      await showRevision(false);
    }
    setRevision(undefined);
  }

  const counts = new Map<string, number>();
  for (const code of matrix.flat()) {
    if (code) counts.set(code, (counts.get(code) || 0) + 1);
  }
  const beadCount = Array.from(counts.values()).reduce((sum, n) => sum + n, 0);
  const cols = matrix[0]?.length || 0;
  const patterns = versions.filter(version => version.kind === 'pattern' && !version.pending_artwork_edit);
  const sample = patterns.find(version => version.sample);
  const choices = image ? sourceChoices(image, versions) : [];
  const compared = choices.find(choice => choice.version.id === sourceChoice) || choices[0];
  const canEditArtwork = !!pattern && image?.id === pattern.source && image?.role === 'artwork'
    && compared?.version.id === image.id && !revision;

  return <>
    <header className="workspace-header">
      <div className="header-content">
        <button type="button" className="wordmark" onClick={goToDesign} disabled={!!editProgress || !!revision}>
          <span className="brand-mark" aria-hidden="true"><i /><i /><i /><i /><i /><i /><i /><i /><i /></span>
          <span>Bead<span>Studio</span></span>
        </button>
        <nav className="workspace-nav" aria-label="工作台">
          <button aria-current={page === 'design' ? 'page' : undefined} onClick={goToDesign} disabled={!!editProgress || !!revision}>
            <span className="pixel-icon icon-plus" aria-hidden="true" />新建
          </button>
          <button aria-current={page === 'review' ? 'page' : undefined} onClick={() => {
            setPage('review');
            window.scrollTo(0, 0);
          }}>
            <span className="pixel-icon icon-grid" aria-hidden="true" />审图
          </button>
        </nav>
        <span className="header-palette" title="MARD 221 社区色卡">
          <span aria-hidden="true"><i /><i /><i /></span>MARD 221
        </span>
      </div>
    </header>
    {page === 'design' ? <DesignFlow onReady={openReview} sample={sample} /> : <main className="review-main" data-mobile-view={mobileView}>
      <div className="page-heading">
        <h1>审图<span aria-hidden="true" className="heading-period">.</span></h1>
        {pattern && <p>{cols} × {matrix.length} 格 · {counts.size} 色 · {beadCount} 颗{dirty ? ' · 有未保存修改' : ''}</p>}
      </div>
      {revision && <section className="revision-bar pixel-panel" aria-label="AI 修改前后对照">
        <div className="source-options">
          <button type="button" disabled={busy} aria-pressed={pattern?.id === revision.before.id}
            onClick={() => void run(() => showRevision(false))}>修改前</button>
          <button type="button" disabled={busy} aria-pressed={pattern?.id === revision.after.id}
            onClick={() => void run(() => showRevision(true))}>修改后</button>
        </div>
        <div className="revision-actions">
          <button type="button" disabled={busy} onClick={() => void run(() => finishRevision(false))}>保留原版</button>
          <button type="button" className="primary-button" disabled={busy}
            onClick={() => void run(() => finishRevision(true))}>保存修改版</button>
        </div>
      </section>}
      {image && pattern && <div className="mobile-review-switch" aria-label="审图视图">
        <button type="button" aria-pressed={mobileView === 'pattern'} onClick={() => setMobileView('pattern')}>拼豆图纸</button>
        <button type="button" aria-pressed={mobileView === 'source'} onClick={() => setMobileView('source')}>对照图片</button>
      </div>}
      <div className="review-layout">
        <div className={`review-workspace${image ? ' has-comparison' : ''}`}>
          {image && compared && <section className="pixel-panel review-source" aria-label="来源对照">
            <div className="panel-heading"><h2>来源对照</h2></div>
            <div className="panel-content">
              <div className="source-options" aria-label="对照图片">
                {choices.map(choice => <button key={choice.version.id} type="button"
                  aria-pressed={choice.version.id === compared.version.id}
                  onClick={() => setSourceChoice(choice.version.id)}>{choice.label}</button>)}
              </div>
              <div className="canvas source-comparison">
                <img src={'/api/images/' + compared.version.id} alt={compared.label} />
              </div>
              {pattern && pattern.project !== image.project &&
                <p className="warning">这张来源图属于另一个项目。</p>}
              {canEditArtwork && <div className="artwork-edit">
                <button type="button" className="primary-button" disabled={busy || !!editProgress}
                  aria-expanded={editOpen}
                  onClick={() => {setEditOpen(!editOpen); setEditError('');}}>用 AI 修改</button>
                {editOpen && <form onSubmit={event => {event.preventDefault(); void editArtwork();}}>
                  <label className="stacked-field">想改哪里？
                    <textarea rows={3} maxLength={2000} value={editRequest} disabled={busy || !!editProgress}
                      placeholder="例如：把刘海高光合成两块，保持眼睛和马尾原样。"
                      onChange={event => setEditRequest(event.target.value)} />
                  </label>
                  <button type="submit" disabled={!editRequest.trim() || busy || !!editProgress}>修改并重转</button>
                </form>}
              </div>}
              {!!editProgress && <p className="mode-note artwork-edit-status" role="status">{editProgress}</p>}
              {editError && <p className="warning" role="alert">{editError}</p>}
              <details className="source-adjustments" hidden={!!editProgress || !!revision}>
                <summary>调整与重转</summary>
                <SourceSettings key={image.id} image={image} pattern={pattern} busy={busy}
                  onEdit={settings => void run(async () => {
                    const edited = await api<Version>('/images/' + image.id + '/edit', settings);
                    setImage(edited);
                    setSourceChoice(edited.id);
                  })}
                  onConvert={settings => {
                    if (!guard()) return;
                    void run(async () => {
                      const version = await api<Version>('/images/' + image.id + '/convert', settings);
                      setReview(undefined);
                      await loadPattern(version);
                    });
                  }} />
              </details>
            </div>
          </section>}
          <section className="review-pattern pixel-panel">
            <div className="panel-heading review-heading">
              <h2>拼豆画布</h2>
            </div>
            <div className="panel-content">
              {status && <p className="warning" role="alert">{status}</p>}
              {busy && <p role="status">处理中…</p>}
              {review?.issues.some(issue => issue.code === 'multiple_components') &&
                <p className="warning">图案有分离部分，请确认是否需要单独制作。</p>}
              {review?.issues.some(issue => issue.code === 'edge_contact' || issue.code === 'insufficient_margin') &&
                <p className="warning">部分边距偏小，请在导出前检查边缘。</p>}
              {pattern ? <div inert={!!editProgress || !!revision}>
                <BeadCanvas matrix={matrix} palette={palette} onChange={setMatrix} resetKey={pattern.id} />
              </div>
                : <div className="review-empty">
                  <span className="pixel-icon icon-grid" aria-hidden="true" />
                  <p>选择一个已有图纸，或先新建一张。</p>
                  <button className="primary-button" onClick={goToDesign}>新建图纸 →</button>
                </div>}
            </div>
          </section>
        </div>
        <aside className="review-sidebar">
          {pattern && <section className="pixel-panel review-materials">
            <div className="panel-heading"><h2>材料统计</h2></div>
            <div className="panel-content">
              <div className="materials-summary"><strong>{counts.size} <small>色</small></strong>
                <strong>{beadCount} <small>颗</small></strong></div>
              <div className="materials">
                {[...counts].sort((a, b) => b[1] - a[1]).map(([code, count]) =>
                  <span key={code}><i style={{background: palette.find(color => color.id === code)?.hex}} />{code}<b>{count}</b></span>)}
              </div>
              <button className="primary-button save-button" disabled={busy || !dirty || !!editProgress || !!revision}
                onClick={() => void run(async () => {
                  setPattern(await api<Version>('/patterns/' + pattern.id + '/save', {matrix}));
                })}>保存为新版本</button>
              {pattern.pending_artwork_edit ? <p className="mode-note">保存修改版后即可导出。</p>
                : dirty ? <p className="mode-note">保存修改后即可导出。</p>
                : <div className="exports">
                  {([['numbered', '编号图纸 PNG'], ['png', '像素 PNG'], ['materials', '材料清单 CSV']]).map(([format, name]) =>
                    <a key={format} download={format === 'materials' ? 'bead-materials.csv' : 'bead-' + format + '.png'}
                      href={'/api/patterns/' + pattern.id + '/export/' + format}>{name}<span aria-hidden="true">↓</span></a>)}
                </div>}
            </div>
          </section>}
          <section className="pixel-panel version-panel">
            <div className="panel-heading"><h2>图纸版本</h2>
              <button type="button" className="text-button" disabled={busy} onClick={() => void refresh().catch(error => setStatus(error.message))}>刷新</button>
            </div>
            <div className="versions">
              {!patterns.length && <p>生成后的图纸会保存在这里。</p>}
              {patterns.map(version => <button className={version.id === pattern?.id ? 'active' : ''}
                disabled={busy || !!editProgress || !!revision} key={version.id} onClick={() => {
                  if (!guard()) return;
                  setEditRequest('');
                  setEditOpen(false);
                  setEditError('');
                  void run(async () => {
                    setReview(undefined);
                    await loadPattern(version);
                  });
                }}>
                <span className="pixel-icon icon-grid" aria-hidden="true" />
                <span>{version.sample ? '动漫头像示例' : '拼豆图纸'}{!version.sample && <small>{version.id.slice(0, 7)}</small>}</span>
              </button>)}
            </div>
          </section>
        </aside>
      </div>
    </main>}
  </>;
}
