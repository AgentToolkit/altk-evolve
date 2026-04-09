// ── Custom Element ────────────────────────────────────────────────────────────
class GitHubRepoCard extends HTMLElement {
  static DEFAULT_TTL = 10800; // 3 hours
  static CACHE_PREFIX = "ghrc:";

  static css = (s, ...v) => String.raw({ raw: s }, ...v);
  static html = (s, ...v) => String.raw({ raw: s }, ...v);

  static STYLES = GitHubRepoCard.css`
  *, *::before, *::after { box-sizing: border-box; }

  /* ─────────────────────────────────────────────────────────────────────────
     Theme tokens — CSS custom properties pierce the Shadow DOM boundary,
     so any of these can be overridden from outside:

       github-repo-card { --ghrc-accent: hotpink; }
       <github-repo-card style="--ghrc-radius-lg: 16px">

     Alpha variants (error bg, etc.) are derived automatically via
     color-mix(), so overriding a base color cascades to all its uses.
  ───────────────────────────────────────────────────────────────────────── */
  :host {
    color-scheme: light dark;

    /* Typography */
    --ghrc-font:          -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
    --ghrc-font-size-sm:  0.75rem;
    --ghrc-font-size-md:  0.875rem;
    --ghrc-font-size-lg:  1rem;
    --ghrc-font-size-xl:  1.5rem;
    --ghrc-line-height-tight: 1;
    --ghrc-font-weight-medium: 500;
    --ghrc-font-weight-semibold: 600;
    --ghrc-font-weight-bold: 700;

    /* Surfaces */
    --ghrc-surface:       #ffffff;   /* card & input surface */

    /* Borders */
    --ghrc-border:        #d0d7de;
    --ghrc-border-subtle: #d8dee4;   /* footer divider, skeleton base */

    /* Text */
    --ghrc-text:          #57606a;
    --ghrc-text-bold:     #24292f;
    --ghrc-text-subtle:   #6e7781;

    /* Accent — links, focus rings */
    --ghrc-accent:        #58a6ff;

    /* Semantic states */
    --ghrc-success:       #3fb950;
    --ghrc-warning:       #d29922;
    --ghrc-error:         #f85149;

    /* Layout scale */
    --ghrc-space-1:       0.25rem;
    --ghrc-space-2:       0.5rem;
    --ghrc-space-3:       0.75rem;
    --ghrc-space-4:       1rem;
    --ghrc-space-5:       1.5rem;

    /* Shape */
    --ghrc-radius-sm:     4px;
    --ghrc-radius-md:     8px;
    --ghrc-radius-lg:     12px;
    --ghrc-radius-pill:   999px;
    --ghrc-radius-avatar: 50%;

    /* Sizing */
    --ghrc-card-min-height:   180px;
    --ghrc-avatar-size:       36px;
    --ghrc-stat-min-width:    112px;
    --ghrc-stat-min-height:   42px;
    --ghrc-border-width:      1px;
    --ghrc-skeleton-line-height: 16px;
    --ghrc-skeleton-updated-width: 120px;
    --ghrc-skeleton-size-width: 72px;
    --ghrc-skeleton-refresh-width: 132px;
    --ghrc-skeleton-release-width: 60px;
    --ghrc-skeleton-refresh-height: 24px;
    --ghrc-skeleton-release-height: 32px;
    --ghrc-skeleton-link-width: 128px;

    /* Motion */
    --ghrc-transition-fast:   0.15s;
    --ghrc-transition-instant: 0.1s;
    --ghrc-shimmer-duration:  1.4s;

    /* Derived alphas */
    --ghrc-state-border-alpha: 30%;
    --ghrc-state-bg-alpha:      8%;

    /* Elements */
    --ghrc-refresh-radius: var(--ghrc-radius-pill);
    --ghrc-release-radius: var(--ghrc-radius-pill);
    --ghrc-badge-radius: var(--ghrc-radius-pill);
    --ghrc-stat-icon-size: var(--ghrc-font-size-lg);

    display: block;
    font-family: var(--ghrc-font);
    color: var(--ghrc-text)
  }

  @media (prefers-color-scheme: dark) {
    :host {
      --ghrc-surface:       #161b22;
      --ghrc-border:        #30363d;
      --ghrc-border-subtle: #21262d;
      --ghrc-text:          #8b949e;
      --ghrc-text-bold:     #e6edf3;
      --ghrc-text-subtle:   #6e7681;
    }
  }

  :host([theme="light"]) {
    --ghrc-surface:       #ffffff;
    --ghrc-border:        #d0d7de;
    --ghrc-border-subtle: #d8dee4;
    --ghrc-text:          #57606a;
    --ghrc-text-bold:     #24292f;
    --ghrc-text-subtle:   #6e7781;
  }

  :host([theme="dark"]) {
    --ghrc-surface:       #161b22;
    --ghrc-border:        #30363d;
    --ghrc-border-subtle: #21262d;
    --ghrc-text:          #8b949e;
    --ghrc-text-bold:     #e6edf3;
    --ghrc-text-subtle:   #6e7681;
  }

  /* ── Card ── */
  .card {
    background: var(--ghrc-surface);
    border: var(--ghrc-border-width) solid var(--ghrc-border);
    border-radius: var(--ghrc-radius-lg);
    padding: var(--ghrc-space-5);
    position: relative;
    min-height: var(--ghrc-card-min-height);
  }

  /* ── Skeleton / loading ── */
  .loading-state {
    display: grid;
    gap: var(--ghrc-space-2);
  }

  .skeleton {
    background: linear-gradient(
      90deg,
      var(--ghrc-border-subtle) 25%,
      var(--ghrc-border)        50%,
      var(--ghrc-border-subtle) 75%
    );
    background-size: 200% 100%;
    animation: shimmer var(--ghrc-shimmer-duration) infinite;
    border-radius: var(--ghrc-radius-sm);
  }

  @keyframes shimmer {
    0%   { background-position: 200% 0; }
    100% { background-position: -200% 0; }
  }

  .sk-title  { height: 20px; width: 56%; }
  .sk-desc   { height: var(--ghrc-skeleton-line-height); width: 84%; }
  .sk-desc2  { height: var(--ghrc-skeleton-line-height); width: 60%; }
  .sk-avatar {
    width: var(--ghrc-avatar-size);
    height: var(--ghrc-avatar-size);
    border-radius: var(--ghrc-radius-avatar);
  }
  .sk-stats  {
    display: flex;
    gap: var(--ghrc-space-4);
    flex-wrap: wrap;
  }
  .sk-stat   {
    flex: 1 1 0;
    min-width: var(--ghrc-stat-min-width);
    height: var(--ghrc-stat-min-height);
    border-radius: var(--ghrc-radius-md);
  }
  .sk-footer {
    display: flex;
    justify-content: space-between;
    align-items: center;
    gap: var(--ghrc-space-2);
    margin-top: var(--ghrc-space-1);
    padding-top: var(--ghrc-space-3);
    border-top: var(--ghrc-border-width) solid var(--ghrc-border-subtle);
    flex-wrap: wrap;
  }
  .sk-footer-meta {
    display: flex;
    gap: var(--ghrc-space-2);
    align-items: center;
    flex-wrap: wrap;
  }
  .sk-footer-right {
    display: flex;
    gap: var(--ghrc-space-2);
    align-items: center;
    flex-wrap: wrap;
  }
  .sk-footer-line {
    height: var(--ghrc-skeleton-line-height);
    width: var(--ghrc-skeleton-updated-width);
  }
  .sk-footer-line.size {
    width: var(--ghrc-skeleton-size-width);
  }
  .sk-refresh {
    height: var(--ghrc-skeleton-refresh-height);
    width: var(--ghrc-skeleton-refresh-width);
    border-radius: var(--ghrc-refresh-radius);
  }
  .sk-link {
    height: var(--ghrc-skeleton-line-height);
    width: var(--ghrc-skeleton-link-width);
  }
  .sk-release {
    height: var(--ghrc-skeleton-release-height);
    width: var(--ghrc-skeleton-release-width);
    border-radius: var(--ghrc-release-radius);
    flex-shrink: 0;
  }

  /* ── Repo header ── */
  .repo-header {
    display: flex;
    align-items: flex-start;
    justify-content: space-between;
    gap: var(--ghrc-space-3);
  }

  .avatar {
    width: var(--ghrc-avatar-size);
    height: var(--ghrc-avatar-size);
    border-radius: var(--ghrc-radius-avatar);
    border: var(--ghrc-border-width) solid var(--ghrc-border);
    flex-shrink: 0;
  }

  .repo-content { display: grid; gap: var(--ghrc-space-4); }
  .repo-header-main {
    display: flex;
    align-items: flex-start;
    gap: var(--ghrc-space-3);
    flex: 1;
  }
  .repo-title-block { flex: 1; align-self: center }

  .repo-full-name {
    font-size: var(--ghrc-font-size-lg);
    font-weight: var(--ghrc-font-weight-semibold);
    color: var(--ghrc-text);
    text-decoration: none;
    display: inline-block;
    max-width: 100%;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }
  .repo-full-name:hover {
    color: var(--ghrc-accent);
  }

  .repo-full-name:focus-visible,
  .stat-value:focus-visible,
  .github-link:focus-visible,
  .cache-refresh-btn:focus-visible {
    outline: 2px solid var(--ghrc-accent);
    outline-offset: 2px;
  }

  .release-badge {
    place-self: center;
    text-align: center;
    padding: var(--ghrc-space-2) var(--ghrc-space-3);
    border-radius: var(--ghrc-radius-pill);
    border: var(--ghrc-border-width) solid color-mix(in srgb, var(--ghrc-success) 22%, var(--ghrc-border));
    background: color-mix(in srgb, var(--ghrc-success) 10%, transparent);
    color: color-mix(in srgb, var(--ghrc-success) 78%, var(--ghrc-text-bold));
    font-size: var(--ghrc-font-size-sm);
    font-weight: var(--ghrc-font-weight-semibold);
    white-space: nowrap;
    flex-shrink: 0;
  }

  .repo-description {
    font-size: var(--ghrc-font-size-md);
    line-height: 1.5;
    margin: 0;
  }

  .repo-badges {
    display: flex;
    flex-wrap: wrap;
    gap: var(--ghrc-space-2);
    align-items: center;
  }

  .repo-badges[hidden] {
    display: none;
  }

  ::slotted(*) {
    display: inline-flex;
    align-items: center;
    gap: var(--ghrc-space-1);
    min-height: 28px;
    padding: 0.35rem 0.65rem;
    border: var(--ghrc-border-width) solid var(--ghrc-border);
    border-radius: var(--ghrc-badge-radius);
    background: color-mix(in srgb, var(--ghrc-accent) 8%, transparent);
    color: var(--ghrc-text-bold);
    font-size: var(--ghrc-font-size-sm);
    line-height: 1;
    text-decoration: none;
    white-space: nowrap;
  }

  ::slotted(a) {
    color: var(--ghrc-text-bold);
    transition: border-color var(--ghrc-transition-fast), color var(--ghrc-transition-fast), background var(--ghrc-transition-fast);
  }

  ::slotted(a:hover) {
    color: var(--ghrc-accent);
    border-color: color-mix(in srgb, var(--ghrc-accent) 32%, var(--ghrc-border));
    background: color-mix(in srgb, var(--ghrc-accent) 12%, transparent);
  }

  ::slotted(img) {
    padding: 0;
    min-height: 0;
    border: none;
    background: none;
    border-radius: 0;
  }

  /* ── Stat grid ── */
  .stats-row {
    display: flex;
    gap: var(--ghrc-space-4);
  }

  .stat-box {
    display: grid;
    grid-template-columns: var(--ghrc-stat-icon-size) 1fr;
    text-align: left;
    transition: border-color var(--ghrc-transition-fast), transform var(--ghrc-transition-instant);
    min-height: var(--ghrc-stat-min-height);
    min-width: var(--ghrc-stat-min-width);
    gap: var(--ghrc-space-1);
  }

  .stat-icon {
    width: var(--ghrc-stat-icon-size);
    height: var(--ghrc-stat-icon-size);
    fill: var(--ghrc-text);
    align-self: center;
  }

  .stat-value {
    font-size: var(--ghrc-font-size-xl);
    line-height: var(--ghrc-line-height-tight);
    font-weight: var(--ghrc-font-weight-bold);
    align-self: center;
    color: var(--ghrc-text-bold);
    text-decoration: none;
    transition: color var(--ghrc-transition-fast);
  }

  .stat-value:hover {
    color: var(--ghrc-accent);
  }

  .stat-label {
    font-size: var(--ghrc-font-size-sm);
    text-transform: uppercase;
    grid-column-start: span 2
  }

  @media (max-width: 640px) and (orientation: portrait) {
    .repo-description,
    .footer-size,
    .cache-refresh-btn,
    .sk-desc,
    .sk-desc2,
    .sk-footer-line.size,
    .sk-refresh {
      display: none !important;
    }

    .sk-stats {
      display: grid;
      grid-template-columns: repeat(2, minmax(0, 1fr));
      gap: var(--ghrc-space-3);
    }

    .sk-stat {
      min-width: 0;
    }

    .stats-row {
      display: grid;
      grid-template-columns: repeat(2, minmax(0, 1fr));
      gap: var(--ghrc-space-3);
    }

    .stat-box {
      min-width: 0;
    }
  }

  /* ── Footer ── */
  .card-footer {
    padding-top: var(--ghrc-space-2);
    border-top: var(--ghrc-border-width) solid var(--ghrc-border-subtle);
    display: flex;
    justify-content: space-between;
    align-items: center;
    font-size: var(--ghrc-font-size-sm);
    color: var(--ghrc-text-subtle);
    flex-wrap: wrap;
    gap: var(--ghrc-space-1);
  }

  .footer-left {
    display: flex;
    align-items: center;
    gap: var(--ghrc-space-2);
    flex-wrap: wrap;
  }

  .footer-right {
    display: flex;
    align-items: center;
    gap: var(--ghrc-space-2);
    flex-wrap: wrap;
  }

  .github-link {
    color: var(--ghrc-text);
    text-decoration: none;
    font-weight: var(--ghrc-font-weight-medium);
    white-space: nowrap;
    transition: color var(--ghrc-transition-fast);
  }

  .github-link:hover {
    color: var(--ghrc-accent);
  }

  .cache-refresh-btn {
    display: inline-flex;
    align-items: center;
    gap: var(--ghrc-space-1);
    min-width: 132px;
    justify-content: center;
    padding: var(--ghrc-space-1) var(--ghrc-space-2);
    border-radius: var(--ghrc-radius-pill);
    font-size: var(--ghrc-font-size-sm);
    font-weight: var(--ghrc-font-weight-semibold);
    border: var(--ghrc-border-width) solid var(--ghrc-border);
    background: none;
    color: var(--ghrc-text);
    cursor: pointer;
    transition:
      color var(--ghrc-transition-fast),
      border-color var(--ghrc-transition-fast),
      background var(--ghrc-transition-fast),
      opacity var(--ghrc-transition-fast);
  }

  .cache-refresh-btn:hover {
    color: var(--ghrc-text-bold);
    border-color: var(--ghrc-text-bold);
  }

  .cache-refresh-btn.fresh {
    color: color-mix(in srgb, var(--ghrc-accent) 62%, var(--ghrc-text));
    border-color: color-mix(in srgb, var(--ghrc-accent) 18%, var(--ghrc-border));
    background:   color-mix(in srgb, var(--ghrc-accent) 8%, transparent);
  }

  .cache-refresh-btn.fresh:hover {
    color: var(--ghrc-accent);
    border-color: color-mix(in srgb, var(--ghrc-accent) 36%, var(--ghrc-border));
    background: color-mix(in srgb, var(--ghrc-accent) 12%, transparent);
  }

  .cache-refresh-btn.stale {
    color: color-mix(in srgb, var(--ghrc-warning) 72%, var(--ghrc-text));
    border-color: color-mix(in srgb, var(--ghrc-warning) 18%, var(--ghrc-border));
    background:   color-mix(in srgb, var(--ghrc-warning) 8%, transparent);
  }

  .cache-refresh-btn.stale:hover {
    color: color-mix(in srgb, var(--ghrc-warning) 88%, var(--ghrc-text-bold));
    border-color: color-mix(in srgb, var(--ghrc-warning) 34%, var(--ghrc-border));
    background: color-mix(in srgb, var(--ghrc-warning) 12%, transparent);
  }

  /* ── Error state ── */
  .error-box {
    display: flex;
    align-items: center;
    gap: var(--ghrc-space-3);
    background: color-mix(in srgb, var(--ghrc-error) var(--ghrc-state-bg-alpha), transparent);
    border: var(--ghrc-border-width) solid color-mix(in srgb, var(--ghrc-error) var(--ghrc-state-border-alpha), transparent);
    border-radius: var(--ghrc-radius-md);
    padding: var(--ghrc-space-3) var(--ghrc-space-4);
    color: var(--ghrc-error);
    font-size: var(--ghrc-font-size-md);
  }

  .error-icon {
    font-size: var(--ghrc-font-size-xl);
    flex-shrink: 0;
  }

  /* ── Visibility helpers ── */
  [hidden] { display: none !important; }
`;

