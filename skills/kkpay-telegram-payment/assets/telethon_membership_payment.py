"""Provider-neutral Telethon membership payment menu.

Copy this file into a Telegram bot, then implement ``create_order`` with the
target project's payment provider and durable local order store.  For direct
USDT/TRX collection, that adapter should use kkpay.DirectPaymentService.
"""

from __future__ import annotations

import html
from dataclasses import dataclass
from decimal import Decimal
from typing import Awaitable, Callable, Sequence

from telethon import Button


# Premium custom-emoji document IDs used by the @mtp22bot-style reference UI.
PAYMENT_BUTTON_ICON_ID = 5438224604499819092
BACK_BUTTON_ICON_ID = 6039539366177541657
PAYMENT_TITLE_EMOJI_HTML = (
    '<tg-emoji emoji-id="5438224604499819092">💕</tg-emoji>'
)


@dataclass(frozen=True)
class MembershipPlan:
    key: str
    name: str
    days: int
    amount: Decimal


@dataclass(frozen=True)
class PaymentOrder:
    """Return this only after the target bot has persisted a pending order."""

    order_id: str
    payment_url: str
    expires_at: str | None = None


DEFAULT_PLANS: tuple[MembershipPlan, ...] = (
    MembershipPlan("month", "月卡", 30, Decimal("38")),
    MembershipPlan("quarter", "季卡", 90, Decimal("88")),
    MembershipPlan("half", "半年卡", 180, Decimal("158")),
    MembershipPlan("year", "年卡", 365, Decimal("268")),
)

# The adapter must persist a unique pending order before returning PaymentOrder.
OrderCreator = Callable[[int, MembershipPlan], Awaitable[PaymentOrder]]


def format_amount(amount: Decimal) -> str:
    """Render 38, 38.5, and 38.25 without float conversion."""
    text = format(amount, "f")
    return (text.rstrip("0").rstrip(".") or "0") if "." in text else text


def _find_plan(plan_key: str, plans: Sequence[MembershipPlan]) -> MembershipPlan | None:
    return next((plan for plan in plans if plan.key == plan_key), None)


def payment_menu_text(
    plans: Sequence[MembershipPlan] = DEFAULT_PLANS,
    *,
    provider_label: str = "OKPay",
    include_title_emoji: bool = False,
) -> str:
    """Build the compact @mtp22bot-style membership plan page."""
    provider = html.escape(provider_label)
    title_icon = f"{PAYMENT_TITLE_EMOJI_HTML} " if include_title_emoji else ""
    lines = [
        f"{title_icon}<b>› {provider} 会员充值</b>",
        "",
        "请选择套餐：",
        "",
    ]
    lines.extend(
        f"• {html.escape(plan.name)}：<code>{format_amount(plan.amount)}</code> USDT"
        for plan in plans
    )
    lines.extend(["", f"点击套餐后会直接生成 {provider} 支付订单。"])
    return "\n".join(lines)


def payment_menu_buttons(
    plans: Sequence[MembershipPlan] = DEFAULT_PLANS,
    *,
    back_data: bytes = b"menu:main",
) -> list[list[Button]]:
    """Two plans per row, then a full-width custom-emoji Back button."""
    plan_buttons = [
        Button.inline(
            f"{plan.name} {format_amount(plan.amount)} USDT",
            f"member:plan:{plan.key}".encode(),
            icon=PAYMENT_BUTTON_ICON_ID,
        )
        for plan in plans
    ]
    rows = [plan_buttons[index : index + 2] for index in range(0, len(plan_buttons), 2)]
    rows.append([Button.inline("返回", back_data, icon=BACK_BUTTON_ICON_ID)])
    return rows


def payment_order_text(plan: MembershipPlan, order: PaymentOrder) -> str:
    """Show an order without treating a generated payment URL as payment proof."""
    expires = (
        f"\n• 请在 <code>{html.escape(order.expires_at)}</code> 前完成支付"
        if order.expires_at
        else ""
    )
    return (
        f"{PAYMENT_TITLE_EMOJI_HTML} <b>› 确认充值</b>\n\n"
        f"• 套餐：<code>{html.escape(plan.name)}</code>\n"
        f"• 周期：<code>{plan.days}天</code>\n"
        f"• 支付金额：<code>{format_amount(plan.amount)}</code> USDT\n"
        f"• 订单号：<code>{html.escape(order.order_id)}</code>"
        f"{expires}\n\n"
        "支付完成后将自动开通；请勿重复创建订单。"
    )


def payment_order_buttons(order: PaymentOrder) -> list[list[Button]]:
    return [
        [
            Button.url(
                "点击支付",
                order.payment_url,
                icon=PAYMENT_BUTTON_ICON_ID,
            )
        ],
        [Button.inline("取消订单", f"member:cancel:{order.order_id}".encode())],
    ]


async def show_payment_menu(
    event,
    *,
    plans: Sequence[MembershipPlan] = DEFAULT_PLANS,
    provider_label: str = "OKPay",
    back_data: bytes = b"menu:main",
) -> None:
    """Edit the current menu rather than sending an extra membership page."""
    await event.edit(
        payment_menu_text(plans, provider_label=provider_label),
        buttons=payment_menu_buttons(plans, back_data=back_data),
        parse_mode="html",
    )


async def handle_plan_selection(
    event,
    *,
    user_id: int,
    plan_key: str,
    create_order: OrderCreator,
    plans: Sequence[MembershipPlan] = DEFAULT_PLANS,
) -> PaymentOrder | None:
    """Acknowledge the callback, then create one durable order and show its URL."""
    plan = _find_plan(plan_key, plans)
    if plan is None:
        await event.answer("套餐不存在")
        return None

    await event.answer()
    order = await create_order(user_id, plan)
    await event.edit(
        payment_order_text(plan, order),
        buttons=payment_order_buttons(order),
        parse_mode="html",
    )
    return order
