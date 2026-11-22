"""Fill the repository's artwork prompts and call the configured model endpoints."""
import base64
import json
import os
import re
from contextlib import contextmanager, nullcontext
from dataclasses import dataclass, field
from pathlib import Path

import httpx
from pydantic import BaseModel, model_validator

PROMPTS = Path(__file__).with_name('prompts')
ANALYSIS = (PROMPTS / 'reference_analysis_prompt.md').read_text(encoding='utf-8')
SCHEMA = json.loads((PROMPTS / 'reference_analysis.schema.json').read_text(encoding='utf-8'))
ARTWORK_STYLE = (PROMPTS / 'artwork_style.md').read_text(encoding='utf-8').rstrip()
ARTWORK = (PROMPTS / 'reference_generation_prompt.md').read_text(encoding='utf-8').replace('{{artwork_style}}', ARTWORK_STYLE)
ARTWORK_EDIT = (PROMPTS / 'reference_edit_prompt.md').read_text(encoding='utf-8').replace('{{artwork_style}}', ARTWORK_STYLE)


class ArtworkPlan(BaseModel):
    model_config = {'extra': 'forbid'}

    subject: str
    composition: str
    preserve: list[str]
    simplify: list[str]
    question: str | None

    @model_validator(mode='after')
    def check_branch(self):
        if self.question is not None:
            if not self.question.strip() or any((self.subject, self.composition, self.preserve, self.simplify)):
                raise ValueError('澄清问题必须单独返回')
        elif not self.subject.strip() or not self.composition.strip():
            raise ValueError('设计计划缺少主体或构图')
        return self


def detail_guidance(size: int, margin: int) -> str:
    usable = size - 2 * margin
    if usable <= 32:
        return (f'可用宽高各约 {usable} 格。按极简图标设计，让关键线条具有约'
                f'图案最大边长的 1/{usable} 以上的厚度。优先保留剪影和关键大色块，省略次要高光、阴影和纹理。')
    if usable <= 64:
        return (f'可用宽高各约 {usable} 格。让关键边线形成连续的实色色带。'
                '对影响辨认的部位，用主色和一两层有反差的色块表达局部结构。')
    return (f'可用宽高各约 {usable} 格。保持主体完整，允许在关键部位增加次级色块，'
            '用连贯的色块表现表情、衣物或道具的结构，归并细碎纹理。')


def artwork_prompt(plan: ArtworkPlan, details: str, colors: int | None, intent: str) -> str:
    fields = {
        'aspect_ratio': '1:1',
        'detail_guidance': details,
        'max_colors': str(colors),
        'subject': plan.subject,
        'composition': plan.composition,
        'preserve': '；'.join(plan.preserve) or '按原图',
        'simplify': '；'.join(plan.simplify) or '按细节要求',
    }
    prompt = render_artwork(ARTWORK, fields, colors)
    if intent:
        prompt += f'\n- **用户要求** {intent}'
    return prompt


def artwork_edit_prompt(request: str, size: int, margin: int, colors: int | None) -> str:
    return render_artwork(ARTWORK_EDIT, {
        'aspect_ratio': '1:1',
        'detail_guidance': detail_guidance(size, margin),
        'max_colors': str(colors),
        'edit_request': request,
    }, colors)


def render_artwork(template: str, fields: dict[str, str], colors: int | None) -> str:
    if colors is None:
        template = re.sub(r'^.*{{max_colors}}.*\n', '', template, flags=re.MULTILINE)
    return re.sub(r'{{(\w+)}}', lambda match: fields[match[1]], template).rstrip()


class ModelError(Exception):
    def __init__(self, code: str, message: str, retryable: bool = False):
        super().__init__(message)
        self.code = code
        self.retryable = retryable


OPENAI_BASE_URL = 'https://api.openai.com/v1/'
DEEPSEEK_BASE_URL = 'https://api.deepseek.com/'
IMAGE_9527_BASE_URL = 'https://9527.codes/v1/'


@dataclass(frozen=True)
class Endpoints:
    analysis_provider: str
    analysis_key: str = field(repr=False)
    image_key: str = field(repr=False)
    image_base_url: str

    @classmethod
    def from_environment(cls, *, require_analysis: bool = True):
        openai_key = os.getenv('OPENAI_API_KEY', '').strip()
        deepseek_key = os.getenv('DEEPSEEK_API_KEY', '').strip()
        gateway_key = os.getenv('BEAD_9527_API_KEY', '').strip()
        analysis_default = 'openai' if openai_key and not (deepseek_key or gateway_key) else 'deepseek'
        provider = os.getenv('BEAD_ANALYSIS_PROVIDER', analysis_default).strip().lower()
        if require_analysis and provider not in ('openai', 'deepseek'):
            raise ModelError('invalid_provider', '视觉分析接口须选择 openai 或 deepseek。')
        analysis_key = deepseek_key if provider == 'deepseek' else openai_key

        image_key = os.getenv('BEAD_IMAGE_API_KEY', '').strip()
        custom_url = os.getenv('BEAD_IMAGE_BASE_URL', '').strip()
        if custom_url:
            try:
                url = httpx.URL(custom_url)
            except httpx.InvalidURL:
                url = None
            if not url or url.scheme != 'https' or not url.host or url.userinfo or url.query or url.fragment:
                raise ModelError('invalid_image_url', '生图接口须填写有效的 HTTPS Base URL。')
            image_base = str(url).rstrip('/') + '/'
        elif gateway_key or not (openai_key or image_key):
            image_base = IMAGE_9527_BASE_URL
        else:
            image_base = OPENAI_BASE_URL

        # The 9527 credential is bound to its gateway. An existing generic key
        # still goes to OpenAI unless the caller explicitly sets a Base URL.
        if image_base == IMAGE_9527_BASE_URL:
            image_key = (image_key if custom_url else '') or gateway_key
            image_variable = 'BEAD_9527_API_KEY'
        elif image_base == OPENAI_BASE_URL:
            image_key = image_key or openai_key
            image_variable = 'BEAD_IMAGE_API_KEY 或 OPENAI_API_KEY'
        else:
            image_variable = 'BEAD_IMAGE_API_KEY'

        missing = []
        if require_analysis and not analysis_key:
            missing.append('DEEPSEEK_API_KEY' if provider == 'deepseek' else 'OPENAI_API_KEY')
        if not image_key:
            missing.append(image_variable)
        if missing:
            raise ModelError('missing_api_key', '服务端未配置 ' + '、'.join(missing) + '，可上传底稿直接转换。')
        return cls(provider, analysis_key, image_key, image_base)

    @property
    def analysis_base_url(self):
        return DEEPSEEK_BASE_URL if self.analysis_provider == 'deepseek' else OPENAI_BASE_URL


