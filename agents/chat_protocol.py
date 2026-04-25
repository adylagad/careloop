"""Reusable ASI:One-compatible Chat Protocol helpers."""
from collections.abc import Awaitable, Callable
from datetime import datetime
from inspect import isawaitable
from uuid import uuid4

from uagents import Context, Protocol
from uagents_core.contrib.protocols.chat import (
    ChatAcknowledgement,
    ChatMessage,
    EndSessionContent,
    StartSessionContent,
    ResourceContent,
    TextContent,
    chat_protocol_spec,
)


ChatResponder = Callable[[Context, str, str], str | Awaitable[str]]


def create_text_chat(text: str, end_session: bool = True) -> ChatMessage:
    content = [TextContent(type="text", text=text)]
    if end_session:
        content.append(EndSessionContent(type="end-session"))
    return ChatMessage(timestamp=datetime.utcnow(), msg_id=uuid4(), content=content)


def create_chat_protocol(agent_label: str, responder: ChatResponder) -> Protocol:
    chat_proto = Protocol(spec=chat_protocol_spec)

    @chat_proto.on_message(ChatMessage)
    async def handle_chat(ctx: Context, sender: str, msg: ChatMessage):
        await ctx.send(
            sender,
            ChatAcknowledgement(
                timestamp=datetime.utcnow(),
                acknowledged_msg_id=msg.msg_id,
            ),
        )

        text_chunks: list[str] = []
        resource_chunks: list[str] = []
        for item in msg.content:
            if isinstance(item, StartSessionContent):
                ctx.logger.info(f"{agent_label}: session started by {sender}")
            elif isinstance(item, TextContent):
                text_chunks.append(item.text)
            elif isinstance(item, ResourceContent):
                resources = item.resource if isinstance(item.resource, list) else [item.resource]
                for resource in resources:
                    resource_chunks.append(
                        f"uri={resource.uri} metadata={resource.metadata}"
                    )
            elif isinstance(item, EndSessionContent):
                ctx.logger.info(f"{agent_label}: session ended by {sender}")
            else:
                ctx.logger.info(f"{agent_label}: ignored content type {type(item).__name__}")

        if not text_chunks and not resource_chunks:
            return

        user_text = "\n".join(text_chunks + resource_chunks)
        response = responder(ctx, sender, user_text)
        if isawaitable(response):
            response = await response
        await ctx.send(sender, create_text_chat(str(response)))

    @chat_proto.on_message(ChatAcknowledgement)
    async def handle_ack(ctx: Context, sender: str, msg: ChatAcknowledgement):
        ctx.logger.info(
            f"{agent_label}: acknowledgement from {sender} for {msg.acknowledged_msg_id}"
        )

    return chat_proto