  static TEMPLATE = GitHubRepoCard.html`
  <!-- Card shell -->
  <div class="card">
    <!-- Loading skeleton -->
    <div class="loading-state">
      <div class="repo-header">
        <div class="repo-header-main">
          <div class="skeleton sk-avatar"></div>
          <div class="repo-title-block">
            <div class="skeleton sk-title"></div>
          </div>
        </div>
        <div class="skeleton sk-release"></div>
      </div>
      <div class="skeleton sk-desc"></div>
      <div class="skeleton sk-desc2"></div>
      <div class="sk-stats">
        <div class="skeleton sk-stat"></div>
        <div class="skeleton sk-stat"></div>
        <div class="skeleton sk-stat"></div>
        <div class="skeleton sk-stat"></div>
      </div>
      <div class="sk-footer">
        <div class="sk-footer-meta">
          <div class="skeleton sk-footer-line"></div>
          <div class="skeleton sk-footer-line size"></div>
        </div>
        <div class="sk-footer-right">
          <div class="skeleton sk-refresh"></div>
          <div class="skeleton sk-link"></div>
        </div>
      </div>
    </div>

    <!-- Error -->
    <div class="error-box" hidden aria-live="polite">
      <span class="error-icon">⚠️</span>
      <span class="error-message"></span>
    </div>

    <!-- Repo content -->
    <div class="repo-content" hidden>
      <div class="repo-header">
        <div class="repo-header-main">
          <img class="avatar" src="" alt="" />
          <div class="repo-title-block">
            <a class="repo-full-name" href="#" target="_blank" rel="noopener"></a>
          </div>
        </div>
        <span class="release-badge" hidden></span>
      </div>

      <p class="repo-description"></p>
      <div class="repo-badges" hidden>
        <slot></slot>
      </div>

      <div class="stats-row">
        <div class="stat-box">
          <svg class="stat-icon" aria-hidden="true" xmlns="http://www.w3.org/2000/svg" viewBox="0 0 640 640"><!--!Font Awesome Free v7.2.0 by @fontawesome - https://fontawesome.com License - https://fontawesome.com/license/free Copyright 2026 Fonticons, Inc.--><path d="M341.5 45.1C337.4 37.1 329.1 32 320.1 32C311.1 32 302.8 37.1 298.7 45.1L225.1 189.3L65.2 214.7C56.3 216.1 48.9 222.4 46.1 231C43.3 239.6 45.6 249 51.9 255.4L166.3 369.9L141.1 529.8C139.7 538.7 143.4 547.7 150.7 553C158 558.3 167.6 559.1 175.7 555L320.1 481.6L464.4 555C472.4 559.1 482.1 558.3 489.4 553C496.7 547.7 500.4 538.8 499 529.8L473.7 369.9L588.1 255.4C594.5 249 596.7 239.6 593.9 231C591.1 222.4 583.8 216.1 574.8 214.7L415 189.3L341.5 45.1z"/></svg>
          <a class="stat-value" data-stat="stars" href="#" target="_blank" rel="noopener"></a>
          <span class="stat-label">Stars</span>
        </div>
        <div class="stat-box">
          <svg class="stat-icon" aria-hidden="true" xmlns="http://www.w3.org/2000/svg" viewBox="0 0 640 640"><!--!Font Awesome Free v7.2.0 by @fontawesome - https://fontawesome.com License - https://fontawesome.com/license/free Copyright 2026 Fonticons, Inc.--><path d="M176 168C189.3 168 200 157.3 200 144C200 130.7 189.3 120 176 120C162.7 120 152 130.7 152 144C152 157.3 162.7 168 176 168zM256 144C256 176.8 236.3 205 208 217.3L208 288L384 288C410.5 288 432 266.5 432 240L432 217.3C403.7 205 384 176.8 384 144C384 99.8 419.8 64 464 64C508.2 64 544 99.8 544 144C544 176.8 524.3 205 496 217.3L496 240C496 301.9 445.9 352 384 352L208 352L208 422.7C236.3 435 256 463.2 256 496C256 540.2 220.2 576 176 576C131.8 576 96 540.2 96 496C96 463.2 115.7 435 144 422.7L144 217.4C115.7 205 96 176.8 96 144C96 99.8 131.8 64 176 64C220.2 64 256 99.8 256 144zM488 144C488 130.7 477.3 120 464 120C450.7 120 440 130.7 440 144C440 157.3 450.7 168 464 168C477.3 168 488 157.3 488 144zM176 520C189.3 520 200 509.3 200 496C200 482.7 189.3 472 176 472C162.7 472 152 482.7 152 496C152 509.3 162.7 520 176 520z"/></svg>
          <a class="stat-value" data-stat="forks" href="#" target="_blank" rel="noopener"></a>
          <span class="stat-label">Forks</span>
        </div>
        <div class="stat-box">
          <svg class="stat-icon" aria-hidden="true" xmlns="http://www.w3.org/2000/svg" viewBox="0 0 640 640"><!--!Font Awesome Free v7.2.0 by @fontawesome - https://fontawesome.com License - https://fontawesome.com/license/free Copyright 2026 Fonticons, Inc.--><path d="M96 192C96 130.1 146.1 80 208 80C269.9 80 320 130.1 320 192C320 253.9 269.9 304 208 304C146.1 304 96 253.9 96 192zM32 528C32 430.8 110.8 352 208 352C305.2 352 384 430.8 384 528L384 534C384 557.2 365.2 576 342 576L74 576C50.8 576 32 557.2 32 534L32 528zM464 128C517 128 560 171 560 224C560 277 517 320 464 320C411 320 368 277 368 224C368 171 411 128 464 128zM464 368C543.5 368 608 432.5 608 512L608 534.4C608 557.4 589.4 576 566.4 576L421.6 576C428.2 563.5 432 549.2 432 534L432 528C432 476.5 414.6 429.1 385.5 391.3C408.1 376.6 435.1 368 464 368z"/></svg>
          <a class="stat-value" data-stat="contributors" href="#" target="_blank" rel="noopener"></a>
          <span class="stat-label">Contributors</span>
        </div>
        <div class="stat-box">
          <svg class="stat-icon" aria-hidden="true" xmlns="http://www.w3.org/2000/svg" viewBox="0 0 640 640"><!--!Font Awesome Free v7.2.0 by @fontawesome - https://fontawesome.com License - https://fontawesome.com/license/free Copyright 2026 Fonticons, Inc.--><path d="M479.5 238.6C488.3 230.2 502.2 229.7 511.7 237.8C521.2 245.9 522.7 259.7 515.8 269.7L514.3 271.6L491.3 298.3C518 311.8 537.6 337.4 542.7 367.9L568 367.9C581.3 367.9 592 378.6 592 391.9C592 405.2 581.3 415.9 568 415.9L544 415.9L544 447.9L568 447.9C581.3 447.9 592 458.6 592 471.9C592 485.2 581.3 495.9 568 495.9L542.7 495.9C535.1 541.3 495.6 575.9 448 575.9C400.4 575.9 361 541.3 353.3 495.9L328 496C314.7 496 304 485.3 304 472C304 458.7 314.7 448 328 448L352 448L352 416L328 416C314.7 416 304 405.3 304 392C304 378.7 314.7 368 328 368L353.3 368C358.4 337.5 378 311.9 404.7 298.4L381.8 271.7C373.2 261.6 374.3 246.5 384.4 237.9C394.5 229.3 409.6 230.4 418.2 240.5L448 275.3L477.8 240.5L479.5 238.7zM223.5 46.6C232.3 38.2 246.2 37.7 255.7 45.8C265.2 53.9 266.7 67.7 259.8 77.7L258.3 79.6L235.3 106.3C262 119.9 281.5 145.5 286.7 176L312 176C325.3 176 336 186.7 336 200C336 213.3 325.3 224 312 224L288 224L288 256L312 256C325.3 256 336 266.7 336 280C336 293.3 325.3 304 312 304L286.7 304C279.1 349.4 239.6 384 192 384C144.4 384 105 349.4 97.3 304L72 304C58.7 304 48 293.3 48 280C48 266.7 58.7 256 72 256L96 256L96 224L72 224C58.7 224 48 213.3 48 200C48 186.7 58.7 176 72 176L97.3 176C102.5 145.5 122 119.9 148.7 106.4L125.8 79.6C117.2 69.6 118.3 54.4 128.4 45.8C138.5 37.2 153.6 38.3 162.2 48.4L192 83.2L221.8 48.4L223.5 46.6z"/></svg>
          <a class="stat-value" data-stat="issues" href="#" target="_blank" rel="noopener"></a>
          <span class="stat-label">Open Issues</span>
        </div>
      </div>

      <div class="card-footer">
        <div class="footer-left">
          <span class="footer-updated"></span>
          <span class="footer-size"></span>
        </div>
        <div class="footer-right">
          <button class="cache-refresh-btn" hidden></button>
          <a class="github-link" href="#" target="_blank" rel="noopener">View on GitHub →</a>
        </div>
      </div>
    </div>
  </div>
`;
  static get observedAttributes() {
    return ["repo", "cache-ttl", "compact", "theme"];
  }

