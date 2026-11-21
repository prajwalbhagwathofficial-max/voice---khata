import { LedgerSummary } from "./protocol";
import { formatInr, formatInrRupees } from "./format";

interface LandingPageProps {
  onLaunchApp: (mode?: "home" | "you_gave" | "you_got") => void;
  summary: LedgerSummary;
  totalDebtors: number;
}

export function LandingPage({ onLaunchApp, summary, totalDebtors }: LandingPageProps) {
  const scrollToSection = (id: string) => {
    const el = document.getElementById(id);
    if (el) {
      el.scrollIntoView({ behavior: "smooth" });
    }
  };

  return (
    <div className="nb-landing">
      {/* 1. FIXED HEADER / NAVIGATION */}
      <header className="nb-nav">
        <div className="nb-nav-container">
          {/* Left: Logo with 10x10 (40px) black square icon with #ffe17c bolt */}
          <div className="nb-logo-wrap" onClick={() => scrollToSection("hero")}>
            <div className="nb-logo-icon" aria-hidden="true">
              <svg width="22" height="22" viewBox="0 0 24 24" fill="none">
                <path
                  d="M13 2L3 14H12L11 22L21 10H12L13 2Z"
                  fill="#ffe17c"
                  stroke="#ffe17c"
                  strokeWidth="1.5"
                  strokeLinecap="round"
                  strokeLinejoin="round"
                />
              </svg>
            </div>
            <div className="nb-logo-text">
              <span className="nb-brand-title">VOICE KHATA</span>
              <span className="nb-brand-badge">PRO 2.0</span>
            </div>
          </div>

          {/* Center: Horizontal links in bold Satoshi */}
          <nav className="nb-nav-links" aria-label="Landing navigation">
            <button type="button" className="nb-nav-link" onClick={() => scrollToSection("features")}>
              Features
            </button>
            <button type="button" className="nb-nav-link" onClick={() => scrollToSection("problem-solution")}>
              Why Us
            </button>
            <button type="button" className="nb-nav-link" onClick={() => scrollToSection("how-it-works")}>
              How It Works
            </button>
            <button type="button" className="nb-nav-link" onClick={() => scrollToSection("personas")}>
              Who It's For
            </button>
          </nav>

          {/* Right: Action Buttons */}
          <div className="nb-nav-actions">
            <button
              type="button"
              className="nb-push-btn nb-btn-black"
              onClick={() => onLaunchApp("home")}
              title="Open Live Khata Dashboard"
            >
              Open Ledger 📊
            </button>
          </div>
        </div>
      </header>

      {/* 2. HERO SECTION */}
      <section id="hero" className="nb-hero-section">
        <div className="nb-hero-grid">
          {/* Left Column: High-Contrast Neo-Brutalist Card */}
          <div
            className="nb-hero-left"
            style={{
              background: "rgba(255, 255, 255, 0.96)",
              backdropFilter: "blur(16px)",
              WebkitBackdropFilter: "blur(16px)",
              border: "3px solid #000000",
              borderRadius: "1.25rem",
              padding: "36px 38px",
              boxShadow: "10px 10px 0px 0px #000000",
            }}
          >
            <div className="nb-pill-badge">
              <span className="nb-badge-dot"></span>
              NEW: AI VOICE ASSISTANT 2.0
            </div>

            <h1 className="nb-hero-title">
              VOICE-FIRST{" "}
              <span
                className="nb-stroke-word"
                style={{
                  background: "#000000",
                  color: "#ffe17c",
                  WebkitTextStroke: "0px",
                  padding: "2px 12px",
                  borderRadius: "8px",
                  display: "inline-block",
                  boxShadow: "3px 3px 0px 0px #000000",
                }}
              >
                MISTAKE-RESISTANT
              </span>{" "}
              LEDGER FOR KIRANA
            </h1>

            <p
              className="nb-hero-subtitle"
              style={{
                color: "#171e19",
                background: "rgba(255, 225, 124, 0.28)",
                borderLeft: "4px solid #000000",
                padding: "14px 18px",
                borderRadius: "0 10px 10px 0",
                fontSize: "16px",
                lineHeight: "1.6",
                marginBottom: "26px",
              }}
            >
              Speech recognition is a component; <strong>trustworthy transaction execution</strong> is the product.
              Deterministic code owns money, identity checks, commits, and balance arithmetic. Zero hallucinations.
            </p>

            <div className="nb-cta-group">
              <button
                type="button"
                className="nb-push-btn nb-btn-black nb-hero-cta"
                onClick={() => onLaunchApp("home")}
              >
                Explore Live Demo 🎙️
              </button>
            </div>

            <div className="nb-hero-tags">
              <span className="nb-tag-item">✓ 100% Integer Paise Math</span>
              <span className="nb-tag-item">✓ Hindi, Telugu, Kannada, English</span>
              <span className="nb-tag-item">✓ WhatsApp Receipts</span>
            </div>
          </div>

          {/* Right Column: Browser Mockup Dashboard */}
          <div className="nb-hero-right">
            <div className="nb-browser-mockup">
              {/* Header Bar */}
              <div className="nb-browser-header">
                <div className="nb-browser-dots">
                  <span className="nb-dot nb-dot-red"></span>
                  <span className="nb-dot nb-dot-yellow"></span>
                  <span className="nb-dot nb-dot-green"></span>
                </div>
                <div className="nb-browser-address">
                  🔒 voice-khata.local/dashboard/live-store
                </div>
                <div className="nb-browser-status">
                  <span className="nb-pulse-live"></span> LIVE
                </div>
              </div>

              {/* Content Area with Sage & Charcoal cards */}
              <div className="nb-browser-content">
                {/* Charcoal Card */}
                <div className="nb-mockup-card nb-card-charcoal">
                  <div className="nb-mockup-card-head">
                    <span className="nb-mockup-tag">LEDGER OVERVIEW</span>
                    <span className="nb-mockup-pill-green">SYNCED</span>
                  </div>
                  <div className="nb-mockup-stats-row">
                    <div className="nb-mockup-stat">
                      <span className="nb-stat-label">Total Outstanding</span>
                      <span className="nb-stat-val nb-val-yellow">
                        {summary.total_owed > 0 ? formatInr(summary.total_owed) : "₹42,850"}
                      </span>
                    </div>
                    <div className="nb-mockup-stat">
                      <span className="nb-stat-label">Received Today</span>
                      <span className="nb-stat-val nb-val-sage">
                        {summary.total_received_today > 0 ? formatInrRupees(summary.total_received_today) : "₹8,400"}
                      </span>
                    </div>
                    <div className="nb-mockup-stat">
                      <span className="nb-stat-label">Active Debtors</span>
                      <span className="nb-stat-val nb-val-white">
                        {totalDebtors > 0 ? totalDebtors : 14} Customers
                      </span>
                    </div>
                  </div>
                </div>

                {/* Sage Card */}
                <div className="nb-mockup-card nb-card-sage">
                  <div className="nb-mockup-card-head">
                    <span className="nb-mockup-tag nb-tag-dark">VOICE STREAM PREVIEW</span>
                    <span className="nb-mockup-audio-pill">🎙️ Gnani Prisma v3.0</span>
                  </div>
                  <div className="nb-mockup-chat-bubble">
                    <div className="nb-bubble-user">
                      <span className="nb-bubble-lang">HI</span> &ldquo;Ramesh ko 500 rupaye udhaar diya&rdquo;
                    </div>
                    <div className="nb-bubble-system">
                      <span className="nb-system-badge">✓ PARSED INTENT</span>
                      <span>CREDIT_GIVEN &bull; Ramesh &bull; ₹500 &bull; Committed to SQLite</span>
                    </div>
                  </div>
                </div>

                {/* Quick Interactive Button in Mockup */}
                <button
                  type="button"
                  className="nb-mockup-btn"
                  onClick={() => onLaunchApp("you_got")}
                >
                  <span>View All Customer Payments &rarr;</span>
                  <span className="nb-mockup-btn-badge">1-Click Live View</span>
                </button>
              </div>
            </div>
          </div>
        </div>
      </section>

      {/* 3. SOCIAL PROOF MARQUEE */}
      <section className="nb-marquee-section">
        <div className="nb-marquee-track">
          <div className="nb-marquee-content">
            <span className="nb-marquee-item">ACME TRADERS</span>
            <span className="nb-marquee-sep">✦</span>
            <span className="nb-marquee-item">GLOBEX KIRANA</span>
            <span className="nb-marquee-sep">✦</span>
            <span className="nb-marquee-item">APNA SUPER BAZAAR</span>
            <span className="nb-marquee-sep">✦</span>
            <span className="nb-marquee-item">KIRANA HUB INDIA</span>
            <span className="nb-marquee-sep">✦</span>
            <span className="nb-marquee-item">RELIANCE RETRO</span>
            <span className="nb-marquee-sep">✦</span>
            <span className="nb-marquee-item">SHARMA PROVISIONS</span>
            <span className="nb-marquee-sep">✦</span>
            <span className="nb-marquee-item">INITECH RETAIL</span>
            <span className="nb-marquee-sep">✦</span>
            <span className="nb-marquee-item">DESI WHOLESALE</span>
            <span className="nb-marquee-sep">✦</span>
            <span className="nb-marquee-item">UMBRELLA STORE</span>
            <span className="nb-marquee-sep">✦</span>
          </div>
          <div className="nb-marquee-content" aria-hidden="true">
            <span className="nb-marquee-item">ACME TRADERS</span>
            <span className="nb-marquee-sep">✦</span>
            <span className="nb-marquee-item">GLOBEX KIRANA</span>
            <span className="nb-marquee-sep">✦</span>
            <span className="nb-marquee-item">APNA SUPER BAZAAR</span>
            <span className="nb-marquee-sep">✦</span>
            <span className="nb-marquee-item">KIRANA HUB INDIA</span>
            <span className="nb-marquee-sep">✦</span>
            <span className="nb-marquee-item">RELIANCE RETRO</span>
            <span className="nb-marquee-sep">✦</span>
            <span className="nb-marquee-item">SHARMA PROVISIONS</span>
            <span className="nb-marquee-sep">✦</span>
            <span className="nb-marquee-item">INITECH RETAIL</span>
            <span className="nb-marquee-sep">✦</span>
            <span className="nb-marquee-item">DESI WHOLESALE</span>
            <span className="nb-marquee-sep">✦</span>
            <span className="nb-marquee-item">UMBRELLA STORE</span>
            <span className="nb-marquee-sep">✦</span>
          </div>
        </div>
      </section>

      {/* 4. PROBLEM VS SOLUTION */}
      <section id="problem-solution" className="nb-problem-solution-section">
        <div className="nb-section-head">
          <div className="nb-section-badge">COMPARISON</div>
          <h2 className="nb-section-title">OLD PAPER DIARIES VS VOICE KHATA</h2>
          <p className="nb-section-sub">
            Why traditional notebook ledgers lose you ₹15,000+ each month in uncollected credit and forgotten entries.
          </p>
        </div>

        <div className="nb-pvs-grid">
          {/* Card A: Problem */}
          <div className="nb-pvs-card nb-card-problem">
            <div className="nb-pvs-header">
              <span className="nb-pvs-icon-badge nb-badge-red">✗</span>
              <div>
                <h3 className="nb-pvs-title">Old Paper Notebooks &amp; Manual Apps</h3>
                <span className="nb-pvs-tag">The Slow, Leaky Way</span>
              </div>
            </div>
            <ul className="nb-pvs-list">
              <li>
                <span className="nb-pvs-x">✗</span>
                <span><strong>Lost Pages &amp; Spilled Chai:</strong> Paper diaries tear, fade, and get lost forever.</span>
              </li>
              <li>
                <span className="nb-pvs-x">✗</span>
                <span><strong>Mental Math Errors:</strong> Adding ₹230, ₹440, and ₹180 in your head during peak rush hours leads to heavy losses.</span>
              </li>
              <li>
                <span className="nb-pvs-x">✗</span>
                <span><strong>Customer Disputes:</strong> Customers say &ldquo;I already paid yesterday&rdquo; with zero proof or timestamp.</span>
              </li>
              <li>
                <span className="nb-pvs-x">✗</span>
                <span><strong>Painful Keyboard Typing:</strong> Typing long customer names on tiny touchscreen keyboards takes 2 minutes per entry.</span>
              </li>
              <li>
                <span className="nb-pvs-x">✗</span>
                <span><strong>Silent AI Hallucinations:</strong> Generic chat assistants guess money amounts when noise occurs.</span>
              </li>
            </ul>
          </div>

          {/* Card B: Solution */}
          <div className="nb-pvs-card nb-card-solution">
            <div className="nb-pvs-header">
              <span className="nb-pvs-icon-badge nb-badge-black">✓</span>
              <div>
                <h3 className="nb-pvs-title">Voice Khata (Neo-Brutalist Engine)</h3>
                <span className="nb-pvs-tag nb-tag-highlight">The Deterministic Voice Way</span>
              </div>
            </div>
            <ul className="nb-pvs-list">
              <li>
                <span className="nb-pvs-check">✓</span>
                <span><strong>Speak in 2 Seconds:</strong> Just tap and say &ldquo;Suresh 350 udhaar&rdquo; in Hindi, Telugu, Kannada, or English.</span>
              </li>
              <li>
                <span className="nb-pvs-check">✓</span>
                <span><strong>100% Deterministic Arithmetic:</strong> Stored strictly in integer paise. No floats, zero arithmetic errors.</span>
              </li>
              <li>
                <span className="nb-pvs-check">✓</span>
                <span><strong>Permanent SQLite Ledger:</strong> Append-only database with transaction audit logs, atomic reversals, and direct delete.</span>
              </li>
              <li>
                <span className="nb-pvs-check">✓</span>
                <span><strong>Instant WhatsApp Receipts:</strong> Generate gentle, compliant payment receipts with one click without pressure tactics.</span>
              </li>
              <li>
                <span className="nb-pvs-check">✓</span>
                <span><strong>Clarification First:</strong> If ambient audio is ambiguous, the system asks rather than guessing your money.</span>
              </li>
            </ul>
          </div>
        </div>
      </section>

      {/* 5. FEATURE GRID */}
      <section id="features" className="nb-features-section">
        <div className="nb-section-head">
          <div className="nb-section-badge">POWERFUL FEATURES</div>
          <h2 className="nb-section-title">ENGINEERED FOR KIRANA SPEED</h2>
          <p className="nb-section-sub">
            Built with extreme precision for high-traffic corner stores, wholesale grain merchants, and retail counters.
          </p>
        </div>

        <div className="nb-feature-grid">
          {/* Card 1 */}
          <div className="nb-feature-card">
            <div className="nb-feature-icon-box">🎙️</div>
            <h3 className="nb-feature-title">Ultra-Fast Voice Input</h3>
            <p className="nb-feature-desc">
              Powered by Gnani Prisma v3.0 STT with custom audio streaming over WebSockets. Sub-500ms voice processing speed.
            </p>
          </div>

          {/* Card 2 */}
          <div className="nb-feature-card">
            <div className="nb-feature-icon-box">🛡️</div>
            <h3 className="nb-feature-title">Deterministic Money Guard</h3>
            <p className="nb-feature-desc">
              AI models propose intent; deterministic code validates customer identity, balance math, and commits to permanent disk.
            </p>
          </div>

          {/* Card 3 */}
          <div className="nb-feature-card">
            <div className="nb-feature-icon-box">📊</div>
            <h3 className="nb-feature-title">You Gave vs You Got</h3>
            <p className="nb-feature-desc">
              Clean separation of daily credit given versus payments collected, organized by date with running totals.
            </p>
          </div>

          {/* Card 4 */}
          <div className="nb-feature-card">
            <div className="nb-feature-icon-box">🗑️</div>
            <h3 className="nb-feature-title">Direct Database Delete</h3>
            <p className="nb-feature-desc">
              Full control over entries. Delete mistakes directly from SQLite with one click and automatic balance reconciliation.
            </p>
          </div>

          {/* Card 5 */}
          <div className="nb-feature-card">
            <div className="nb-feature-icon-box">🌐</div>
            <h3 className="nb-feature-title">Native Indic Languages</h3>
            <p className="nb-feature-desc">
              Seamlessly handles code-mixed Hindi, Kannada number systems, Telugu phrasing, and English colloquial terms.
            </p>
          </div>

          {/* Card 6 */}
          <div className="nb-feature-card">
            <div className="nb-feature-icon-box">🧾</div>
            <h3 className="nb-feature-title">Itemised Bills &amp; Receipts</h3>
            <p className="nb-feature-desc">
              Attach camera photos, PDFs, and line items (e.g. Atta 5kg, Rice 10kg) with consent-guarded customer messaging.
            </p>
          </div>
        </div>
      </section>

      {/* 6. HOW IT WORKS */}
      <section id="how-it-works" className="nb-how-section">
        <div className="nb-section-head nb-head-dark">
          <div className="nb-section-badge nb-badge-sage">SIMPLE 3-STEP PIPELINE</div>
          <h2 className="nb-section-title text-white">HOW VOICE KHATA WORKS</h2>
          <p className="nb-section-sub text-sage">
            From your voice in the store to a locked database record in milliseconds.
          </p>
        </div>

        <div className="nb-steps-container">
          <div className="nb-steps-line" aria-hidden="true"></div>

          <div className="nb-steps-grid">
            {/* Step 1 */}
            <div className="nb-step-item">
              <div className="nb-step-circle nb-circle-sage">
                <span className="nb-step-num">01</span>
              </div>
              <h3 className="nb-step-title">Speak Your Entry</h3>
              <p className="nb-step-desc">
                Hold the mic and say &ldquo;Ramesh 500 rupaye udhaar&rdquo; or &ldquo;Suresh ne 200 diye&rdquo; in your natural tongue.
              </p>
            </div>

            {/* Step 2 */}
            <div className="nb-step-item">
              <div className="nb-step-circle nb-circle-yellow">
                <span className="nb-step-num">02</span>
              </div>
              <h3 className="nb-step-title">Deterministic Check</h3>
              <p className="nb-step-desc">
                Rule engine validates the customer alias, exact rupee amount, and rejects ambiguity or pressure language.
              </p>
            </div>

            {/* Step 3 */}
            <div className="nb-step-item">
              <div className="nb-step-circle nb-circle-white">
                <span className="nb-step-num">03</span>
              </div>
              <h3 className="nb-step-title">Committed &amp; Synced</h3>
              <p className="nb-step-desc">
                Atomic SQLite commit locks the transaction. Outstanding balances and daily totals update instantly on screen.
              </p>
            </div>
          </div>
        </div>
      </section>

      {/* 7. USE CASE PERSONAS */}
      <section id="personas" className="nb-personas-section">
        <div className="nb-section-head">
          <div className="nb-section-badge">TARGET PERSONAS</div>
          <h2 className="nb-section-title">BUILT FOR EVERY RETAIL PROFILE</h2>
          <p className="nb-section-sub">
            From high-turnover grocery stalls to bulk commodities and daily dairy vendors.
          </p>
        </div>

        <div className="nb-personas-grid">
          {/* Card 1: Sage */}
          <div className="nb-persona-card nb-persona-sage">
            <div className="nb-persona-pill">RETAIL GROCERY</div>
            <h3 className="nb-persona-title">Corner Kirana Stores</h3>
            <p className="nb-persona-desc">
              Dealing with 150+ rapid walk-in customers every morning and evening. No time to unlock phones, search apps, and type amounts on keyboards.
            </p>
            <div className="nb-persona-footer">
              <span className="nb-persona-highlight">⚡ 2-second voice credit entry</span>
            </div>
          </div>

          {/* Card 2: Yellow (Hero Persona) */}
          <div className="nb-persona-card nb-persona-yellow">
            <div className="nb-persona-pill">WHOLESALE MERCHANTS</div>
            <h3 className="nb-persona-title">Grain, Spices &amp; Oil Traders</h3>
            <p className="nb-persona-desc">
              Managing large credit tickets (₹5,000 to ₹1,00,000+) across regular buyers with complex itemised bill receipts and frequent partial payments.
            </p>
            <div className="nb-persona-footer">
              <span className="nb-persona-highlight">🛡️ 100% accurate integer paise accounting</span>
            </div>
          </div>

          {/* Card 3: Dark Charcoal */}
          <div className="nb-persona-card nb-persona-charcoal">
            <div className="nb-persona-pill nb-pill-dark">DAILY ESSENTIALS</div>
            <h3 className="nb-persona-title text-white">Milk, Dairy &amp; Bakeries</h3>
            <p className="nb-persona-desc text-sage">
              Daily micro-transactions where customers pay weekly or monthly. Instant tracking of daily milk pouches and loafs with gentle WhatsApp summaries.
            </p>
            <div className="nb-persona-footer">
              <span className="nb-persona-highlight nb-hl-sage">📋 Daily payments ledger with 1-click delete</span>
            </div>
          </div>
        </div>
      </section>

      {/* 8. FINAL CTA */}
      <section className="nb-final-cta-section">
        <div className="nb-final-cta-box">
          <div className="nb-pill-badge">NO CREDIT CARD NEEDED &bull; 100% FREE DEMO</div>
          <h2 className="nb-final-title">
            START RECORDING YOUR KHATA IN <span className="nb-stroke-word">SECONDS</span>
          </h2>
          <p className="nb-final-sub">
            Join hundreds of retail kiranas running faster, mistake-resistant ledgers with voice.
          </p>

          <div className="nb-cta-group nb-cta-center">
            <button
              type="button"
              className="nb-push-btn nb-btn-black nb-hero-cta"
              onClick={() => onLaunchApp("you_gave")}
            >
              Open Voice Khata App ⚡
            </button>
            <button
              type="button"
              className="nb-push-btn nb-btn-white"
              onClick={() => onLaunchApp("home")}
            >
              View Store Dashboard 📊
            </button>
          </div>
        </div>
      </section>

      {/* 10. FOOTER */}
      <footer className="nb-footer">
        <div className="nb-footer-grid">
          {/* Col 1 */}
          <div className="nb-footer-col">
            <div className="nb-logo-wrap mb-4">
              <div className="nb-logo-icon" aria-hidden="true">
                <svg width="22" height="22" viewBox="0 0 24 24" fill="none">
                  <path
                    d="M13 2L3 14H12L11 22L21 10H12L13 2Z"
                    fill="#ffe17c"
                    stroke="#ffe17c"
                    strokeWidth="1.5"
                    strokeLinecap="round"
                    strokeLinejoin="round"
                  />
                </svg>
              </div>
              <div className="nb-logo-text">
                <span className="nb-brand-title text-white">VOICE KHATA</span>
                <span className="nb-brand-badge">KIRANA PS2</span>
              </div>
            </div>
            <p className="nb-footer-desc">
              Voice-first, mistake-resistant ledger for Indian retail stores. Deterministic math, zero pressure tactics, trusted transactions.
            </p>
            <div className="nb-social-icons">
              <a href="#hero" className="nb-social-btn" title="Twitter / X" onClick={(e) => { e.preventDefault(); scrollToSection("hero"); }}>
                𝕏
              </a>
              <a href="#hero" className="nb-social-btn" title="GitHub" onClick={(e) => { e.preventDefault(); scrollToSection("hero"); }}>
                ⌘
              </a>
              <a href="#hero" className="nb-social-btn" title="LinkedIn" onClick={(e) => { e.preventDefault(); scrollToSection("hero"); }}>
                in
              </a>
              <a href="#hero" className="nb-social-btn" title="Discord" onClick={(e) => { e.preventDefault(); scrollToSection("hero"); }}>
                💬
              </a>
            </div>
          </div>

          {/* Col 2 */}
          <div className="nb-footer-col">
            <h4 className="nb-footer-heading">PRODUCT</h4>
            <ul className="nb-footer-links">
              <li><button type="button" onClick={() => onLaunchApp("home")}>Dashboard Overview</button></li>
              <li><button type="button" onClick={() => onLaunchApp("you_gave")}>You Gave (उधार दिया)</button></li>
              <li><button type="button" onClick={() => onLaunchApp("you_got")}>You Got (जमा मिला)</button></li>
              <li><button type="button" onClick={() => scrollToSection("features")}>Features &amp; Tech</button></li>
            </ul>
          </div>

          {/* Col 3 */}
          <div className="nb-footer-col">
            <h4 className="nb-footer-heading">ARCHITECTURE</h4>
            <ul className="nb-footer-links">
              <li><span>Gnani Prisma v3.0 STT</span></li>
              <li><span>Gnani Timbre v3.0 TTS</span></li>
              <li><span>Deterministic Rule Engine</span></li>
              <li><span>SQLite Append-Only Ledger</span></li>
              <li><span>Pure WebSocket Data Flow</span></li>
            </ul>
          </div>

          {/* Col 4 */}
          <div className="nb-footer-col">
            <h4 className="nb-footer-heading">COMPLIANCE</h4>
            <ul className="nb-footer-links">
              <li><span>No Telephony / No Spam</span></li>
              <li><span>No Lending / Pressure Terms</span></li>
              <li><span>AI Disclosure On 1st Turn</span></li>
              <li><span>Zero Plaintext PII Logging</span></li>
              <li><span>Integer Paise Standard</span></li>
            </ul>
          </div>
        </div>

        <div className="nb-footer-bottom">
          <p>© 2026 Voice Khata. Built with Neo-Brutalist Architecture for Kirana Stores. All rights reserved.</p>
          <div className="nb-footer-bottom-links">
            <button type="button" onClick={() => onLaunchApp("home")}>App Live Status: OK</button>
            <span className="nb-dot-sep">&bull;</span>
            <button type="button" onClick={() => scrollToSection("hero")}>Back to Top ↑</button>
          </div>
        </div>
      </footer>
    </div>
  );
}
