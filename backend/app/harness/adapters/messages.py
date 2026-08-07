"""canonical 消息模型（内部统一格式，协议无关）

TECH_SPEC §4：block 风格——text / thinking / toolCall 独立 block，
工具参数用对象。贯穿 agent 循环 / 压缩管线 / DB 边界，协议转换
只发生在 streaming 边界（adapters 层）。

与 DB 列映射（Message 表不变）：
- user / assistant（纯文本）→ role + content
- assistant（含 toolCall block）→ role + content + tool_calls 列（OpenAI 形状 JSON）
- toolResult → role="tool" + content + tool_call_id
"""

from typing import Literal, NotRequired, TypedDict, TypeGuard


class SystemMessage(TypedDict):
    role: Literal["system"]
    content: str


class TextBlock(TypedDict):
    type: Literal["text"]
    text: str


class ThinkingBlock(TypedDict):
    """DeepSeek reasoning_content 的对应物（推理模型思考过程）"""
    type: Literal["thinking"]
    thinking: str


class ToolCallBlock(TypedDict):
    """工具调用请求。arguments 用对象（Anthropic input 风格），to_openai 时序列化为字符串"""
    type: Literal["toolCall"]
    id: str
    name: str
    arguments: dict


class UserMessage(TypedDict):
    role: Literal["user"]
    content: str | list[TextBlock]  # DB 来的原始文本是 str，直接透传


class AssistantMessage(TypedDict):
    role: Literal["assistant"]
    content: list[TextBlock | ThinkingBlock | ToolCallBlock]  # 三种 block 可共存


class ToolResultMessage(TypedDict):
    role: Literal["toolResult"]
    tool_call_id: str
    tool_name: NotRequired[str]  # agent 回填必带；DB 回读按 tool_call_id 匹配，匹配不到缺省
    content: str
    is_error: NotRequired[bool]  # 默认 False；agent 回填时映射 tool_status == "error"


Message = SystemMessage | UserMessage | AssistantMessage | ToolResultMessage

# content block 联合（assistant.content 的元素类型）
Block = TextBlock | ThinkingBlock | ToolCallBlock


# ── Block 类型守卫（TypedDict 联合收窄，Pyright 无法从 b["type"] 自动收窄）──


def is_text_block(b: Block) -> TypeGuard[TextBlock]:
    return b.get("type") == "text"


def is_thinking_block(b: Block) -> TypeGuard[ThinkingBlock]:
    return b.get("type") == "thinking"


def is_tool_call_block(b: Block) -> TypeGuard[ToolCallBlock]:
    return b.get("type") == "toolCall"
