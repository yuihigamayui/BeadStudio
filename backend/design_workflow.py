"""A persisted design run from user input to an editable pattern version."""
import hashlib
import io
import json
import logging
import uuid
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager, closing
from typing import Literal

import cv2
import numpy as np
from fastapi import APIRouter, Form, Header, HTTPException, UploadFile
from PIL import Image, ImageOps
from pydantic import BaseModel, Field, model_validator

from backend import design_model
from backend.design_model import ArtworkPlan, ModelError, artwork_edit_prompt, artwork_prompt, detail_guidance
from backend.generation import Strategy, generate

LOG = logging.getLogger(__name__)


class Board(BaseModel):
    cols: int = Field(ge=10, le=100)
    rows: int = Field(ge=10, le=100)
    margin_cells: int = Field(1, ge=0, le=5)

    @model_validator(mode='after')
    def check_geometry(self):
        if self.cols != self.rows:
            raise ValueError('当前支持方形图纸')
        if 2 * self.margin_cells >= self.cols:
            raise ValueError('留边超出图纸空间')
        return self


class Palette(BaseModel):
    id: Literal['mard'] = 'mard'
    max_colors: int | None = Field(16, ge=2, le=32)


class RunSpec(BaseModel):
    mode: Literal['design_then_convert', 'convert_only', 'edit_then_convert'] = 'design_then_convert'
    board: Board
    palette: Palette = Field(default_factory=Palette)
    artwork_id: str | None = None
    parent_pattern_id: str | None = None
    edit_request: str | None = None
    legacy_strategy: Strategy | None = None


class ArtworkEdit(BaseModel):
    request: str = Field(min_length=1, max_length=2000)


class Answer(BaseModel):
    question_id: str
    answer: str = Field(min_length=1, max_length=2000)


def normalized_image(raw: bytes) -> Image.Image:
    with Image.open(io.BytesIO(raw)) as image:
        if image.width * image.height > 25_000_000:
            raise ValueError('图片像素过大')
        return ImageOps.exif_transpose(image).convert('RGBA')