  constructor() {
    super();
    this._shadow = this.attachShadow({ mode: "open" });
    this._abortController = null;
    this._requestId = 0;
    this._els = null;
    this._render();
  }

  get repo() {
    return this.getAttribute("repo") ?? "";
  }

  set repo(value) {
    if (value == null || value === "") {
      this.removeAttribute("repo");
      return;
    }
    this.setAttribute("repo", value);
  }

  get cacheTtl() {
    return this.getAttribute("cache-ttl");
  }

  set cacheTtl(value) {
    if (value == null || value === "") {
      this.removeAttribute("cache-ttl");
      return;
    }
    this.setAttribute("cache-ttl", String(value));
  }

  get compact() {
    return this.hasAttribute("compact");
  }

  set compact(value) {
    this.toggleAttribute("compact", Boolean(value));
  }

  get theme() {
    return this.getAttribute("theme") ?? "auto";
  }

  set theme(value) {
    if (value == null || value === "" || value === "auto") {
      this.removeAttribute("theme");
      return;
    }
    this.setAttribute("theme", value);
  }

  // ── Lifecycle ───────────────────────────────────────────────────────────────

  connectedCallback() {
    const repo = this.getAttribute("repo");
    if (repo && !this._hasFetchedInitialRepo) {
      this._hasFetchedInitialRepo = true;
      this._fetchRepo(repo);
    }
  }

