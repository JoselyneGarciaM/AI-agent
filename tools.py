import json
import uuid
import re
from menu import MENU

_order: list[dict] = []
_payment_confirmed = False
_payment_token: str | None = None


def get_menu() -> dict:
    result = {}
    for category, items in MENU.items():
        result[category] = {
            name: {"price": f"${data['price']:.2f}", "description": data["description"]}
            for name, data in items.items()
        }
    return {"menu": result, "note": "Prices are fixed. Do not quote any price not listed here."}


def add_to_order(item_name: str, quantity: int = 1) -> dict:
    for category, items in MENU.items():
        if item_name in items:
            unit_price = items[item_name]["price"]
            _order.append({
                "item": item_name,
                "quantity": quantity,
                "unit_price": unit_price,
                "subtotal": round(unit_price * quantity, 2),
            })
            total = round(sum(i["subtotal"] for i in _order), 2)
            return {
                "success": True,
                "added": item_name,
                "quantity": quantity,
                "unit_price": f"${unit_price:.2f}",
                "order_total": f"${total:.2f}",
                "current_order": _order.copy(),
            }
    return {"success": False, "error": f"'{item_name}' not found on the menu. Use get_menu to see available items."}


def view_order() -> dict:
    total = round(sum(i["subtotal"] for i in _order), 2)
    return {
        "order": _order.copy(),
        "total": f"${total:.2f}",
        "item_count": len(_order),
    }


def tokenize_payment(card_last4: str, expiry: str, billing_zip: str) -> dict:
    """
    Accepts only non-sensitive card identifiers (last 4 digits, expiry, zip).
    Never accepts full card numbers. Returns an opaque payment token.
    """
    if not re.fullmatch(r"\d{4}", card_last4):
        return {"success": False, "error": "Provide only the last 4 digits of the card, not the full number."}
    if not re.fullmatch(r"(0[1-9]|1[0-2])/\d{2}", expiry):
        return {"success": False, "error": "Expiry must be in MM/YY format."}
    if not re.fullmatch(r"\d{5}", billing_zip):
        return {"success": False, "error": "Billing ZIP must be 5 digits."}

    token = f"tok_{uuid.uuid4().hex[:16]}"
    return {
        "success": True,
        "payment_token": token,
        "card_last4": card_last4,
        "message": "Card tokenized. Use this token to process payment — the system never stores or sees full card numbers.",
    }


def process_payment(payment_token: str) -> dict:
    global _payment_confirmed, _payment_token
    if not _order:
        return {"success": False, "error": "Cannot process payment: the order is empty."}
    if not payment_token.startswith("tok_"):
        return {"success": False, "error": "Invalid payment token. Tokenize the card first with tokenize_payment."}

    total = round(sum(i["subtotal"] for i in _order), 2)
    # Simulate payment authorization
    _payment_confirmed = True
    _payment_token = payment_token
    return {
        "success": True,
        "amount_charged": f"${total:.2f}",
        "payment_token": payment_token,
        "status": "AUTHORIZED",
        "message": "Payment authorized. You may now confirm the order.",
    }


def confirm_order() -> dict:
    if not _payment_confirmed:
        return {
            "success": False,
            "error": "Order cannot be confirmed without a successful payment. Process payment first.",
        }
    if not _order:
        return {"success": False, "error": "Cannot confirm an empty order."}

    order_id = f"ORD-{uuid.uuid4().hex[:8].upper()}"
    total = round(sum(i["subtotal"] for i in _order), 2)
    return {
        "success": True,
        "order_id": order_id,
        "confirmed_items": _order.copy(),
        "total": f"${total:.2f}",
        "payment_token": _payment_token,
        "message": "Order confirmed! Ready to dispatch.",
    }


def dispatch_delivery(order_id: str, delivery_address: str) -> dict:
    if not _payment_confirmed:
        return {"success": False, "error": "Cannot dispatch: payment has not been confirmed."}
    if not order_id.startswith("ORD-"):
        return {"success": False, "error": "Invalid order ID. Confirm the order first."}
    if not delivery_address.strip():
        return {"success": False, "error": "Delivery address is required."}

    eta_minutes = 30
    tracking_id = f"TRK-{uuid.uuid4().hex[:8].upper()}"
    return {
        "success": True,
        "tracking_id": tracking_id,
        "order_id": order_id,
        "delivery_address": delivery_address,
        "estimated_delivery": f"{eta_minutes} minutes",
        "message": f"Your order is on its way! Track it with {tracking_id}.",
    }


TOOL_DEFINITIONS = [
    {
        "name": "get_menu",
        "description": "Returns the full menu with all available items and their exact prices. Always call this before quoting prices — never invent or assume prices.",
        "input_schema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "add_to_order",
        "description": "Adds a menu item to the current order. Only accepts items that exist on the menu.",
        "input_schema": {
            "type": "object",
            "properties": {
                "item_name": {"type": "string", "description": "Exact name of the menu item"},
                "quantity": {"type": "integer", "description": "Number of this item to add", "minimum": 1},
            },
            "required": ["item_name"],
        },
    },
    {
        "name": "view_order",
        "description": "Returns the current order contents and running total.",
        "input_schema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "tokenize_payment",
        "description": (
            "Tokenizes the customer's card using only non-sensitive identifiers: last 4 digits, expiry (MM/YY), "
            "and billing ZIP. NEVER ask for or accept the full card number. Returns a payment token."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "card_last4": {"type": "string", "description": "Last 4 digits of the card only"},
                "expiry": {"type": "string", "description": "Card expiry in MM/YY format"},
                "billing_zip": {"type": "string", "description": "5-digit billing ZIP code"},
            },
            "required": ["card_last4", "expiry", "billing_zip"],
        },
    },
    {
        "name": "process_payment",
        "description": "Processes payment using a payment token (from tokenize_payment). Must be called before confirming the order.",
        "input_schema": {
            "type": "object",
            "properties": {
                "payment_token": {"type": "string", "description": "Token from tokenize_payment"},
            },
            "required": ["payment_token"],
        },
    },
    {
        "name": "confirm_order",
        "description": "Confirms the order. Only succeeds if payment has been processed. Never call this before process_payment succeeds.",
        "input_schema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "dispatch_delivery",
        "description": "Dispatches the confirmed order for delivery. Requires a confirmed order ID and a delivery address.",
        "input_schema": {
            "type": "object",
            "properties": {
                "order_id": {"type": "string", "description": "Order ID from confirm_order"},
                "delivery_address": {"type": "string", "description": "Full delivery address"},
            },
            "required": ["order_id", "delivery_address"],
        },
    },
]

TOOL_HANDLERS = {
    "get_menu": lambda args: get_menu(),
    "add_to_order": lambda args: add_to_order(args["item_name"], args.get("quantity", 1)),
    "view_order": lambda args: view_order(),
    "tokenize_payment": lambda args: tokenize_payment(args["card_last4"], args["expiry"], args["billing_zip"]),
    "process_payment": lambda args: process_payment(args["payment_token"]),
    "confirm_order": lambda args: confirm_order(),
    "dispatch_delivery": lambda args: dispatch_delivery(args["order_id"], args["delivery_address"]),
}
