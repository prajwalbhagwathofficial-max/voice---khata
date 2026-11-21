"""Real, tamper-evident HTML receipt renderer for VoiceKhata transactions."""

from __future__ import annotations

import html
from datetime import datetime, timezone
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from server.ledger.models import BillRecord, EntryItemRecord, LedgerEventRecord


def format_inr(paise: int) -> str:
    """Format paise as INR rupees string."""
    rupees = paise / 100
    if paise % 100 == 0:
        return f"{int(rupees):,}"
    return f"{rupees:,.2f}"


def format_timestamp(epoch_ms: int) -> str:
    """Format epoch milliseconds to readable IST / shop-local time."""
    try:
        dt = datetime.fromtimestamp(epoch_ms / 1000, tz=timezone.utc)
        # Asia/Kolkata is UTC+5:30
        from datetime import timedelta
        ist_dt = dt + timedelta(hours=5, minutes=30)
        return ist_dt.strftime("%d %b %Y, %I:%M %p")
    except Exception:
        return "Recent"


def render_receipt_html(
    event: LedgerEventRecord,
    customer_name: str,
    balance_inr: int,
    items: list[EntryItemRecord] | None = None,
    bills: list[BillRecord] | None = None,
    shop_name: str = "Voice Khata Kirana Store",
) -> str:
    """Render a standalone, beautifully styled Neo-Brutalist digital ledger receipt."""
    safe_customer = html.escape(customer_name)
    safe_shop = html.escape(shop_name)
    safe_event_id = html.escape(event.event_id)
    safe_date_phrase = html.escape(event.date_phrase or "")
    safe_note = html.escape(event.note or "")
    
    amount_str = format_inr(event.amount_paise)
    balance_str = f"{balance_inr:,}"
    time_str = format_timestamp(event.ts)
    
    is_credit = event.type.value in ("CREDIT_GIVEN", "CREDIT")
    type_badge_text = "उधार दिया (Credit Added to Ledger)" if is_credit else "जमा मिला (Payment Received)"
    type_color = "#ff5f57" if is_credit else "#28c840"
    heading_text = "आपका उधार VoiceKhata पर जोड़ दिया गया है" if is_credit else "आपकी जमा राशि VoiceKhata पर दर्ज कर ली गई है"
    subheading_text = "Your Udhar has been successfully added on VoiceKhata" if is_credit else "Your Payment has been successfully recorded on VoiceKhata"
    
    # Items HTML
    items_html = ""
    if items and len(items) > 0:
        items_rows = ""
        total_items_paise = 0
        for it in items:
            total_items_paise += it.amount_paise
            qty = f" ({html.escape(it.qty_text)})" if it.qty_text else ""
            items_rows += f"""
            <tr>
              <td><strong>{html.escape(it.name)}</strong>{qty}</td>
              <td class="num">₹{it.amount_inr:,}</td>
            </tr>
            """
        items_html = f"""
        <div class="receipt-section">
          <div class="receipt-section-title">📦 Itemised Bill / सामान की सूची ({len(items)})</div>
          <table class="receipt-items-table">
            <thead>
              <tr>
                <th>Item / सामान</th>
                <th class="num">Amount / राशि</th>
              </tr>
            </thead>
            <tbody>
              {items_rows}
            </tbody>
            <tfoot>
              <tr>
                <td><strong>Items Total / कुल सामान</strong></td>
                <td class="num"><strong>₹{total_items_paise // 100:,}</strong></td>
              </tr>
            </tfoot>
          </table>
        </div>
        """

    # Bills HTML
    bills_html = ""
    if bills and len(bills) > 0:
        bills_links = ""
        for b in bills:
            bills_links += f"""
            <div class="receipt-bill-preview">
              <img src="/receipt/{safe_event_id}/bill/{html.escape(b.id)}" alt="Attached Bill" class="bill-img" />
              <div class="bill-caption">📎 Attached Paper Bill / चालान पर्ची</div>
            </div>
            """
        bills_html = f"""
        <div class="receipt-section">
          <div class="receipt-section-title">📎 Attached Bill Photo / पर्ची</div>
          {bills_links}
        </div>
        """

    return f"""<!doctype html>
<html lang="hi">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>Voice Khata Receipt • {safe_customer} ₹{amount_str}</title>
  
  <!-- Open Graph for WhatsApp, SMS & Social Previews -->
  <meta property="og:title" content="Voice Khata Receipt: {safe_customer} — ₹{amount_str} Udhar" />
  <meta property="og:description" content="{heading_text} • {safe_shop} • तारीख: {time_str}" />
  <meta property="og:type" content="website" />
  
  <link rel="preconnect" href="https://api.fontshare.com" />
  <link href="https://api.fontshare.com/v2/css?f[]=cabinet-grotesk@800,700,500,400&f[]=satoshi@700,500,400&display=swap" rel="stylesheet" />
  
  <style>
    :root {{
      --primary: #ffe17c;
      --charcoal: #171e19;
      --bg: #faf8ee;
      --border: #000000;
      --red: #ff5f57;
      --green: #28c840;
    }}
    * {{
      box-sizing: border-box;
      margin: 0;
      padding: 0;
    }}
    body {{
      font-family: 'Satoshi', -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
      background-color: var(--bg);
      background-image: radial-gradient(rgba(0, 0, 0, 0.08) 1.5px, transparent 1.5px);
      background-size: 28px 28px;
      color: #000000;
      min-height: 100vh;
      display: flex;
      flex-direction: column;
      align-items: center;
      padding: 20px 16px 60px 16px;
    }}
    .receipt-container {{
      width: 100%;
      max-width: 540px;
      margin: 0 auto;
      display: flex;
      flex-direction: column;
      gap: 16px;
    }}
    /* TOPBAR */
    .receipt-topbar {{
      display: flex;
      align-items: center;
      justify-content: space-between;
      background-color: var(--primary);
      border: 2px solid var(--border);
      border-radius: 8px;
      padding: 12px 18px;
      box-shadow: 4px 4px 0 0 var(--border);
    }}
    .brand-wrap {{
      display: flex;
      align-items: center;
      gap: 10px;
    }}
    .brand-icon {{
      width: 32px;
      height: 32px;
      background: #000;
      border-radius: 6px;
      display: flex;
      align-items: center;
      justify-content: center;
      box-shadow: 2px 2px 0 0 #000;
    }}
    .brand-title {{
      font-family: 'Cabinet Grotesk', sans-serif;
      font-size: 18px;
      font-weight: 900;
      letter-spacing: -0.03em;
    }}
    .verified-pill {{
      font-family: 'Cabinet Grotesk', sans-serif;
      font-size: 11px;
      font-weight: 800;
      background: #fff;
      border: 1.5px solid #000;
      border-radius: 999px;
      padding: 3px 8px;
      box-shadow: 2px 2px 0 0 #000;
    }}
    /* SUCCESS BANNER */
    .status-banner {{
      background-color: #28c840;
      border: 2px solid var(--border);
      border-radius: 8px;
      padding: 16px 20px;
      box-shadow: 4px 4px 0 0 var(--border);
      color: #000000;
      text-align: center;
    }}
    .status-icon {{
      font-size: 28px;
      margin-bottom: 4px;
    }}
    .status-title {{
      font-family: 'Cabinet Grotesk', sans-serif;
      font-size: 18px;
      font-weight: 900;
      line-height: 1.25;
      margin-bottom: 4px;
    }}
    .status-sub {{
      font-size: 13px;
      font-weight: 600;
      opacity: 0.9;
    }}
    /* MAIN RECEIPT CARD */
    .receipt-card {{
      background: #ffffff;
      border: 2px solid var(--border);
      border-top: 6px solid {type_color};
      border-radius: 10px;
      padding: 24px 22px;
      box-shadow: 6px 6px 0 0 var(--border);
      display: flex;
      flex-direction: column;
      gap: 20px;
    }}
    .store-header {{
      display: flex;
      align-items: flex-start;
      justify-content: space-between;
      border-bottom: 2px solid var(--border);
      padding-bottom: 14px;
    }}
    .store-name {{
      font-family: 'Cabinet Grotesk', sans-serif;
      font-size: 20px;
      font-weight: 900;
      color: #000000;
    }}
    .store-tag {{
      font-size: 12px;
      color: #52525b;
      margin-top: 2px;
    }}
    .type-pill {{
      font-family: 'Cabinet Grotesk', sans-serif;
      font-size: 11px;
      font-weight: 800;
      background-color: {type_color};
      color: #000;
      padding: 4px 10px;
      border: 1.5px solid #000;
      border-radius: 6px;
      box-shadow: 2px 2px 0 0 #000;
      white-space: nowrap;
    }}
    /* MAIN AMOUNT BOX */
    .amount-display-box {{
      background-color: #faf8ee;
      border: 2px solid var(--border);
      border-radius: 8px;
      padding: 16px 20px;
      box-shadow: 3px 3px 0 0 var(--border);
      text-align: center;
    }}
    .amount-label {{
      font-family: 'Cabinet Grotesk', sans-serif;
      font-size: 12px;
      font-weight: 800;
      letter-spacing: 0.06em;
      color: #71717a;
      text-transform: uppercase;
    }}
    .amount-val {{
      font-family: 'Cabinet Grotesk', sans-serif;
      font-size: 38px;
      font-weight: 900;
      color: {type_color};
      line-height: 1.1;
      margin: 6px 0;
    }}
    .amount-caption {{
      font-size: 12px;
      color: #52525b;
      font-weight: 600;
    }}
    /* DETAILS GRID */
    .details-grid {{
      display: grid;
      grid-template-columns: 1fr 1fr;
      gap: 12px;
    }}
    .detail-item {{
      background: #f4f4f5;
      border: 1.5px solid var(--border);
      border-radius: 6px;
      padding: 10px 12px;
    }}
    .detail-label {{
      font-family: 'Cabinet Grotesk', sans-serif;
      font-size: 11px;
      font-weight: 800;
      color: #71717a;
      text-transform: uppercase;
      letter-spacing: 0.04em;
    }}
    .detail-val {{
      font-family: 'Cabinet Grotesk', sans-serif;
      font-size: 15px;
      font-weight: 900;
      color: #000000;
      margin-top: 3px;
    }}
    .balance-box {{
      grid-column: 1 / -1;
      background: #fff8e1;
      border: 2px solid var(--border);
      border-radius: 8px;
      padding: 12px 14px;
      display: flex;
      align-items: center;
      justify-content: space-between;
      box-shadow: 2px 2px 0 0 var(--border);
    }}
    .balance-val {{
      font-family: 'Cabinet Grotesk', sans-serif;
      font-size: 20px;
      font-weight: 900;
      color: #ff5f57;
    }}
    /* SECTIONS */
    .receipt-section {{
      display: flex;
      flex-direction: column;
      gap: 8px;
    }}
    .receipt-section-title {{
      font-family: 'Cabinet Grotesk', sans-serif;
      font-size: 13px;
      font-weight: 800;
      color: #000000;
    }}
    .receipt-items-table {{
      width: 100%;
      border-collapse: collapse;
      border: 1.5px solid var(--border);
      border-radius: 6px;
      overflow: hidden;
      font-size: 13px;
    }}
    .receipt-items-table th {{
      background: #171e19;
      color: #fff;
      padding: 8px 12px;
      text-align: left;
      font-family: 'Cabinet Grotesk', sans-serif;
      font-size: 12px;
      font-weight: 800;
    }}
    .receipt-items-table td {{
      padding: 8px 12px;
      border-bottom: 1px solid #e4e4e7;
    }}
    .receipt-items-table tfoot td {{
      background: #faf8ee;
      border-top: 1.5px solid var(--border);
      border-bottom: none;
    }}
    .num {{
      text-align: right;
    }}
    .receipt-bill-preview {{
      border: 1.5px solid var(--border);
      border-radius: 8px;
      overflow: hidden;
      background: #f4f4f5;
      text-align: center;
    }}
    .bill-img {{
      max-width: 100%;
      max-height: 280px;
      object-fit: contain;
      display: block;
      margin: 0 auto;
    }}
    .bill-caption {{
      padding: 8px;
      font-size: 11px;
      font-weight: 700;
      color: #52525b;
      border-top: 1px solid #e4e4e7;
      background: #ffffff;
    }}
    /* FOOTER & ACTIONS */
    .receipt-footer-box {{
      background: #faf8ee;
      border: 1.5px solid var(--border);
      border-radius: 6px;
      padding: 10px 12px;
      font-size: 11px;
      color: #52525b;
      line-height: 1.4;
      display: flex;
      flex-direction: column;
      gap: 4px;
    }}
    .receipt-id-row {{
      font-family: ui-monospace, monospace;
      font-size: 10px;
      word-break: break-all;
    }}
    .receipt-actions-row {{
      display: flex;
      gap: 10px;
    }}
    .receipt-btn {{
      flex: 1;
      display: inline-flex;
      align-items: center;
      justify-content: center;
      gap: 6px;
      padding: 12px 14px;
      border: 2px solid var(--border);
      border-radius: 8px;
      font-family: 'Cabinet Grotesk', sans-serif;
      font-size: 14px;
      font-weight: 800;
      cursor: pointer;
      box-shadow: 4px 4px 0 0 var(--border);
      text-decoration: none;
      color: #000000;
      transition: all 0.15s ease;
    }}
    .receipt-btn:hover {{
      transform: translate(2px, 2px);
      box-shadow: 2px 2px 0 0 var(--border);
    }}
    .btn-print {{
      background: #ffffff;
    }}
    .btn-whatsapp-share {{
      background: #28c840;
    }}
    @media print {{
      body {{
        background: #fff;
        padding: 0;
      }}
      .receipt-actions-row, .receipt-topbar {{
        display: none !important;
      }}
      .receipt-card {{
        box-shadow: none;
        border: 1px solid #000;
      }}
    }}
  </style>
</head>
<body>
  <div class="receipt-container">
    <header class="receipt-topbar">
      <div class="brand-wrap">
        <div class="brand-icon">
          <svg width="18" height="18" viewBox="0 0 24 24" fill="none">
            <path d="M13 2L3 14H12L11 22L21 10H12L13 2Z" fill="#ffe17c" stroke="#171e19" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" />
          </svg>
        </div>
        <div class="brand-title">Voice Khata</div>
      </div>
      <div class="verified-pill">✓ Ledger Verified</div>
    </header>

    <div class="status-banner">
      <div class="status-icon">✅</div>
      <div class="status-title">{heading_text}</div>
      <div class="status-sub">{subheading_text}</div>
    </div>

    <main class="receipt-card">
      <div class="store-header">
        <div>
          <h1 class="store-name">{safe_shop}</h1>
          <div class="store-tag">Official Digital Ledger Receipt / डिजिटल बहीखाता रसीद</div>
        </div>
        <div class="type-pill">{type_badge_text}</div>
      </div>

      <div class="amount-display-box">
        <div class="amount-label">नया उधार जोड़ा गया (Udhar Added)</div>
        <div class="amount-val">₹{amount_str}</div>
        <div class="amount-caption">दर्ज स्थिति: पुष्टि हो चुकी है (Confirmed &amp; Recorded on Ledger)</div>
      </div>

      <div class="details-grid">
        <div class="detail-item">
          <div class="detail-label">Customer / ग्राहक</div>
          <div class="detail-val">{safe_customer}</div>
        </div>
        <div class="detail-item">
          <div class="detail-label">Date &amp; Time / तारीख व समय</div>
          <div class="detail-val">{time_str}{f' &bull; {html.escape(event.effective_date)}' if event.effective_date else ''}</div>
        </div>
        <div class="balance-box">
          <div>
            <div class="detail-label">Total Outstanding Balance / कुल बाकी उधार</div>
            <div style="font-size: 11px; color: #71717a;">Total amount owed to shop</div>
          </div>
          <div class="balance-val">₹{balance_str}</div>
        </div>
      </div>

      {items_html}
      {bills_html}

      {f'<div class="receipt-section"><div class="receipt-section-title">📝 Note / विवरण</div><p style="font-size: 13px; padding: 8px 12px; background: #f4f4f5; border: 1.5px solid #000; border-radius: 6px;">{safe_note}</p></div>' if safe_note else ''}

      <div class="receipt-footer-box">
        <div>🔒 <strong>Tamper-Evident Ledger Entry:</strong> Cryptographically logged with shop-local business date Asia/Kolkata.</div>
        <div class="receipt-id-row">Transaction Hash: {safe_event_id}</div>
        <div>No customer phone numbers stored on ledger database. Verified by KhataSaathi VoiceKhata.</div>
      </div>
    </main>

    <div class="receipt-actions-row">
      <button type="button" class="receipt-btn btn-print" onclick="window.print()">
        🖨️ Print / Save PDF
      </button>
      <a href="/" class="receipt-btn btn-whatsapp-share">
        ⚡ Open Voice Khata
      </a>
    </div>
  </div>
</body>
</html>
"""