  disconnectedCallback() {
    if (this._abortController) this._abortController.abort();
  }

  attributeChangedCallback(name, oldVal, newVal) {
    if (name === "repo" && newVal && newVal !== oldVal && this.isConnected) {
      this._hasFetchedInitialRepo = true;
      this._fetchRepo(newVal);
      return;
    }

    if (name === "compact" && oldVal !== newVal) {
      this._syncVisibilityOptions();
    }
  }

  get _ttl() {
    const val = parseInt(this.getAttribute("cache-ttl"), 10);
    return isNaN(val) ? GitHubRepoCard.DEFAULT_TTL : val;
  }

  get _isCompact() {
    return this.compact;
  }

  // ── Cache helpers ───────────────────────────────────────────────────────────

  _cacheKey(repoPath) {
    return GitHubRepoCard.CACHE_PREFIX + repoPath.toLowerCase();
  }

  _readCache(repoPath, { allowExpired = false } = {}) {
    try {
      const raw = localStorage.getItem(this._cacheKey(repoPath));
      if (!raw) return null;
      const entry = JSON.parse(raw);
      const ageSeconds = (Date.now() - entry.cachedAt) / 1000;
      if (!allowExpired && ageSeconds > this._ttl) return null; // expired
      return entry;
    } catch {
      return null;
    }
  }

