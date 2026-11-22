import base64
import csv
import io
import json
import time
from collections import Counter

import httpx
import pytest
from fastapi.testclient import TestClient
from PIL import Image, ImageDraw

import backend.main as main
from backend import design_model


PLAN = {
    'subject': '虎斑猫头',
    'composition': '居中，移除家具背景',
    'preserve': ['宽的深棕条纹', '两块黄绿色虹膜'],
    'simplify': ['省略胡须'],
    'question': None,
}


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(main, 'DATA', tmp_path)
    for name in ('OPENAI_API_KEY', 'DEEPSEEK_API_KEY', 'BEAD_ANALYSIS_PROVIDER', 'BEAD_ANALYSIS_MODEL',
                 'BEAD_IMAGE_BASE_URL', 'BEAD_IMAGE_API_KEY', 'BEAD_IMAGE_MODEL', 'BEAD_9527_API_KEY'):
        monkeypatch.delenv(name, raising=False)
    with TestClient(main.app) as api:
        yield api


@pytest.fixture
def draft():
    image = Image.new('RGBA', (180, 160))
    draw = ImageDraw.Draw(image)
    draw.ellipse((50, 25, 135, 135), fill='#b48150')
    draw.ellipse((66, 51, 85, 72), fill='#81a836')
    draw.ellipse((105, 51, 124, 72), fill='#81a836')
    draw.rectangle((83, 92, 104, 104), fill='#521d1d')
    buffer = io.BytesIO()
    image.save(buffer, 'PNG')
    return buffer.getvalue()


def create_project(client, draft=None, intent=''):
    files = {'image': ('cat.png', draft, 'image/png')} if draft else None
    response = client.post('/api/v1/projects', data={'intent': intent}, files=files)
    assert response.status_code == 201, response.text
    return response.json()


def submit(client, project, *, size=29, colors=7, artwork=None, key=None):
    return client.post(
        f"/api/v1/projects/{project['id']}/runs",
        headers={'Idempotency-Key': key} if key else None,
        json={'mode': 'convert_only' if artwork else 'design_then_convert',
              'artwork_id': artwork, 'board': {'rows': size, 'cols': size},
              'palette': {'id': 'mard', 'max_colors': colors}},
    )


def complete(client, run, expected='ready'):
    assert run.status_code == 202, run.text
    path = '/api/v1/runs/' + run.json()['id']
    deadline = time.monotonic() + 20
    while time.monotonic() < deadline:
        state = client.get(path).json()
        if state['status'] not in ('queued', 'running'):
            assert state['status'] == expected, state
            return state
        time.sleep(.05)
    pytest.fail(f'run timed out: {state}')


def mock_model(monkeypatch, draft, clarify=False):
    monkeypatch.setenv('OPENAI_API_KEY', 'test-openai-key')
    captured = []
    client_factory = httpx.Client

    def respond(request):
        captured.append(request)
        if request.url.path in ('/v1/responses', '/responses'):
            answer = PLAN
            if clarify and len(captured) == 1:
                answer = {'subject': '', 'composition': '', 'preserve': [], 'simplify': [], 'question': '只做猫头吗？'}
            return httpx.Response(200, json={'output': [{'type': 'message', 'content': [
                {'type': 'output_text', 'text': json.dumps(answer)},
            ]}]})
        return httpx.Response(200, json={'data': [{'b64_json': base64.b64encode(draft).decode()}]})

    def provider_client(**kwargs):
        return client_factory(transport=httpx.MockTransport(respond), **kwargs)

    monkeypatch.setattr(design_model.httpx, 'Client', provider_client)
    return captured


@pytest.mark.parametrize('size,with_image,colors', [(29, True, 7), (100, False, None)])
def test_model_pipeline(client, draft, monkeypatch, size, with_image, colors):
    captured = mock_model(monkeypatch, draft)
    project = create_project(client, draft if with_image else None, '保留虎斑猫的眼睛')
    run = complete(client, submit(client, project, size=size, colors=colors))
    analysis, artwork = captured
    payload = json.loads(analysis.content)
    inputs = payload['input'][0]['content']
    intent = json.loads(inputs[0]['text'])
    assert intent['目标宽'] == size == intent['目标高']
    assert str(size - 2) in intent['细节要求'] and intent['颜色上限'] == colors
    assert ('输入' in payload['instructions'] and payload['text']['format']['strict'])
    assert ('极简图标' in intent['细节要求']) == (size == 29)
    assert ('次级色块' in intent['细节要求']) == (size == 100)
    assert ('颜色上限' in run['artwork_prompt']) == (colors is not None)
    assert '宽的深棕条纹' in run['artwork_prompt'] and '{{' not in run['artwork_prompt']
    assert run['plan'] == PLAN
    if with_image:
        assert inputs[1]['image_url'].startswith('data:image/png;base64,')
        assert artwork.url.path == '/v1/images/edits'
        assert b'name="image"; filename="reference.png"' in artwork.content
    else:
        assert len(inputs) == 1
        assert artwork.url.path == '/v1/images/generations'
        assert json.loads(artwork.content)['background'] == 'transparent'
    assert client.get(run['artwork_url']).status_code == 200
    assert run['stats']['rows'] == size == run['stats']['cols']
    assert min(run['stats']['margins'].values()) >= 1