def frame_artwork(image: Image.Image, board: Board) -> Image.Image:
    alpha = image.getchannel('A')
    bounds = alpha.point(lambda value: 255 if value >= 128 else 0).getbbox()
    if bounds is None:
        raise ValueError('底稿没有可放置豆子的内容')
    scale = max(16, 1024 // board.cols)
    side = board.cols * scale
    inner = side - 2 * board.margin_cells * scale
    subject = ImageOps.contain(image.crop(bounds), (inner, inner), Image.Resampling.LANCZOS)
    frame = Image.new('RGBA', (side, side))
    frame.paste(subject, ((side - subject.width) // 2, (side - subject.height) // 2))
    return frame


def inspect_grid(matrix: np.ndarray, board: Board) -> tuple[dict, list[dict]]:
    occupied = matrix != None
    ys, xs = np.where(occupied)
    if not len(xs):
        raise ValueError('转换后图纸为空')
    margins = {'left': int(xs.min()), 'right': board.cols - 1 - int(xs.max()),
               'top': int(ys.min()), 'bottom': board.rows - 1 - int(ys.max())}
    components, _ = cv2.connectedComponents(occupied.astype(np.uint8), connectivity=4)
    stats = {'cols': board.cols, 'rows': board.rows, 'occupied': len(xs),
             'colors': len(set(matrix[occupied])), 'components': components - 1, 'margins': margins}
    issues = [{'code': 'edge_contact' if cells == 0 else 'insufficient_margin', 'side': side}
              for side, cells in margins.items() if cells == 0 or cells < board.margin_cells]
    if components > 2:
        issues.append({'code': 'multiple_components', 'count': components - 1})
    return stats, issues


def design_routes(db, get, put, data_directory):
    pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix='bead-design')

    def connection():
        conn = db()
        conn.execute('CREATE TABLE IF NOT EXISTS design_projects(id TEXT PRIMARY KEY, image_id TEXT, intent TEXT)')
        conn.execute("""CREATE TABLE IF NOT EXISTS design_runs(
            id TEXT PRIMARY KEY, project TEXT, spec TEXT, idem TEXT, payload TEXT,
            UNIQUE(project, idem))""")
        return conn

    def read_run(ident):
        with closing(connection()) as conn:
            row = conn.execute('SELECT project,spec,payload FROM design_runs WHERE id=?', (ident,)).fetchone()
        if row is None:
            raise HTTPException(404, '生成任务不存在')
        return row[0], RunSpec.model_validate_json(row[1]), json.loads(row[2])

    def update(state, **fields):
        state.update(fields)
        with closing(connection()) as conn, conn:
            conn.execute('UPDATE design_runs SET payload=? WHERE id=?', (json.dumps(state), state['id']))

    def write_image(image, project, parent, **metadata):
        filename = uuid.uuid4().hex + '.png'
        path = data_directory() / filename
        image.save(path)
        return put(project, 'image', parent, {'file': filename, 'width': image.width, 'height': image.height,
                                             'sha256': hashlib.sha256(path.read_bytes()).hexdigest(), **metadata})

    def image_bytes(ident):
        return (data_directory() / get(ident)['file']).read_bytes()

    def reference_png(raw):
        image = normalized_image(raw)
        image.thumbnail((1536, 1536), Image.Resampling.LANCZOS)
        output = io.BytesIO()
        image.save(output, format='PNG')
        return output.getvalue()

    def execute(ident):
        project, spec, state = read_run(ident)
        try:
            size = spec.board.cols
            colors = spec.palette.max_colors
            if spec.mode == 'design_then_convert':
                with closing(connection()) as conn:
                    image_id, intent = conn.execute(
                        'SELECT image_id,intent FROM design_projects WHERE id=?', (project,),
                    ).fetchone()
                update(state, status='running', stage='analysis')
                with design_model.model_provider() as provider:
                    reference = reference_png(image_bytes(image_id)) if image_id else None
                    details = detail_guidance(size, spec.board.margin_cells)
                    for clarification in state.get('clarifications', []):
                        intent += '\n澄清问题：' + clarification['question'] + '\n用户回答：' + clarification['answer']
                    plan: ArtworkPlan = provider.analyze(reference, intent, size, details, colors)
                    if plan.question is not None:
                        update(state, status='needs_input', question=plan.question, question_id=uuid.uuid4().hex)
                        return
                    prompt = artwork_prompt(plan, details, colors, intent)
                    update(state, stage='artwork', plan=plan.model_dump(), artwork_prompt=prompt,
                           analysis_model=provider.analysis_model, image_model=provider.image_model)
                    draft = normalized_image(provider.draw(reference, prompt))
            elif spec.mode == 'edit_then_convert':
                image_id = spec.artwork_id
                update(state, status='running', stage='artwork')
                with design_model.model_provider(require_analysis=False) as provider:
                    prompt = artwork_edit_prompt(spec.edit_request, size, spec.board.margin_cells, colors)
                    update(state, artwork_prompt=prompt, image_model=provider.image_model,
                           parent_pattern_id=spec.parent_pattern_id)
                    draft = normalized_image(provider.draw(reference_png(image_bytes(image_id)), prompt))
            else:
                draft = normalized_image(image_bytes(spec.artwork_id))

            artwork_id = spec.artwork_id
            if spec.mode != 'convert_only':
                if draft.getchannel('A').getextrema()[0] == 255:
                    raise ModelError('opaque_artwork', '模型底稿缺少透明留空。可调整要求后重新提交。', True)
                artwork_id = write_image(draft, project, image_id, role='artwork')['id']

            update(state, status='running', stage='conversion', artwork_id=artwork_id,
                   artwork_url='/api/images/' + artwork_id)
            strategy = spec.legacy_strategy or Strategy(algorithm='pindou_refine', size=size, colors=colors)
            frame = draft if spec.legacy_strategy else frame_artwork(draft, spec.board)
            matrix, _, _, metrics = generate(frame, strategy)
            stats, issues = inspect_grid(matrix, spec.board)
            payload = {
                'source': artwork_id, 'matrix': matrix.tolist(), 'strategy': strategy.model_dump(),
                'metrics': metrics, 'run_id': ident, 'palette': spec.palette.model_dump(),
            }
            if not spec.legacy_strategy:
                payload['board'] = spec.board.model_dump()
            if spec.parent_pattern_id:
                payload['pending_artwork_edit'] = True
            pattern = put(project, 'pattern', spec.parent_pattern_id or artwork_id, payload)
            version = pattern['id']
            update(state, status='ready', stage='review', pattern_version_id=version,
                   preview_url=f'/api/patterns/{version}/export/png', stats=stats, issues=issues)
        except ModelError as exc:
            update(state, status='failed', error={'code': exc.code, 'message': str(exc), 'retryable': exc.retryable})
        except (OSError, ValueError, RuntimeError) as exc:
            LOG.warning('Design run %s failed at %s: %s', ident, state['stage'], exc)
            message = '生图接口返回的底稿无法读取。' if state['stage'] == 'artwork' else '无法生成图纸，请检查输入或底稿。'
            update(state, status='failed', error={'code': 'design_failed', 'message': message, 'retryable': True})
        except Exception:
            LOG.exception('Unexpected failure in design run %s', ident)
            update(state, status='failed', error={'code': 'design_failed', 'message': '生成任务异常中断。', 'retryable': True})

    @asynccontextmanager
    async def lifespan(_app):
        with closing(connection()) as conn, conn:
            pending = conn.execute("SELECT payload FROM design_runs WHERE json_extract(payload,'$.status') IN ('running','queued')")
            for (payload,) in pending.fetchall():
                state = json.loads(payload)
                state.update(status='failed', error={
                    'code': 'interrupted', 'message': '服务重启，任务已中断。请重新提交。', 'retryable': True,
                })
                conn.execute('UPDATE design_runs SET payload=? WHERE id=?', (json.dumps(state), state['id']))
        yield

    router = APIRouter(prefix='/api/v1', lifespan=lifespan)

    @router.get('/capabilities')
    def capabilities():
        return {'boards': {'shape': 'square', 'min': 10, 'max': 100}, 'palettes': ['mard'],
                'model_configured': design_model.model_configured()}

    @router.post('/projects', status_code=201)
    def create_project(image: UploadFile | None = None, intent: str = Form('', max_length=2000)):
        intent = intent.strip()
        if image is None and not intent:
            raise HTTPException(422, '请上传图片或描述要拼的图案')
        ident = uuid.uuid4().hex
        image_id = None
        if image is not None:
            raw = image.file.read(20_000_001)
            if len(raw) > 20_000_000:
                raise HTTPException(413, '图片上限 20MB')
            try:
                source = normalized_image(raw)
            except (OSError, ValueError, Image.DecompressionBombError) as exc:
                raise HTTPException(422, '无效图片或像素过大') from exc
            image_id = write_image(source, ident, None, role='reference')['id']
        with closing(connection()) as conn, conn:
            conn.execute('INSERT INTO design_projects VALUES(?,?,?)', (ident, image_id, intent))
        return {'id': ident, 'image_id': image_id, 'intent': intent}

    def enqueue(project: str, body: RunSpec, idempotency_key: str | None):
        ident = uuid.uuid4().hex
        stages = {'design_then_convert': 'analysis', 'edit_then_convert': 'artwork', 'convert_only': 'conversion'}
        state = {'id': ident, 'project_id': project, 'status': 'queued', 'stage': stages[body.mode]}
        spec = body.model_dump_json()
        with closing(connection()) as conn, conn:
            if not conn.execute('SELECT 1 FROM design_projects WHERE id=?', (project,)).fetchone():
                raise HTTPException(404, '项目不存在')
            created = conn.execute('INSERT INTO design_runs VALUES(?,?,?,?,?) ON CONFLICT(project,idem) DO NOTHING',
                                   (ident, project, spec, idempotency_key, json.dumps(state))).rowcount
            if not created:
                saved, payload = conn.execute('SELECT spec,payload FROM design_runs WHERE project=? AND idem=?',
                                              (project, idempotency_key)).fetchone()
                if saved != spec:
                    raise HTTPException(409, '重复使用的请求标识对应不同规格')
                return json.loads(payload)
        pool.submit(execute, ident)
        return state

    @router.post('/projects/{project}/runs', status_code=202)
    def create_run(project: str, body: RunSpec, idempotency_key: str | None = Header(None, max_length=128)):
        if body.mode == 'edit_then_convert' or body.parent_pattern_id or body.edit_request or body.legacy_strategy:
            raise HTTPException(422, '请从已有图纸提交 AI 修改。')
        if body.mode == 'convert_only':
            source = get(body.artwork_id) if body.artwork_id else None
            if source is None or source['project'] != project or source['kind'] != 'image':
                raise HTTPException(422, '请选择此项目的一张底稿')
        return enqueue(project, body, idempotency_key)

    @router.post('/patterns/{ident}/artwork-edits', status_code=202)
    def edit_artwork(ident: str, body: ArtworkEdit, idempotency_key: str | None = Header(None, max_length=128)):
        request = body.request.strip()
        if not request:
            raise HTTPException(422, '请填写想修改的内容。')
        pattern = get(ident)
        if pattern['kind'] != 'pattern' or pattern.get('pending_artwork_edit'):
            raise HTTPException(422, '请选择一张已采用的图纸。')
        artwork = get(pattern['source'])
        if artwork.get('role') != 'artwork' or artwork['project'] != pattern['project']:
            raise HTTPException(422, '这张图纸没有可以继续修改的 AI 底稿。')
        strategy = Strategy.model_validate(pattern['strategy'])
        board = Board.model_validate(pattern['board']) if 'board' in pattern else Board(
            rows=len(pattern['matrix']), cols=len(pattern['matrix'][0]), margin_cells=0,
        )
        palette = Palette.model_validate(pattern.get('palette', {'max_colors': strategy.colors}))
        return enqueue(pattern['project'], RunSpec(
            mode='edit_then_convert', board=board, palette=palette, artwork_id=artwork['id'],
            parent_pattern_id=ident, edit_request=request,
            legacy_strategy=None if 'board' in pattern else strategy,
        ), idempotency_key)

    @router.post('/patterns/{ident}/accept-artwork-edit')
    def accept_artwork_edit(ident: str):
        with closing(db()) as conn, conn:
            conn.execute('BEGIN IMMEDIATE')
            row = conn.execute('SELECT kind,payload FROM versions WHERE id=?', (ident,)).fetchone()
            if row is None:
                raise HTTPException(404, '版本不存在')
            if row[0] != 'pattern':
                raise HTTPException(422, '请选择一张拼豆图纸。')
            payload = json.loads(row[1])
            if payload.pop('pending_artwork_edit', False):
                conn.execute('UPDATE versions SET payload=? WHERE id=?', (json.dumps(payload), ident))
        return get(ident)

    @router.get('/runs/{ident}')
    def status(ident: str):
        _, spec, state = read_run(ident)
        return {**state, 'spec': spec.model_dump()}

    @router.post('/runs/{ident}/answers', status_code=202)
    def answer(ident: str, body: Answer):
        if not body.answer.strip():
            raise HTTPException(422, '请填写回答')
        with closing(connection()) as conn, conn:
            conn.execute('BEGIN IMMEDIATE')
            row = conn.execute('SELECT payload FROM design_runs WHERE id=?', (ident,)).fetchone()
            if row is None:
                raise HTTPException(404, '生成任务不存在')
            state = json.loads(row[0])
            if state['status'] != 'needs_input' or state.get('question_id') != body.question_id:
                raise HTTPException(409, '该问题已失效')
            state.setdefault('clarifications', []).append({'question': state.pop('question'), 'answer': body.answer.strip()})
            state.pop('question_id')
            state.update(status='queued', stage='analysis')
            conn.execute('UPDATE design_runs SET payload=? WHERE id=?', (json.dumps(state), ident))
        pool.submit(execute, ident)
        return state

    return router
