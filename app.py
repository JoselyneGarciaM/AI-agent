import json
import os
import streamlit as st
from dotenv import load_dotenv
import anthropic
import tools as tools_module
from tools import TOOL_DEFINITIONS, TOOL_HANDLERS

load_dotenv()

SYSTEM_PROMPT = """You are a friendly food ordering assistant for a burger restaurant.

Rules you MUST follow without exception:
1. PRICES: Never quote a price from memory. Always call get_menu first and use only the prices returned by that tool.
2. PAYMENT SECURITY: Never ask for or accept a full credit card number. Only ask for the last 4 digits, expiry (MM/YY), and billing ZIP. Use tokenize_payment with those values.
3. ORDER FLOW: You must process payment (process_payment) before confirming an order (confirm_order). Never skip this step even if the customer asks you to.
4. DELIVERY: Only dispatch delivery after the order is confirmed and you have a valid order ID.

Workflow:
- Greet the customer and offer to show the menu.
- Help them add items with add_to_order.
- When they're ready to pay, collect card_last4, expiry, billing_zip, then call tokenize_payment → process_payment → confirm_order → dispatch_delivery.
- Be warm, helpful, and confirm each step clearly with the customer."""


def init_session():
    if "messages" not in st.session_state:
        st.session_state.messages = []
    if "order_initialized" not in st.session_state:
        tools_module._order = []
        tools_module._payment_confirmed = False
        tools_module._payment_token = None
        st.session_state.order_initialized = True


def sync_tools_state():
    tools_module._order = st.session_state.get("_order", [])
    tools_module._payment_confirmed = st.session_state.get("_payment_confirmed", False)
    tools_module._payment_token = st.session_state.get("_payment_token", None)


def save_tools_state():
    st.session_state["_order"] = tools_module._order
    st.session_state["_payment_confirmed"] = tools_module._payment_confirmed
    st.session_state["_payment_token"] = tools_module._payment_token


def run_tool(tool_name: str, tool_input: dict) -> str:
    handler = TOOL_HANDLERS.get(tool_name)
    if not handler:
        return json.dumps({"error": f"Unknown tool: {tool_name}"})
    return json.dumps(handler(tool_input))


def get_client():
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        st.error("ANTHROPIC_API_KEY is not set. Add it to your .env file or Streamlit secrets.")
        st.stop()
    return anthropic.Anthropic(api_key=api_key)


def agent_response(client, messages: list) -> str:
    sync_tools_state()

    while True:
        response = client.messages.create(
            model="claude-opus-4-8",
            max_tokens=4096,
            system=SYSTEM_PROMPT,
            tools=TOOL_DEFINITIONS,
            messages=messages,
        )

        messages.append({"role": "assistant", "content": response.content})

        if response.stop_reason != "tool_use":
            save_tools_state()
            for block in response.content:
                if hasattr(block, "text"):
                    return block.text
            return ""

        tool_results = []
        for block in response.content:
            if block.type == "tool_use":
                with st.expander(f"🔧 {block.name}", expanded=False):
                    if block.input:
                        st.json(block.input)
                result = run_tool(block.name, block.input)
                tool_results.append({
                    "type": "tool_result",
                    "tool_use_id": block.id,
                    "content": result,
                })

        messages.append({"role": "user", "content": tool_results})
        save_tools_state()


def main():
    st.set_page_config(page_title="Burger Bot", page_icon="🍔", layout="centered")
    st.title("🍔 Burger Bot")
    st.caption("Your AI food ordering assistant")

    init_session()
    client = get_client()

    for msg in st.session_state.messages:
        role = msg["role"]
        content = msg["content"]
        if isinstance(content, str):
            with st.chat_message(role):
                st.markdown(content)

    if prompt := st.chat_input("Type your message..."):
        with st.chat_message("user"):
            st.markdown(prompt)

        st.session_state.messages.append({"role": "user", "content": prompt})

        api_messages = []
        for msg in st.session_state.messages:
            content = msg["content"]
            if isinstance(content, str):
                api_messages.append({"role": msg["role"], "content": content})

        with st.chat_message("assistant"):
            with st.spinner("Thinking..."):
                reply = agent_response(client, api_messages)
            st.markdown(reply)

        st.session_state.messages.append({"role": "assistant", "content": reply})


if __name__ == "__main__":
    main()