def test_local_conversion_exports_and_idempotency(client, draft):
    assert client.get('/api/v1/capabilities').json()['model_configured'] is False
    project = create_project(client, draft)
    artwork = project['image_id']
    request = submit(client, project, size=48, artwork=artwork, key='design-01')
    run = complete(client, request)
    assert submit(client, project, size=48, artwork=artwork, key='design-01').json()['id'] == run['id']
    assert submit(client, project, size=29, artwork=artwork, key='design-01').status_code == 409
    version_id = run['pattern_version_id']
    pattern = client.get('/api/versions/' + version_id).json()
    counts = Counter(code for row in pattern['matrix'] for code in row if code is not None)
    assert sum(counts.values()) == run['stats']['occupied']
    assert len(counts) <= 7 and len(counts) == run['stats']['colors']
    assert pattern['strategy']['algorithm'] == 'pindou_refine'
    pattern['matrix'][0][0] = next(iter(counts))
    saved = client.post(f'/api/patterns/{version_id}/save', json={'matrix': pattern['matrix']}).json()
    assert saved['parent'] == version_id and saved['run_id'] == run['id']
    for ident in (version_id, saved['id']):
        export = f'/api/patterns/{ident}/export'
        result = client.get(export + '/materials')
        rows = csv.DictReader(io.StringIO(result.content.decode('utf-8-sig')))
        expected = run['stats']['occupied'] + (ident != version_id)
        assert sum(int(row['count']) for row in rows) == expected
        for kind in ('numbered', 'png'):
            result = client.get(export + '/' + kind)
            assert result.status_code == 200
            Image.open(io.BytesIO(result.content)).verify()


def test_question_continues_same_run(client, draft, monkeypatch):
    captured = mock_model(monkeypatch, draft, clarify=True)
    project = create_project(client, draft)
    run = complete(client, submit(client, project), 'needs_input')
    path = f"/api/v1/runs/{run['id']}/answers"
    answer = {'question_id': run['question_id'], 'answer': '对，只做猫头'}
    resumed = client.post(path, json=answer)
    result = complete(client, resumed)
    revised = json.loads(captured[1].content)['input'][0]['content'][0]['text']
    assert '对，只做猫头' in revised
    assert result['id'] == run['id'] and result['spec']['board']['rows'] == 29
    assert client.post(path, json=answer).status_code == 409


def test_design_without_key_reports_configuration(client):
    project = create_project(client, intent='一只鸭子')
    run = complete(client, submit(client, project), 'failed')
    assert run['error']['code'] == 'missing_api_key'
    assert run['stage'] == 'analysis'


def test_deepseek_analysis_and_image_gateway_use_separate_credentials(client, draft, monkeypatch):
    captured = mock_model(monkeypatch, draft)
    monkeypatch.setenv('BEAD_ANALYSIS_PROVIDER', 'deepseek')
    monkeypatch.setenv('DEEPSEEK_API_KEY', 'analysis-test-key')
    monkeypatch.setenv('BEAD_IMAGE_BASE_URL', 'https://image-gateway.example/v1')
    assert client.get('/api/v1/capabilities').json()['model_configured'] is False
    monkeypatch.setenv('BEAD_IMAGE_API_KEY', 'image-test-key')
    assert client.get('/api/v1/capabilities').json()['model_configured'] is True

    project = create_project(client, draft)
    run = complete(client, submit(client, project))
    analysis, artwork = captured
    assert analysis.url == 'https://api.deepseek.com/responses'
    assert artwork.url == 'https://image-gateway.example/v1/images/edits'
    assert analysis.headers['Authorization'] == 'Bearer analysis-test-key'
    assert artwork.headers['Authorization'] == 'Bearer image-test-key'
    payload = json.loads(analysis.content)
    assert payload['model'] == run['analysis_model'] == 'deepseek-flash'
    assert payload['reasoning'] == {'effort': 'none'}
    assert 'strict' not in payload['text']['format'] and 'store' not in payload
    assert run['image_model'] == 'gpt-image-2.6-sunburst'
    assert b'gpt-image-2.6-sunburst' in artwork.content
    assert b'input_fidelity' not in artwork.content


