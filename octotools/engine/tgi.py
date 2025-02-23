# Reference: https://github.com/zou-group/textgrad/blob/main/textgrad/engine/openai.py

try:
    from openai import OpenAI
except ImportError:
    raise ImportError(
        "If you'd like to use OpenAI models, please install the openai package by running `pip install openai`, and add 'OPENAI_API_KEY' to your environment variables."
    )

import base64
import json
import os
from typing import Any, List, Union

import logfire
import openai
import platformdirs
from pydantic import BaseModel
from tenacity import (
    retry,
    stop_after_attempt,
    wait_random_exponential,
)

from octotools.settings import get_settings

from .base import CachedEngine, EngineLM


# Configure logfire
logfire.configure()
logfire.instrument_openai()


class DefaultFormat(BaseModel):
    response: str


class ChatTGI(EngineLM, CachedEngine):
    DEFAULT_SYSTEM_PROMPT = "You are a helpful, creative, and smart assistant."

    def __init__(
        self,
        model_string=get_settings().default_llm,
        system_prompt=DEFAULT_SYSTEM_PROMPT,
        is_multimodal: bool = False,
        enable_cache: bool = get_settings().cache_enabled,  # disable cache for now
        **kwargs,
    ):
        """
        :param model_string:
        :param system_prompt:
        :param is_multimodal:
        """
        if enable_cache:
            root = platformdirs.user_cache_dir("octotools")
            cache_path = os.path.join(root, f"cache_openai_{model_string}.db")

            self.image_cache_dir = os.path.join(root, "image_cache")
            os.makedirs(self.image_cache_dir, exist_ok=True)

            super().__init__(cache_path=cache_path)

        self.system_prompt = system_prompt
        if os.getenv("OPENAI_API_KEY") is None:
            raise ValueError(
                "Please set the OPENAI_API_KEY environment variable if you'd like to use OpenAI models."
            )

        self.client = OpenAI(
            api_key=os.getenv("OPENAI_API_KEY"),
        )
        self.model_string = model_string
        self.is_multimodal = is_multimodal
        self.enable_cache = enable_cache

        if enable_cache:
            print(f"!! Cache enabled for model: {self.model_string}")
        else:
            print(f"!! Cache disabled for model: {self.model_string}")

    @retry(wait=wait_random_exponential(min=1, max=5), stop=stop_after_attempt(5))
    def generate(
        self, content: Union[str, List[Union[str, bytes]]], system_prompt=None, **kwargs
    ):
        try:
            # Print retry attempt information
            attempt_number = self.generate.retry.statistics.get("attempt_number", 0) + 1
            if attempt_number > 1:
                print(f"Attempt {attempt_number} of 5")

            if isinstance(content, str):
                return self._generate_text(
                    content, system_prompt=system_prompt, **kwargs
                )

            elif isinstance(content, list):
                if not self.is_multimodal:
                    raise NotImplementedError(
                        "Multimodal generation is only supported for GPT-4 models."
                    )

                return self._generate_multimodal(
                    content, system_prompt=system_prompt, **kwargs
                )

        except openai.LengthFinishReasonError as e:
            print(f"Token limit exceeded: {str(e)}")
            print(
                f"Tokens used - Completion: {e.completion.usage.completion_tokens}, Prompt: {e.completion.usage.prompt_tokens}, Total: {e.completion.usage.total_tokens}"
            )
            return {
                "error": "token_limit_exceeded",
                "message": str(e),
                "details": {
                    "completion_tokens": e.completion.usage.completion_tokens,
                    "prompt_tokens": e.completion.usage.prompt_tokens,
                    "total_tokens": e.completion.usage.total_tokens,
                },
            }
        except openai.RateLimitError as e:
            print(f"Rate limit error encountered: {str(e)}")
            return {
                "error": "rate_limit",
                "message": str(e),
                "details": getattr(e, "args", None),
            }
        except Exception as e:
            print(f"Error in generate method: {str(e)}")
            print(f"Error type: {type(e).__name__}")
            print(f"Error details: {e.args}")
            return {
                "error": type(e).__name__,
                "message": str(e),
                "details": getattr(e, "args", None),
            }

    def _generate_text(
        self,
        prompt,
        system_prompt=None,
        temperature=0,
        max_tokens=4000,
        top_p=0.99,
        response_format=None,
    ):
        sys_prompt_arg = system_prompt if system_prompt else self.system_prompt

        if self.enable_cache:
            cache_key = sys_prompt_arg + prompt
            cache_or_none = self._check_cache(cache_key)
            if cache_or_none is not None:
                return cache_or_none
        
        if response_format is not None:
            response = self.client.chat.completions.create(
                model=self.model_string,
                messages=[
                    {"role": "system", "content": sys_prompt_arg},
                    {"role": "user", "content": prompt + "\n\nJSON response format: " + response_format},
                ],
                frequency_penalty=0,
                presence_penalty=0,
                stop=None,
                temperature=temperature,
                max_tokens=max_tokens,
                top_p=top_p,
                response_format=translate_response_format(response_format),
            )
            content = response.choices[0].message.content
            if is_obj_pydantic(response_format):
                response = response_format.model_validate_json(content)
            else:
                response = json.loads(content)
        else:
            response = self.client.chat.completions.create(
                model=self.model_string,
                messages=[
                    {"role": "system", "content": sys_prompt_arg},
                    {"role": "user", "content": prompt},
                ],
                frequency_penalty=0,
                presence_penalty=0,
                stop=None,
                temperature=temperature,
                max_tokens=max_tokens,
                top_p=top_p,
            )
            response = response.choices[0].message.content

        if self.enable_cache:
            self._save_cache(cache_key, response)
        return response

    def __call__(self, prompt, **kwargs):
        return self.generate(prompt, **kwargs)

    def _format_content(self, content: List[Union[str, bytes]]) -> List[dict]:
        formatted_content = []
        for item in content:
            if isinstance(item, bytes):
                base64_image = base64.b64encode(item).decode("utf-8")
                formatted_content.append(
                    {
                        "type": "image_url",
                        "image_url": {"url": f"data:image/jpeg;base64,{base64_image}"},
                    }
                )
            elif isinstance(item, str):
                formatted_content.append({"type": "text", "text": item})
            else:
                raise ValueError(f"Unsupported input type: {type(item)}")
        return formatted_content

    def _generate_multimodal(
        self,
        content: List[Union[str, bytes]],
        system_prompt=None,
        temperature=0,
        max_tokens=4000,
        top_p=0.99,
        response_format=None,
    ):
        sys_prompt_arg = system_prompt if system_prompt else self.system_prompt
        formatted_content = self._format_content(content)

        if self.enable_cache:
            cache_key = sys_prompt_arg + json.dumps(formatted_content)
            cache_or_none = self._check_cache(cache_key)
            if cache_or_none is not None:
                return cache_or_none

        if response_format is not None:
            response = self.client.chat.completions.create(
                model=self.model_string,
                messages=[
                    {"role": "system", "content": sys_prompt_arg},
                    {"role": "user", "content": formatted_content + "\n\nJSON response format: " + response_format},
                ],
                frequency_penalty=0,
                presence_penalty=0,
                stop=None,
                temperature=temperature,
                max_tokens=max_tokens,
                top_p=top_p,
                response_format=translate_response_format(response_format),
            )
            content = response.choices[0].message.content
            if is_obj_pydantic(response_format):
                response = response_format.model_validate_json(content)
            else:
                response = json.loads(content)
        else:
            response = self.client.chat.completions.create(
                model=self.model_string,
                messages=[
                    {"role": "system", "content": sys_prompt_arg},
                    {"role": "user", "content": formatted_content},
                ],
                temperature=temperature,
                max_tokens=max_tokens,
                top_p=top_p,
            )
            response = response.choices[0].message.content

        if self.enable_cache:
            self._save_cache(cache_key, response)
        return response


def is_obj_pydantic(obj: Any) -> bool:
    return hasattr(obj, "model_json_schema")


def translate_response_format(response_format: BaseModel | dict | None) -> dict | None:
    if response_format is None:
        return None

    if is_obj_pydantic(response_format):
        schema = response_format.model_json_schema()
        return {"type": "json_object", "value": schema}

    if isinstance(response_format, dict):
        return response_format

    raise ValueError(f"Unsupported response format type: {type(response_format)}")