  _writeCache(repoPath, data, contributors, release) {
    try {
      const entry = { data, contributors, release, cachedAt: Date.now() };
      localStorage.setItem(this._cacheKey(repoPath), JSON.stringify(entry));
    } catch {
      // localStorage unavailable (private browsing, quota exceeded) — fail silently
    }
  }

  _clearCache(repoPath) {
    try {
      localStorage.removeItem(this._cacheKey(repoPath));
    } catch { /* ignore */ }
  }

  async _readJsonSafe(response) {
    try {
      return await response.json();
    } catch {
      return null;
    }
  }

  _getContributorCountFromLinkHeader(linkHeader) {
    if (!linkHeader) return null;
    const lastMatch = linkHeader.match(/<([^>]+)>;\s*rel="last"/);
    if (!lastMatch) return null;

    try {
      const lastUrl = new URL(lastMatch[1]);
      const page = parseInt(lastUrl.searchParams.get("page"), 10);
      return Number.isFinite(page) && page > 0 ? page : null;
    } catch {
      return null;
    }
  }

  // ── Fetch ───────────────────────────────────────────────────────────────────

  async _fetchRepo(repoPath, forceRefresh = false) {
    if (this._abortController) this._abortController.abort();
    const abortController = new AbortController();
    this._abortController = abortController;
    const requestId = ++this._requestId;
    const cachedEntry = this._readCache(repoPath, { allowExpired: true });

    // Cache hit — render instantly, skip network
    if (!forceRefresh) {
      const cached = this._readCache(repoPath);
      if (cached) {
        this._setLoading(false);
        this._updateCard(
          cached.data,
          cached.contributors,
          cached.release ?? null,
          cached.cachedAt
        );
        return;
      }
    }

    this._setLoading(true);
    this._clearError();

    try {
      const [repoRes, contribRes, releaseRes] = await Promise.all([
        fetch(`https://api.github.com/repos/${repoPath}`, {
          signal: abortController.signal,
        }),
        fetch(
          `https://api.github.com/repos/${repoPath}/contributors?per_page=1&anon=true`,
          { signal: abortController.signal }
        ),
        fetch(
          `https://api.github.com/repos/${repoPath}/releases/latest`,
          { signal: abortController.signal }
        ),
      ]);

      if (!repoRes.ok) {
        const isRateLimited =
          repoRes.status === 403 &&
          (repoRes.headers.get("x-ratelimit-remaining") === "0" ||
            repoRes.headers.get("retry-after"));
        const msg =
          repoRes.status === 404
            ? "Repository not found."
            : isRateLimited
            ? "Rate limit exceeded. Try again later."
            : `GitHub API error ${repoRes.status}.`;
        throw new Error(msg);
      }

      const repoData = await this._readJsonSafe(repoRes);
      const data = {
        ...(cachedEntry?.data ?? {}),
        ...(repoData ?? {}),
      };

      let contributors = cachedEntry?.contributors ?? "—";
      if (contribRes.ok) {
        const contributorCountFromHeader = this._getContributorCountFromLinkHeader(
          contribRes.headers.get("link")
        );
        if (contributorCountFromHeader != null) {
          contributors = contributorCountFromHeader.toLocaleString();
        } else {
          const contribData = await this._readJsonSafe(contribRes);
          contributors = Array.isArray(contribData)
            ? contribData.length.toLocaleString()
            : cachedEntry?.contributors ?? "—";
        }
      }

      let release = cachedEntry?.release ?? null;
      if (releaseRes.ok) {
        const releaseData = await this._readJsonSafe(releaseRes);
        release = releaseData?.tag_name || releaseData?.name || cachedEntry?.release || null;
      }

      if (requestId !== this._requestId) return;

      this._writeCache(repoPath, data, contributors, release);
      this._updateCard(data, contributors, release, Date.now());
    } catch (err) {
      if (err.name !== "AbortError") {
        if (requestId !== this._requestId) return;

        // Fall back to stale cache when the network request fails or is rate limited.
        const stale = this._readCache(repoPath, { allowExpired: true });

        if (stale) {
          this._updateCard(
            stale.data,
            stale.contributors,
            stale.release ?? null,
            stale.cachedAt,
            true
          );
        } else {
          this._showError(err.message || "An unexpected error occurred.");
        }
      }
    } finally {
      if (requestId === this._requestId) {
        this._setLoading(false);
      }
    }
  }

