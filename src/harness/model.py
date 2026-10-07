from copy import deepcopy
from dataclasses import dataclass
from typing import Protocol
import httpx
import ollama

from .config import ModelConfig


@dataclass
class ToolCall:
    name: str
    arguments: object


@dataclass
class ModelReply:
    content: str
    tool_calls: list[ToolCall]


class ModelError(Exception):
    pass


class ModelClient(Protocol):
    def chat(self, messages: list[dict], tools: list[dict]) -> ModelReply: ...


class OllamaModel:
    def __init__(self, cfg: ModelConfig):
        self.cfg = cfg
        self.client = ollama.Client(host=cfg.host)

    def chat(self, messages, tools):
        try:
            response = self.client.chat(model=self.cfg.name, messages=messages, tools=tools,
                                        think=self.cfg.think,
                                        options={'temperature': self.cfg.temperature, 'num_ctx': self.cfg.num_ctx})
        except (ollama.ResponseError, httpx.HTTPError, ConnectionError) as error:
            raise ModelError(str(error)) from error
        return ModelReply(response.message.content or '', [
            ToolCall(call.function.name, call.function.arguments)
            for call in response.message.tool_calls or []])


class ScriptedModel:
    def __init__(self, replies: list[ModelReply | Exception], repeat_last=False):
        self.replies = list(replies)
        self.repeat_last = repeat_last
        self.last = None
        self.calls: list[list[dict]] = []

    def chat(self, messages, tools):
        self.calls.append(deepcopy(messages))
        if self.replies:
            item = self.replies.pop(0)
        elif self.repeat_last and self.last is not None:
            item = self.last
        else:
            item = ModelReply('(script exhausted)', [])
        if isinstance(item, Exception):
            raise item
        self.last = item
        return item
