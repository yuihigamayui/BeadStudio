# BeadStudio API 接入

如果只用「底稿直转」，启动 BeadStudio 后就可以直接使用。要开启「AI 设计」，先配置视觉分析和生图接口。也可以从自己的程序调用 BeadStudio，把图片转换成图纸。

## 1. 配置 AI 模型

默认用 **DeepSeek V4.1 Flash（`deepseek-flash`）** 看图和规划，用 **GPT Image 2.6 Sunburst（`gpt-image-2.6-sunburst`）** 经 9527CODE 绘制和编辑底稿。只需配置两把密钥。

1. 在 DeepSeek 控制台创建 API Key。在 9527CODE 控制台创建能调用 `gpt-image-2.6-sunburst` 的令牌。 [DeepSeek 接入](https://api-docs.deepseek.com/) · [9527CODE 接入与令牌](https://doc.9527.codes/)
2. 在项目根目录设置环境变量，把示例里的占位文字换成自己的密钥。
3. 从**同一个终端**启动服务。已启动的服务需要先停止，再用新配置启动。

**Windows PowerShell**

```powershell
$env:DEEPSEEK_API_KEY = "填写 DeepSeek 密钥"
$env:BEAD_9527_API_KEY = "填写 9527CODE 密钥"
.\start.bat
```

**macOS 终端**

先按 [README 的 macOS 步骤](../README.md#macos)安装依赖和构建前端。激活项目的 Python 环境后，在同一个终端运行下面的命令。

```bash
export DEEPSEEK_API_KEY='填写 DeepSeek 密钥'
export BEAD_9527_API_KEY='填写 9527CODE 密钥'
python -m uvicorn backend.main:app --host 127.0.0.1 --port 8000
```

默认端口下，打开 `http://127.0.0.1:8000/api/v1/capabilities`。看到 `"model_configured": true` 就可以回到网页，选「AI 设计」并提交一张测试图。这个字段只检查后端配置，模型是否可用要看实际生成结果。如果页面提示模型接口鉴权失败，请检查对应服务的密钥和模型权限。

密钥填在后端环境变量里，无需放到前端或仓库。使用 AI 时，相关图片与文字会发送给配置的模型服务。

**可选配置。** 只设置 `OPENAI_API_KEY` 时，可直连 OpenAI，用 `gpt-5-mini` 分析、`gpt-image-1.5` 生图。更换生图网关时，设置 `BEAD_IMAGE_BASE_URL` 和该网关的 `BEAD_IMAGE_API_KEY`。可用 `BEAD_ANALYSIS_PROVIDER`、`BEAD_ANALYSIS_MODEL` 和 `BEAD_IMAGE_MODEL` 覆盖选择。

## 2. 调用 BeadStudio API

先按 README 启动服务。下面的 Python 示例读取内置虎斑猫，转成 **29×29、最多 7 色**的图纸，然后把编号图纸和材料清单保存到当前目录。用项目的 Python 环境在仓库根目录运行即可，这个示例走本地底稿直转。

```python
import os
import time
from pathlib import Path

import httpx

base_url = os.getenv("BEADSTUDIO_URL", "http://127.0.0.1:8000")
sample = Path("frontend/public/tabby-example.png")

with httpx.Client(base_url=base_url, timeout=15, trust_env=False) as client:
    with sample.open("rb") as image:
        response = client.post(
            "/api/v1/projects",
            files={"image": (sample.name, image, "image/png")},
        )
    project = response.raise_for_status().json()

    response = client.post(f"/api/v1/projects/{project['id']}/runs", json={
        "mode": "convert_only",
        "artwork_id": project["image_id"],
        "board": {"cols": 29, "rows": 29, "margin_cells": 1},
        "palette": {"id": "mard", "max_colors": 7},
    })
    run_id = response.raise_for_status().json()["id"]

    deadline = time.monotonic() + 60
    while True:
        run = client.get(f"/api/v1/runs/{run_id}").raise_for_status().json()
        if run["status"] == "ready":
            break
        if run["status"] == "failed":
            raise RuntimeError(run["error"]["message"])
        if time.monotonic() >= deadline:
            raise TimeoutError(f"等待超时，可继续查询任务 {run_id}")
        time.sleep(1)

    pattern_id = run["pattern_version_id"]
    for kind, filename in (("numbered", "bead-chart.png"), ("materials", "bead-materials.csv")):
        response = client.get(f"/api/patterns/{pattern_id}/export/{kind}")
        Path(filename).write_bytes(response.raise_for_status().content)

    print(run["stats"])
```

要走 AI 设计，创建项目时可以在表单中传入 `intent`，提交任务时改用 `mode: "design_then_convert"`，省略 `artwork_id`。模型需要澄清时，任务会返回 `needs_input`，用 `POST /api/v1/runs/{id}/answers` 提交 `question_id` 和 `answer` 后继续轮询。AI 任务可以适当延长示例的等待时间。

已有 AI 底稿的图纸可通过 `POST /api/v1/patterns/{id}/artwork-edits` 提交 `{"request":"把刘海高光合成两块"}`。任务完成后可以查看新图纸，并通过 `POST /api/v1/patterns/{新图纸ID}/accept-artwork-edit` 采用修改版。完整接口在本机的 `http://127.0.0.1:8000/docs`。

## 3. 上游 API 参考

| 用途 | 项目调用的接口 | 官方参考 |
| --- | --- | --- |
| DeepSeek 视觉分析 | `POST https://api.deepseek.com/responses`，模型 `deepseek-flash` | [图片输入](https://api-docs.deepseek.com/guides/vision/) · [Responses API](https://api-docs.deepseek.com/api/create-response/) |
| OpenAI 视觉分析 | `POST https://api.openai.com/v1/responses` | [Responses API](https://developers.openai.com/api/reference/resources/responses/methods/create) |
| 文字生图 | 生图 Base URL 下的 `POST /images/generations` | [Images API 生成](https://developers.openai.com/api/reference/resources/images/methods/generate) |
| 参考图生图和底稿编辑 | 生图 Base URL 下的 `POST /images/edits` | [Images API 编辑](https://developers.openai.com/api/reference/resources/images/methods/edit) |
| 9527CODE 生图网关 | `https://9527.codes/v1`（默认生图地址） | [9527CODE 文档](https://doc.9527.codes/) |

自定义生图网关需要支持上述 Images API 路径、透明 PNG 和 `data[0].b64_json` 响应。OpenAI 官方生图的 Base URL 是 `https://api.openai.com/v1`。
