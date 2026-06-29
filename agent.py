import json
import os
from dotenv import load_dotenv
import anthropic
from tools import TOOL_DEFINITIONS, TOOL_HANDLERS

load_dotenv()

client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])

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


def run_tool(tool_name: str, tool_input: dict) -> str:
    handler = TOOL_HANDLERS.get(tool_name)
    if not handler:
        return json.dumps({"error": f"Unknown tool: {tool_name}"})
    result = handler(tool_input)
    return json.dumps(result)


def chat():
    print("Food Ordering Agent — type 'quit' to exit\n")
    messages: list[dict] = []

    while True:
        user_input = input("You: ").strip()
        if user_input.lower() in ("quit", "exit"):
            print("Goodbye!")
            break
        if not user_input:
            continue

        messages.append({"role": "user", "content": user_input})

        while True:
            with client.messages.stream(
                model="claude-opus-4-8",
                max_tokens=4096,
                thinking={"type": "adaptive"},
                system=SYSTEM_PROMPT,
                tools=TOOL_DEFINITIONS,
                messages=messages,
            ) as stream:
                response = stream.get_final_message()

            messages.append({"role": "assistant", "content": response.content})

            if response.stop_reason != "tool_use":
                for block in response.content:
                    if hasattr(block, "text"):
                        print(f"\nAgent: {block.text}\n")
                break

            tool_results = []
            for block in response.content:
                if block.type == "tool_use":
                    print(f"  [calling {block.name}]")
                    result = run_tool(block.name, block.input)
                    tool_results.append({
                        "type": "tool_result",
                        "tool_use_id": block.id,
                        "content": result,
                    })

            messages.append({"role": "user", "content": tool_results})


if __name__ == "__main__":
    chat()
