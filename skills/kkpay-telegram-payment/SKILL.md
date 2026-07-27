---
name: kkpay-telegram-payment
description: "Use when adding a Telegram membership recharge/payment menu, USDT/TRX direct collection, or an existing hosted payment provider to a bot. Provides the @mtp22bot-style two-column plan picker, premium custom emoji buttons, durable order workflow, and a Telethon template backed by kkpay-client."
metadata:
  short-description: "Telegram membership payment UI and premium emoji template"
---

# KKPay Telegram Payment

Use this skill for a Telegram membership/recharge page with the compact payment UI
shown below. It is a **presentation and workflow template**; prices, provider, and
fulfillment remain owned by the target bot.

## UI contract

Render the plan menu by editing the current text message when possible:

```text
› OKPay 会员充值

请选择套餐：

• 月卡：38 USDT
• 季卡：88 USDT
• 半年卡：158 USDT
• 年卡：268 USDT

点击套餐后会直接生成 OKPay 支付订单。

[💕 月卡 38 USDT] [💕 季卡 88 USDT]
[💕 半年卡 158 USDT] [💕 年卡 268 USDT]
[↩ 返回]
```

- Keep two plan buttons per row and one full-width Back button.
- Treat the displayed plans as defaults only; read the target project's plan and
  pricing settings before changing a live bot.
- Preserve the target's callback prefix/state machine. The bundled template uses
  `member:plan:<plan>` only as a safe example.
- Telegram's coloured chat wallpaper belongs to the user's client theme; the bot
  controls the message layout, inline buttons, and premium emoji only.

## Premium custom emoji

The reference template uses these Telegram custom-emoji document IDs:

| Use | ID | Telethon form |
| --- | ---: | --- |
| plan / payment | `5438224604499819092` | `icon=PAYMENT_BUTTON_ICON_ID` |
| back | `6039539366177541657` | `icon=BACK_BUTTON_ICON_ID` |

For button icons, use `Button.inline(..., icon=<integer>)` or
`Button.url(..., icon=<integer>)`; never put `<tg-emoji>` inside button text.
For message text or captions, use an HTML tag with a normal-emoji fallback, for
example `<tg-emoji emoji-id="5438224604499819092">💕</tg-emoji>`, and set
`parse_mode="html"`.

## Payment path selection

1. **USDT-TRC20 or TRX direct collection:** use `DirectPaymentService` or
   `AsyncDirectPaymentService` from this repository. Reuse its SQLite ledger,
   unique exact amount, chain verification, and `poll_payment`/`poll_pending`
   fulfillment lease. Do not reimplement a QR, transaction matcher, or order
   ledger.
2. **Hosted provider such as OKPay:** retain this UI, but call the target
   project's already configured provider adapter after the user chooses a plan.
   Do not invent or copy a provider signing protocol into this SDK.
3. Save a pending order before showing a payment URL or QR. Fulfill only after
   verified payment, idempotently; provider callbacks need a polling fallback
   where the provider does not retry callbacks.

## Integration workflow

1. Inspect the target bot's current payment handlers, order storage, prices, and
   PM2 process before editing it.
2. Copy `assets/telethon_membership_payment.py` into the target project or adapt
   its `payment_menu_text`, `payment_menu_buttons`, and `handle_plan_selection`
   helpers.
3. Implement the injected `create_order(user_id, plan)` adapter in the target
   project. It must create and persist the local pending order, then return its
   ID, URL, and optional expiry.
4. Wire the `member:plan:*` callbacks, acknowledge callbacks silently, and
   return with `event.edit` rather than sending duplicate menus.
5. Test plan layout, premium icons, duplicate callbacks, expired/cancelled
   orders, and exactly-once fulfillment. Never commit tokens, `.env`, SQLite
   order data, session files, or private addresses.

## Template

`assets/telethon_membership_payment.py` is a provider-neutral Telethon template.
It deliberately contains no bot token, private key, receiver address, merchant
credential, or provider-specific signing code. Compile it after copying:

```bash
python3 -m py_compile telethon_membership_payment.py
```