def test_edit_artwork_preserves_spec_and_requires_acceptance(client, draft, monkeypatch):
    captured = mock_model(monkeypatch, draft)
    reference = io.BytesIO()
    Image.new('RGB', (40, 60), '#a5a0b1').save(reference, 'PNG')
    project = create_project(client, reference.getvalue())
    initial = complete(client, submit(client, project, size=48, colors=7))
    original_id = initial['pattern_version_id']
    original = client.get('/api/versions/' + original_id).json()
    captured.clear()
    monkeypatch.delenv('OPENAI_API_KEY')
    monkeypatch.setenv('BEAD_ANALYSIS_PROVIDER', 'deepseek')
    monkeypatch.setenv('BEAD_IMAGE_API_KEY', 'image-test-key')

    route = f'/api/v1/patterns/{original_id}/artwork-edits'
    assert client.post(route, json={'request': '   '}).status_code == 422
    request = '把高光合成一块，其他部分保持原样。'
    headers = {'Idempotency-Key': 'combine-highlights'}
    result = complete(client, client.post(route, json={'request': request}, headers=headers))
    assert client.post(route, json={'request': request}, headers=headers).json()['id'] == result['id']
    assert result['parent_pattern_id'] == original_id
    assert result['spec']['board'] == original['board']
    assert result['spec']['palette'] == original['palette']
    call, = captured
    assert call.url.path == '/v1/images/edits'
    assert call.headers['Authorization'] == 'Bearer image-test-key'
    prompt = result['artwork_prompt']
    assert request in prompt and '46 格' in prompt and '颜色上限** 7 色' in prompt
    assert '只改用户点名的部位' in prompt and '画面中不绘制像素方块' in prompt
    assert draft in call.content and reference.getvalue() not in call.content

    new_id = result['pattern_version_id']
    candidate = client.get('/api/versions/' + new_id).json()
    assert candidate['parent'] == original_id
    assert candidate['source'] != original['source']
    assert candidate['pending_artwork_edit'] is True
    asset = client.get('/api/versions/' + candidate['source']).json()
    assert asset['parent'] == original['source']
    assert client.post(f'/api/v1/patterns/{new_id}/artwork-edits', json={'request': request}).status_code == 422

    path = f'/api/v1/patterns/{new_id}/accept-artwork-edit'
    accepted = client.post(path).json()
    assert 'pending_artwork_edit' not in accepted and accepted['matrix'] == candidate['matrix']
    assert client.post(path).json() == accepted
    assert client.get('/api/versions/' + original_id).json() == original


def test_edit_artwork_requires_ai_source(client, draft):
    project = create_project(client, draft)
    run = complete(client, submit(client, project, artwork=project['image_id']))
    route = f"/api/v1/patterns/{run['pattern_version_id']}/artwork-edits"
    assert client.post(route, json={'request': '更换颜色'}).status_code == 422



def test_default_models_with_the_two_provider_keys(client, draft, monkeypatch):
    captured = mock_model(monkeypatch, draft)
    monkeypatch.delenv('OPENAI_API_KEY')
    monkeypatch.setenv('DEEPSEEK_API_KEY', 'test-deepseek-key')
    monkeypatch.setenv('BEAD_9527_API_KEY', 'test-9527-key')
    assert client.get('/api/v1/capabilities').json()['model_configured'] is True

    project = create_project(client, draft)
    run = complete(client, submit(client, project))
    analysis, artwork = captured
    assert analysis.url == 'https://api.deepseek.com/responses'
    assert artwork.url == 'https://9527.codes/v1/images/edits'
    assert analysis.headers['Authorization'] == 'Bearer test-deepseek-key'
    assert artwork.headers['Authorization'] == 'Bearer test-9527-key'
    assert run['analysis_model'] == 'deepseek-flash'
    assert run['image_model'] == 'gpt-image-2.6-sunburst'

    monkeypatch.setenv('BEAD_IMAGE_BASE_URL', 'https://another-image-gateway.example/v1')
    assert client.get('/api/v1/capabilities').json()['model_configured'] is False


def test_review_sample_installed_once_and_editable(tmp_path, monkeypatch):
    monkeypatch.setattr(main, 'DATA', tmp_path)
    with TestClient(main.app) as api:
        versions = api.get('/api/projects').json()
        sample, = [version for version in versions if version['kind'] == 'pattern']
        assert sample['sample'] is True
        assert sample['board']['rows'] == sample['board']['cols'] == 58
        beads = [code for row in sample['matrix'] for code in row if code is not None]
        assert len(beads) == 1871 and len(set(beads)) == 21

        artwork = api.get('/api/versions/' + sample['source']).json()
        reference = api.get('/api/versions/' + artwork['parent']).json()
        assert (artwork['role'], reference['role']) == ('artwork', 'reference')
        for asset in (artwork, reference):
            with Image.open(io.BytesIO(api.get('/api/images/' + asset['id']).content)) as image:
                image.verify()
        export = f"/api/patterns/{sample['id']}/export"
        for kind in ('numbered', 'png', 'materials'):
            assert api.get(export + '/' + kind).status_code == 200

        response = api.post(f"/api/patterns/{sample['id']}/save", json={'matrix': sample['matrix']})
        assert response.status_code == 200
        saved = response.json()
        assert saved['source'] == sample['source'] and saved['parent'] == sample['id']

    with TestClient(main.app) as api:
        reopened = api.get('/api/projects').json()
        assert len(reopened) == 4
        assert [version for version in reopened if version.get('sample')] == [sample]
        assert any(version['id'] == saved['id'] for version in reopened)