  // ── Render ──────────────────────────────────────────────────────────────────

  _render() {
    this._shadow.innerHTML = `<style>${GitHubRepoCard.STYLES}</style>${GitHubRepoCard.TEMPLATE}`;

    this._els = {
      card: this._shadow.querySelector(".card"),
      loadingState: this._shadow.querySelector(".loading-state"),
      errorBox: this._shadow.querySelector(".error-box"),
      errorMessage: this._shadow.querySelector(".error-message"),
      repoContent: this._shadow.querySelector(".repo-content"),
      avatar: this._shadow.querySelector(".avatar"),
      repoFullName: this._shadow.querySelector(".repo-full-name"),
      releaseBadge: this._shadow.querySelector(".release-badge"),
      repoDescription: this._shadow.querySelector(".repo-description"),
      repoBadges: this._shadow.querySelector(".repo-badges"),
      badgesSlot: this._shadow.querySelector("slot"),
      footerUpdated: this._shadow.querySelector(".footer-updated"),
      footerSize: this._shadow.querySelector(".footer-size"),
      githubLink: this._shadow.querySelector(".github-link"),
      cacheRefreshBtn: this._shadow.querySelector(".cache-refresh-btn"),
      skDesc: this._shadow.querySelector(".sk-desc"),
      skDesc2: this._shadow.querySelector(".sk-desc2"),
      skFooterSize: this._shadow.querySelector(".sk-footer-line.size"),
      skRefresh: this._shadow.querySelector(".sk-refresh"),
      stars: this._shadow.querySelector("[data-stat='stars']"),
      forks: this._shadow.querySelector("[data-stat='forks']"),
      contributors: this._shadow.querySelector("[data-stat='contributors']"),
      issues: this._shadow.querySelector("[data-stat='issues']"),
    };

    const { cacheRefreshBtn } = this._els;
    cacheRefreshBtn.textContent = "Refresh";

    cacheRefreshBtn.addEventListener("click", () => {
      const repo = this.getAttribute("repo");
      if (!repo) return;
      this._fetchRepo(repo, true);
    });

    this._els.badgesSlot.addEventListener("slotchange", () => {
      this._syncBadgeSlot();
    });

    this._syncVisibilityOptions();
    this._syncBadgeSlot();
  }

