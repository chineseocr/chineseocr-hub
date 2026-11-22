"""Backend extension point. No model SDK, weights or customer prompts are bundled."""

import base64
import json
import time
from typing import List, Protocol

import httpx


class Backend(Protocol):
    def extract(self, provider: dict, system: str, user: str, images: List[bytes]) -> dict:
        """Return raw_text and actual usage; do not manufacture confidence or coordinates."""
        ...


class BackendError(Exception):
    pass


class OpenAIBackend:
    def extract(self, provider, system, user, images):
        """Send the resolved prompts and actual page PNGs to the configured vision service.

        No fixed business answer exists in this backend. Redirects and ambient proxy settings are disabled; upstream error bodies are not exposed. Usage comes only from the service."""
        content = [{"type": "text", "text": user}]
        content += [
            {
                "type": "image_url",
                "image_url": {"url": "data:image/png;base64," + base64.b64encode(image).decode("ascii")},
            }
            for image in images
        ]
        headers = {"Authorization": "Bearer " + provider["api_key"]} if provider.get("api_key") else {}
        payload = {
            "model": provider["model"],
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": content}],
            "temperature": 0,
            "max_tokens": provider["max_tokens"],
            "stream": False,
        }
        start = time.monotonic()
        try:
            # Ignore ambient proxy variables; endpoint choice is an explicit administrator action.
            with httpx.Client(
                timeout=provider["timeout_seconds"], follow_redirects=False, trust_env=False
            ) as client:
                with client.stream(
                    "POST", provider["base_url"] + "/chat/completions", json=payload, headers=headers
                ) as response:
                    if response.status_code >= 300:
                        # Bodies can contain credentials/documents. Never expose them.
                        raise BackendError(
                            f"模型服务返回 HTTP {response.status_code}；请检查服务日志与模型配置"
                        )
                    chunks, size = [], 0
                    for chunk in response.iter_bytes():
                        size += len(chunk)
                        if size > 2 * 1024 * 1024:
                            raise BackendError("模型响应超过 2MB")
                        chunks.append(chunk)
                    body = json.loads(b"".join(chunks))
            choice = body["choices"][0]
            if choice.get("finish_reason") == "length":
                raise BackendError("模型输出被截断，请增加 token 上限或缩小页范围")
            raw = choice["message"]["content"]
            if not isinstance(raw, str):
                raise BackendError("模型未返回文本；当前适配器需要 chat completions 文本输出")
            return {
                "raw_text": raw,
                "usage": body.get("usage"),
                "duration_ms": round((time.monotonic() - start) * 1000),
                "backend": "openai",
            }
        except BackendError:
            raise
        except httpx.TimeoutException:
            raise BackendError("模型调用超时；未自动重试，避免重复计费") from None
        except (httpx.HTTPError, ValueError, KeyError, IndexError, TypeError):
            raise BackendError("模型响应或连接无效，请检查兼容接口和服务状态") from None


BACKENDS = {"openai": OpenAIBackend()}


def discover_models(base_url: str, api_key: str) -> dict:
    """Probe only /models: discover IDs without submitting documents or paying for inference.

    This establishes HTTP compatibility, not visual extraction quality. Credentials and
    upstream response bodies are intentionally excluded from error messages.
    """
    headers = {"Authorization": "Bearer " + api_key} if api_key else {}
    started = time.monotonic()
    try:
        with httpx.Client(timeout=15, follow_redirects=False, trust_env=False) as client:
            with client.stream("GET", base_url + "/models", headers=headers) as response:
                if response.status_code != 200:
                    raise BackendError(f"模型服务返回 HTTP {response.status_code}；请检查服务日志与模型配置")
                data, size = [], 0
                for chunk in response.iter_bytes():
                    size += len(chunk)
                    if size > 2 * 1024 * 1024:
                        raise BackendError("模型响应超过 2MB")
                    data.append(chunk)
                body = json.loads(b"".join(data))
        models = [{"id": m["id"]} for m in body["data"] if isinstance(m.get("id"), str)]
        if not models:
            raise BackendError("模型服务未返回可用模型")
        return {"models": models, "duration_ms": round((time.monotonic() - started) * 1000)}
    except BackendError:
        raise
    except (httpx.HTTPError, ValueError, KeyError, TypeError, AttributeError):
        raise BackendError("无法读取模型列表，请检查服务地址与访问凭证") from None