def render_receipt_not_found_html(event_id: str) -> str:
    """Render a clean error page if the receipt/event_id was not found."""
    safe_id = html.escape(event_id)
    return f"""<!doctype html>
<html lang="hi">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>Receipt Not Found • Voice Khata</title>
  <style>
    body {{
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
      background: #faf8ee;
      display: flex;
      align-items: center;
      justify-content: center;
      min-height: 100vh;
      margin: 0;
      padding: 20px;
    }}
    .card {{
      background: #fff;
      border: 2px solid #000;
      border-top: 6px solid #ff5f57;
      border-radius: 10px;
      padding: 32px 24px;
      box-shadow: 6px 6px 0 0 #000;
      max-width: 480px;
      text-align: center;
    }}
    h1 {{
      font-size: 20px;
      margin: 12px 0 6px;
    }}
    p {{
      font-size: 14px;
      color: #52525b;
      line-height: 1.5;
    }}
    .id-tag {{
      font-family: monospace;
      font-size: 11px;
      background: #f4f4f5;
      padding: 4px 8px;
      border: 1px solid #000;
      border-radius: 4px;
      margin: 12px 0;
      display: inline-block;
      word-break: break-all;
    }}
    a {{
      display: inline-block;
      margin-top: 16px;
      padding: 10px 18px;
      background: #ffe17c;
      color: #000;
      border: 2px solid #000;
      border-radius: 6px;
      text-decoration: none;
      font-weight: 800;
      box-shadow: 3px 3px 0 0 #000;
    }}
  </style>
</head>
<body>
  <div class="card">
    <div style="font-size: 36px;">⚠️</div>
    <h1>रसीद नहीं मिली / Receipt Not Found</h1>
    <p>यह लेन-देन मौजूद नहीं है या बहीखाता से निरस्त (Reversed) कर दिया गया है।</p>
    <div class="id-tag">ID: {safe_id}</div>
    <div>
      <a href="/">← Go to Voice Khata</a>
    </div>
  </div>
</body>
</html>
"""