  // ── DOM helpers ─────────────────────────────────────────────────────────────

  _setLoading(on) {
    const { loadingState, repoContent, errorBox } = this._els;
    loadingState.hidden = !on;
    if (on) {
      repoContent.hidden = true;
      errorBox.hidden = true;
    }
  }

  _clearError() {
    this._els.errorBox.hidden = true;
  }

  _syncBadgeSlot() {
    const { repoBadges, badgesSlot } = this._els;
    const hasBadges = badgesSlot.assignedNodes({ flatten: true }).some((node) => {
      if (node.nodeType === Node.ELEMENT_NODE) return true;
      return node.nodeType === Node.TEXT_NODE && node.textContent.trim() !== "";
    });
    repoBadges.hidden = !hasBadges;
  }

  _syncVisibilityOptions() {
    const compact = this._isCompact;
    const {
      cacheRefreshBtn,
      footerSize,
      repoDescription,
      skDesc,
      skDesc2,
      skFooterSize,
      skRefresh,
    } = this._els;

    repoDescription.hidden = compact;
    skDesc.hidden = compact;
    skDesc2.hidden = compact;
    if (footerSize) footerSize.hidden = compact;
    if (skFooterSize) skFooterSize.hidden = compact;
    cacheRefreshBtn.hidden = compact;
    if (skRefresh) skRefresh.hidden = compact;

  }