def model_configured() -> bool:
    try:
        Endpoints.from_environment()
    except ModelError:
        return False
    return True


class OpenAIArtwork:
    def __init__(self, analysis_client: httpx.Client | None, image_client: httpx.Client, settings: Endpoints):
        self.analysis_client = analysis_client
        self.image_client = image_client
        self.deepseek_analysis = settings.analysis_provider == 'deepseek'
        self.analysis_model = os.getenv('BEAD_ANALYSIS_MODEL', 'deepseek-flash' if self.deepseek_analysis else 'gpt-5-mini')
        image_default = 'gpt-image-2.6-sunburst' if settings.image_base_url != OPENAI_BASE_URL else 'gpt-image-1.5'
        self.image_model = os.getenv('BEAD_IMAGE_MODEL', image_default)

    @staticmethod
    def post(client: httpx.Client, path: str, **kwargs):
        try:
            response = client.post(path, **kwargs)
            response.raise_for_status()
            return response.json()
        except httpx.HTTPStatusError as exc:
            status = exc.response.status_code
            if status in (401, 403):
                raise ModelError('provider_access', '模型接口鉴权失败或没有模型访问权限。') from exc
            raise ModelError('provider_http_error', f'模型接口返回 HTTP {status}。', status == 429 or status >= 500) from exc
        except httpx.RequestError as exc:
            raise ModelError('provider_connection', '无法连接模型接口，或等待模型结果超时。', True) from exc
        except ValueError as exc:
            raise ModelError('provider_response', '模型接口返回了无法解析的响应。') from exc

    def analyze(self, image: bytes | None, intent: str, size: int, details: str, colors: int | None) -> ArtworkPlan:
        settings = {'目标宽': size, '目标高': size, '细节要求': details, '颜色上限': colors, '用户要求': intent}
        content = [{'type': 'input_text', 'text': json.dumps(settings, ensure_ascii=False)}]
        if image is not None:
            data = base64.b64encode(image).decode('ascii')
            content.append({'type': 'input_image', 'image_url': 'data:image/png;base64,' + data})
        response_format = {'type': 'json_schema', 'name': 'artwork_plan', 'schema': SCHEMA}
        payload = {
            'model': self.analysis_model,
            'instructions': ANALYSIS,
            'input': [{'role': 'user', 'content': content}],
            'text': {'format': response_format},
        }
        if self.deepseek_analysis:
            payload['reasoning'] = {'effort': 'none'}
            payload['max_output_tokens'] = 1200
        else:
            response_format['strict'] = True
            payload['store'] = False
        if self.analysis_client is None:
            raise ModelError('missing_analysis_provider', '此任务未配置视觉分析接口。')
        result = self.post(self.analysis_client, 'responses', json=payload)
        parts = [part for item in result.get('output', []) if item.get('type') == 'message'
                 for part in item.get('content', [])]
        if any(part.get('type') == 'refusal' for part in parts):
            raise ModelError('analysis_refused', '模型没有生成设计计划，请调整图片或描述。')
        try:
            answer = next(part['text'] for part in parts if part.get('type') == 'output_text')
            return ArtworkPlan.model_validate_json(answer)
        except (KeyError, StopIteration, ValueError) as exc:
            raise ModelError('invalid_plan', '视觉模型没有返回完整的设计计划。', True) from exc

    def draw(self, image: bytes | None, prompt: str) -> bytes:
        options = {'model': self.image_model, 'prompt': prompt, 'size': '1024x1024',
                   'background': 'transparent', 'output_format': 'png'}
        if image is None:
            result = self.post(self.image_client, 'images/generations', json=options)
        else:
            if self.image_model == 'gpt-image-1.5':
                options['input_fidelity'] = 'high'
            result = self.post(self.image_client, 'images/edits', data=options, files={'image': ('reference.png', image, 'image/png')})
        try:
            return base64.b64decode(result['data'][0]['b64_json'], validate=True)
        except (KeyError, IndexError, TypeError, ValueError) as exc:
            raise ModelError('invalid_artwork', '生图模型没有返回有效的 PNG 数据。', True) from exc


@contextmanager
def model_provider(*, require_analysis: bool = True):
    settings = Endpoints.from_environment(require_analysis=require_analysis)
    analysis_client = httpx.Client(
        base_url=settings.analysis_base_url, headers={'Authorization': f'Bearer {settings.analysis_key}'},
        timeout=300, follow_redirects=False,
    ) if require_analysis else nullcontext()
    with (
        analysis_client as analysis,
        httpx.Client(base_url=settings.image_base_url,
                     headers={'Authorization': f'Bearer {settings.image_key}', 'User-Agent': 'BeadStudio/1.0'},
                     timeout=300, follow_redirects=False) as image,
    ):
        yield OpenAIArtwork(analysis, image, settings)
