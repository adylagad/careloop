"""
Chat Protocol implementation — required for ASI:One discoverability (Track 1).
See: https://fetch.ai/docs/concepts/agent-services/chat-protocol
"""
from uagents import Context, Model, Protocol
from uagents_core.contrib.protocols.chat import (
    ChatAcknowledgement,
    ChatMessage,
    TextContent,
)


chat_proto = Protocol(name="AgentChatProtocol", version="0.3.0")


@chat_proto.on_message(ChatMessage)
async def handle_chat(ctx: Context, sender: str, msg: ChatMessage):
    ack = ChatAcknowledgement(timestamp=msg.timestamp, acknowledged_msg_id=msg.msg_id)
    await ctx.send(sender, ack)

    for item in msg.content:
        if hasattr(item, "text"):
            ctx.logger.info(f"Chat from {sender}: {item.text}")
            reply = ChatMessage(
                timestamp=msg.timestamp,
                msg_id=msg.msg_id,
                content=[TextContent(type="text", text=f"Careloop: {item.text}")],
            )
            await ctx.send(sender, reply)


@chat_proto.on_message(ChatAcknowledgement)
async def handle_ack(ctx: Context, sender: str, msg: ChatAcknowledgement):
    ctx.logger.info(f"Ack from {sender} for msg {msg.acknowledged_msg_id}")