  _showError(msg) {
    const { errorBox, errorMessage, repoContent } = this._els;
    errorMessage.textContent = msg;
    errorBox.hidden = false;
    repoContent.hidden = true;
  }

  _updateCard(data, contributors, release, cachedAt = null, isStale = false) {
    const {
      avatar,
      cacheRefreshBtn,
      footerSize,
      footerUpdated,
      githubLink,
      issues,
      contributors: contributorsEl,
      forks,
      releaseBadge,
      repoContent,
      repoDescription,
      repoFullName,
      stars,
    } = this._els;

    avatar.src = data.owner.avatar_url;
    avatar.alt = data.owner.login;
    avatar.loading = "lazy";
    avatar.decoding = "async";

    const link = repoFullName;
    link.textContent = data.full_name;
    link.href = data.html_url;
    githubLink.href = data.html_url;
    stars.href = `${data.html_url}/stargazers`;
    forks.href = `${data.html_url}/forks`;
    contributorsEl.href = `${data.html_url}/graphs/contributors`;
    issues.href = `${data.html_url}/issues`;

    if (release) {
      releaseBadge.textContent = release;
      releaseBadge.hidden = false;
    } else {
      releaseBadge.hidden = true;
    }

    repoDescription.textContent = data.description || "No description provided.";

    const fmt = (n) => (typeof n === "number" ? n.toLocaleString() : "—");
    stars.textContent = fmt(data.stargazers_count);
    forks.textContent = fmt(data.forks_count);
    contributorsEl.textContent = contributors;
    issues.textContent = fmt(data.open_issues_count);

    const updated = new Date(data.pushed_at).toLocaleDateString("en-US", {
      year: "numeric", month: "short", day: "numeric",
    });
    footerUpdated.textContent = `Last push: ${updated}`;

    const sizeFmt = data.size >= 1024
      ? `${(data.size / 1024).toFixed(1)} MB`
      : `${data.size} KB`;
    footerSize.textContent = `Size: ${sizeFmt}`;

    if (cachedAt || isStale) {
      cacheRefreshBtn.className = `cache-refresh-btn ${isStale ? "stale" : "fresh"}`;
    } else {
      cacheRefreshBtn.className = "cache-refresh-btn";
    }
    cacheRefreshBtn.textContent = "Refresh";

    repoContent.hidden = false;
    this._syncVisibilityOptions();
  }
}

if (!customElements.get("github-repo-card")) {
  customElements.define("github-repo-card", GitHubRepoCard);
}
