class Bot {
    constructor() {
        this.apiBase = "/api";
        this.socket = null;
        this.socketConnected = false;
        this.visionChart = null;
        this.visionSeries = null;
        this.visionTrendSeries = null;
        this.visionPriceLines = [];
        this.lastVisualSymbol = null;
        this.logicTooltip = null;
        this.validationLogs = [];
        this.radarPage = 1;
        this.radarPageSize = 9;
        this.radarFilter = 'ALL';
        this.radarTrades = [];
        this.positionsPage = 1;
        this.positionsPageSize = 20;
        this.positionsRows = [];
        this.journalPage = 1;
        this.journalPageSize = 25;
        this.journalRows = [];
        this.alerts = [];
        this.edgeDiagnostics = null;
        this.strategyValidation = null;
        this.ictBlockers = null;
        this.licenses = [];
        this.brokers = [];
        this.activeBrokerStatus = null;
        this.users = [];
        this.currentUser = null;
        this.permissions = new Set(['read', 'trade', 'panic', 'settings', 'users', 'licenses', 'brokers']);
        this.settingsNotificationTimer = null;
        this.init();
    }

    safeSetText(id, value) {
        const el = document.getElementById(id);
        if (el) {
            el.textContent = value;
        }
    }

    setBadge(id, text, state = 'neutral') {
        const el = document.getElementById(id);
        if (!el) return;
        el.textContent = text;
        el.className = `status-badge ${state}`;
    }

    hasPermission(permission) {
        return this.permissions.has(permission);
    }

    setAllInputs(id, value) {
        document.querySelectorAll(`#${id}`).forEach((el) => {
            if (el.type === 'checkbox') {
                el.checked = Boolean(value);
            } else {
                el.value = value;
            }
        });
    }

    findSettingInput(id) {
        const selector = `#${id}`;
        const visible = Array.from(document.querySelectorAll(selector)).find((el) => {
            return el.offsetParent !== null || el.closest('.modal')?.style.display === 'block';
        });
        return visible || document.querySelector(selector);
    }

    readFormValue(form, id, fallback = '') {
        const scoped = form?.querySelector(`#${id}`);
        const el = scoped || this.findSettingInput(id);
        if (!el) return fallback;
        return el.type === 'checkbox' ? el.checked : el.value;
    }

    escapeHtml(value) {
        return String(value ?? '')
            .replace(/&/g, '&amp;')
            .replace(/</g, '&lt;')
            .replace(/>/g, '&gt;')
            .replace(/"/g, '&quot;')
            .replace(/'/g, '&#039;');
    }

    showNotification(message, type = 'success') {
        const ids = ['settingsNotification', 'settingsModalNotification'];
        let shown = false;
        ids.forEach((id) => {
            const el = document.getElementById(id);
            if (!el) return;
            el.textContent = message;
            el.className = `settings-notification ${type}`;
            el.style.display = 'block';
            shown = true;
        });
        if (!shown) {
            alert(message);
            return;
        }
        if (this.settingsNotificationTimer) {
            window.clearTimeout(this.settingsNotificationTimer);
        }
        this.settingsNotificationTimer = window.setTimeout(() => {
            ids.forEach((id) => {
                const el = document.getElementById(id);
                if (el) {
                    el.style.display = 'none';
                }
            });
        }, 5000);
    }

    formatLogValue(value) {
        if (value === null || value === undefined) return '-';
        if (typeof value === 'object') {
            return `<pre class="log-json">${this.escapeHtml(JSON.stringify(value, null, 2))}</pre>`;
        }
        return this.escapeHtml(value);
    }

    createLogAccordionItem(log, title, meta = '') {
        const item = document.createElement('div');
        item.className = 'accordion-item log-accordion-item';

        const details = Object.entries(log || {})
            .map(([key, value]) => `
                <div class="log-detail-row">
                    <strong>${this.escapeHtml(key)}</strong>
                    <span>${this.formatLogValue(value)}</span>
                </div>
            `)
            .join('');

        item.innerHTML = `
            <button class="accordion-header" type="button">
                <span class="accordion-title">${this.escapeHtml(title)}</span>
                <span class="accordion-meta">${this.escapeHtml(meta)} <i class="fas fa-chevron-down"></i></span>
            </button>
            <div class="accordion-details">
                <div class="log-detail-actions">
                    <button class="btn-small btn-reset" type="button">Open Popup</button>
                </div>
                ${details}
            </div>
        `;

        const header = item.querySelector('.accordion-header');
        const detailPanel = item.querySelector('.accordion-details');
        header.addEventListener('click', () => {
            detailPanel.classList.toggle('expanded');
            item.classList.toggle('open');
        });
        item.querySelector('.btn-small')?.addEventListener('click', (e) => {
            e.stopPropagation();
            this.showLogDetails(log);
        });
        return item;
    }

    formatMoney(value) {
        if (value === null || value === undefined || Number.isNaN(Number(value))) {
            return '--';
        }
        const amount = Number(value);
        const sign = amount > 0 ? '+' : '';
        return `${sign}$${amount.toFixed(2)}`;
    }

    setMoney(id, value) {
        const el = document.getElementById(id);
        if (!el) return;
        const amount = Number(value);
        el.textContent = this.formatMoney(value);
        el.classList.toggle('metric-profit', Number.isFinite(amount) && amount > 0);
        el.classList.toggle('metric-loss', Number.isFinite(amount) && amount < 0);
    }

    formatTime(value) {
        if (!value) return '--';
        const parsed = new Date(value);
        if (Number.isNaN(parsed.getTime())) return '--';
        return parsed.toLocaleTimeString();
    }

    formatAlertTime(value) {
        if (!value) return '--';
        const parsed = new Date(value);
        if (Number.isNaN(parsed.getTime())) return '--';
        return parsed.toLocaleString([], { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' });
    }

    renderScanStatus(scan) {
        const data = scan || {};
        const mode = data.on_new_candle
            ? `M${data.timeframe_minutes || 5} candle`
            : `${data.interval_seconds || 5}s loop`;
        this.safeSetText('scanMode', mode);
        this.safeSetText('lastScan', this.formatTime(data.last_scan_at));
        const seconds = Number(data.seconds_until_next_scan);
        this.safeSetText('nextScan', Number.isFinite(seconds) ? `${Math.max(0, Math.round(seconds))}s` : '--');
        const cooldown = data.trade_cooldown_minutes != null ? `${data.trade_cooldown_minutes}m` : '--';
        const maxTrades = data.max_trades_per_symbol != null ? ` | ${data.max_trades_per_symbol}/symbol` : '';
        this.safeSetText('tradeCooldown', `${cooldown}${maxTrades}`);
    }

    formatProfitProtectionState(tm = {}) {
        const parts = [];
        if (tm.trailing_tp) parts.push(`Trail ${Math.round((tm.trailing_tp_trigger_pct || 0) * 100)}%`);
        if (tm.partial_tp) parts.push(`Part ${Number(tm.partial_tp_trigger_r || 0).toFixed(1)}R`);
        if (tm.reverse_profit_exit) parts.push('Reverse');
        return parts.length ? parts.join(' | ') : 'Off';
    }

    init() {
        this.setupListeners();
        this.initSidebar();
        this.verifyLicensedFooter();
        this.setupSocket();
        this.setupVisionChart();
        this.updateDashboard();
        this.loadSignals();
        this.loadSessions();
        this.loadKillStatus();
        this.loadFutureTrades();
        this.loadApiEndpoints();
        this.loadAlerts();
        this.loadRiskStatus();
        this.loadJournal();
        this.loadEdgeDiagnostics();
        this.loadStrategyValidation();
        this.loadIctBlockers();
        this.loadCurrentUser();
        this.loadLicenseStatus();
        this.loadLicenses();
        this.loadBrokers();
        this.loadBrokerStatus();
        this.loadUsers();
        this.loadSettings();

        this.setupVisionCardClicks();

        this.startStaggeredPolling();
    }

    initSidebar() {
        document.querySelectorAll(".platform-sidebar .nav-link").forEach((link) => {
            const label = link.dataset.label || link.dataset.tooltip || link.textContent.trim();
            link.dataset.label = label;
            link.setAttribute("title", label);
            link.setAttribute("aria-label", label);
        });

        document.body.classList.remove("sidebar-mobile-open");
        document.body.classList.toggle("sidebar-collapsed", localStorage.getItem("nexusSidebarCollapsed") === "true");
        this.setSidebarMobileOpen(false);
    }

    setSidebarMobileOpen(open) {
        document.body.classList.toggle("sidebar-mobile-open", open);
        const toggle = document.getElementById("sidebarToggle");
        if (toggle) {
            toggle.setAttribute("aria-expanded", String(open));
            toggle.setAttribute("aria-label", "Close menu");
            toggle.setAttribute("title", "Close menu");
        }
        const mobileToggle = document.getElementById("mobileSidebarToggle");
        if (mobileToggle) {
            mobileToggle.setAttribute("aria-expanded", String(open));
        }
    }

    setSidebarCollapsed() {
        const collapsed = !document.body.classList.contains("sidebar-collapsed");
        document.body.classList.toggle("sidebar-collapsed", collapsed);
        localStorage.setItem("nexusSidebarCollapsed", String(collapsed));
        this.setSidebarMobileOpen(false);
    }

    isMobileSidebar() {
        return window.matchMedia("(max-width: 900px)").matches;
    }

    toggleSidebar() {
        if (this.isMobileSidebar()) {
            this.setSidebarMobileOpen(!document.body.classList.contains("sidebar-mobile-open"));
        } else {
            this.setSidebarCollapsed();
        }
    }

    closeMobileSidebar() {
        this.setSidebarMobileOpen(false);
    }

    async verifyLicensedFooter() {
        const footer = document.querySelector('[data-nexus-licensed-footer="true"]');
        const text = footer ? footer.textContent || '' : '';
        const compactText = 'Powered by Nexus Trading Systems · Developed by Eliud Karanja Ndiritu · Licensed Software';
        const intact = footer && text.includes(compactText);
        try {
            await fetch(`${this.apiBase}/branding/integrity`, {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({intact: Boolean(intact)}),
            });
        } catch (error) {
            console.warn('Branding integrity check unavailable', error);
        }
        if (!intact) {
            document.body.classList.add('branding-integrity-warning');
            document.getElementById('startBtn')?.setAttribute('disabled', 'disabled');
            setTimeout(() => {
                this.showNotification('Licensed footer integrity warning: live start controls are disabled in this browser session.', 'error');
            }, 800);
        }
    }

    setupListeners() {
        document.getElementById("startBtn").addEventListener("click", () =>
            this.start()
        );
        document.getElementById("stopBtn").addEventListener("click", () =>
            this.stop()
        );

        document.querySelectorAll(".nav-link").forEach((link) => {
            link.addEventListener("click", (e) => this.navigate(e));
        });
        document.getElementById("sidebarToggle")?.addEventListener("click", () => this.toggleSidebar());
        document.getElementById("mobileSidebarToggle")?.addEventListener("click", () => this.toggleSidebar());
        document.getElementById("sidebarBackdrop")?.addEventListener("click", () => this.closeMobileSidebar());
        document.getElementById("logoutBtn")?.addEventListener("click", () => this.logout());
        document.querySelectorAll(".sidebar-utility").forEach((link) => {
            link.addEventListener("click", (e) => this.navigate(e));
        });
        document.querySelectorAll('[data-open-provider-profile], [data-open-support]').forEach((button) => {
            button.addEventListener('click', (event) => {
                const modal = document.getElementById('providerProfileModal');
                if (!modal) return;
                event.preventDefault();
                modal.style.display = 'flex';
                modal.classList.add('open');
                modal.setAttribute('aria-hidden', 'false');
            });
        });
        document.querySelectorAll('[data-close-provider-modal]').forEach((button) => {
            button.addEventListener('click', () => this.closeProviderModal());
        });

        // kill switch controls
        document.getElementById('killToggle')?.addEventListener('change', () => this.toggleKill());
        document.getElementById('killSymbol')?.addEventListener('change', () => this.loadKillStatus());

        // rule toggles (live update)
        ['ruleEma', 'ruleVolume', 'rulePo3'].forEach((id) => {
            const el = document.getElementById(id);
            if (el) {
                el.addEventListener('change', () => this.updateRules());
            }
        });

        // quick action buttons
        document.getElementById('approveSignalBtn')?.addEventListener('click', () => this.approveSignal());
        document.getElementById('rejectSignalBtn')?.addEventListener('click', () => this.rejectSignal());
        document.getElementById('pauseBtn')?.addEventListener('click', () => this.togglePause());
        document.getElementById('panicCloseBtn')?.addEventListener('click', () => this.openPanicConfirm());
        document.getElementById('panicCloseBtnTop')?.addEventListener('click', () => this.openPanicConfirm());
        document.getElementById('panicCancelBtn')?.addEventListener('click', () => this.closePanicConfirm());
        document.getElementById('panicCancelX')?.addEventListener('click', () => this.closePanicConfirm());
        document.getElementById('panicConfirmBtn')?.addEventListener('click', () => this.panicClose());
        document.getElementById('symbolSearch')?.addEventListener('input', (e) => this.filterSymbolLists(e.target.value));
        document.getElementById('radarPrevBtn')?.addEventListener('click', () => this.changeRadarPage(-1));
        document.getElementById('radarNextBtn')?.addEventListener('click', () => this.changeRadarPage(1));
        document.getElementById('radarPageSize')?.addEventListener('change', (event) => {
            this.radarPageSize = Math.max(1, Number(event.target.value || 9));
            this.radarPage = 1;
            this.renderGlobalRadar();
        });
        document.getElementById('positionsPrevBtn')?.addEventListener('click', () => this.changePositionsPage(-1));
        document.getElementById('positionsNextBtn')?.addEventListener('click', () => this.changePositionsPage(1));
        document.getElementById('positionsPageSize')?.addEventListener('change', (event) => {
            this.positionsPageSize = Math.max(1, Number(event.target.value || 20));
            this.positionsPage = 1;
            this.renderPositionsTable();
        });
        document.getElementById('journalRefreshBtn')?.addEventListener('click', () => this.loadJournal());
        document.getElementById('journalPrevBtn')?.addEventListener('click', () => this.changeJournalPage(-1));
        document.getElementById('journalNextBtn')?.addEventListener('click', () => this.changeJournalPage(1));
        document.getElementById('journalPageSize')?.addEventListener('change', (event) => {
            this.journalPageSize = Math.max(1, Number(event.target.value || 25));
            this.journalPage = 1;
            this.renderJournal();
        });
        ['journalDecisionFilter', 'journalGradeFilter', 'journalTypeFilter', 'journalDateFilter'].forEach((id) => {
            document.getElementById(id)?.addEventListener('change', () => this.loadJournal());
        });
        document.getElementById('journalSymbolFilter')?.addEventListener('input', () => {
            window.clearTimeout(this.journalFilterTimer);
            this.journalFilterTimer = window.setTimeout(() => this.loadJournal(), 350);
        });
        document.querySelectorAll('.radar-filter').forEach((btn) => {
            btn.addEventListener('click', () => {
                document.querySelectorAll('.radar-filter').forEach((x) => x.classList.remove('active'));
                btn.classList.add('active');
                this.radarFilter = btn.dataset.filter || 'ALL';
                this.radarPage = 1;
                this.renderGlobalRadar();
            });
        });

        // settings form
        document.getElementById('settingsForm')?.addEventListener('submit', (e) => this.saveSettings(e));
        document.getElementById('settingsModalForm')?.addEventListener('submit', (e) => this.saveSettings(e));
        document.getElementById('licenseActivateForm')?.addEventListener('submit', (e) => this.activateLicense(e));
        document.getElementById('licenseCreateForm')?.addEventListener('submit', (e) => this.createLicense(e));
        document.getElementById('brokerProfileForm')?.addEventListener('submit', (e) => this.createBrokerProfile(e));
        document.getElementById('brokerCancelEditBtn')?.addEventListener('click', () => this.resetBrokerForm());
        document.getElementById('brokerType')?.addEventListener('change', () => this.updateBrokerFieldHints());
        document.getElementById('userCreateForm')?.addEventListener('submit', (e) => this.createUser(e));
        document.getElementById('resetBtn')?.addEventListener('click', () => this.resetSettingsDefaults());
        document.getElementById('exportBtn')?.addEventListener('click', () => this.exportSettings());
        document.querySelectorAll('.settings-tab').forEach((btn) => {
            btn.addEventListener('click', () => this.setSettingsTab(btn.dataset.settingsTab || 'basic'));
        });

        // modal controls
        document.querySelectorAll('.close').forEach((btn) => {
            btn.addEventListener('click', () => {
                const modal = btn.closest('.modal');
                if (modal) {
                    modal.style.display = 'none';
                    modal.classList.remove('open');
                    modal.setAttribute('aria-hidden', 'true');
                }
            });
        });
        window.addEventListener('click', (e) => {
            if (e.target.classList.contains('modal')) {
                e.target.style.display = 'none';
                e.target.classList.remove('open');
                e.target.setAttribute('aria-hidden', 'true');
            }
        });

        // bento card click handlers (legacy) 
        document.querySelectorAll('.bento-card').forEach((card) => {
            card.addEventListener('click', (e) => {
                const popupId = card.dataset.popup;
                if (popupId) {
                    this.openPopup(popupId);
                }
            });
        });

        // full-card detail modal behavior
        document.querySelectorAll('.detail-card').forEach((card) => {
            card.addEventListener('click', () => {
                const title = card.dataset.cardTitle || 'Card Details';
                const details = card.dataset.cardDetails || '';
                const renderType = card.dataset.cardRender || 'default';
                const bodyElement = document.getElementById('cardDetailsBody');
                const titleElement = document.getElementById('cardDetailsTitle');
                if (titleElement) titleElement.textContent = title;
                if (bodyElement) {
                    bodyElement.innerHTML = '';
                    if (renderType === 'table') {
                        const rows = [];
                        card.querySelectorAll('[data-detail]').forEach((field) => {
                            const key = field.dataset.detail || field.textContent.trim();
                            const value = field.textContent.trim();
                            rows.push(`<tr><td><strong>${key}:</strong></td><td>${value}</td></tr>`);
                        });
                        bodyElement.innerHTML = `<table class="card-details-table">${rows.join('')}</table>`;
                    } else if (renderType === 'list') {
                        const items = [];
                        card.querySelectorAll('[data-detail]').forEach((field) => {
                            const key = field.dataset.detail || field.textContent.trim();
                            const value = field.textContent.trim();
                            items.push(`<li><strong>${key}:</strong> ${value}</li>`);
                        });
                        bodyElement.innerHTML = `<ul class="card-details-list">${items.join('')}</ul>`;
                    } else {
                        bodyElement.innerHTML = `<div class="card-details-item"><strong>${title}</strong><p>${details}</p></div>`;
                    }
                }
                const modal = document.getElementById('cardDetailsModal');
                if (modal) modal.style.display = 'block';
            });
        });

        // Strategy bento actions
        document.getElementById('seeAllSignalsBtn')?.addEventListener('click', () => {
            const modal = document.getElementById('cardDetailsModal');
            const titleElement = document.getElementById('cardDetailsTitle');
            const bodyElement = document.getElementById('cardDetailsBody');
            if (titleElement) titleElement.textContent = 'All Signals';
            if (bodyElement) {
                // Populate with signals list (mock or from data)
                const signals = [
                    { symbol: 'EURUSD', type: 'BUY', summary: 'Strong uptrend' },
                    { symbol: 'GBPUSD', type: 'SELL', summary: 'Overbought' }
                ];
                const items = signals.map(s => `<li><strong>${s.symbol} ${s.type}:</strong> ${s.summary}</li>`).join('');
                bodyElement.innerHTML = `<ul class="card-details-list">${items}</ul>`;
            }
            if (modal) modal.style.display = 'block';
        });

        document.getElementById('openSettingsModal')?.addEventListener('click', () => {
            const modal = document.getElementById('settingsModal');
            if (modal) {
                modal.style.display = 'block';
                this.loadSettings();
            }
        });

        document.getElementById('cancelSettingsBtn')?.addEventListener('click', () => {
            const modal = document.getElementById('settingsModal');
            if (modal) modal.style.display = 'none';
        });

        document.querySelectorAll('.tab-btn').forEach((btn) => {
            btn.addEventListener('click', () => {
                document.querySelectorAll('.tab-btn').forEach((x) => x.classList.remove('active'));
                document.querySelectorAll('.tab-panel').forEach((panel) => panel.classList.remove('active'));
                btn.classList.add('active');
                document.getElementById(`tab-${btn.dataset.tab}`).classList.add('active');
            });
        });

        // modal actions
        document.getElementById('approveCardBtn')?.addEventListener('click', () => this.approveCard());
        document.getElementById('rejectCardBtn')?.addEventListener('click', () => this.rejectCard());

        const tooltip = document.getElementById('logicTagTooltip');
        this.logicTooltip = tooltip;
    }

    closeProviderModal() {
        const modal = document.getElementById('providerProfileModal');
        if (!modal) return;
        modal.style.display = 'none';
        modal.classList.remove('open');
        modal.setAttribute('aria-hidden', 'true');
    }

    openPopup(popupId) {
        const modal = document.getElementById(popupId);
        if (modal) {
            modal.style.display = 'block';
            // Populate popup data based on popupId
            this.populatePopupData(popupId);
        }
    }

    populatePopupData(popupId) {
        switch(popupId) {
            case 'equity-popup':
                this.populateEquityPopup();
                break;
            case 'drawdown-popup':
                this.populateDrawdownPopup();
                break;
            case 'status-popup':
                this.populateStatusPopup();
                break;
            case 'performance-popup':
                this.populatePerformancePopup();
                break;
            case 'top-pair-popup':
                this.populateTopPairPopup();
                break;
            case 'session-popup':
                this.populateSessionPopup();
                break;
            case 'risk-popup':
                this.populateRiskPopup();
                break;
            case 'news-popup':
                this.populateNewsPopup();
                break;
        }
    }

    populateEquityPopup() {
        // Get current equity data from dashboard elements
        const equity = document.getElementById('equity')?.textContent || '$0.00';
        const dailyChange = document.getElementById('dailyChange')?.textContent || '+0.00%';

        this.safeSetText('popupBalance', equity);
        this.safeSetText('popupEquity', equity);
        this.safeSetText('popupDailyChange', dailyChange);
        // Note: Floating P&L would need to be calculated from positions data
        this.safeSetText('popupFloating', '$0.00');
    }

    populateDrawdownPopup() {
        // This would populate with current open positions and their P&L
        const drawdownTrades = document.getElementById('drawdownTrades');
        if (drawdownTrades) {
            drawdownTrades.innerHTML = '<p>No active positions to display</p>';
        }
    }

    populateStatusPopup() {
        // Get status data from dashboard
        const status = document.getElementById('status')?.textContent || 'Unknown';
        this.safeSetText('mt5Connection', status === 'Running' ? 'Connected' : 'Disconnected');
        this.safeSetText('apiHeartbeat', '-- ms');
        this.safeSetText('botVersion', '1.0.0');
        this.safeSetText('uptime', '00:00:00');

        const statusLogs = document.getElementById('statusLogs');
        if (statusLogs) {
            statusLogs.innerHTML = '<div>Bot initialized successfully</div><div>MT5 connection established</div>';
        }
    }

    populatePerformancePopup() {
        // Get performance data from dashboard
        const winRate = document.getElementById('winRate')?.textContent || '0%';
        const profitFactor = document.getElementById('profitFactor')?.textContent || '0.00';

        this.safeSetText('popupWinRate', winRate);
        this.safeSetText('popupProfitFactor', profitFactor);
        this.safeSetText('popupAvgWin', '$0.00');
        this.safeSetText('popupAvgLoss', '$0.00');
        this.safeSetText('popupTotalTrades', '0');
        this.safeSetText('popupExpectancy', '$0.00');
    }

    populateTopPairPopup() {
        // This would show the best performing pair from recent trades
        this.safeSetText('popupTopSymbol', 'EURUSD');
        this.safeSetText('popupTopChange', '+2.45%');
        this.safeSetText('topPairTrades', '12');
        this.safeSetText('topPairWinRate', '75%');
        this.safeSetText('topPairPnL', '$245.67');
    }

    populateSessionPopup() {
        // Update session volatilities based on current market conditions
        const sessions = ['tokyo', 'london', 'ny'];
        sessions.forEach(session => {
            const element = document.getElementById(`${session}Volatility`);
            if (element) {
                element.textContent = session === 'london' ? 'High' : 'Low';
                element.setAttribute('data-volatility', session === 'london' ? 'high' : 'low');
            }
        });
    }

    populateRiskPopup() {
        // Get risk data from dashboard
        this.safeSetText('popupMarginUsed', '$0.00');
        this.safeSetText('popupMarginFree', '$10,000.00');
        this.safeSetText('popupMarginLevel', '1000%');
        this.safeSetText('popupMaxExposure', '5%');
    }

    populateNewsPopup() {
        // This would load news events from an API
        const newsEvents = document.getElementById('newsEvents');
        if (newsEvents) {
            newsEvents.innerHTML = '<div>No recent news events</div>';
        }
    }

    openPanicConfirm() {
        const modal = document.getElementById('panicConfirmModal');
        if (!modal) return;
        modal.style.display = 'block';
        modal.classList.add('show');
        modal.setAttribute('aria-hidden', 'false');
    }

    closePanicConfirm() {
        const modal = document.getElementById('panicConfirmModal');
        if (!modal) return;
        modal.style.display = 'none';
        modal.classList.remove('show');
        modal.setAttribute('aria-hidden', 'true');
    }

    panicClose() {
        const confirmBtn = document.getElementById('panicConfirmBtn');
        if (confirmBtn) {
            confirmBtn.disabled = true;
            confirmBtn.innerHTML = '<i class="fas fa-spinner fa-spin"></i> Sending...';
        }
        fetch(`${this.apiBase}/panic-close`, {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
            },
        })
        .then(response => response.json())
        .then(data => {
            this.closePanicConfirm();
            const drawdown = document.getElementById('drawdown-popup');
            if (drawdown) drawdown.style.display = 'none';
            if (data.status === 'success') {
                this.showNotification('Panic close triggered. Global kill switch enabled.', 'success');
            } else {
                this.showNotification(data.message || 'Panic close request failed.', 'error');
            }
            this.updateDashboard();
        })
        .catch(error => {
            console.error('Error initiating panic close:', error);
            this.showNotification('Error initiating panic close. Please try again.', 'error');
        })
        .finally(() => {
            if (confirmBtn) {
                confirmBtn.disabled = false;
                confirmBtn.innerHTML = '<i class="fas fa-triangle-exclamation"></i> Confirm Panic Close';
            }
        });
    }

    filterSymbolLists(query) {
        const needle = String(query || '').trim().toUpperCase();
        ['pendingOrdersTable', 'positionsTable'].forEach((id) => {
            const root = document.getElementById(id);
            if (!root) return;
            root.querySelectorAll('li, tr').forEach((row) => {
                const text = row.textContent.toUpperCase();
                row.style.display = !needle || text.includes(needle) ? '' : 'none';
            });
        });
    }

    approveCard() {
        alert('Card approved!');
        // Add logic to approve the card (e.g., send to backend)
        document.getElementById('cardDetailsModal').style.display = 'none';
    }

    rejectCard() {
        alert('Card rejected!');
        // Add logic to reject the card (e.g., send to backend)
        document.getElementById('cardDetailsModal').style.display = 'none';
    }

    setupSocket() {
        if (!window.io) return;

        this.socket = io();

        this.socket.on('connect', () => {
            this.socketConnected = true;
            console.info('Socket.IO connected');
        });

        this.socket.on('disconnect', () => {
            this.socketConnected = false;
            console.info('Socket.IO disconnected');
        });

        this.socket.on('dashboard_update', (payload) => {
            this.applyRealtimePayload(payload);
        });

        this.socket.on('validation_logs', (payload) => {
            this.validationLogs = payload || [];
            this.renderValidationLogs();
        });

        this.socket.on('alert', (alert) => {
            this.addAlert(alert);
        });
    }

    startStaggeredPolling() {
        const fallback = (fn, interval) => {
            setInterval(() => {
                if (!this.socketConnected) fn();
            }, interval);
        };

        fallback(() => this.loadBotStatus(), 5000);
        fallback(() => this.loadPositions(), 5000);
        fallback(() => this.loadStats(), 10000);
        fallback(() => this.loadSignals(), 10000);
        fallback(() => this.loadRiskStatus(), 10000);
        fallback(() => this.loadLogs(), 15000);
        fallback(() => {
            this.loadEdgeDiagnostics();
            this.loadStrategyValidation();
            this.loadIctBlockers();
            this.loadLicenseStatus();
            this.loadLicenses();
            this.loadBrokers();
        }, 30000);
        setInterval(() => {
            if (document.getElementById("future-trades")?.classList.contains("active")) {
                this.loadFutureTrades();
            }
        }, 15000);
        setInterval(() => this.loadAlerts(), 30000);
    }

    addAlert(alert) {
        if (!alert) return;
        this.alerts.push(alert);
        this.alerts = this.alerts.slice(-50);
        this.showAlertToast(alert);
        this.renderAlertsPanel();
    }

    async loadAlerts() {
        try {
            const res = await fetch(`${this.apiBase}/alerts?limit=25`);
            const data = await res.json();
            if (data.status === 'success') {
                this.alerts = data.data || [];
                this.renderAlertsPanel();
            }
        } catch (e) {
            console.warn('Alerts unavailable', e);
            this.renderAlertsPanel('Alerts unavailable');
        }
    }

    renderAlertsPanel(errorMessage = '') {
        const list = document.getElementById('alertsPanelList');
        const count = document.getElementById('alertsCountBadge');
        if (!list) return;
        if (count) count.textContent = String(this.alerts.length || 0);

        if (errorMessage) {
            list.innerHTML = `<div class="error-state"><i class="fas fa-circle-exclamation"></i><span>${this.escapeHtml(errorMessage)}</span></div>`;
            return;
        }

        const alerts = (this.alerts || []).slice(-8).reverse();
        if (!alerts.length) {
            list.innerHTML = '<div class="empty-state-compact">No alerts yet</div>';
            return;
        }

        list.innerHTML = alerts.map((alert) => `
            <article class="alert-row ${this.escapeHtml(alert.severity || 'info')}">
                <div class="alert-row-icon"><i class="fas ${this.alertIcon(alert.severity)}"></i></div>
                <div>
                    <div class="alert-row-title">
                        <strong>${this.escapeHtml(alert.title || 'Alert')}</strong>
                        <span>${this.escapeHtml(alert.category || 'system')}</span>
                    </div>
                    <p>${this.escapeHtml(alert.message || '')}</p>
                    <small>${this.formatAlertTime(alert.timestamp || alert.created_at)}</small>
                </div>
            </article>
        `).join('');
    }

    alertIcon(severity = 'info') {
        if (severity === 'success') return 'fa-circle-check';
        if (severity === 'warning') return 'fa-triangle-exclamation';
        if (severity === 'danger') return 'fa-circle-exclamation';
        return 'fa-circle-info';
    }

    async loadRiskStatus() {
        try {
            const res = await fetch(`${this.apiBase}/risk/status`);
            const data = await res.json();
            if (data.status === 'success') {
                this.renderRiskMonitor(data.data || {});
            }
        } catch (e) {
            console.warn('Risk monitor unavailable', e);
            this.renderRiskMonitorError();
        }
    }

    renderRiskMonitor(data = {}) {
        const riskStatus = String(data.risk_status || 'SAFE').toUpperCase();
        const badgeState = riskStatus === 'DANGER' ? 'danger' : riskStatus === 'CAUTION' ? 'warning' : 'success';
        this.setBadge('riskStatusBadge', riskStatus, badgeState);

        this.setRiskMeter('riskExposureFill', data.risk_used_pct, badgeState);
        this.setRiskMeter('riskDrawdownFill', data.drawdown_used_pct, data.drawdown_used_pct >= 1 ? 'danger' : data.drawdown_used_pct >= 0.7 ? 'warning' : 'success');
        this.setRiskMeter('riskDailyCapFill', data.daily_cap_progress, data.daily_cap_progress >= 1 ? 'warning' : 'success');
        const regime = data.current_market_regime || {};
        this.safeSetText('riskRegimeLabel', regime.label ? `${String(regime.label).toUpperCase()} ${(Number(regime.confidence || 0) * 100).toFixed(0)}%` : 'Unknown');
        this.setRiskMeter('riskRegimeFill', regime.confidence || 0, this.regimeState(regime.label));

        this.safeSetText('riskExposureLabel', `${this.formatMoney(data.total_open_risk)} / ${this.formatMoney(data.max_open_risk)}`);
        this.safeSetText('riskDrawdownLabel', `${this.formatMoney(-(Number(data.session_drawdown || 0)))} / ${this.formatMoney(data.max_drawdown_allowed)}`);
        this.safeSetText('riskDailyCapLabel', `${this.formatMoney(data.daily_realized_pnl)} / ${this.formatMoney(data.daily_cap_amount)}`);

        const lockouts = document.getElementById('riskLockoutsList');
        if (lockouts) {
            const items = data.symbols_locked_out || [];
            lockouts.innerHTML = items.length
                ? items.map((item) => `<span class="setup-chip fail">${this.escapeHtml(item.symbol)}: ${this.escapeHtml(item.reason || 'Locked')}</span>`).join('')
                : '<span class="setup-chip pass">None</span>';
        }

        const blockers = document.getElementById('riskBlockersList');
        if (blockers) {
            const items = data.risk_blockers || [];
            blockers.innerHTML = items.length
                ? items.map((item) => `
                    <div class="risk-blocker ${this.escapeHtml(item.severity || 'info')}">
                        <i class="fas ${item.severity === 'danger' ? 'fa-circle-exclamation' : 'fa-triangle-exclamation'}"></i>
                        <span>${this.escapeHtml(item.reason || 'Risk blocker')}</span>
                    </div>
                `).join('')
                : '<div class="empty-state-compact">No active blockers</div>';
        }
    }

    setRiskMeter(id, value, state = 'success') {
        const el = document.getElementById(id);
        if (!el) return;
        const pct = Math.max(0, Math.min(100, Number(value || 0) * 100));
        el.style.width = `${pct}%`;
        el.dataset.state = state;
    }

    regimeState(label = '') {
        const value = String(label || '').toLowerCase();
        if (['news-driven', 'volatile', 'low-liquidity'].includes(value)) return 'danger';
        if (['ranging', 'compression'].includes(value)) return 'warning';
        if (['trending', 'expansion'].includes(value)) return 'success';
        return 'neutral';
    }

    renderRiskMonitorError() {
        this.setBadge('riskStatusBadge', 'ERROR', 'danger');
        const blockers = document.getElementById('riskBlockersList');
        if (blockers) {
            blockers.innerHTML = '<div class="risk-blocker danger"><i class="fas fa-circle-exclamation"></i><span>Risk endpoint unavailable</span></div>';
        }
    }

    showAlertToast(alert) {
        const stack = document.getElementById('alertToastStack');
        if (!stack) return;
        const toast = document.createElement('div');
        toast.className = `alert-toast ${alert.severity || 'info'}`;

        const title = document.createElement('strong');
        title.textContent = alert.title || 'Alert';
        const meta = document.createElement('span');
        meta.textContent = `${alert.category || 'system'}${alert.symbol ? ` | ${alert.symbol}` : ''}`;
        const message = document.createElement('p');
        message.textContent = alert.message || '';

        toast.appendChild(title);
        toast.appendChild(meta);
        toast.appendChild(message);
        stack.prepend(toast);

        window.setTimeout(() => {
            toast.classList.add('leaving');
            window.setTimeout(() => toast.remove(), 250);
        }, 8000);

        while (stack.children.length > 5) {
            stack.lastElementChild?.remove();
        }
    }

    setupVisionChart() {
        const container = document.getElementById('visionChart');
        if (!container || !window.LightweightCharts) return;

        container.innerHTML = '';
        this.visionChart = LightweightCharts.createChart(container, {
            width: container.clientWidth,
            height: 220,
            layout: {
                background: { color: '#0f172a' },
                textColor: '#e2e8f0',
            },
            grid: {
                vertLines: { color: 'rgba(148, 163, 184, 0.1)' },
                horzLines: { color: 'rgba(148, 163, 184, 0.1)' },
            },
            crosshair: {
                mode: 0,
            },
            timeScale: {
                visible: false,
                borderColor: 'rgba(148, 163, 184, 0.3)',
            },
        });

        if (this.visionChart && typeof this.visionChart.addLineSeries === 'function') {
            this.visionSeries = this.visionChart.addLineSeries({
                color: '#60a5fa',
                lineWidth: 2,
            });
        } else if (this.visionChart && typeof this.visionChart.addAreaSeries === 'function') {
            console.warn('addLineSeries not available; using addAreaSeries fallback');
            this.visionSeries = this.visionChart.addAreaSeries({
                topColor: 'rgba(96, 165, 250, 0.5)',
                bottomColor: 'rgba(96, 165, 250, 0.01)',
                lineColor: '#60a5fa',
                lineWidth: 2,
            });
        } else {
            console.error('Vision chart: no series method available');
            this.visionSeries = null;
        }

        if (this.visionChart && typeof this.visionChart.addLineSeries === 'function') {
            this.visionTrendSeries = this.visionChart.addLineSeries({
                color: '#f59e0b',
                lineWidth: 2,
                lineStyle: 2,
                priceLineVisible: false,
                lastValueVisible: false,
            });
        }
    }

    applyRealtimePayload(payload) {
        if (!payload) return;

        const status = payload.status || {};
        this.safeSetText('botStatus', status.running ? 'Online' : 'Offline');
        this.setBadge('topBotBadge', status.running ? 'Bot Online' : 'Bot Offline', status.running ? 'success' : 'neutral');
        this.safeSetText('sidebarBotState', status.running ? 'Bot Online' : 'Bot Offline');
        const sidebarBotState = document.getElementById('sidebarBotState');
        if (sidebarBotState) sidebarBotState.dataset.state = status.running ? 'online' : 'offline';
        const statusIcon = document.getElementById('statusIcon');
        if (statusIcon) statusIcon.className = status.running ? 'fas fa-circle online' : 'fas fa-circle';
        this.safeSetText('connected', status.connected ? 'Connected' : 'Disconnected');
        const brokerLabel = status.broker?.name || status.broker?.type || 'Broker';
        this.setBadge('topConnectionBadge', status.connected ? `${brokerLabel} Connected` : `${brokerLabel} Offline`, status.connected ? 'success' : 'danger');
        this.safeSetText('sidebarConnectionState', status.connected ? `${brokerLabel} Connected` : `${brokerLabel} Offline`);
        const sidebarConnectionDot = document.getElementById('sidebarConnectionDot');
        if (sidebarConnectionDot) sidebarConnectionDot.dataset.state = status.connected ? 'connected' : 'disconnected';
        this.safeSetText('balance', `$${(status.balance || 0).toFixed(2)}`);
        this.safeSetText('equity', `$${(status.equity || 0).toFixed(2)}`);
        this.safeSetText('margin', `$${(status.free_margin || 0).toFixed(2)}`);
        this.renderBrokerStatus(status);
        this.setMoney('dailyProfit', status.daily_profit);
        this.setMoney('floatingProfit', status.floating_profit);
        this.setMoney('realizedProfit', status.realized_profit);
        this.setMoney('netProfit', status.net_profit);
        this.setMoney('drawdown', status.floating_drawdown);
        this.safeSetText('activeTrades', status.active_trades || 0);
        this.safeSetText('botScoreGrade', this.formatReadinessLabel(status, status.bot_score?.grade ? `${status.bot_score.grade} - ${status.bot_score.label || 'Ready'}` : '--'));
        this.renderScanStatus(status.scan);
        this.renderLicenseStatus(status.license || {});
        const tm = status.trade_management || {};
        const lockPips = Number(tm.trailing_sl_lock_pips || 0);
        this.safeSetText('trailingSlState', tm.trailing_sl ? `On @ ${Math.round((tm.trailing_sl_trigger_pct || 0) * 100)}% / +${lockPips.toFixed(1)}p` : 'Off');
        this.safeSetText('trailingTpState', this.formatProfitProtectionState(tm));

        if (payload.signals) {
            const signals = payload.signals.recent || [];
            this.updateMarketWatch(signals);
            this.updateVision(signals);
            this.updateExecutionTimeline(signals);
            this.renderStrategyBreakdown(signals);
            this.renderSpreadSafety(signals);
        }
        if (payload.logs) {
            this.renderRejectionSummary(payload.logs.rejections || []);
        }
        if (payload.alerts) {
            this.alerts = payload.alerts || [];
            this.renderAlertsPanel();
        }
        if (payload.risk) {
            this.renderRiskMonitor(payload.risk);
        }
        this.renderRiskBanner(status);
        this.renderBlockedTradingPanel(status);

        // Optional stats update
        if (payload.stats || payload.statistics) {
            const stats = payload.stats || payload.statistics || {};
            this.safeSetText('winRate', stats.win_rate != null ? `${(stats.win_rate * 100).toFixed(1)}%` : '0%');
            this.safeSetText('expectancy', stats.expectancy != null ? stats.expectancy.toFixed(2) : '0');
            this.safeSetText('avgWin', stats.avg_win != null ? `$${stats.avg_win.toFixed(2)}` : '$0');
            this.safeSetText('avgLoss', stats.avg_loss != null ? `$${Math.abs(stats.avg_loss).toFixed(2)}` : '$0');
            this.safeSetText('totalTrades', stats.total_trades || stats.trades || 0);
        }

        if (payload.pending_orders) {
            const pendingBody = document.getElementById('pendingOrdersBody');
            if (pendingBody && payload.pending_orders.length) {
                pendingBody.innerHTML = '';
                payload.pending_orders.forEach((o) => {
                    const row = document.createElement('tr');
                    row.innerHTML = `
                        <td>${o.symbol}</td>
                        <td>${o.action || '-'}</td>
                        <td>${o.entry ? Number(o.entry).toFixed(5) : '-'}</td>
                        <td>${o.sl ? Number(o.sl).toFixed(5) : '-'}</td>
                        <td>${o.tp ? Number(o.tp).toFixed(5) : '-'}</td>
                        <td>${o.status || 'PENDING'}</td>
                    `;
                    pendingBody.appendChild(row);
                });
            } else if (pendingBody) {
                pendingBody.innerHTML = '<tr><td colspan="6" style="text-align:center">No pending orders</td></tr>';
            }
        }

        if (payload.positions) {
            const activeTbody = document.querySelector('#activeTradesBody');
            if (!activeTbody) {
                this.loadPositions();
            } else if (payload.positions.length) {
                activeTbody.innerHTML = '';
                payload.positions.forEach((pos) => {
                    const row = activeTbody.insertRow();
                    const profitColor = pos.profit >= 0 ? '#10b981' : '#ef4444';
                    const rText = pos.r_multiple != null ? `${Number(pos.r_multiple).toFixed(2)}R` : '--';
                    row.innerHTML = `
                        <td>${pos.symbol}</td>
                        <td>${pos.type}</td>
                        <td>${pos.volume}</td>
                        <td>${pos.entry ? Number(pos.entry).toFixed(5) : '-'}</td>
                        <td>${pos.current ? Number(pos.current).toFixed(5) : '-'}</td>
                        <td>${pos.sl ? Number(pos.sl).toFixed(5) : '-'}</td>
                        <td>${pos.tp ? Number(pos.tp).toFixed(5) : '-'}</td>
                        <td>${rText}</td>
                        <td style="color: ${profitColor}">${this.formatMoney(pos.profit)}</td>
                        <td>${this.formatPositionManagement(pos)}</td>
                    `;
                });
            } else {
                activeTbody.innerHTML = '';
                activeTbody.innerHTML = '<tr><td colspan="10" style="text-align:center">No active trades</td></tr>';
            }
        }

        if (payload.metrics) this.updateVisionPanel(payload.metrics || {});
        if (payload.validation_logs) this.renderValidationLogs(payload.validation_logs || []);
    }

    setupVisionCardClicks() {
        document.querySelectorAll('.vision-card .view-details').forEach((btn) => {
            btn.addEventListener('click', (e) => {
                e.stopPropagation();
                const key = btn.dataset.cardKey;
                this.openDeepDiveModal(key);
            });
        });

        document.querySelectorAll('.vision-card').forEach((card) => {
            card.addEventListener('click', () => {
                const key = card.dataset.visionCard;
                this.openDeepDiveModal(key);
            });
        });

        document.getElementById('closeDeepDiveBtn')?.addEventListener('click', () => {
            this.closeDeepDiveModal();
        });

        // Close drag or outside click for central modal
        document.querySelector('#deepDiveModal')?.addEventListener('click', (e) => {
            if (e.target.id === 'deepDiveModal') {
                this.closeDeepDiveModal();
            }
        });

        document.querySelector('#deepDiveModal .modal-close')?.addEventListener('click', () => {
            this.closeDeepDiveModal();
        });

        window.addEventListener('keydown', (e) => {
            if (e.key === 'Escape') this.closeDeepDiveModal();
        });
    }

    openDeepDiveModal(cardKey) {
        const titleMap = {
            trendConf: 'Trend Confidence',
            modelScore: 'Model Score',
            emaCheck: 'EMA Check',
            pressure: 'Position Pressure',
            apiExplorer: 'API Explorer',
        };
        const title = titleMap[cardKey] || 'Deep Dive';
        const modalTitle = document.getElementById('deepDiveTitle');
        const subtext = document.getElementById('deepDiveSubtext');
        const logsElem = document.getElementById('validationLogsList');
        const apiElem = document.getElementById('apiEndpointsList');

        if (modalTitle) modalTitle.textContent = title;

        if (cardKey === 'apiExplorer') {
            if (subtext) subtext.textContent = 'Backend API endpoints and docs';
            if (apiElem) {
                apiElem.style.display = 'block';
                this.renderApiEndpoints(this.apiEndpoints || []);
            }
            if (logsElem) logsElem.style.display = 'none';
        } else {
            if (subtext) subtext.textContent = 'Latest validation logs for selected metric';
            if (apiElem) apiElem.style.display = 'none';
            if (logsElem) {
                logsElem.style.display = 'block';
                this.renderValidationLogs(this.validationLogs || []);
            }
        }

        const modal = document.getElementById('deepDiveModal');
        if (modal) modal.classList.add('open');
    }

    closeDeepDiveModal() {
        const modal = document.getElementById('deepDiveModal');
        if (modal) modal.classList.remove('open');
    }

    renderValidationLogs(logs = []) {
        this.validationLogs = logs;
        const list = document.getElementById('validationLogsList');
        if (!list) return;
        list.innerHTML = '';
        if (!logs.length) {
            list.innerHTML = '<li>No validation logs available yet</li>';
            return;
        }
        logs.slice(-20).reverse().forEach((log) => {
            const item = document.createElement('li');
            const statusClass = log.status ? log.status.toLowerCase() : 'pending';
            item.innerHTML = `<div>${new Date(log.timestamp || Date.now()).toLocaleTimeString()}</div> <div><strong>${log.message || log.detail}</strong></div><span class="status-pill ${statusClass}">${(log.status || 'PENDING').toUpperCase()}</span>`;
            list.appendChild(item);
        });
    }

    async loadApiEndpoints() {
        try {
            const res = await fetch('/api/endpoints');
            if (!res.ok) {
                console.warn('API endpoint list failed', res.status);
                return;
            }

            const data = await res.json();
            if (data?.status === 'success' && Array.isArray(data.data)) {
                this.apiEndpoints = data.data;
                const count = data.data.length;
                const counter = document.getElementById('apiEndpointCount');
                if (counter) {
                    counter.textContent = `${count} endpoints`;
                }
            }
        } catch (err) {
            console.error('loadApiEndpoints error', err);
        }
    }

    renderApiEndpoints(endpoints = []) {
        const list = document.getElementById('apiEndpointsList');
        if (!list) return;
        list.innerHTML = '';

        if (!endpoints.length) {
            list.innerHTML = '<li>No endpoints discovered</li>';
            return;
        }

        endpoints.forEach((endpoint) => {
            const card = document.createElement('li');
            const method = (endpoint.method || 'GET').toUpperCase();
            const path = endpoint.path || '/';
            const desc = endpoint.description || '';
            card.innerHTML = `<div><span class="status-pill ${method.toLowerCase()}">${method}</span> <strong>${path}</strong></div><div>${desc}</div>`;
            list.appendChild(card);
        });
    }

    updateVisionPanel(metrics = {}) {
        const trend = metrics.trend_confidence ?? 0;
        const model = metrics.model_score ?? 0;
        const ema = metrics.ema_check ?? 0;
        const pressure = metrics.position_pressure ?? 0;

        this.setConviction('trendConfidenceVal', trend);
        this.setConviction('modelScoreVal', model);
        this.setConviction('emaCheckVal', ema);
        this.setConviction('pressureVal', pressure);

        this.setConvictionBarValue('trendConf', trend);
        this.setConvictionBarValue('modelScore', model);
        this.setConvictionBarValue('emaCheck', ema);
        this.setConvictionBarValue('pressure', pressure);
    }

    setConviction(id, value) {
        const el = document.getElementById(id);
        if (!el) return;
        const normalized = Math.max(0, Math.min(1, Number(value)));
        el.textContent = normalized.toFixed(2);
    }

    setConvictionBarValue(cardKey, value) {
        const card = document.querySelector(`[data-vision-card="${cardKey}"] .conviction-bar`);
        if (!card) return;
        const normalized = Math.max(0, Math.min(1, Number(value)));
        card.dataset.value = normalized.toFixed(2);
        card.style.setProperty('--conviction-percent', `${normalized * 100}%`);

        const tone = normalized >= 0.7 ? 'high' : normalized >= 0.4 ? 'medium' : 'low';
        card.classList.remove('conviction-low', 'conviction-medium', 'conviction-high');
        card.classList.add(`conviction-${tone}`);
    }

    showLogicTooltip(target, details, event) {
        if (!this.logicTooltip) return;
        this.logicTooltip.textContent = details;
        this.logicTooltip.classList.add('visible');

        const rect = target.getBoundingClientRect();
        const menu = document.querySelector('.main-content');
        const baseRect = menu ? menu.getBoundingClientRect() : { left: 0, top: 0 };

        this.logicTooltip.style.left = `${rect.left - baseRect.left + rect.width / 2}px`;
        this.logicTooltip.style.top = `${rect.top - baseRect.top - 36}px`;
        this.logicTooltip.style.display = 'block';
    }

    hideLogicTooltip() {
        if (!this.logicTooltip) return;
        this.logicTooltip.classList.remove('visible');
        this.logicTooltip.style.display = 'none';
    }

    navigate(e) {
        e.preventDefault();
        const page = e.currentTarget.dataset.page;

        document.querySelectorAll(".nav-link, .sidebar-utility").forEach((l) =>
            l.classList.remove("active")
        );
        if (e.currentTarget.classList.contains("nav-link") || e.currentTarget.classList.contains("sidebar-utility")) {
            e.currentTarget.classList.add("active");
        }

        document.querySelectorAll(".page").forEach((p) =>
            p.classList.remove("active")
        );
        document.getElementById(page).classList.add("active");

        if (page === "positions") this.loadPositions();
        if (page === "future-trades") this.loadFutureTrades();
        if (page === "logs") this.loadLogs();
        if (page === "journal") this.loadJournal();
        if (page === "settings") this.loadSettings();
        this.closeMobileSidebar();
    }

    async loadJournal() {
        const tbody = document.getElementById('journalTableBody');
        if (!tbody) return;
        tbody.innerHTML = '<tr><td colspan="9">Loading journal...</td></tr>';
        const params = new URLSearchParams();
        const symbol = document.getElementById('journalSymbolFilter')?.value?.trim();
        const decision = document.getElementById('journalDecisionFilter')?.value;
        const grade = document.getElementById('journalGradeFilter')?.value;
        const tradeType = document.getElementById('journalTypeFilter')?.value;
        const date = document.getElementById('journalDateFilter')?.value;
        if (symbol) params.set('symbol', symbol.toUpperCase());
        if (decision) params.set('decision', decision);
        if (grade) params.set('grade', grade);
        if (tradeType) params.set('trade_type', tradeType);
        if (date) params.set('date', date);
        params.set('limit', '250');

        try {
            const res = await fetch(`${this.apiBase}/journal?${params.toString()}`);
            const data = await res.json();
            if (data.status !== 'success') {
                throw new Error(data.message || 'Journal unavailable');
            }
            this.journalRows = data.data || [];
            this.journalPage = 1;
            this.renderJournal();
        } catch (e) {
            tbody.innerHTML = `<tr><td colspan="9" class="metric-loss">${this.escapeHtml(e.message)}</td></tr>`;
        }
    }

    changeJournalPage(delta) {
        const totalPages = Math.max(1, Math.ceil(this.journalRows.length / this.journalPageSize));
        this.journalPage = Math.min(totalPages, Math.max(1, this.journalPage + delta));
        this.renderJournal();
    }

    formatPageInfo(total, start, count, label, page, totalPages) {
        if (!total) return `0 ${label}`;
        const first = start + 1;
        const last = start + count;
        return `${first}-${last} of ${total} | ${page}/${totalPages}`;
    }

    renderJournal(rows = this.journalRows) {
        const tbody = document.getElementById('journalTableBody');
        if (!tbody) return;
        const totalPages = Math.max(1, Math.ceil(rows.length / this.journalPageSize));
        this.journalPage = Math.min(totalPages, Math.max(1, this.journalPage));
        const start = (this.journalPage - 1) * this.journalPageSize;
        const pageRows = rows.slice(start, start + this.journalPageSize);
        const pageInfo = document.getElementById('journalPageInfo');
        const prev = document.getElementById('journalPrevBtn');
        const next = document.getElementById('journalNextBtn');
        if (pageInfo) pageInfo.textContent = this.formatPageInfo(rows.length, start, pageRows.length, 'entries', this.journalPage, totalPages);
        if (prev) prev.disabled = this.journalPage <= 1;
        if (next) next.disabled = this.journalPage >= totalPages;
        if (!rows.length) {
            tbody.innerHTML = '<tr><td colspan="9">No journal entries match the filters</td></tr>';
            return;
        }
        tbody.innerHTML = pageRows.map((row) => {
            const decision = String(row.execution_decision || 'WAIT').toUpperCase();
            const decisionClass = decision === 'READY' ? 'success' : decision === 'REJECTED' ? 'danger' : decision === 'WATCH' ? 'warning' : 'neutral';
            const score = row.score != null ? `${(Number(row.score) * 100).toFixed(0)}%` : '--';
            const conviction = row.final_conviction != null ? `${(Number(row.final_conviction) * 100).toFixed(0)}%` : '--';
            return `
                <tr>
                    <td>${this.escapeHtml(this.formatAlertTime(row.timestamp))}</td>
                    <td><strong>${this.escapeHtml(row.symbol || '--')}</strong></td>
                    <td>${this.escapeHtml(row.direction || '--')}</td>
                    <td>${this.escapeHtml(row.trade_type || '--')}</td>
                    <td><span class="setup-grade grade-${String(row.grade || '').toLowerCase()}">${this.escapeHtml(row.grade || '--')}</span></td>
                    <td>${score}</td>
                    <td>${conviction}</td>
                    <td><span class="status-badge ${decisionClass}">${this.escapeHtml(decision)}</span></td>
                    <td>${this.escapeHtml(row.rejection_reason || row.archetype || '--')}</td>
                </tr>
            `;
        }).join('');
    }

    async loadStrategyValidation() {
        const badge = document.getElementById('strategyValidationBadge');
        if (!badge) return;
        try {
            this.setBadge('strategyValidationBadge', 'Loading', 'neutral');
            const res = await fetch(`${this.apiBase}/analytics/strategy-validation`);
            const data = await res.json();
            if (data.status !== 'success') {
                throw new Error(data.message || 'Strategy validation unavailable');
            }
            this.strategyValidation = data.data || {};
            this.renderStrategyValidation(this.strategyValidation);
        } catch (e) {
            this.setBadge('strategyValidationBadge', 'Error', 'danger');
            const target = document.getElementById('validationGatesList');
            if (target) {
                target.innerHTML = `<div class="error-state"><i class="fas fa-circle-exclamation"></i><span>${this.escapeHtml(e.message)}</span></div>`;
            }
        }
    }

    renderStrategyValidation(data = {}) {
        const status = String(data.status || 'UNPROVEN').toUpperCase();
        const state = status === 'VALIDATED' ? 'success' : status === 'PROMISING' ? 'warning' : status === 'DEGRADED' ? 'danger' : 'neutral';
        this.setBadge('strategyValidationBadge', status, state);

        const context = data.context || {};
        const datasets = data.datasets || {};
        const thresholds = data.thresholds || {};
        this.safeSetText(
            'strategyValidationContext',
            `Strategy ${context.strategy_version || 'local-dev'} | Config ${context.config_version || '--'} | Group ${context.symbol_group || 'default'} | Forward ${datasets.forward_trades_matching_context ?? 0}/${datasets.forward_trades_all ?? 0} | Historical ${datasets.historical_trades ?? 0}`
        );

        const overall = data.overall || {};
        const ci = overall.confidence_interval || {};
        this.safeSetText('validationSampleSize', `${overall.sample_size ?? 0} / ${thresholds.min_sample ?? 30}`);
        this.safeSetText('validationExpectancy', overall.expectancy != null ? this.formatMoney(overall.expectancy) : '--');
        this.safeSetText('validationSharpe', this.formatEdgeMetric(overall.sharpe, 2));
        this.safeSetText('validationDrawdown', overall.drawdown != null ? this.formatMoney(overall.drawdown) : '--');
        this.safeSetText('validationAverageR', overall.average_r != null ? `${this.formatEdgeMetric(overall.average_r, 2)}R` : '--');
        this.safeSetText('validationCi', ci.low == null ? '--' : `${this.formatMoney(ci.low)} to ${this.formatMoney(ci.high)}`);

        this.renderValidationGates(data.gates || {});
        this.renderValidationRolling(data.rolling_latest || null);
    }

    renderValidationGates(gates = {}) {
        const target = document.getElementById('validationGatesList');
        if (!target) return;
        const labels = [
            ['minimum_sample_met', 'Minimum sample met'],
            ['expectancy_positive', 'Expectancy positive'],
            ['confidence_interval_positive', 'CI above zero'],
            ['expectancy_stable', 'Rolling expectancy stable'],
            ['degradation_detected', 'Degradation detected', true],
        ];
        target.innerHTML = labels.map(([key, label, inverse]) => {
            const passed = Boolean(gates[key]);
            const good = inverse ? !passed : passed;
            return `
                <div class="validation-gate-row ${good ? 'pass' : 'fail'}">
                    <i class="fas ${good ? 'fa-check' : 'fa-triangle-exclamation'}"></i>
                    <span>${this.escapeHtml(label)}</span>
                    <strong>${passed ? 'Yes' : 'No'}</strong>
                </div>
            `;
        }).join('');
    }

    renderValidationRolling(latest = null) {
        const target = document.getElementById('validationRollingList');
        if (!target) return;
        if (!latest) {
            target.innerHTML = '<div class="empty-state-compact">No forward-test windows yet</div>';
            return;
        }
        const ci = latest.confidence_interval || {};
        const rows = [
            ['Window', latest.window ?? 0],
            ['Rolling expectancy', this.formatMoney(latest.expectancy)],
            ['Rolling Sharpe', this.formatEdgeMetric(latest.sharpe, 2)],
            ['Rolling drawdown', this.formatMoney(latest.drawdown)],
            ['Rolling R', `${this.formatEdgeMetric(latest.rolling_r_multiple, 2)}R`],
            ['Rolling CI', ci.low == null ? '--' : `${this.formatMoney(ci.low)} to ${this.formatMoney(ci.high)}`],
        ];
        target.innerHTML = rows.map(([label, value]) => `
            <div class="validation-gate-row neutral">
                <span>${this.escapeHtml(label)}</span>
                <strong>${this.escapeHtml(value)}</strong>
            </div>
        `).join('');
    }

    async loadLicenseStatus() {
        try {
            const res = await fetch(`${this.apiBase}/license/status`);
            const data = await res.json();
            if (data.status === 'success') {
                this.renderLicenseStatus(data.data || {});
            }
        } catch (e) {
            console.warn('License status unavailable', e);
        }
    }

    renderLicenseStatus(data = {}) {
        const license = data.license || {};
        const status = String(data.status || 'missing').toUpperCase();
        const days = data.days_remaining;
        const state = data.trading_allowed ? (status === 'EXPIRING_SOON' || status === 'GRACE' ? 'warning' : 'success') : 'danger';
        this.safeSetText('licenseStatus', status);
        this.safeSetText('licenseKey', this.maskLicenseKey(license.license_key || ''));
        this.safeSetText('licenseActivatedAt', this.formatDateOnly(license.activated_at));
        this.safeSetText('licenseExpiresAt', this.formatDateOnly(license.expires_at));
        this.safeSetText('licenseDaysRemaining', days == null ? '--' : String(days));
        this.safeSetText('topLicenseBadge', data.trading_allowed ? `License ${status}` : 'License Blocked');
        this.setBadge('topLicenseBadge', data.trading_allowed ? `License ${status}` : 'License Blocked', state);
        this.setBadge('licenseManagementBadge', data.trading_allowed ? 'Licensed' : 'Trading Blocked', state);
        const banner = document.getElementById('riskWarningBanner');
        const title = document.getElementById('riskWarningTitle');
        const message = document.getElementById('riskWarningMessage');
        if (banner && !data.trading_allowed) {
            banner.classList.remove('hidden');
            if (title) title.textContent = 'License attention required';
            if (message) message.textContent = data.reason || 'Live trading is disabled until a valid license is activated.';
        }
    }

    async loadCurrentUser() {
        try {
            const res = await fetch(`${this.apiBase}/auth/me`);
            const data = await res.json();
            if (data.status !== 'success') return;
            const payload = data.data || {};
            this.currentUser = payload.user || null;
            this.permissions = new Set(payload.permissions || ['read']);
            this.applyRolePermissions();
        } catch (e) {
            console.warn('User role unavailable', e);
        }
    }

    applyRolePermissions() {
        const permissions = ['trade', 'panic', 'settings', 'users', 'licenses', 'brokers'];
        permissions.forEach((permission) => {
            document.querySelectorAll(`[data-permission="${permission}"]`).forEach((el) => {
                el.style.display = this.hasPermission(permission) ? '' : 'none';
            });
        });

        document.querySelectorAll('#startBtn, #stopBtn, [data-broker-action], [data-license-action]').forEach((el) => {
            const required = el.dataset.brokerAction ? 'brokers' : el.dataset.licenseAction ? 'licenses' : 'trade';
            el.disabled = !this.hasPermission(required);
        });
        document.getElementById('saveSettingsBtn')?.toggleAttribute('disabled', !this.hasPermission('settings'));
    }

    maskLicenseKey(key) {
        const clean = String(key || '');
        if (!clean) return '--';
        if (clean.length <= 10) return clean;
        return `${clean.slice(0, 10)}...${clean.slice(-4)}`;
    }

    formatDateOnly(value) {
        if (!value) return '--';
        const date = new Date(value);
        if (Number.isNaN(date.getTime())) return '--';
        return date.toLocaleDateString();
    }

    async activateLicense(e) {
        e.preventDefault();
        const licenseKey = document.getElementById('activateLicenseKey')?.value?.trim();
        const machineId = document.getElementById('activateMachineId')?.value?.trim();
        if (!licenseKey) {
            this.showNotification('Enter a license key to activate.', 'error');
            return;
        }
        try {
            const res = await fetch(`${this.apiBase}/license/activate`, {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({license_key: licenseKey, machine_id: machineId}),
            });
            const data = await res.json();
            if (data.status !== 'success') throw new Error(data.message || 'Activation failed');
            this.showNotification('License activated for this machine.', 'success');
            await this.loadLicenseStatus();
            await this.loadLicenses();
        } catch (err) {
            this.showNotification(`Activation error: ${err.message}`, 'error');
        }
    }

    async createLicense(e) {
        e.preventDefault();
        const payload = {
            customer_name: document.getElementById('newLicenseCustomer')?.value?.trim(),
            email: document.getElementById('newLicenseEmail')?.value?.trim(),
            max_accounts: Number(document.getElementById('newLicenseMaxAccounts')?.value || 1),
        };
        if (!payload.customer_name || !payload.email) {
            this.showNotification('Customer name and email are required.', 'error');
            return;
        }
        try {
            const res = await fetch(`${this.apiBase}/licenses/create`, {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify(payload),
            });
            const data = await res.json();
            if (data.status !== 'success') throw new Error(data.message || 'License create failed');
            this.showNotification(`License created: ${data.data.license_key}`, 'success');
            await this.loadLicenses();
        } catch (err) {
            this.showNotification(`Create error: ${err.message}`, 'error');
        }
    }

    async loadLicenses() {
        const tbody = document.getElementById('licensesTableBody');
        if (!tbody) return;
        try {
            const res = await fetch(`${this.apiBase}/licenses`);
            const data = await res.json();
            if (data.status !== 'success') throw new Error(data.message || 'Licenses unavailable');
            this.licenses = data.data || [];
            this.renderLicenses();
        } catch (err) {
            tbody.innerHTML = `<tr><td colspan="10" class="metric-loss">${this.escapeHtml(err.message)}</td></tr>`;
        }
    }

    renderLicenses() {
        const tbody = document.getElementById('licensesTableBody');
        if (!tbody) return;
        if (!this.licenses.length) {
            tbody.innerHTML = '<tr><td colspan="10">No licenses created yet</td></tr>';
            return;
        }
        tbody.innerHTML = this.licenses.map((item) => {
            const active = item.is_active !== false;
            const days = this.daysUntil(item.expires_at);
            return `
                <tr>
                    <td><strong>${this.escapeHtml(item.license_key || '--')}</strong></td>
                    <td>${this.escapeHtml(item.customer_name || '--')}</td>
                    <td>${this.escapeHtml(item.email || '--')}</td>
                    <td><span class="status-badge ${active ? 'success' : 'danger'}">${active ? 'Active' : 'Revoked'}</span></td>
                    <td>${this.escapeHtml(this.formatDateOnly(item.activated_at))}</td>
                    <td>${this.escapeHtml(this.formatDateOnly(item.expires_at))}</td>
                    <td>${days == null ? '--' : this.escapeHtml(String(days))}</td>
                    <td title="${this.escapeHtml(item.machine_id || '')}">${this.escapeHtml(this.truncateMiddle(item.machine_id || 'Unbound', 18))}</td>
                    <td>${this.escapeHtml(item.hostname || '--')}</td>
                    <td>
                        <button class="btn-small btn-reset" type="button" data-license-action="extend" data-key="${this.escapeHtml(item.license_key)}">Extend</button>
                        <button class="btn-small btn-reset" type="button" data-license-action="reset" data-key="${this.escapeHtml(item.license_key)}">Reset</button>
                        <button class="btn-small btn-danger" type="button" data-license-action="revoke" data-key="${this.escapeHtml(item.license_key)}">Revoke</button>
                    </td>
                </tr>
            `;
        }).join('');
        tbody.querySelectorAll('[data-license-action]').forEach((button) => {
            button.addEventListener('click', () => this.handleLicenseAction(button.dataset.licenseAction, button.dataset.key));
        });
        this.applyRolePermissions();
    }

    daysUntil(value) {
        if (!value) return null;
        const expires = new Date(value);
        if (Number.isNaN(expires.getTime())) return null;
        return Math.floor((expires.getTime() - Date.now()) / 86400000);
    }

    truncateMiddle(value, max = 18) {
        const text = String(value || '');
        if (text.length <= max) return text;
        const edge = Math.max(4, Math.floor((max - 3) / 2));
        return `${text.slice(0, edge)}...${text.slice(-edge)}`;
    }

    async handleLicenseAction(action, key) {
        if (!key) return;
        const endpoints = {
            extend: ['licenses/extend', {license_key: key, days: 365}],
            reset: ['licenses/reset-machine', {license_key: key}],
            revoke: ['licenses/revoke', {license_key: key}],
        };
        const selected = endpoints[action];
        if (!selected) return;
        if (action === 'revoke' && !confirm(`Revoke license ${key}?`)) return;
        try {
            const res = await fetch(`${this.apiBase}/${selected[0]}`, {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify(selected[1]),
            });
            const data = await res.json();
            if (data.status !== 'success') throw new Error(data.message || 'License action failed');
            this.showNotification(`License ${action} completed.`, 'success');
            await this.loadLicenses();
            await this.loadLicenseStatus();
        } catch (err) {
            this.showNotification(`License action error: ${err.message}`, 'error');
        }
    }

    renderBrokerStatus(status = {}) {
        const broker = status.broker || {};
        const connected = Boolean(status.connected || broker.connected);
        this.safeSetText('brokerActiveName', broker.name || broker.account || '--');
        this.safeSetText('brokerConnectionStatus', connected ? 'Connected' : 'Offline');
        this.safeSetText('brokerBalance', `$${(status.balance || 0).toFixed(2)}`);
        const marginLevel = status.margin_level == null ? '--' : `${Number(status.margin_level || 0).toFixed(1)}%`;
        this.safeSetText('brokerMarginLevel', marginLevel);
        const spread = broker.spread_pips == null ? '--' : `${Number(broker.spread_pips).toFixed(1)}p ${broker.spread_symbol || ''}`.trim();
        this.safeSetText('brokerSpread', spread);
        this.setBadge('brokerManagementBadge', connected ? 'Connected' : 'Profiles', connected ? 'success' : 'neutral');
    }

    async loadBrokers() {
        const tbody = document.getElementById('brokersTableBody');
        if (!tbody) return;
        try {
            const res = await fetch(`${this.apiBase}/brokers`);
            const data = await res.json();
            if (data.status !== 'success') throw new Error(data.message || 'Brokers unavailable');
            const payload = data.data || {};
            this.brokers = payload.profiles || [];
            this.renderBrokerProfiles();
        } catch (err) {
            tbody.innerHTML = `<tr><td colspan="7" class="metric-loss">${this.escapeHtml(err.message)}</td></tr>`;
            this.setBadge('brokerManagementBadge', 'Unavailable', 'danger');
        }
    }

    async loadBrokerStatus() {
        try {
            const res = await fetch(`${this.apiBase}/broker/status`);
            const data = await res.json();
            if (data.status === 'success') {
                this.activeBrokerStatus = data.data || null;
            }
        } catch (err) {
            console.warn('Broker status unavailable', err);
        }
    }

    renderBrokerProfiles() {
        const tbody = document.getElementById('brokersTableBody');
        if (!tbody) return;
        if (!this.brokers.length) {
            tbody.innerHTML = '<tr><td colspan="7">No broker profiles yet</td></tr>';
            return;
        }
        tbody.innerHTML = this.brokers.map((item) => {
            const disabled = Boolean(item.is_disabled);
            const active = Boolean(item.is_active);
            return `
                <tr>
                    <td><strong>${this.escapeHtml(item.name || '--')}</strong></td>
                    <td>${this.escapeHtml(String(item.broker_type || '--').toUpperCase())}</td>
                    <td>${this.escapeHtml(item.account || '--')}</td>
                    <td>${this.escapeHtml(item.server || '--')}</td>
                    <td><span class="status-badge ${disabled ? 'danger' : 'success'}">${disabled ? 'Disabled' : 'Enabled'}</span></td>
                    <td><span class="status-badge ${active ? 'success' : 'neutral'}">${active ? 'Active' : 'Standby'}</span></td>
                    <td>
                        <button class="btn-small btn-reset" type="button" data-broker-action="test" data-id="${item.id}">Test</button>
                        <button class="btn-small btn-reset" type="button" data-broker-action="edit" data-id="${item.id}">Edit</button>
                        <button class="btn-small btn-reset" type="button" data-broker-action="active" data-id="${item.id}" ${disabled || active ? 'disabled' : ''}>Use</button>
                        <button class="btn-small btn-danger" type="button" data-broker-action="disable" data-id="${item.id}" ${disabled ? 'disabled' : ''}>Disable</button>
                    </td>
                </tr>
            `;
        }).join('');
        tbody.querySelectorAll('[data-broker-action]').forEach((button) => {
            button.addEventListener('click', () => this.handleBrokerAction(button.dataset.brokerAction, Number(button.dataset.id || 0)));
        });
        const active = this.brokers.find((item) => item.is_active);
        this.setBadge('brokerManagementBadge', active ? `Active: ${active.name || active.account || 'Broker'}` : 'No Active Broker', active ? 'success' : 'warning');
        this.applyRolePermissions();
    }

    async createBrokerProfile(e) {
        e.preventDefault();
        const profileId = Number(document.getElementById('brokerProfileId')?.value || 0);
        const brokerType = document.getElementById('brokerType')?.value || 'mt5';
        const payload = {
            id: profileId || undefined,
            name: document.getElementById('brokerName')?.value?.trim(),
            broker_type: brokerType,
            account: document.getElementById('brokerAccount')?.value?.trim(),
            server: document.getElementById('brokerServer')?.value?.trim(),
            password: document.getElementById('brokerPassword')?.value || '',
            is_active: Boolean(document.getElementById('brokerSetActive')?.checked),
            metadata: {
                market: document.getElementById('brokerMarket')?.value || 'spot',
                testnet: brokerType === 'binance' ? Boolean(document.getElementById('brokerTestnet')?.checked) : false,
                paper: brokerType === 'binance' ? Boolean(document.getElementById('brokerPaper')?.checked) : false,
                live_trading_enabled: brokerType === 'binance' ? Boolean(document.getElementById('brokerLiveEnabled')?.checked) : false,
            },
        };
        if (!payload.name || !payload.broker_type) {
            this.showNotification('Broker name and type are required.', 'error');
            return;
        }
        try {
            const endpoint = profileId ? 'brokers/edit' : 'brokers/add';
            const res = await fetch(`${this.apiBase}/${endpoint}`, {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify(payload),
            });
            const data = await res.json();
            if (data.status !== 'success') throw new Error(data.message || 'Broker create failed');
            this.showNotification('Broker profile saved. Restart the bot after switching active profiles.', 'success');
            this.resetBrokerForm();
            await this.loadBrokers();
        } catch (err) {
            this.showNotification(`Broker create error: ${err.message}`, 'error');
        }
    }

    async handleBrokerAction(action, id) {
        if (!id) return;
        if (action === 'edit') {
            this.editBrokerProfile(id);
            return;
        }
        const endpoints = {
            test: ['brokers/test', {id}],
            active: ['brokers/active', {id}],
            disable: ['brokers/disable', {id, disabled: true}],
        };
        const selected = endpoints[action];
        if (!selected) return;
        try {
            const res = await fetch(`${this.apiBase}/${selected[0]}`, {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify(selected[1]),
            });
            const data = await res.json();
            if (data.status !== 'success') throw new Error(data.message || 'Broker action failed');
            this.showNotification(action === 'test' ? 'Broker connection test passed.' : 'Broker profile updated.', 'success');
            await this.loadBrokers();
        } catch (err) {
            this.showNotification(`Broker ${action} error: ${err.message}`, 'error');
        }
    }

    editBrokerProfile(id) {
        const profile = this.brokers.find((item) => Number(item.id) === Number(id));
        if (!profile) return;
        this.safeSetText('brokerFormTitle', 'Edit Broker Account');
        const fields = {
            brokerProfileId: profile.id || '',
            brokerName: profile.name || '',
            brokerType: profile.broker_type || 'mt5',
            brokerAccount: profile.account || '',
            brokerServer: profile.server || '',
            brokerPassword: '',
            brokerMarket: profile.metadata?.market || 'spot',
        };
        Object.entries(fields).forEach(([id, value]) => {
            const el = document.getElementById(id);
            if (el) el.value = value;
        });
        const active = document.getElementById('brokerSetActive');
        if (active) active.checked = Boolean(profile.is_active);
        const testnet = document.getElementById('brokerTestnet');
        if (testnet) testnet.checked = profile.metadata?.testnet !== false;
        const paper = document.getElementById('brokerPaper');
        if (paper) paper.checked = profile.metadata?.paper !== false;
        const live = document.getElementById('brokerLiveEnabled');
        if (live) live.checked = Boolean(profile.metadata?.live_trading_enabled);
        this.updateBrokerFieldHints();
    }

    resetBrokerForm() {
        document.getElementById('brokerProfileForm')?.reset();
        const id = document.getElementById('brokerProfileId');
        if (id) id.value = '';
        this.safeSetText('brokerFormTitle', 'Add Broker Account');
        this.updateBrokerFieldHints();
    }

    updateBrokerFieldHints() {
        const type = document.getElementById('brokerType')?.value || 'mt5';
        const account = document.getElementById('brokerAccount');
        const server = document.getElementById('brokerServer');
        const password = document.getElementById('brokerPassword');
        if (type === 'binance') {
            if (account) account.placeholder = 'Binance API key';
            if (password) password.placeholder = 'Binance API secret';
            if (server) server.placeholder = 'Optional API base URL, usually blank';
        } else {
            if (account) account.placeholder = 'Account number';
            if (password) password.placeholder = 'Password';
            if (server) server.placeholder = 'Server';
        }
    }

    async loadUsers() {
        const tbody = document.getElementById('usersTableBody');
        if (!tbody || !this.hasPermission('users')) return;
        try {
            const res = await fetch(`${this.apiBase}/users`);
            const data = await res.json();
            if (data.status !== 'success') throw new Error(data.message || 'Users unavailable');
            this.users = data.data || [];
            this.renderUsers();
        } catch (err) {
            tbody.innerHTML = `<tr><td colspan="5" class="metric-loss">${this.escapeHtml(err.message)}</td></tr>`;
        }
    }

    renderUsers() {
        const tbody = document.getElementById('usersTableBody');
        if (!tbody) return;
        if (!this.users.length) {
            tbody.innerHTML = '<tr><td colspan="5">No users created yet</td></tr>';
            return;
        }
        tbody.innerHTML = this.users.map((user) => `
            <tr>
                <td><strong>${this.escapeHtml(user.email || '--')}</strong></td>
                <td>
                    <select data-user-role="${user.id}">
                        <option value="viewer" ${user.role === 'viewer' ? 'selected' : ''}>Viewer</option>
                        <option value="trader" ${user.role === 'trader' ? 'selected' : ''}>Trader</option>
                        <option value="admin" ${user.role === 'admin' ? 'selected' : ''}>Admin</option>
                    </select>
                </td>
                <td><span class="status-badge ${user.is_active ? 'success' : 'danger'}">${user.is_active ? 'Active' : 'Disabled'}</span></td>
                <td>${this.escapeHtml(user.tenant_id || '--')}</td>
                <td>
                    <button class="btn-small btn-reset" type="button" data-user-action="save" data-id="${user.id}">Save</button>
                    <button class="btn-small btn-danger" type="button" data-user-action="toggle" data-id="${user.id}" data-active="${user.is_active ? '1' : '0'}">${user.is_active ? 'Disable' : 'Enable'}</button>
                </td>
            </tr>
        `).join('');
        tbody.querySelectorAll('[data-user-action]').forEach((button) => {
            button.addEventListener('click', () => this.handleUserAction(button.dataset.userAction, Number(button.dataset.id || 0), button.dataset.active));
        });
        this.setBadge('userManagementBadge', `${this.users.length} Users`, 'success');
        this.applyRolePermissions();
    }

    async createUser(e) {
        e.preventDefault();
        const payload = {
            email: document.getElementById('newUserEmail')?.value?.trim(),
            password: document.getElementById('newUserPassword')?.value || '',
            role: document.getElementById('newUserRole')?.value || 'viewer',
        };
        if (!payload.email || !payload.password) {
            this.showNotification('Email and password are required.', 'error');
            return;
        }
        try {
            const res = await fetch(`${this.apiBase}/users/create`, {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify(payload),
            });
            const data = await res.json();
            if (data.status !== 'success') throw new Error(data.message || 'User create failed');
            this.showNotification('User created.', 'success');
            document.getElementById('userCreateForm')?.reset();
            await this.loadUsers();
        } catch (err) {
            this.showNotification(`User create error: ${err.message}`, 'error');
        }
    }

    async handleUserAction(action, id, activeFlag) {
        if (!id) return;
        const roleSelect = document.querySelector(`[data-user-role="${id}"]`);
        const payload = {id};
        if (action === 'save') {
            payload.role = roleSelect?.value || 'viewer';
        } else if (action === 'toggle') {
            payload.is_active = activeFlag !== '1';
        }
        try {
            const res = await fetch(`${this.apiBase}/users/update`, {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify(payload),
            });
            const data = await res.json();
            if (data.status !== 'success') throw new Error(data.message || 'User update failed');
            this.showNotification('User updated.', 'success');
            await this.loadUsers();
        } catch (err) {
            this.showNotification(`User update error: ${err.message}`, 'error');
        }
    }

    async loadIctBlockers() {
        const badge = document.getElementById('ictBlockersBadge');
        if (!badge) return;
        try {
            this.setBadge('ictBlockersBadge', 'Loading', 'neutral');
            const res = await fetch(`${this.apiBase}/analytics/ict-blockers?limit=8`);
            const data = await res.json();
            if (data.status !== 'success') {
                throw new Error(data.message || 'ICT diagnostics unavailable');
            }
            this.ictBlockers = data.data || {};
            this.renderIctBlockers(this.ictBlockers);
        } catch (e) {
            this.setBadge('ictBlockersBadge', 'Error', 'danger');
            const target = document.getElementById('ictTopBlockersList');
            if (target) {
                target.innerHTML = `<div class="error-state"><i class="fas fa-circle-exclamation"></i><span>${this.escapeHtml(e.message)}</span></div>`;
            }
        }
    }

    renderIctBlockers(data = {}) {
        const summary = data.summary || {};
        const recommendation = summary.recommendation || {};
        const scanned = Number(summary.total_scanned_setups || 0);
        const blocked = Number(summary.total_blocked_setups || 0);
        const passed = Number(summary.total_passed_setups || 0);
        const nearMisses = data.near_misses || [];
        const blockRate = scanned > 0 ? blocked / scanned : 0;
        const badgeState = scanned === 0 ? 'neutral' : passed > 0 ? 'warning' : 'danger';

        this.safeSetText('ictScannedSetups', scanned);
        this.safeSetText('ictBlockedSetups', blocked);
        this.safeSetText('ictPassedSetups', passed);
        this.safeSetText('ictNearMissCount', nearMisses.length);
        this.safeSetText('ictBlockersRecommendation', recommendation.message || 'Run a diagnostics backtest to populate ICT blocker analytics.');
        this.setBadge('ictBlockersBadge', scanned ? `${Math.round(blockRate * 100)}% Blocked` : 'No Data', badgeState);

        this.renderIctGateConfig(data.gate_config || {});
        this.renderIctSessionClock(data.session_clock || {});
        this.renderIctTopBlockers(summary);
        this.renderIctBreakdown('ictSymbolBreakdownList', data.symbol_breakdown || {});
        this.renderIctBreakdown('ictSessionBreakdownList', data.session_breakdown || {});
        this.renderIctNearMisses(nearMisses);
    }

    renderIctGateConfig(config = {}) {
        const target = document.getElementById('ictGateConfigList');
        if (!target) return;
        const items = [
            ['ICT', config.ict_enabled ? 'On' : 'Off', config.ict_enabled],
            ['Sweep', config.require_liquidity_sweep ? 'Required' : 'Optional', config.require_liquidity_sweep],
            ['BOS/CHoCH', config.require_bos_or_choch ? 'Required' : 'Optional', config.require_bos_or_choch],
            ['FVG Retest', config.require_fvg_retest ? 'Required' : 'Optional', config.require_fvg_retest],
            ['Wait Retest', config.wait_for_retest ? 'On' : 'Off', config.wait_for_retest],
            ['Early Entry', config.early_entry_enabled ? 'On' : 'Off', config.early_entry_enabled],
            ['Min Score', config.min_setup_score ?? '0.80', true],
            ['Min Conviction', config.min_conviction ?? '0.70', true],
            ['Min RR', config.min_rr ?? '1.5', true],
            ['Sessions', config.allowed_sessions || 'London,NewYork', true],
        ];
        target.innerHTML = items.map(([label, value, active]) => `
            <span class="ict-gate-chip ${active ? 'active' : 'muted'}">
                <strong>${this.escapeHtml(label)}</strong>
                ${this.escapeHtml(value)}
            </span>
        `).join('');
    }

    renderIctSessionClock(clock = {}) {
        const target = document.getElementById('ictSessionClockList');
        if (!target) return;
        const next = clock.next_allowed_session || {};
        const localTime = clock.server_time_local ? this.formatClockTime(clock.server_time_local) : '--';
        const utcTime = clock.server_time_utc ? this.formatClockTime(clock.server_time_utc, true) : '--';
        const nextText = next.name
            ? `${next.name} in ${this.formatDurationMinutes(next.minutes_until_start)}`
            : 'No allowed session found';
        const schedule = (clock.schedule || []).map((item) => `
            <span class="ict-session-window ${item.allowed ? 'allowed' : 'blocked'}">
                <strong>${this.escapeHtml(item.name)}</strong>
                ${this.escapeHtml(item.start_utc)}-${this.escapeHtml(item.end_utc)} UTC
            </span>
        `).join('');
        target.innerHTML = `
            <div class="ict-clock-grid">
                <span><strong>Detected</strong>${this.escapeHtml(clock.detected_session || '--')}</span>
                <span><strong>Allowed Now</strong>${clock.current_session_allowed ? 'Yes' : 'No'}</span>
                <span><strong>UTC</strong>${this.escapeHtml(utcTime)}</span>
                <span><strong>Local</strong>${this.escapeHtml(localTime)} ${this.escapeHtml(clock.local_timezone || '')}</span>
                <span><strong>Next</strong>${this.escapeHtml(nextText)}</span>
                <span><strong>Basis</strong>${this.escapeHtml(clock.basis || 'UTC')}</span>
            </div>
            <div class="ict-session-window-list">${schedule}</div>
        `;
    }

    formatClockTime(value, forceUtc = false) {
        const date = new Date(value);
        if (Number.isNaN(date.getTime())) return '--';
        return date.toLocaleString([], {
            hour: '2-digit',
            minute: '2-digit',
            second: '2-digit',
            hour12: false,
            timeZone: forceUtc ? 'UTC' : undefined,
        });
    }

    formatDurationMinutes(value) {
        const minutes = Number(value);
        if (!Number.isFinite(minutes)) return 'n/a';
        if (minutes <= 0) return 'now';
        if (minutes < 60) return `${Math.round(minutes)}m`;
        const hours = Math.floor(minutes / 60);
        const rest = Math.round(minutes % 60);
        return rest ? `${hours}h ${rest}m` : `${hours}h`;
    }

    renderIctTopBlockers(summary = {}) {
        const target = document.getElementById('ictTopBlockersList');
        if (!target) return;
        const rows = [summary.most_common_blocker, summary.second_most_common_blocker].filter(Boolean);
        if (!rows.length) {
            target.innerHTML = '<div class="empty-state-compact">No blocker rows yet</div>';
            return;
        }
        const total = Number(summary.total_scanned_setups || 0);
        target.innerHTML = rows.map((row, index) => {
            const name = Array.isArray(row) ? row[0] : row.condition;
            const count = Number(Array.isArray(row) ? row[1] : row.count || 0);
            const pct = total > 0 ? Math.round((count / total) * 100) : 0;
            return `
                <article class="ict-blocker-row">
                    <div>
                        <strong>${index + 1}. ${this.escapeHtml(this.formatIctLabel(name))}</strong>
                        <span>${this.escapeHtml(count)} setups</span>
                    </div>
                    <span class="ict-blocker-percent">${pct}%</span>
                </article>
            `;
        }).join('');
    }

    renderIctBreakdown(id, breakdown = {}) {
        const target = document.getElementById(id);
        if (!target) return;
        const rows = Object.entries(breakdown).map(([group, counts]) => {
            const sorted = Object.entries(counts || {}).sort((a, b) => Number(b[1]) - Number(a[1]));
            const total = sorted.reduce((sum, [, count]) => sum + Number(count || 0), 0);
            const top = sorted.slice(0, 3).map(([name, count]) => `${this.formatIctLabel(name)} ${count}`).join(' | ');
            return { group, total, top };
        }).sort((a, b) => b.total - a.total);

        if (!rows.length) {
            target.innerHTML = '<div class="empty-state-compact">No breakdown data yet</div>';
            return;
        }
        target.innerHTML = rows.slice(0, 6).map((row) => `
            <article class="ict-breakdown-row">
                <div>
                    <strong>${this.escapeHtml(row.group)}</strong>
                    <span>${this.escapeHtml(row.top || 'No blockers')}</span>
                </div>
                <span>${this.escapeHtml(row.total)}</span>
            </article>
        `).join('');
    }

    renderIctNearMisses(rows = []) {
        const target = document.getElementById('ictNearMissList');
        if (!target) return;
        if (!rows.length) {
            target.innerHTML = '<div class="empty-state-compact">No near misses yet</div>';
            return;
        }
        target.innerHTML = rows.slice().reverse().slice(0, 8).map((row) => {
            const score = Number(row.setup_score);
            const conviction = Number(row.conviction);
            const rr = Number(row.rr);
            return `
                <article class="ict-near-miss-row">
                    <div class="ict-near-miss-main">
                        <strong>${this.escapeHtml(row.symbol || '--')} ${this.escapeHtml(row.direction || '--')}</strong>
                        <span>${this.escapeHtml(row.session || '--')} | failed ${this.escapeHtml(row.failed_count || 0)}</span>
                    </div>
                    <div class="ict-near-miss-metrics">
                        <span>Score ${Number.isFinite(score) ? score.toFixed(2) : 'n/a'}</span>
                        <span>Conv ${Number.isFinite(conviction) ? conviction.toFixed(2) : 'n/a'}</span>
                        <span>RR ${Number.isFinite(rr) ? rr.toFixed(2) : 'n/a'}</span>
                    </div>
                    <small>${this.escapeHtml(this.formatIctReasonList(row.failed_reasons || ''))}</small>
                </article>
            `;
        }).join('');
    }

    formatIctLabel(value) {
        return String(value || 'unknown')
            .replace(/_/g, ' ')
            .replace(/\b\w/g, (char) => char.toUpperCase());
    }

    formatIctReasonList(value) {
        return String(value || '')
            .split(';')
            .filter(Boolean)
            .map((item) => this.formatIctLabel(item))
            .join(', ') || 'No failed reasons recorded';
    }

    async loadEdgeDiagnostics() {
        const badge = document.getElementById('edgeDiagnosticsBadge');
        if (!badge) return;
        try {
            this.setBadge('edgeDiagnosticsBadge', 'Loading', 'neutral');
            const res = await fetch(`${this.apiBase}/analytics/edge-diagnostics?min_sample=3&limit=8`);
            const data = await res.json();
            if (data.status !== 'success') {
                throw new Error(data.message || 'Edge diagnostics unavailable');
            }
            this.edgeDiagnostics = data.data || {};
            this.renderEdgeDiagnostics(this.edgeDiagnostics);
        } catch (e) {
            this.setBadge('edgeDiagnosticsBadge', 'Error', 'danger');
            const target = document.getElementById('edgeWeakestList');
            if (target) {
                target.innerHTML = `<div class="error-state"><i class="fas fa-circle-exclamation"></i><span>${this.escapeHtml(e.message)}</span></div>`;
            }
        }
    }

    renderEdgeDiagnostics(data = {}) {
        const summary = data.summary || {};
        const overall = data.overall || {};
        this.safeSetText('edgeClosedTrades', summary.closed_trades ?? 0);
        this.safeSetText('edgeMatchedTrades', `${summary.matched_journal_trades ?? 0} / ${summary.closed_trades ?? 0}`);
        this.safeSetText('edgeExpectancy', overall.expectancy != null ? this.formatMoney(overall.expectancy) : '--');
        const pf = Number(overall.profit_factor);
        this.safeSetText('edgeProfitFactor', Number.isFinite(pf) ? pf.toFixed(3) : 'n/a');

        const state = (overall.expectancy || 0) > 0 && (overall.profit_factor || 0) > 1 ? 'success' : 'danger';
        this.setBadge('edgeDiagnosticsBadge', state === 'success' ? 'Positive Edge' : 'Negative Edge', state);
        this.renderEdgeComponentList('edgeStrongestList', data.strongest_edge_contributors || [], 'positive');
        this.renderEdgeComponentList('edgeWeakestList', data.weakest_edge_contributors || [], 'negative');
        this.renderEdgeComboList('edgeToxicCombos', data.toxic_combinations || [], 'negative');
        this.renderEdgeComboList('edgeProfitableCombos', data.profitable_combinations || [], 'positive');
    }

    formatEdgeMetric(value, decimals = 2) {
        const number = Number(value);
        if (!Number.isFinite(number)) return 'n/a';
        return number.toFixed(decimals);
    }

    confidenceBadge(item = {}) {
        const quality = String(item.confidence_quality || 'low').toLowerCase();
        const sample = Number(item.sample_size || 0);
        const warning = sample < 30 ? ' sample warning' : '';
        return `<span class="edge-confidence ${this.escapeHtml(quality)}${warning}">${this.escapeHtml(quality.toUpperCase())}${sample < 30 ? ' | LOW N' : ''}</span>`;
    }

    renderEdgeComponentList(id, rows = [], tone = 'neutral') {
        const target = document.getElementById(id);
        if (!target) return;
        const filtered = rows.slice(0, 8);
        if (!filtered.length) {
            target.innerHTML = '<div class="empty-state-compact">Not enough closed trades yet</div>';
            return;
        }
        target.innerHTML = filtered.map((item) => {
            const exp = Number(item.expectancy || 0);
            const rowTone = exp >= 0 ? 'positive' : 'negative';
            const ci = item.confidence_interval || {};
            return `
                <article class="edge-component-row ${rowTone}">
                    <div>
                        <strong>${this.escapeHtml(item.component || 'component')}</strong>
                        <span>${this.escapeHtml(item.value || 'unknown')}</span>
                    </div>
                    <div class="edge-metrics">
                        <span>n ${this.escapeHtml(item.sample_size ?? 0)}</span>
                        <span>WR ${this.formatEdgeMetric((item.win_rate || 0) * 100, 1)}%</span>
                        <span>Exp ${this.formatMoney(exp)}</span>
                        <span>R ${item.average_r == null ? 'n/a' : this.formatEdgeMetric(item.average_r, 2)}</span>
                        <span>PF ${this.formatEdgeMetric(item.profit_factor, 2)}</span>
                    </div>
                    <div class="edge-ci">
                        CI ${this.formatMoney(ci.low)} to ${this.formatMoney(ci.high)}
                        ${this.confidenceBadge(item)}
                    </div>
                </article>
            `;
        }).join('');
    }

    renderEdgeComboList(id, rows = [], tone = 'neutral') {
        const target = document.getElementById(id);
        if (!target) return;
        const filtered = rows.slice(0, 6);
        if (!filtered.length) {
            target.innerHTML = '<div class="empty-state-compact">No statistically useful combinations yet</div>';
            return;
        }
        target.innerHTML = filtered.map((item) => {
            const exp = Number(item.expectancy || 0);
            const rowTone = exp >= 0 ? 'positive' : 'negative';
            return `
                <article class="edge-combo-row ${rowTone}">
                    <strong>${this.escapeHtml(item.combination || 'unknown')}</strong>
                    <div>
                        <span>n ${this.escapeHtml(item.sample_size ?? 0)}</span>
                        <span>Exp ${this.formatMoney(exp)}</span>
                        <span>PF ${this.formatEdgeMetric(item.profit_factor, 2)}</span>
                        <span>DD ${this.formatMoney(item.drawdown_contribution)}</span>
                    </div>
                    ${this.confidenceBadge(item)}
                </article>
            `;
        }).join('');
    }

    async start() {
        try {
            const ruleConfig = {
                ema: document.getElementById("ruleEma")?.checked ?? true,
                volume: document.getElementById("ruleVolume")?.checked ?? true,
                po3: document.getElementById("rulePo3")?.checked ?? true,
            };

            const payload = {
                ...ruleConfig,
                volume: document.getElementById("volume")?.value,
            };
            await this.loadBrokerStatus();
            const brokerType = String(this.activeBrokerStatus?.broker_type || '').toLowerCase();
            if (brokerType === 'binance') {
                payload.symbols = null;
                payload.symbol_source = 'active_broker';
            } else {
                payload.symbols = document.getElementById("symbols")?.value;
                payload.symbol_source = 'ui';
            }

            const res = await fetch(`${this.apiBase}/bot/start`, {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify(payload),
            });
            const data = await res.json();
            if (data.status === "success") {
                alert("Bot started!");
                this.updateDashboard();
            } else {
                alert("Error: " + (data.message || "Failed to start"));
            }
        } catch (e) {
            alert("Error: " + e.message);
        }
    }

    async stop() {
        try {
            const res = await fetch(`${this.apiBase}/bot/stop`, {
                method: "POST",
            });
            const data = await res.json();
            if (data.status === "success") {
                alert("Bot stopped!");
                this.updateDashboard();
            }
        } catch (e) {
            alert("Error: " + e.message);
        }
    }

    async logout() {
        try {
            await fetch('/api/auth/logout', { method: 'POST' });
        } finally {
            window.location.href = '/login';
        }
    }

    async updateDashboard() {
        try {
            const [statusRes, logsRes, statsRes] = await Promise.all([
                fetch(`${this.apiBase}/bot/status`),
                fetch(`${this.apiBase}/logs`),
                fetch(`${this.apiBase}/stats`),
            ]);

            const statusData = await statusRes.json();
            const logsData = await logsRes.json();
            const statsData = await statsRes.json();

            if (statusData) {
                const d = statusData;
                this.safeSetText('botStatus', d.running ? 'Online' : 'Offline');
                this.setBadge('topBotBadge', d.running ? 'Bot Online' : 'Bot Offline', d.running ? 'success' : 'neutral');
                const statusIcon = document.getElementById('statusIcon');
                if (statusIcon) statusIcon.className = d.running ? 'fas fa-circle online' : 'fas fa-circle';
                this.safeSetText('connected', d.connected ? 'Connected' : 'Disconnected');
                const brokerLabel = d.broker?.name || d.broker?.type || 'Broker';
                this.setBadge('topConnectionBadge', d.connected ? `${brokerLabel} Connected` : `${brokerLabel} Offline`, d.connected ? 'success' : 'danger');
                this.safeSetText('balance', `$${(d.balance || 0).toFixed(2)}`);
                this.safeSetText('equity', `$${(d.equity || 0).toFixed(2)}`);
                this.safeSetText('margin', `$${(d.free_margin || 0).toFixed(2)}`);
                this.setMoney('dailyProfit', d.daily_profit);
                this.setMoney('floatingProfit', d.floating_profit);
                this.setMoney('realizedProfit', d.realized_profit);
                this.setMoney('netProfit', d.net_profit);
                this.setMoney('drawdown', d.floating_drawdown);
                this.setMoney('openRisk', d.current_open_risk);
                this.setMoney('maxOpenRisk', d.max_open_risk);
                const openRiskPct = Number(d.open_risk_pct);
                const maxOpenRiskPct = Number(d.max_open_risk_pct);
                const riskUsage = Number.isFinite(openRiskPct) && Number.isFinite(maxOpenRiskPct) && maxOpenRiskPct > 0
                    ? Math.min(100, Math.max(0, (openRiskPct / maxOpenRiskPct) * 100))
                    : 0;
                const openRiskMeter = document.getElementById('openRiskMeter');
                if (openRiskMeter) {
                    openRiskMeter.style.width = `${riskUsage}%`;
                    openRiskMeter.dataset.state = riskUsage >= 90 ? 'danger' : riskUsage >= 70 ? 'warning' : 'ok';
                }
                this.safeSetText('openRiskPct', Number.isFinite(openRiskPct) ? `${(openRiskPct * 100).toFixed(1)}% used` : '0.0% used');
                this.safeSetText('openRiskState', d.current_open_risk > 0 ? `${riskUsage.toFixed(0)}% of risk cap` : 'No active stop-risk');
                const sizingMode = String(d.position_sizing_mode || 'fixed').replace('_', ' ');
                this.safeSetText('sizingMode', sizingMode.charAt(0).toUpperCase() + sizingMode.slice(1));
                this.safeSetText('activeTrades', d.active_trades || 0);
                this.renderScanStatus(d.scan);
                const tm = d.trade_management || {};
                const lockPips = Number(tm.trailing_sl_lock_pips || 0);
                this.safeSetText('trailingSlState', tm.trailing_sl ? `On @ ${Math.round((tm.trailing_sl_trigger_pct || 0) * 100)}% / +${lockPips.toFixed(1)}p` : 'Off');
                this.safeSetText('trailingTpState', this.formatProfitProtectionState(tm));
                const eventGuard = [
                    tm.false_move_detection ? 'Trap' : null,
                    tm.news_mode ? `News ${Math.round((tm.news_risk_multiplier || 1) * 100)}%` : null,
                    tm.news_ladder ? `Ladder ${tm.news_ladder_max_addons || 0}x` : null
                ].filter(Boolean).join(' + ');
                this.safeSetText('eventGuardState', eventGuard || 'Off');

                const marginLevel = d.margin_level || 0;
                this.safeSetText('marginLevel', `${marginLevel.toFixed(2)}%`);

                const floatingPnl = d.floating_profit != null ? d.floating_profit : (d.equity || 0) - (d.balance || 0);
                this.setMoney('floatingPnl', floatingPnl);

                const botScore = d.bot_score || {};
                const scoreValue = Number(botScore.score);
                const botIQ = Number.isFinite(scoreValue)
                    ? Math.min(100, Math.max(0, scoreValue))
                    : Math.min(100, Math.max(0, ((d.connected ? 1 : 0) * 0.6 + (d.running ? 1 : 0) * 0.4) * 100));
                this.safeSetText('botIQ', `${botIQ.toFixed(0)}%`);
                this.safeSetText('botScoreGrade', this.formatReadinessLabel(d, botScore.grade ? `${botScore.grade} - ${botScore.label || 'Ready'}` : '--'));
                const logicFill = document.getElementById('logicHealthFill');
                if (logicFill) logicFill.style.width = `${botIQ}%`;

                if (d.max_exposure != null) {
                    this.safeSetText('riskMaxExposure', `${Math.round(d.max_exposure * 100)}%`);
                }
                if (d.daily_profit != null) {
                    this.safeSetText('dailyProfitCap', `${(d.daily_profit * 100).toFixed(2)}%`);
                }

                if (statsData && statsData.data) {
                    const s = statsData.data;
                    this.safeSetText('winRate', s.win_rate != null ? `${(s.win_rate * 100).toFixed(1)}%` : '0%');
                    this.safeSetText('expectancy', s.expectancy != null ? s.expectancy.toFixed(2) : '0');
                    this.safeSetText('avgWin', s.avg_win != null ? `$${s.avg_win.toFixed(2)}` : '$0');
                    this.safeSetText('avgLoss', s.avg_loss != null ? `$${Math.abs(s.avg_loss).toFixed(2)}` : '$0');
                    this.safeSetText('totalTrades', s.total_trades || s.trades || 0);
                }

                const startBtn = document.getElementById('startBtn');
                const stopBtn = document.getElementById('stopBtn');
                if (startBtn) startBtn.disabled = d.running;
                if (stopBtn) stopBtn.disabled = !d.running;

                this.symbols = d.symbols || [];
                this.renderRiskBanner(d);
                this.renderBlockedTradingPanel(d);
            }

            if (logsData && logsData.data) {
                const scoredSignals = [
                    ...(logsData.data.signals || []),
                    ...(logsData.data.future_trades || [])
                ];
                this.renderStrategyBreakdown(scoredSignals);
                this.renderSpreadSafety(scoredSignals);
                this.renderRejectionSummary(logsData.data.rejections || []);
            }

            await this.loadPendingOrders();
            await this.loadPositions();
        } catch (e) {
            console.error("Dashboard update error:", e);
            this.renderDashboardError('Dashboard data unavailable. Retrying with fallback polling.');
        }
    }

    async loadBotStatus() {
        try {
            const res = await fetch(`${this.apiBase}/bot/status`);
            const status = await res.json();
            if (status) {
                this.applyRealtimePayload({ status });
            }
        } catch (e) {
            console.warn('Bot status fallback poll failed', e);
        }
    }

    async loadStats() {
        try {
            const res = await fetch(`${this.apiBase}/stats`);
            const data = await res.json();
            const stats = data.data || {};
            this.safeSetText('winRate', stats.win_rate != null ? `${(stats.win_rate * 100).toFixed(1)}%` : '0%');
            this.safeSetText('expectancy', stats.expectancy != null ? stats.expectancy.toFixed(2) : '0');
            this.safeSetText('avgWin', stats.avg_win != null ? `$${stats.avg_win.toFixed(2)}` : '$0');
            this.safeSetText('avgLoss', stats.avg_loss != null ? `$${Math.abs(stats.avg_loss).toFixed(2)}` : '$0');
            this.safeSetText('totalTrades', stats.total_trades || stats.trades || 0);
        } catch (e) {
            console.warn('Stats fallback poll failed', e);
        }
    }

    renderRiskBanner(status = {}) {
        const banner = document.getElementById('riskWarningBanner');
        if (!banner) return;

        const drawdown = Math.abs(Number(status.floating_drawdown || 0));
        const openRiskPct = Number(status.open_risk_pct || 0);
        const connected = Boolean(status.connected);
        const running = Boolean(status.running);
        const license = status.license || {};
        const readiness = status.readiness || {};
        const readinessBlockers = Array.isArray(readiness.blockers) ? readiness.blockers : [];
        const readinessWarnings = Array.isArray(readiness.warnings) ? readiness.warnings : [];

        let title = '';
        let message = '';
        let level = 'warning';

        if (license && license.trading_allowed === false) {
            title = 'License attention required';
            message = license.reason || 'Live trading is disabled until a valid license is activated.';
            level = 'danger';
        } else if (readinessBlockers.length) {
            title = 'Trading is currently blocked';
            message = readinessBlockers.slice(0, 3).map((item) => item.message || item).join(' ');
            level = readinessBlockers.some((item) => ['license_block', 'broker_disconnected', 'startup_validation'].includes(item.code)) ? 'danger' : 'warning';
        } else if (readinessWarnings.length) {
            title = 'Trading readiness warning';
            message = readinessWarnings.slice(0, 2).map((item) => item.message || item).join(' ');
        } else if (!connected && running) {
            title = 'Broker connection is down';
            message = 'The bot is marked online but the active broker is disconnected. Avoid manual execution until connection recovers.';
            level = 'danger';
        } else if (drawdown > 0 && openRiskPct >= 0.04) {
            title = 'Elevated open risk';
            message = `Open risk is ${(openRiskPct * 100).toFixed(1)}% with floating drawdown ${this.formatMoney(-drawdown)}.`;
        } else if (drawdown > 0) {
            title = 'Floating drawdown active';
            message = `Current floating drawdown is ${this.formatMoney(-drawdown)}. Monitor protection levels.`;
        }

        if (!title) {
            banner.classList.add('hidden');
            return;
        }

        banner.classList.remove('hidden', 'danger', 'warning');
        banner.classList.add(level);
        this.safeSetText('riskWarningTitle', title);
        this.safeSetText('riskWarningMessage', message);
    }

    formatReadinessLabel(status = {}, fallback = '--') {
        const readiness = status.readiness || {};
        const blockers = Array.isArray(readiness.blockers) ? readiness.blockers : [];
        const warnings = Array.isArray(readiness.warnings) ? readiness.warnings : [];
        if (blockers.length) {
            return `Blocked: ${blockers.length} reason${blockers.length === 1 ? '' : 's'}`;
        }
        if (warnings.length) {
            return `Warning: ${warnings.length}`;
        }
        if (readiness.can_trade_now === true) {
            return 'Ready';
        }
        return fallback;
    }

    renderBlockedTradingPanel(status = {}) {
        const panel = document.getElementById('blockedTradingPanel');
        if (!panel) return;
        const readiness = status.readiness || {};
        const blockers = Array.isArray(readiness.blockers) ? readiness.blockers : [];
        const warnings = Array.isArray(readiness.warnings) ? readiness.warnings : [];
        const list = document.getElementById('blockedReasonsList');
        const badge = document.getElementById('readinessStateBadge');
        const fill = document.getElementById('readinessMeterFill');
        const validation = readiness.backtest_validation || {};

        const blocked = blockers.length > 0;
        panel.classList.toggle('hidden', !blocked && warnings.length === 0);

        const state = this.resolveReadinessState(status, readiness);
        if (badge) {
            badge.textContent = state.label;
            badge.className = `status-badge ${state.className}`;
        }
        if (fill) {
            fill.style.width = `${state.percent}%`;
            fill.dataset.state = state.label.toLowerCase().replace(/\s+/g, '-');
        }

        if (list) {
            const rows = blockers.length ? blockers : warnings;
            if (!rows.length) {
                list.innerHTML = '<li>All safety checks are clear. Continue monitoring strategy validation before scaling risk.</li>';
            } else {
                list.innerHTML = rows.map((item) => `<li>${this.escapeHtml(item.message || String(item))}</li>`).join('');
            }
        }

        if (validation.sample != null) {
            panel.dataset.sample = validation.sample;
            panel.dataset.expectancy = validation.expectancy;
            panel.dataset.profitFactor = validation.profit_factor;
        }
    }

    resolveReadinessState(status = {}, readiness = {}) {
        const validation = readiness.backtest_validation || {};
        const blockers = Array.isArray(readiness.blockers) ? readiness.blockers : [];
        const brokerConnected = Boolean(status.connected);
        const marketOpen = status.market_open !== false;
        const licenseOk = !(status.license && status.license.trading_allowed === false);
        const sampleOk = Number(validation.sample || 0) >= Number(validation.min_sample || 30);
        const expectancyOk = Number(validation.expectancy || 0) > 0;
        const pfOk = Number(validation.profit_factor || 0) >= Number(validation.min_profit_factor || 1.2);

        if (blockers.length || !brokerConnected || !marketOpen || !licenseOk || !sampleOk || !expectancyOk) {
            return {label: 'BLOCKED', className: 'danger', percent: 12};
        }
        if (!readiness.can_trade_now) {
            return {label: 'WATCH ONLY', className: 'warning', percent: 30};
        }
        if (sampleOk && expectancyOk && pfOk && brokerConnected && marketOpen && licenseOk) {
            return {label: 'LIVE READY', className: 'success', percent: 100};
        }
        if (sampleOk && expectancyOk && brokerConnected && licenseOk) {
            return {label: 'CENT ACCOUNT READY', className: 'warning', percent: 76};
        }
        return {label: 'DEMO READY', className: 'neutral', percent: 55};
    }

    renderDashboardError(message) {
        this.setBadge('topBotBadge', 'Data Error', 'danger');
        const banner = document.getElementById('riskWarningBanner');
        if (!banner) return;
        banner.classList.remove('hidden', 'warning');
        banner.classList.add('danger');
        this.safeSetText('riskWarningTitle', 'Dashboard update failed');
        this.safeSetText('riskWarningMessage', message);
    }

    async loadPositions() {
        try {
            const res = await fetch(`${this.apiBase}/positions`);
            if (!res.ok) {
                console.warn('loadPositions: non-OK response', res.status);
                this.markNoPositions();
                return;
            }

            const data = await res.json();
            const tbody = document.querySelector("#positionsTable tbody");
            const activeTbody = document.querySelector("#activeTradesBody");
            if (!tbody) {
                console.warn('loadPositions: positions table not found');
                return;
            }

            if (activeTbody) activeTbody.innerHTML = "";

            const positions = (data && data.data) ? data.data : [];
            this.positionsRows = positions;
            this.positionsPage = Math.min(
                Math.max(1, Math.ceil(positions.length / this.positionsPageSize)),
                Math.max(1, this.positionsPage)
            );
            if (positions.length > 0) {
                this.renderPositionsTable();
                positions.forEach((pos) => {
                    const entry = Number(pos.entry || 0);
                    const current = Number(pos.current || 0);
                    const profit = Number(pos.profit || 0);
                    const profitColor = profit >= 0 ? "#10b981" : "#ef4444";
                    const state = pos.trade_state || {};
                    const rText = pos.r_multiple != null ? `${Number(pos.r_multiple).toFixed(2)}R` : '--';
                    const management = this.formatPositionManagement(pos);

                    if (activeTbody) {
                        const activeRow = activeTbody.insertRow();
                        activeRow.innerHTML = this.buildPositionRowHtml(pos, entry, current, profit, profitColor, rText, management);
                    }
                });
            } else {
                this.markNoPositions();
            }
        } catch (e) {
            console.error("Positions error:", e);
            this.markNoPositions();
        }
    }

    changePositionsPage(delta) {
        const totalPages = Math.max(1, Math.ceil(this.positionsRows.length / this.positionsPageSize));
        this.positionsPage = Math.min(totalPages, Math.max(1, this.positionsPage + delta));
        this.renderPositionsTable();
    }

    renderPositionsTable() {
        const tbody = document.querySelector("#positionsTable tbody");
        if (!tbody) return;
        const rows = this.positionsRows || [];
        const totalPages = Math.max(1, Math.ceil(rows.length / this.positionsPageSize));
        this.positionsPage = Math.min(totalPages, Math.max(1, this.positionsPage));
        const start = (this.positionsPage - 1) * this.positionsPageSize;
        const pageRows = rows.slice(start, start + this.positionsPageSize);
        const pageInfo = document.getElementById('positionsPageInfo');
        const prev = document.getElementById('positionsPrevBtn');
        const next = document.getElementById('positionsNextBtn');
        if (pageInfo) pageInfo.textContent = this.formatPageInfo(rows.length, start, pageRows.length, 'positions', this.positionsPage, totalPages);
        if (prev) prev.disabled = this.positionsPage <= 1;
        if (next) next.disabled = this.positionsPage >= totalPages;
        if (!rows.length) {
            this.markNoPositions();
            return;
        }

        tbody.innerHTML = pageRows.map((pos) => {
            const entry = Number(pos.entry || 0);
            const current = Number(pos.current || 0);
            const profit = Number(pos.profit || 0);
            const profitColor = profit >= 0 ? "#10b981" : "#ef4444";
            const rText = pos.r_multiple != null ? `${Number(pos.r_multiple).toFixed(2)}R` : '--';
            const management = this.formatPositionManagement(pos);
            return `<tr>${this.buildPositionRowHtml(pos, entry, current, profit, profitColor, rText, management)}</tr>`;
        }).join('');
    }

    buildPositionRowHtml(pos, entry, current, profit, profitColor, rText, management) {
        return `
            <td>${pos.symbol || '-'}</td>
            <td>${pos.type || '-'}</td>
            <td>${pos.volume || '-'}</td>
            <td>${entry.toFixed(5)}</td>
            <td>${current.toFixed(5)}</td>
            <td>${pos.sl ? Number(pos.sl).toFixed(5) : '-'}</td>
            <td>${pos.tp ? Number(pos.tp).toFixed(5) : '-'}</td>
            <td>${rText}</td>
            <td style="color: ${profitColor}">${this.formatMoney(profit)}</td>
            <td>${management}</td>
        `;
    }

    formatPositionManagement(pos = {}) {
        const state = pos.trade_state || {};
        const chips = [];
        chips.push(`<span class="setup-chip ${state.status === 'ACTIVE' ? 'pass' : ''}">${state.status || 'EXTERNAL'}</span>`);
        if (state.trade_horizon?.type) chips.push(`<span class="setup-chip">${state.trade_horizon.type}</span>`);
        if (state.partial_tp_taken) chips.push('<span class="setup-chip pass">Partial TP</span>');
        if (state.reverse_exit_done) chips.push('<span class="setup-chip pass">Reverse exit</span>');
        if (state.news_ladder_count) chips.push(`<span class="setup-chip pass">News adds ${state.news_ladder_count}</span>`);
        if (state.max_favorable_r != null) chips.push(`<span class="setup-chip">MFE ${Number(state.max_favorable_r).toFixed(2)}R</span>`);
        return `<div class="position-management">${chips.join('')}</div>`;
    }

    markNoPositions() {
        const tbody = document.querySelector("#positionsTable tbody");
        const activeTbody = document.querySelector("#activeTradesBody");
        if (tbody) {
            tbody.innerHTML = '<tr><td colspan="10" style="text-align:center">No positions</td></tr>';
        }
        const pageInfo = document.getElementById('positionsPageInfo');
        const prev = document.getElementById('positionsPrevBtn');
        const next = document.getElementById('positionsNextBtn');
        if (pageInfo) pageInfo.textContent = '0 positions';
        if (prev) prev.disabled = true;
        if (next) next.disabled = true;
        if (activeTbody) {
            activeTbody.innerHTML = '<tr><td colspan="10" style="text-align:center">No active trades</td></tr>';
        }
    }

    async loadPendingOrders() {
        try {
            const res = await fetch(`${this.apiBase}/pending-orders`);
            const data = await res.json();
            const tbody = document.getElementById('pendingOrdersBody');
            if (!tbody) return;
            tbody.innerHTML = '';

            const orders = data.data || [];
            if (orders.length === 0) {
                tbody.innerHTML = '<tr><td colspan="6" style="text-align:center">No pending orders</td></tr>';
                return;
            }

            orders.forEach((o) => {
                const row = document.createElement('tr');
                row.innerHTML = `
                    <td>${o.symbol}</td>
                    <td>${o.action || '-'}</td>
                    <td>${o.entry ? Number(o.entry).toFixed(5) : '-'}</td>
                    <td>${o.sl ? Number(o.sl).toFixed(5) : '-'}</td>
                    <td>${o.tp ? Number(o.tp).toFixed(5) : '-'}</td>
                    <td>${o.status || 'PENDING'}</td>
                `;
                tbody.appendChild(row);
            });
        } catch (e) {
            console.error('Pending orders error:', e);
        }
    }

    async loadLogs() {
        try {
            const res = await fetch(`${this.apiBase}/logs`);
            const data = await res.json();
            const container = document.getElementById("logsContainer");

            const rejections = data.data ? data.data.rejections || [] : [];
            const trades = data.data ? data.data.trades || [] : [];
            const signals = data.data ? data.data.signals || [] : [];
            const futureTrades = data.data ? data.data.future_trades || [] : [];
            this.renderStrategyBreakdown([...signals, ...futureTrades]);
            this.renderSpreadSafety([...signals, ...futureTrades]);
            this.renderRejectionSummary(rejections);

            if (!container) return;
            container.innerHTML = "";

            if (rejections.length === 0 && trades.length === 0 && signals.length === 0 && futureTrades.length === 0) {
                container.innerHTML = "<p>No logs</p>";
                return;
            }

            const groups = [
                {
                    title: 'Trades',
                    items: trades,
                    label: (log) => `${log.event || 'Trade'} - ${log.symbol || ''} ${log.trade_style ? '(' + log.trade_style + ')' : ''}`,
                    meta: (log) => log.timestamp ? new Date(log.timestamp).toLocaleString() : '',
                },
                {
                    title: 'Signals',
                    items: signals,
                    label: (signal) => `${signal.symbol || 'N/A'} - ${signal.type || signal.nature || 'Signal'}`,
                    meta: (signal) => signal.timestamp ? new Date(signal.timestamp).toLocaleString() : '',
                },
                {
                    title: 'Future Trades',
                    items: futureTrades,
                    label: (item) => `${item.symbol || 'N/A'} - ${item.type || item.nature || item.setup_name || 'Watchlist'}`,
                    meta: (item) => item.phase || item.action_needed || '',
                },
                {
                    title: 'Rejections',
                    items: rejections,
                    label: (log) => `Rejected - ${log.symbol || ''}`,
                    meta: (log) => log.reason || '',
                },
            ];

            groups.forEach((group) => {
                if (!group.items.length) return;
                const section = document.createElement('section');
                section.className = 'log-section';
                section.innerHTML = `<h4>${this.escapeHtml(group.title)} <span>${group.items.length}</span></h4>`;
                group.items.slice().reverse().forEach((item) => {
                    section.appendChild(this.createLogAccordionItem(item, group.label(item), group.meta(item)));
                });
                container.appendChild(section);
            });
        } catch (e) {
            console.error("Logs error:", e);
        }
    }

    async loadSignals() {
        try {
            const res = await fetch(`${this.apiBase}/signals`);
            const data = await res.json();
            const container = document.getElementById("signalsTable");
            container.innerHTML = "";

            const signals = (data.data && data.data.recent) ? data.data.recent : (data.data ? data.data.recent || [] : []);
            if (signals && signals.length > 0) {
                const table = document.createElement('table');
                table.className = 'table signals-table';
                table.innerHTML = `
                    <thead>
                        <tr>
                            <th>Symbol</th>
                            <th>Signal</th>
                            <th>Entry</th>
                            <th>Conviction</th>
                            <th>Style</th>
                            <th>Status</th>
                        </tr>
                    </thead>
                    <tbody></tbody>
                `;
                container.appendChild(table);
                const tbody = table.querySelector('tbody');

                signals.forEach(sig => {
                    const row = tbody.insertRow();
                    const conviction = sig.conviction || 0;
                    const convictionClass = conviction >= 0.6 ? 'high' : conviction >= 0.3 ? 'medium' : 'low';
                    row.innerHTML = `
                        <td>${sig.symbol}</td>
                        <td>${sig.nature || sig.type || ''}</td>
                        <td>${sig.entry ? sig.entry.toFixed(5) : ''}</td>
                        <td><span class="conviction-bar ${convictionClass}" style="width: ${conviction * 100}%"></span> ${conviction.toFixed(3)}</td>
                        <td>${sig.trade_style || ''}</td>
                        <td>${sig.status || ''}</td>
                    `;
                    row.style.cursor = 'pointer';
                    row.addEventListener('click', () => this.showSignalDetails(sig));
                });

                // update hunter & vision modules
                this.updateMarketWatch(signals);
                this.updateVision(signals);
                this.updateExecutionTimeline(signals);
            } else {
                container.innerHTML = '<div class="empty-state"><p>No signals</p></div>';
                this.updateMarketWatch([]);
                this.updateVision([]);
                this.updateExecutionTimeline([]);
            }
        } catch (e) {
            console.error('Signals error:', e);
        }
    }

    updateMarketWatch(signals) {
        const body = document.getElementById('marketWatchBody');
        if (!body) return;
        body.innerHTML = '';

        if (!signals || signals.length === 0) {
            body.innerHTML = '<tr><td colspan="4" style="text-align:center">No market signals available</td></tr>';
            return;
        }

        const byConvictions = signals
            .slice()
            .sort((a, b) => ((b.setup_score?.score || b.conviction || 0) - (a.setup_score?.score || a.conviction || 0)));

        byConvictions.slice(0, 15).forEach((sig) => {
            const row = document.createElement('tr');
            const c = sig.conviction || 0;
            const setup = sig.setup_score?.score || 0;
            const convictionClass = c >= 0.6 ? 'high' : c >= 0.3 ? 'medium' : 'low';
            const price = sig.current_price || sig.entry || 0;
            const direction = (sig.nature || sig.type || '').toUpperCase();

            row.innerHTML = `
                <td>${sig.symbol || '-'}</td>
                <td>${price ? price.toFixed(5) : '-'}</td>
                <td><span class="conviction-bar ${convictionClass}" style="width: ${Math.min(100, Math.max(0, Math.max(c, setup)*100))}%"></span> ${Math.max(c, setup).toFixed(2)}</td>
                <td>${direction}${sig.early_entry ? ' | EARLY' : ''}</td>
            `;
            body.appendChild(row);
        });
    }

    updateVision(signals) {
        const top = (signals || []).slice().sort((a, b) => ((b.setup_score?.score || b.conviction || 0) - (a.setup_score?.score || a.conviction || 0)))[0];
        const bestConv = top ? (top.conviction || 0) : 0;
        const setupScore = top ? (top.setup_score?.score || 0) : 0;
        const price = (value) => Number.isFinite(Number(value)) ? Number(value).toFixed(5) : '--';
        const direction = top ? String(top.type || top.nature || top.direction || 'Unknown').toUpperCase() : '--';
        const horizon = top?.trade_horizon || {};
        const horizonType = horizon.type || 'INTRADAY';
        const holdTime = horizon.hold_time ? ` (${horizon.hold_time})` : '';
        const grade = top ? this.getSetupGrade(top) : '--';
        const archetype = top?.setup_score?.archetype || top?.setup_score?.summary || top?.strategy || top?.nature || 'Waiting for cleaner structure';
        const decision = top ? this.getDecisionLabel(top, bestConv, setupScore) : 'WAIT';
        const rr = top ? this.calculateRiskReward(top) : null;
        const spread = top?.spread_safety || top?.spread || top?.spread_pips;
        const spreadLabel = typeof spread === 'object'
            ? `${spread.safe === false ? 'High' : 'OK'}${spread.spread_pips != null ? ` / ${Number(spread.spread_pips).toFixed(1)}p` : ''}`
            : (Number.isFinite(Number(spread)) ? `${Number(spread).toFixed(1)}p` : '--');

        this.safeSetText('topSymbol', top ? top.symbol : 'None');
        this.safeSetText('bestConviction', `${(bestConv * 100).toFixed(1)}%`);
        this.safeSetText('nextTradeAction', top ? `${direction} @ ${price(top.entry)}` : 'None');
        this.safeSetText('hotZone', top ? price(top.entry) : 'None');
        this.safeSetText('decisionSymbol', top ? top.symbol : 'No setup');
        this.safeSetText('decisionArchetype', archetype);
        this.safeSetText('decisionTradeType', top ? `${horizonType}${holdTime}` : '--');
        this.safeSetText('decisionDirection', direction);
        this.safeSetText('decisionGrade', grade);
        this.safeSetText('decisionEntry', top ? price(top.entry) : '--');
        const regime = top?.market_regime || top?.setup_score?.market_regime || {};
        const regimeLabel = regime.label ? `${String(regime.label).toUpperCase()} ${regime.confidence != null ? `(${(Number(regime.confidence) * 100).toFixed(0)}%)` : ''}` : '--';
        this.safeSetText('decisionRegime', regimeLabel);
        this.safeSetText('decisionSl', top ? price(top.stop_loss || top.sl) : '--');
        this.safeSetText('decisionTp', top ? price(top.take_profit || top.tp) : '--');
        this.safeSetText('decisionRr', rr ? `1:${rr.toFixed(2)}` : '--');
        this.safeSetText('decisionSpread', spreadLabel);
        this.safeSetText('decisionReason', this.buildDecisionReason(top, decision, grade));
        this.renderAdaptiveScore(top);

        const badge = document.getElementById('decisionBadge');
        if (badge) {
            badge.textContent = decision;
            badge.className = `decision-badge ${decision.toLowerCase().replace(/[^a-z0-9]+/g, '-')}`;
        }

        if (top) {
            this.safeSetText('visionSymbol', top.symbol);
            this.safeSetText('visionType', direction);
            this.safeSetText('visionEntry', price(top.entry));
            this.safeSetText('actionStatus', decision);
            const action = document.getElementById('actionStatus');
            if (action) action.style.background = bestConv >= 0.6 ? '#22c55e' : bestConv >= 0.35 ? '#f59e0b' : '#ef4444';
        } else {
            this.safeSetText('visionSymbol', '--');
            this.safeSetText('visionType', '--');
            this.safeSetText('visionEntry', '--');
            this.safeSetText('actionStatus', 'NO SIGNAL');
            const action = document.getElementById('actionStatus');
            if (action) action.style.background = '#64748b';
        }

        this.renderDecisionComponents(top);
        this.renderSetupChecklist(top);
        this.setConviction('setupScoreVal', setupScore);
        this.setConvictionBarValue('setupScore', setupScore);
        this.setConvictionBarValue('modelScore', bestConv);
        this.setVisionGauge(Math.max(bestConv, setupScore));
        this.renderVisionChart(top);
        this.loadChartVisuals(top);
    }

    renderAdaptiveScore(signal) {
        const label = document.getElementById('adaptiveScoreLabel');
        const fill = document.getElementById('adaptiveScoreFill');
        const reasons = document.getElementById('adaptiveScoreReasons');
        if (!label || !fill || !reasons) return;

        const adaptive = signal?.adaptive_weighting || signal?.setup_score?.adaptive_weighting;
        if (!signal || !adaptive) {
            label.textContent = 'No adjustment';
            fill.style.width = '50%';
            fill.dataset.state = 'neutral';
            reasons.innerHTML = '<span class="setup-chip">Waiting for closed-trade evidence</span>';
            return;
        }

        const base = Number(adaptive.base_score || 0);
        const adjusted = Number(adaptive.adjusted_score || base);
        const multiplier = Number(adaptive.multiplier || 1);
        const delta = adjusted - base;
        const state = adaptive.suppressed ? 'danger' : delta > 0.005 ? 'success' : delta < -0.005 ? 'warning' : 'neutral';
        label.textContent = adaptive.suppressed
            ? 'Suppressed'
            : `${(base * 100).toFixed(0)}% -> ${(adjusted * 100).toFixed(0)}% (${multiplier.toFixed(2)}x)`;
        fill.style.width = `${Math.max(5, Math.min(100, adjusted * 100))}%`;
        fill.dataset.state = state;

        const explanationRows = adaptive.explanations || [];
        reasons.innerHTML = explanationRows.length
            ? explanationRows.slice(0, 5).map((text) => `<span class="setup-chip ${state === 'success' ? 'pass' : state === 'danger' ? 'fail' : ''}">${this.escapeHtml(text)}</span>`).join('')
            : '<span class="setup-chip">No statistically confident adaptive adjustment yet</span>';
    }

    getDecisionLabel(signal, conviction, setupScore) {
        const status = String(signal?.status || signal?.decision || '').toUpperCase();
        if (status.includes('TRADE') || status.includes('APPROVED') || signal?.approved) return 'READY';
        if (status.includes('WAIT') || status.includes('REJECT')) return 'WAIT';
        if (setupScore >= 0.58 && conviction >= 0.45) return 'READY';
        if (setupScore >= 0.50 || conviction >= 0.40 || signal?.early_entry) return 'WATCH';
        return 'WAIT';
    }

    calculateRiskReward(signal) {
        const entry = Number(signal?.entry || signal?.current_price);
        const sl = Number(signal?.stop_loss || signal?.sl);
        const tp = Number(signal?.take_profit || signal?.tp);
        if (![entry, sl, tp].every(Number.isFinite)) return null;
        const risk = Math.abs(entry - sl);
        const reward = Math.abs(tp - entry);
        if (!risk || !reward) return null;
        return reward / risk;
    }

    renderDecisionComponents(signal) {
        const passedBox = document.getElementById('decisionPassed');
        const missingBox = document.getElementById('decisionMissing');
        if (!passedBox || !missingBox) return;

        const components = signal?.setup_score?.components || [];
        const passed = components.filter((item) => item.passed);
        const missing = components.filter((item) => !item.passed);

        const render = (items, fallback, tone = '') => {
            if (!items.length) return `<span class="setup-chip ${tone}">${fallback}</span>`;
            return items.slice(0, 7).map((item) => `
                <span class="setup-chip ${item.passed ? 'pass' : 'fail'}" title="${this.escapeHtml(item.detail || '')}">
                    ${item.passed ? 'OK' : 'Missing'} ${this.escapeHtml(item.label || 'Component')}
                </span>
            `).join('');
        };

        passedBox.innerHTML = render(passed, signal ? 'No confirmed components yet' : 'No confirmations yet');
        missingBox.innerHTML = render(missing, signal ? 'No major blocker detected' : 'Waiting for signal', signal ? 'pass' : 'fail');
    }

    buildDecisionReason(signal, decision, grade) {
        if (!signal) return 'No ranked setup yet. Waiting for structure.';
        const components = signal?.setup_score?.components || [];
        const missing = components.filter((item) => !item.passed).slice(0, 2).map((item) => item.label);
        const archetype = signal?.setup_score?.archetype || signal?.setup_score?.summary || signal?.nature || 'setup';
        if (decision === 'READY') return `READY: Grade ${grade} ${archetype}. Execution filters are aligned.`;
        if (decision === 'WATCH') {
            return `WATCH: Grade ${grade} ${archetype}. ${missing.length ? `Needs ${missing.join(' and ')}.` : 'Waiting for stronger confirmation.'}`;
        }
        return `WAIT: Grade ${grade} ${archetype}. ${missing.length ? `Missing ${missing.join(' and ')}.` : 'Quality is below execution threshold.'}`;
    }

    renderSetupChecklist(signal) {
        const box = document.getElementById('setupChecklist');
        if (!box) return;
        const components = signal?.setup_score?.components || [];
        if (!components.length) {
            box.innerHTML = '<span>No setup components yet</span>';
            return;
        }
        box.innerHTML = components.slice(0, 6).map((item) => `
            <span class="setup-chip ${item.passed ? 'pass' : 'fail'}" title="${item.detail || ''}">
                ${item.passed ? '✓' : '×'} ${item.label}
            </span>
        `).join('');
    }

    getSetupScore(signal) {
        return Number(signal?.setup_score?.score || signal?.confluence_score || signal?.conviction || 0);
    }

    getSetupGrade(signal) {
        const grade = signal?.setup_score?.grade;
        if (grade) return grade;
        const score = this.getSetupScore(signal);
        if (score >= 0.78) return 'A';
        if (score >= 0.65) return 'B';
        if (score >= 0.50) return 'C';
        return 'D';
    }

    renderStrategyBreakdown(signals = []) {
        const grid = document.getElementById('strategyBreakdownGrid');
        if (!grid) return;

        const scored = (signals || [])
            .filter((signal) => signal && (signal.setup_score || signal.confluence_score != null || signal.conviction != null))
            .slice()
            .sort((a, b) => this.getSetupScore(b) - this.getSetupScore(a))
            .slice(0, 6);

        if (!scored.length) {
            grid.innerHTML = '<div class="empty-state-compact">Waiting for scored setups...</div>';
            return;
        }

        grid.innerHTML = scored.map((signal) => {
            const score = this.getSetupScore(signal);
            const grade = this.getSetupGrade(signal);
            const components = signal.setup_score?.components || [];
            const passed = components.filter((item) => item.passed).length;
            const componentHtml = components.slice(0, 5).map((item) => `
                <span class="setup-chip ${item.passed ? 'pass' : 'fail'}" title="${item.detail || ''}">
                    ${item.passed ? '✓' : '×'} ${item.label}
                </span>
            `).join('');

            return `
                <button class="strategy-breakdown-card" type="button" onclick='bot.showSignalDetails(${JSON.stringify(signal).replace(/'/g, '&apos;')})'>
                    <div class="strategy-breakdown-top">
                        <strong>${signal.symbol || '-'}</strong>
                        <span class="setup-grade grade-${String(grade).toLowerCase()}">${grade}</span>
                    </div>
                    <div class="strategy-score-line">
                        <div class="strategy-score-bar"><span style="width:${Math.min(100, Math.max(0, score * 100))}%"></span></div>
                        <b>${(score * 100).toFixed(0)}%</b>
                    </div>
                    <p>${signal.setup_score?.summary || signal.nature || signal.type || 'Composite setup'}</p>
                    <small>${passed}/${components.length || 8} components passed</small>
                    <div class="setup-checklist">${componentHtml || '<span>No component detail</span>'}</div>
                </button>
            `;
        }).join('');
    }

    renderSpreadSafety(signals = []) {
        const grid = document.getElementById('spreadSafetyGrid');
        if (!grid) return;

        const items = (signals || [])
            .filter((signal) => signal && signal.symbol)
            .reduce((acc, signal) => {
                acc[signal.symbol] = signal;
                return acc;
            }, {});

        const rows = Object.values(items).slice(0, 8);
        if (!rows.length) {
            grid.innerHTML = '<div class="empty-state-compact">No spread data yet</div>';
            return;
        }

        grid.innerHTML = rows.map((signal) => {
            const spread = signal.spread_safety || signal.setup_score?.spread || {};
            const safe = spread.safe !== false;
            const value = spread.spread_pips != null ? `${Number(spread.spread_pips).toFixed(2)} pips` : 'n/a';
            return `
                <div class="spread-safety-row ${safe ? 'safe' : 'unsafe'}">
                    <strong>${signal.symbol}</strong>
                    <span>${value}</span>
                    <em>${safe ? 'Safe' : 'Avoid'}</em>
                </div>
            `;
        }).join('');
    }

    renderRejectionSummary(rejections = []) {
        const grid = document.getElementById('rejectionSummaryGrid');
        if (!grid) return;

        const grouped = {};
        (rejections || []).slice(-80).forEach((item) => {
            const symbol = item.symbol || 'GLOBAL';
            if (!grouped[symbol]) grouped[symbol] = [];
            grouped[symbol].push(item.reason || item.message || 'Rejected');
        });

        const rows = Object.entries(grouped).slice(-8).reverse();
        if (!rows.length) {
            grid.innerHTML = '<div class="empty-state-compact">No recent rejections</div>';
            return;
        }

        grid.innerHTML = rows.map(([symbol, reasons]) => {
            const reason = reasons[reasons.length - 1] || 'Rejected';
            return `
                <div class="rejection-summary-row">
                    <strong>${symbol}</strong>
                    <span>${reasons.length}x</span>
                    <p>${reason}</p>
                </div>
            `;
        }).join('');
    }

    setVisionGauge(conviction) {
        const ring = document.getElementById('visionRingFill');
        const level = Math.min(1, Math.max(0, conviction || 0));
        const offset = 314 - (314 * level);
        if (ring) ring.style.strokeDashoffset = offset;

        const text = document.getElementById('visionRingValue');
        if (text) text.textContent = `${(level * 100).toFixed(0)}%`;

        if (level >= 0.6) {
            ring.style.stroke = '#10b981';
        } else if (level >= 0.35) {
            ring.style.stroke = '#f59e0b';
        } else {
            ring.style.stroke = '#ef4444';
        }
    }

    renderVisionChart(signal) {
        if (!this.visionChart || !this.visionSeries) return;

        if (!signal) {
            this.visionSeries.setData([
                { time: Math.floor(Date.now() / 1000) - 3, value: 0.0 },
            ]);
            if (this.visionTrendSeries) this.visionTrendSeries.setData([]);
            this.clearVisionPriceLines();
            return;
        }

        const entry = Number(signal.entry || 0);
        const tp = Number(signal.tp || entry + entry * 0.0015);
        const sl = Number(signal.sl || entry - entry * 0.0015);

        const now = Math.floor(Date.now() / 1000);
        const chartData = [
            { time: now - 30, value: sl },
            { time: now - 20, value: entry },
            { time: now - 10, value: (entry + tp) / 2 },
            { time: now, value: tp },
        ];

        this.visionSeries.setData(chartData);

        this.visionSeries.setMarkers([
            { time: now - 30, position: 'below', color: '#ef4444', shape: 'circle', text: `SL ${sl.toFixed(5)}` },
            { time: now - 20, position: 'below', color: '#60a5fa', shape: 'circle', text: `ENTRY ${entry.toFixed(5)}` },
            { time: now, position: 'above', color: '#10b981', shape: 'circle', text: `TP ${tp.toFixed(5)}` },
        ]);

        this.drawTradePriceLines(entry, sl, tp);
    }

    clearVisionPriceLines() {
        if (!this.visionSeries || !this.visionPriceLines) return;
        this.visionPriceLines.forEach((line) => {
            try {
                this.visionSeries.removePriceLine(line);
            } catch (e) {
                // Lightweight Charts can throw if a line was already removed.
            }
        });
        this.visionPriceLines = [];
    }

    drawTradePriceLines(entry, sl, tp) {
        if (!this.visionSeries || typeof this.visionSeries.createPriceLine !== 'function') return;
        this.clearVisionPriceLines();
        [
            { price: entry, color: '#60a5fa', title: 'ENTRY' },
            { price: sl, color: '#ef4444', title: 'SL' },
            { price: tp, color: '#22c55e', title: 'TP' },
        ].forEach((line) => {
            if (!Number.isFinite(line.price)) return;
            this.visionPriceLines.push(this.visionSeries.createPriceLine({
                price: line.price,
                color: line.color,
                lineWidth: 1,
                lineStyle: 2,
                axisLabelVisible: true,
                title: line.title,
            }));
        });
    }

    async loadChartVisuals(signal) {
        if (!signal?.symbol || !this.visionSeries) return;
        const symbol = signal.symbol;
        if (this.lastVisualSymbol === symbol && this.lastVisualLoadAt && Date.now() - this.lastVisualLoadAt < 10000) return;
        this.lastVisualSymbol = symbol;
        this.lastVisualLoadAt = Date.now();

        try {
            const res = await fetch(`${this.apiBase}/chart-visuals/${encodeURIComponent(symbol)}`);
            if (!res.ok) return;
            const payload = await res.json();
            const visuals = payload.data || {};
            if (Array.isArray(visuals.candles) && visuals.candles.length) {
                this.visionSeries.setData(visuals.candles);
            }
            if (this.visionTrendSeries) {
                const points = visuals.trendline?.points || [];
                this.visionTrendSeries.setData(points.length >= 2 ? points : []);
            }
            if (this.visionSeries && typeof this.visionSeries.createPriceLine === 'function') {
                const entry = Number(signal.entry || 0);
                const sl = Number(signal.sl || signal.stop_loss || 0);
                const tp = Number(signal.tp || signal.take_profit || 0);
                this.drawTradePriceLines(entry, sl, tp);
                (visuals.levels || []).forEach((level) => {
                    const value = Number(level.value);
                    if (!Number.isFinite(value)) return;
                    this.visionPriceLines.push(this.visionSeries.createPriceLine({
                        price: value,
                        color: level.color || '#94a3b8',
                        lineWidth: 1,
                        lineStyle: 1,
                        axisLabelVisible: true,
                        title: level.label || 'Level',
                    }));
                });
            }
        } catch (e) {
            console.warn('Chart visuals unavailable:', e);
        }
    }

    buildLogicPills(message) {
        if (!message) return [];
        const pills = [];
        const tags = [
            { key: 'EMA', label: 'EMA ✅', detail: 'EMA filter status is included in this logic step.' },
            { key: 'FVG', label: 'FVG 🎯', detail: 'Convening Fair Value Gap pattern detection logic.' },
            { key: 'VOL', label: 'VOL 🔥', detail: 'Volume confirmation rule is referenced.' },
            { key: 'PENDING', label: 'PENDING ⏳', detail: 'Order is pending execution or waiting for conditions.' },
            { key: 'FILLED', label: 'FILLED 🎉', detail: 'Order filled and now active.' },
            { key: 'REJECT', label: 'REJECT ❌', detail: 'Signal or order rejected by risk rules.' },
            { key: 'KILLED', label: 'KILLED ⚡', detail: 'Symbol or global kill switch was triggered.' },
        ];

        const normalized = message.toUpperCase();
        tags.forEach((entry) => {
            if (normalized.includes(entry.key)) {
                pills.push(`<span class="logic-pill" data-details="${entry.detail}" title="${entry.detail}">${entry.label}</span>`);
            }
        });

        if (normalized.match(/\b0\.[0-9]+\b/)) {
            const detail = `Conviction level in message (${message}).`;
            pills.push(`<span class="logic-pill" data-details="${detail}" title="${detail}">Conviction</span>`);
        }

        // Ensure at least one pill shown for reading
        if (pills.length === 0) {
            pills.push(`<span class="logic-pill" data-details="General logic update" title="General logic update">Logic</span>`);
        }

        return pills;
    }

    updateExecutionTimeline(signals) {
        const timeline = document.getElementById('executionTimeline');
        if (!timeline) return;
        timeline.innerHTML = '';

        if (!signals || signals.length === 0) {
            timeline.innerHTML = '<div class="timeline-item">No upcoming entries</div>';
            return;
        }

        const sorted = (signals || []).slice().sort((a, b) => (b.conviction || 0) - (a.conviction || 0)).slice(0, 6);
        sorted.forEach((s) => {
            const item = document.createElement('div');
            const entryPrice = s.entry || 0;
            const currentPrice = s.current_price || entryPrice || 0;
            const pips = entryPrice && currentPrice ? Math.abs((entryPrice - currentPrice) / (entryPrice > 10 ? 0.0001 : 0.01)).toFixed(1) : 'n/a';
            const color = (s.conviction || 0) >= 0.6 ? '#10b981' : (s.conviction || 0) >= 0.35 ? '#f59e0b' : '#ef4444';

            item.className = 'timeline-item';
            item.style.borderColor = color;
            item.innerHTML = `
                <strong>${s.symbol || 'N/A'}</strong><br>
                ${s.type || s.nature || 'N/A'}<br>
                <span>${pips} pips to entry</span>
            `;
            timeline.appendChild(item);
        });
    }

    showLogDetails(log) {
        const modal = document.getElementById('logModal');
        const details = document.getElementById('logDetails');
        if (!modal || !details) {
            console.warn('Log details modal not found');
            return;
        }
        const entries = Object.entries(log).filter(([k]) => k !== 'timestamp');
        details.innerHTML = `
            <p><strong>Timestamp:</strong> <span>${new Date(log.timestamp).toLocaleString()}</span></p>
            ${entries
                .map(
                    ([key, value]) =>
                        `<p><strong>${this.escapeHtml(key)}:</strong> <span>${this.formatLogValue(value)}</span></p>`
                )
                .join('')}
        `;
        modal.style.display = 'block';
    }

    showSignalDetails(signal) {
        const modal = document.getElementById('signalModal');
        const details = document.getElementById('signalDetails');
        if (!modal || !details) {
            console.warn('Signal details modal not found');
            return;
        }

        const fields = [
            ['Symbol', signal.symbol],
            ['Signal', signal.nature || signal.type],
            ['Entry', signal.entry ? signal.entry.toFixed(5) : 'N/A'],
            ['SL', signal.sl ? signal.sl.toFixed(5) : 'N/A'],
            ['TP', signal.tp ? signal.tp.toFixed(5) : 'N/A'],
            ['Style', signal.trade_style || 'N/A'],
            ['Trade Type', signal.trade_horizon ? `${signal.trade_horizon.type} (${signal.trade_horizon.hold_time})` : 'N/A'],
            ['Trade Type Reason', signal.trade_horizon ? signal.trade_horizon.reason : 'N/A'],
            ['Conviction', signal.conviction != null ? signal.conviction.toFixed(3) : 'N/A'],
            ['Confluence', signal.confluence_score != null ? signal.confluence_score.toFixed(3) : 'N/A'],
            ['Early Setup Score', signal.setup_score ? `${signal.setup_score.score.toFixed(3)} (${signal.setup_score.grade})` : 'N/A'],
            ['Setup Archetype', signal.setup_score ? signal.setup_score.archetype : 'N/A'],
            ['Setup Summary', signal.setup_score ? signal.setup_score.summary : 'N/A'],
            ['Liquidity Sweep', signal.liquidity_sweep ? signal.liquidity_sweep.description : 'N/A'],
            ['MSS/BOS', signal.market_structure_shift ? signal.market_structure_shift.description : 'N/A'],
            ['HTF Bias', signal.higher_timeframe_bias ? signal.higher_timeframe_bias.description : 'N/A'],
            ['Session', signal.session_bias ? `${signal.session_bias.session}: ${signal.session_bias.description}` : 'N/A'],
            ['Displacement', signal.displacement ? signal.displacement.description : 'N/A'],
            ['Premium/Discount', signal.premium_discount ? signal.premium_discount.description : 'N/A'],
            ['Spread', signal.spread_safety ? signal.spread_safety.description : 'N/A'],
            ['Scalp Potential', signal.scalp_potential ? signal.scalp_potential.label : 'N/A'],
            ['Trend Strength', signal.trend_strength ? signal.trend_strength.label : 'N/A'],
            ['Order Block', signal.order_block ? signal.order_block.description : 'N/A'],
            ['Liquidity Zone', signal.liquidity_zone ? signal.liquidity_zone.description : 'N/A'],
            ['Divergence', signal.divergence ? signal.divergence.label : 'N/A'],
            ['Status', signal.status || 'N/A'],
        ];

        details.innerHTML = `
            <div class="signal-detail-grid">
                ${fields
                    .map(
                        ([label, value]) =>
                            `<div class="signal-detail-row"><strong>${label}:</strong> <span>${value}</span></div>`
                    )
                    .join('')}
            </div>
        `;
        modal.style.display = 'block';
    }

    async loadSessions() {
        try {
            const res = await fetch(`${this.apiBase}/sessions`);
            const data = await res.json();
            const list = document.getElementById('sessionsList');
            list.innerHTML = '';
            if (data.data) {
                Object.entries(data.data).forEach(([name, period]) => {
                    const li = document.createElement('li');
                    li.textContent = `${name}: ${period.start} - ${period.end}`;
                    list.appendChild(li);
                });
            }
        } catch (e) {
            console.error('Sessions error:', e);
        }
    }

    async loadKillStatus() {
        try {
            const res = await fetch(`${this.apiBase}/kill`);
            const data = await res.json();
            if (data.data) {
                const killAll = document.getElementById('killAll');
                if (killAll) killAll.textContent = data.data.all ? 'On' : 'Off';
                const sidebarKillState = document.getElementById('sidebarKillState');
                if (sidebarKillState) {
                    sidebarKillState.textContent = data.data.all ? 'Locked' : 'Ready';
                    sidebarKillState.dataset.state = data.data.all ? 'locked' : 'ready';
                }
                const sel = document.getElementById('killSymbol');
                if (!sel) return;
                sel.innerHTML = '';
                sel.appendChild(new Option('all','all'));
                this.symbols.forEach(sym => sel.appendChild(new Option(sym,sym)));
                const chk = document.getElementById('killToggle');
                if (chk) chk.checked = !!data.data[sel.value];
            }
        } catch (e) {
            console.error('Kill status error:', e);
        }
    }

    async updateRules() {
        try {
            const payload = {
                ema: document.getElementById('ruleEma').checked,
                volume: document.getElementById('ruleVolume').checked,
                po3: document.getElementById('rulePo3').checked,
            };
            await fetch(`${this.apiBase}/bot/rules`, {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify(payload),
            });
        } catch (e) {
            console.error('Rule update error:', e);
        }
    }

    async toggleKill() {
        try {
            const symbol = document.getElementById('killSymbol').value;
            const chk = document.getElementById('killToggle');
            const action = chk.checked ? 'disable' : 'enable';
            await fetch(`${this.apiBase}/kill`, {
                method: 'POST',
                headers: {'Content-Type':'application/json'},
                body: JSON.stringify({symbol, action})
            });
            this.loadKillStatus();
        } catch(e) {
            console.error('Kill toggle error:', e);
        }
    }

    async loadSettings() {
        try {
            const res = await fetch(`${this.apiBase}/config`);
            const data = await res.json();
            if (data.data) {
                const cfg = data.data;
                // Trading Parameters
                this.setAllInputs('symbols', cfg.TRADING_SYMBOLS || 'EURUSD,GBPUSD,USDJPY');
                this.setAllInputs('timeframe', cfg.TIMEFRAME || 'M5');
                this.setAllInputs('volume', cfg.TRADE_VOLUME || '0.01');
                this.setAllInputs('positionSizingMode', cfg.POSITION_SIZING_MODE || 'fixed');

                // Risk Management
                this.setAllInputs('riskPct', cfg.RISK_PERCENT || '1.0');
                this.setAllInputs('maxExposurePct', cfg.MAX_EXPOSURE_PERCENT || '5');
                this.setAllInputs('maxDrawdownPct', cfg.MAX_DRAWDOWN_PERCENT || '5');
                this.setAllInputs('minProfitPips', cfg.MIN_PROFIT_PIPS || '50');
                this.setAllInputs('dailyProfitCap', cfg.DAILY_PROFIT_CAP || '2.0');
                this.setAllInputs('scanIntervalSeconds', cfg.SCAN_INTERVAL_SECONDS || '3');
                this.setAllInputs('engineLoopSleepSeconds', cfg.ENGINE_LOOP_SLEEP_SECONDS || '3');
                this.setAllInputs('scanOnNewCandle', cfg.SCAN_ON_NEW_CANDLE === true);
                this.setAllInputs('scanTimeframeMinutes', cfg.SCAN_TIMEFRAME_MINUTES || '5');
                this.setAllInputs('duplicateSignalCooldownSeconds', cfg.DUPLICATE_SIGNAL_COOLDOWN_SECONDS || '300');
                this.setAllInputs('minExpectedR', cfg.MIN_EXPECTED_R || '1.2');
                this.setAllInputs('takeProfitR', cfg.TAKE_PROFIT_R_MULTIPLIER || '1.5');
                this.setAllInputs('takeProfitRScalp', cfg.TAKE_PROFIT_R_MULTIPLIER_SCALP || '1.2');
                this.setAllInputs('trailingStopTriggerPct', cfg.TRAILING_STOP_TRIGGER_PCT || '20');
                this.setAllInputs('trailingStopLockPips', cfg.TRAILING_STOP_LOCK_PIPS || '0.5');
                this.setAllInputs('trailingStopStepPct', cfg.TRAILING_STOP_STEP_PCT || '15');
                this.setAllInputs('trailingStopMinStepPips', cfg.TRAILING_STOP_MIN_STEP_PIPS || '0.3');
                this.setAllInputs('trailingTpEnabled', cfg.FEATURE_TRAILING_TAKE_PROFIT !== false);
                this.setAllInputs('trailingTpTriggerPct', cfg.TRAILING_TP_TRIGGER_PCT || '80');
                this.setAllInputs('trailingTpExtensionPct', cfg.TRAILING_TP_EXTENSION_PCT || '50');
                this.setAllInputs('trailingTpCooldownSeconds', cfg.TRAILING_TP_COOLDOWN_SECONDS || '300');
                this.setAllInputs('partialTpEnabled', cfg.FEATURE_PARTIAL_TAKE_PROFIT !== false);
                this.setAllInputs('partialTpTriggerR', cfg.PARTIAL_TP_TRIGGER_R || '0.30');
                this.setAllInputs('partialTpClosePct', cfg.PARTIAL_TP_CLOSE_PCT || '50');
                this.setAllInputs('reverseProfitExitEnabled', cfg.FEATURE_REVERSE_PROFIT_EXIT !== false);
                this.setAllInputs('reverseProfitMinR', cfg.REVERSE_PROFIT_MIN_R || '0.15');
                this.setAllInputs('reverseProfitGivebackPct', cfg.REVERSE_PROFIT_GIVEBACK_PCT || '25');

                // Signal Lockout System
                this.setAllInputs('signalLockoutEnabled', cfg.SIGNAL_LOCKOUT_ENABLED !== false);
                this.setAllInputs('maxTradesPerSymbol', cfg.MAX_TRADES_PER_SYMBOL || '1');
                this.setAllInputs('tradeCooldownMinutes', cfg.TRADE_COOLDOWN_MINUTES || '15');
                this.setAllInputs('noRevengeCooldown', cfg.NO_REVENGE_COOLDOWN_SECONDS ? cfg.NO_REVENGE_COOLDOWN_SECONDS / 3600 : '24');
                this.setAllInputs('professionalGateEnabled', cfg.FEATURE_PROFESSIONAL_EXECUTION_GATE !== false);
                this.setAllInputs('minExecutionGrade', cfg.MIN_EXECUTION_GRADE || 'B');
                this.setAllInputs('allowCGradeScalps', cfg.ALLOW_C_GRADE_SCALPS === true);
                this.setAllInputs('minProfessionalScore', cfg.MIN_PROFESSIONAL_SETUP_SCORE || '0.62');
                this.setAllInputs('minProfessionalConviction', cfg.MIN_PROFESSIONAL_CONVICTION || '0.30');
                this.setAllInputs('minSessionScoreForTrade', cfg.MIN_SESSION_SCORE_FOR_TRADE || '0.45');
                this.setAllInputs('earlyEntryEnabled', cfg.FEATURE_EARLY_ENTRY !== false);
                this.setAllInputs('earlyEntryMinScore', cfg.EARLY_ENTRY_MIN_SCORE || '0.50');
                this.setAllInputs('minSessionScoreForScalp', cfg.MIN_SESSION_SCORE_FOR_SCALP || '0.65');
                this.setAllInputs('executionSetupScoreThreshold', cfg.EXECUTION_SETUP_SCORE_THRESHOLD || '0.45');
                this.setAllInputs('executionArchetypeScoreThreshold', cfg.EXECUTION_ARCHETYPE_SCORE_THRESHOLD || '0.58');
                this.setAllInputs('marketExecutionScoreThreshold', cfg.MARKET_EXECUTION_SCORE_THRESHOLD || '0.60');
                this.setAllInputs('marketExecutionConvictionThreshold', cfg.MARKET_EXECUTION_CONVICTION_THRESHOLD || '0.55');
                this.setAllInputs('blockContextWatchTrades', cfg.BLOCK_CONTEXT_WATCH_TRADES !== false);
                this.setAllInputs('newsModeEnabled', cfg.FEATURE_NEWS_MODE !== false);
                this.setAllInputs('newsBlockUnsafe', cfg.NEWS_BLOCK_UNSAFE !== false);
                this.setAllInputs('newsRiskMultiplier', cfg.NEWS_RISK_MULTIPLIER || '35');
                this.setAllInputs('newsAllowRetestFollow', cfg.NEWS_ALLOW_RETEST_FOLLOW !== false);
                this.setAllInputs('newsLadderEnabled', cfg.FEATURE_NEWS_LADDER !== false);
                this.setAllInputs('newsLadderMaxAddons', cfg.NEWS_LADDER_MAX_ADDONS || '2');
                this.setAllInputs('newsLadderMinR', cfg.NEWS_LADDER_MIN_R || '0.55');
                this.setAllInputs('newsLadderVolumePct', cfg.NEWS_LADDER_VOLUME_PCT || '35');
                this.setAllInputs('newsLadderCooldownSeconds', cfg.NEWS_LADDER_COOLDOWN_SECONDS || '180');

                // MT5 Connection
                this.setAllInputs('mt5Account', cfg.MT5_ACCOUNT || '');
                this.setAllInputs('mt5Server', cfg.MT5_SERVER || '');
                this.setAllInputs('mt5Password', '');
                this.safeSetText('mt5PasswordState', cfg.MT5_PASSWORD_SET ? 'Password saved. Leave blank to keep it.' : 'No password saved yet.');
                this.setAllInputs('telegramBotToken', '');
                this.safeSetText('telegramTokenState', cfg.TELEGRAM_BOT_TOKEN_SET ? 'Telegram token saved. Leave blank to keep it.' : 'No Telegram token saved yet.');
                this.setAllInputs('telegramChatId', cfg.TELEGRAM_CHAT_ID || '');
                this.setAllInputs('discordWebhook', '');
                this.safeSetText('discordWebhookState', cfg.DISCORD_WEBHOOK_SET ? 'Discord webhook saved. Leave blank to keep it.' : 'No Discord webhook saved yet.');

                this.setAllInputs('warRoomEnabled', cfg.WAR_ROOM_ENABLED !== false);

                // Legacy Rules (for backward compatibility)
                if (cfg.RULES) {
                    document.getElementById('ruleEma').checked = cfg.RULES.ema;
                    document.getElementById('ruleVolume').checked = cfg.RULES.volume;
                    document.getElementById('rulePo3').checked = cfg.RULES.po3;
                }
            }
        } catch(e) {
            console.error('Settings load error:', e);
        }
    }

    setSettingsTab(tab = 'basic') {
        document.querySelectorAll('.settings-tab').forEach((btn) => {
            btn.classList.toggle('active', (btn.dataset.settingsTab || 'basic') === tab);
        });
        document.querySelectorAll('.settings-panel').forEach((panel) => {
            panel.classList.toggle('active', (panel.dataset.settingsPanel || 'basic') === tab);
        });
    }

    validateSettingsForm(form) {
        const inputs = form?.querySelectorAll('input[type="number"]') || [];
        for (const input of inputs) {
            if (input.disabled) continue;
            const label = input.dataset.numericLabel || input.closest('.form-group')?.querySelector('label')?.textContent?.trim() || input.id;
            const value = Number(input.value);
            if (input.required && input.value === '') {
                throw new Error(`${label} is required.`);
            }
            if (input.value !== '' && !Number.isFinite(value)) {
                throw new Error(`${label} must be a valid number.`);
            }
            if (input.min !== '' && value < Number(input.min)) {
                throw new Error(`${label} must be at least ${input.min}.`);
            }
            if (input.max !== '' && value > Number(input.max)) {
                throw new Error(`${label} must be no more than ${input.max}.`);
            }
        }
        const symbols = String(this.readFormValue(form, 'symbols', '') || '')
            .split(',')
            .map((x) => x.trim())
            .filter(Boolean);
        if (!symbols.length) {
            throw new Error('Add at least one trading symbol.');
        }
    }

    resetSettingsDefaults() {
        if (!confirm('Reset the settings form to conservative defaults? Save afterwards to apply them.')) return;
        const defaults = {
            symbols: 'EURUSD,GBPUSD,USDJPY',
            timeframe: 'M5',
            volume: '0.01',
            positionSizingMode: 'fixed',
            riskPct: '1',
            maxExposurePct: '5',
            maxDrawdownPct: '5',
            minProfitPips: '50',
            dailyProfitCap: '2',
            scanIntervalSeconds: '3',
            engineLoopSleepSeconds: '3',
            scanOnNewCandle: false,
            scanTimeframeMinutes: '5',
            duplicateSignalCooldownSeconds: '300',
            minExpectedR: '1.2',
            takeProfitR: '1.5',
            takeProfitRScalp: '1.2',
            trailingStopTriggerPct: '20',
            trailingStopLockPips: '0.5',
            trailingStopStepPct: '15',
            trailingStopMinStepPips: '0.3',
            trailingTpEnabled: true,
            trailingTpTriggerPct: '80',
            trailingTpExtensionPct: '50',
            trailingTpCooldownSeconds: '300',
            partialTpEnabled: true,
            partialTpTriggerR: '0.30',
            partialTpClosePct: '50',
            reverseProfitExitEnabled: true,
            reverseProfitMinR: '0.15',
            reverseProfitGivebackPct: '25',
            signalLockoutEnabled: true,
            maxTradesPerSymbol: '1',
            tradeCooldownMinutes: '3',
            noRevengeCooldown: '24',
            professionalGateEnabled: true,
            minExecutionGrade: 'B',
            allowCGradeScalps: false,
            minProfessionalScore: '0.62',
            minProfessionalConviction: '0.30',
            minSessionScoreForTrade: '0.45',
            earlyEntryEnabled: true,
            earlyEntryMinScore: '0.50',
            minSessionScoreForScalp: '0.65',
            executionSetupScoreThreshold: '0.45',
            executionArchetypeScoreThreshold: '0.58',
            marketExecutionScoreThreshold: '0.60',
            marketExecutionConvictionThreshold: '0.55',
            blockContextWatchTrades: true,
            warRoomEnabled: true,
            newsModeEnabled: true,
            newsBlockUnsafe: true,
            newsRiskMultiplier: '35',
            newsAllowRetestFollow: true,
            newsLadderEnabled: true,
            newsLadderMaxAddons: '2',
            newsLadderMinR: '0.55',
            newsLadderVolumePct: '35',
            newsLadderCooldownSeconds: '180',
        };
        Object.entries(defaults).forEach(([id, value]) => this.setAllInputs(id, value));
        this.setAllInputs('mt5Password', '');
        this.setAllInputs('telegramBotToken', '');
        this.setAllInputs('discordWebhook', '');
        this.showNotification('Defaults loaded in the form. Review and save to apply.', 'info');
    }

    async saveSettings(e) {
        e.preventDefault();
        const form = e.target;
        const submitter = e.submitter || form?.querySelector('button[type="submit"]');
        if (submitter) submitter.disabled = true;
        try {
            this.validateSettingsForm(form);
            if (!confirm('Save configuration changes and apply them to the running bot where possible?')) {
                return;
            }
            const config = {
                // Trading Parameters
                TRADING_SYMBOLS: this.readFormValue(form, 'symbols', document.getElementById('symbols')?.value || ''),
                TIMEFRAME: this.readFormValue(form, 'timeframe', document.getElementById('timeframe')?.value || 'M5'),
                TRADE_VOLUME: parseFloat(this.readFormValue(form, 'volume', document.getElementById('volume')?.value || '0.01')),
                POSITION_SIZING_MODE: this.readFormValue(form, 'positionSizingMode', document.getElementById('positionSizingMode')?.value || 'fixed'),

                // Risk Management
                RISK_PERCENT: parseFloat(this.readFormValue(form, 'riskPct', '1')),
                MAX_EXPOSURE_PERCENT: parseFloat(this.readFormValue(form, 'maxExposurePct', '5')),
                MAX_DRAWDOWN_PERCENT: parseFloat(this.readFormValue(form, 'maxDrawdownPct', '5')),
                MIN_PROFIT_PIPS: parseFloat(this.readFormValue(form, 'minProfitPips', document.getElementById('minProfitPips')?.value || '10')),
                DAILY_PROFIT_CAP: parseFloat(this.readFormValue(form, 'dailyProfitCap', '2')),
                SCAN_INTERVAL_SECONDS: parseInt(this.readFormValue(form, 'scanIntervalSeconds', '3')),
                ENGINE_LOOP_SLEEP_SECONDS: parseFloat(this.readFormValue(form, 'engineLoopSleepSeconds', '3')),
                SCAN_ON_NEW_CANDLE: Boolean(this.readFormValue(form, 'scanOnNewCandle', document.getElementById('scanOnNewCandle')?.checked ?? false)),
                SCAN_TIMEFRAME_MINUTES: parseInt(this.readFormValue(form, 'scanTimeframeMinutes', '5')),
                DUPLICATE_SIGNAL_COOLDOWN_SECONDS: parseInt(this.readFormValue(form, 'duplicateSignalCooldownSeconds', '300')),
                MIN_EXPECTED_R: parseFloat(this.readFormValue(form, 'minExpectedR', document.getElementById('minExpectedR')?.value || '1.2')),
                TAKE_PROFIT_R_MULTIPLIER: parseFloat(this.readFormValue(form, 'takeProfitR', document.getElementById('takeProfitR')?.value || '1.5')),
                TAKE_PROFIT_R_MULTIPLIER_SCALP: parseFloat(this.readFormValue(form, 'takeProfitRScalp', document.getElementById('takeProfitRScalp')?.value || '1.2')),
                TRAILING_STOP_TRIGGER_PCT: parseFloat(this.readFormValue(form, 'trailingStopTriggerPct', document.getElementById('trailingStopTriggerPct')?.value || '20')),
                TRAILING_STOP_LOCK_PIPS: parseFloat(this.readFormValue(form, 'trailingStopLockPips', document.getElementById('trailingStopLockPips')?.value || '0.5')),
                TRAILING_STOP_STEP_PCT: parseFloat(this.readFormValue(form, 'trailingStopStepPct', document.getElementById('trailingStopStepPct')?.value || '15')),
                TRAILING_STOP_MIN_STEP_PIPS: parseFloat(this.readFormValue(form, 'trailingStopMinStepPips', document.getElementById('trailingStopMinStepPips')?.value || '0.3')),
                FEATURE_TRAILING_TAKE_PROFIT: Boolean(this.readFormValue(form, 'trailingTpEnabled', document.getElementById('trailingTpEnabled')?.checked ?? true)),
                TRAILING_TP_TRIGGER_PCT: parseFloat(this.readFormValue(form, 'trailingTpTriggerPct', document.getElementById('trailingTpTriggerPct')?.value || '80')),
                TRAILING_TP_EXTENSION_PCT: parseFloat(this.readFormValue(form, 'trailingTpExtensionPct', document.getElementById('trailingTpExtensionPct')?.value || '50')),
                TRAILING_TP_COOLDOWN_SECONDS: parseInt(this.readFormValue(form, 'trailingTpCooldownSeconds', document.getElementById('trailingTpCooldownSeconds')?.value || '300')),
                FEATURE_PARTIAL_TAKE_PROFIT: Boolean(this.readFormValue(form, 'partialTpEnabled', document.getElementById('partialTpEnabled')?.checked ?? true)),
                PARTIAL_TP_TRIGGER_R: parseFloat(this.readFormValue(form, 'partialTpTriggerR', document.getElementById('partialTpTriggerR')?.value || '0.30')),
                PARTIAL_TP_CLOSE_PCT: parseFloat(this.readFormValue(form, 'partialTpClosePct', document.getElementById('partialTpClosePct')?.value || '50')),
                FEATURE_REVERSE_PROFIT_EXIT: Boolean(this.readFormValue(form, 'reverseProfitExitEnabled', document.getElementById('reverseProfitExitEnabled')?.checked ?? true)),
                REVERSE_PROFIT_MIN_R: parseFloat(this.readFormValue(form, 'reverseProfitMinR', document.getElementById('reverseProfitMinR')?.value || '0.15')),
                REVERSE_PROFIT_GIVEBACK_PCT: parseFloat(this.readFormValue(form, 'reverseProfitGivebackPct', document.getElementById('reverseProfitGivebackPct')?.value || '25')),

                // Signal Lockout System
                SIGNAL_LOCKOUT_ENABLED: Boolean(this.readFormValue(form, 'signalLockoutEnabled', document.getElementById('signalLockoutEnabled')?.checked ?? true)),
                MAX_TRADES_PER_SYMBOL: parseInt(this.readFormValue(form, 'maxTradesPerSymbol', '1')),
                TRADE_COOLDOWN_MINUTES: parseInt(this.readFormValue(form, 'tradeCooldownMinutes', '3')),
                NO_REVENGE_COOLDOWN_SECONDS: parseInt(this.readFormValue(form, 'noRevengeCooldown', document.getElementById('noRevengeCooldown')?.value || '24')) * 3600,
                FEATURE_PROFESSIONAL_EXECUTION_GATE: Boolean(this.readFormValue(form, 'professionalGateEnabled', document.getElementById('professionalGateEnabled')?.checked ?? true)),
                MIN_EXECUTION_GRADE: this.readFormValue(form, 'minExecutionGrade', document.getElementById('minExecutionGrade')?.value || 'B'),
                ALLOW_C_GRADE_SCALPS: Boolean(this.readFormValue(form, 'allowCGradeScalps', document.getElementById('allowCGradeScalps')?.checked ?? false)),
                MIN_PROFESSIONAL_SETUP_SCORE: parseFloat(this.readFormValue(form, 'minProfessionalScore', document.getElementById('minProfessionalScore')?.value || '0.62')),
                MIN_PROFESSIONAL_CONVICTION: parseFloat(this.readFormValue(form, 'minProfessionalConviction', document.getElementById('minProfessionalConviction')?.value || '0.30')),
                MIN_SESSION_SCORE_FOR_TRADE: parseFloat(this.readFormValue(form, 'minSessionScoreForTrade', document.getElementById('minSessionScoreForTrade')?.value || '0.45')),
                FEATURE_EARLY_ENTRY: Boolean(this.readFormValue(form, 'earlyEntryEnabled', document.getElementById('earlyEntryEnabled')?.checked ?? true)),
                EARLY_ENTRY_MIN_SCORE: parseFloat(this.readFormValue(form, 'earlyEntryMinScore', document.getElementById('earlyEntryMinScore')?.value || '0.50')),
                MIN_SESSION_SCORE_FOR_SCALP: parseFloat(this.readFormValue(form, 'minSessionScoreForScalp', document.getElementById('minSessionScoreForScalp')?.value || '0.65')),
                EXECUTION_SETUP_SCORE_THRESHOLD: parseFloat(this.readFormValue(form, 'executionSetupScoreThreshold', document.getElementById('executionSetupScoreThreshold')?.value || '0.45')),
                EXECUTION_ARCHETYPE_SCORE_THRESHOLD: parseFloat(this.readFormValue(form, 'executionArchetypeScoreThreshold', document.getElementById('executionArchetypeScoreThreshold')?.value || '0.58')),
                MARKET_EXECUTION_SCORE_THRESHOLD: parseFloat(this.readFormValue(form, 'marketExecutionScoreThreshold', document.getElementById('marketExecutionScoreThreshold')?.value || '0.60')),
                MARKET_EXECUTION_CONVICTION_THRESHOLD: parseFloat(this.readFormValue(form, 'marketExecutionConvictionThreshold', document.getElementById('marketExecutionConvictionThreshold')?.value || '0.55')),
                BLOCK_CONTEXT_WATCH_TRADES: Boolean(this.readFormValue(form, 'blockContextWatchTrades', document.getElementById('blockContextWatchTrades')?.checked ?? true)),
                FEATURE_NEWS_MODE: Boolean(this.readFormValue(form, 'newsModeEnabled', document.getElementById('newsModeEnabled')?.checked ?? true)),
                NEWS_BLOCK_UNSAFE: Boolean(this.readFormValue(form, 'newsBlockUnsafe', document.getElementById('newsBlockUnsafe')?.checked ?? true)),
                NEWS_RISK_MULTIPLIER: parseFloat(this.readFormValue(form, 'newsRiskMultiplier', '35')),
                NEWS_ALLOW_RETEST_FOLLOW: Boolean(this.readFormValue(form, 'newsAllowRetestFollow', document.getElementById('newsAllowRetestFollow')?.checked ?? true)),
                FEATURE_NEWS_LADDER: Boolean(this.readFormValue(form, 'newsLadderEnabled', document.getElementById('newsLadderEnabled')?.checked ?? true)),
                NEWS_LADDER_MAX_ADDONS: parseInt(this.readFormValue(form, 'newsLadderMaxAddons', '2')),
                NEWS_LADDER_MIN_R: parseFloat(this.readFormValue(form, 'newsLadderMinR', '0.55')),
                NEWS_LADDER_VOLUME_PCT: parseFloat(this.readFormValue(form, 'newsLadderVolumePct', '35')),
                NEWS_LADDER_COOLDOWN_SECONDS: parseInt(this.readFormValue(form, 'newsLadderCooldownSeconds', '180')),

                // MT5 Connection
                MT5_ACCOUNT: this.readFormValue(form, 'mt5Account', ''),
                MT5_PASSWORD: this.readFormValue(form, 'mt5Password', ''),
                MT5_SERVER: this.readFormValue(form, 'mt5Server', ''),
                TELEGRAM_BOT_TOKEN: this.readFormValue(form, 'telegramBotToken', ''),
                TELEGRAM_CHAT_ID: this.readFormValue(form, 'telegramChatId', ''),
                DISCORD_WEBHOOK: this.readFormValue(form, 'discordWebhook', ''),

                WAR_ROOM_ENABLED: Boolean(this.readFormValue(form, 'warRoomEnabled', document.getElementById('warRoomEnabled')?.checked ?? true)),

                // Legacy Rules (for backward compatibility)
                RULES: {
                    ema: document.getElementById('ruleEma')?.checked || false,
                    volume: document.getElementById('ruleVolume')?.checked || false,
                    po3: document.getElementById('rulePo3')?.checked || false,
                },
            };

            const res = await fetch(`${this.apiBase}/config`, {
                method: 'POST',
                headers: {'Content-Type':'application/json'},
                body: JSON.stringify(config)
            });
            const data = await res.json();
            if (data.status === 'success') {
                await this.loadSettings();
                await this.updateDashboard();
                this.showNotification('Settings saved and applied.', 'success');
            } else {
                this.showNotification('Error: ' + (data.message || 'Unknown error while saving settings.'), 'error');
            }
        } catch(e) {
            this.showNotification('Save error: ' + e.message, 'error');
        } finally {
            if (submitter) submitter.disabled = false;
        }
    }

    async exportSettings() {
        try {
            const res = await fetch(`${this.apiBase}/config`);
            const data = await res.json();
            if (data.data) {
                const configJson = JSON.stringify(data.data, null, 2);
                const blob = new Blob([configJson], { type: 'application/json' });
                const url = URL.createObjectURL(blob);

                const a = document.createElement('a');
                a.href = url;
                a.download = `nexus-trading-config-${new Date().toISOString().split('T')[0]}.json`;
                document.body.appendChild(a);
                a.click();
                document.body.removeChild(a);
                URL.revokeObjectURL(url);
            } else {
                alert('No configuration data available to export.');
            }
        } catch(e) {
            alert('Export error: ' + e.message);
        }
    }

    async loadFutureTrades() {
        const grid = document.getElementById("globalRadarGrid");
        const emptyState = document.getElementById("globalRadarEmpty");
        if (grid) {
            grid.style.display = "grid";
            grid.innerHTML = `
                <div class="loading-state">
                    <i class="fas fa-spinner fa-spin"></i>
                    <span>Loading global radar...</span>
                </div>
            `;
        }
        if (emptyState) emptyState.style.display = "none";

        try {
            const response = await fetch(`${this.apiBase}/logs`);
            if (!response.ok) {
                throw new Error(`Logs endpoint returned ${response.status}`);
            }
            const data = await response.json();
            if (grid) grid.innerHTML = "";
            
            const futureTrades = (data.data && data.data.future_trades) ? data.data.future_trades : [];
            this.radarTrades = futureTrades.slice().reverse();
            this.renderRadarSummary(futureTrades);
            this.renderGlobalRadar();
            return;
            
            if (futureTrades.length > 0) {
                emptyState.style.display = "none";
                grid.style.display = "grid";
                
                futureTrades.slice().reverse().forEach((trade) => {
                    const card = document.createElement("div");
                    card.className = "watchlist-card";
                    card.style.cursor = 'pointer';
                    
                    const convictionScore = trade.conviction_score || 0;
                    const phase = trade.phase || "Monitoring";
                    const actionNeeded = trade.action_needed || "Waiting";
                    const setupName = trade.setup_name || "Setup Detected";
                    const trigger = trade.trigger || "Awaiting confirmation";
                    const setupScore = trade.setup_score?.score || trade.confluence_score || 0;
                    const setupGrade = trade.setup_score?.grade || '-';
                    const archetype = trade.setup_score?.archetype || trade.setup_name || 'Setup';
                    const components = trade.setup_score?.components || [];
                    const passedComponents = components.filter((item) => item.passed).length;
                    const spread = trade.spread_safety || trade.setup_score?.spread || {};
                    const spreadSafe = spread.safe !== false;
                    const spreadText = spread.spread_pips != null ? `${Number(spread.spread_pips).toFixed(2)}p` : 'n/a';
                    const componentChips = components.slice(0, 4).map((item) => `
                        <span class="setup-chip ${item.passed ? 'pass' : 'fail'}" title="${item.detail || ''}">
                            ${item.passed ? '✓' : '×'} ${item.label}
                        </span>
                    `).join('');
                    
                    // Status color based on conviction
                    let statusColor = "#6b7280"; // gray
                    if (convictionScore >= 80) statusColor = "#10b981"; // green
                    else if (convictionScore >= 60) statusColor = "#f59e0b"; // yellow
                    else if (convictionScore >= 40) statusColor = "#ef4444"; // red
                    
                    card.innerHTML = `
                        <div class="watchlist-card-header">
                            <div class="watchlist-card-symbol">${trade.symbol}</div>
                            <div class="watchlist-badge" style="background: ${statusColor}">${phase}</div>
                        </div>
                        <div class="watchlist-card-body">
                            <div class="watchlist-card-type">${trade.type || trade.nature || 'Signal'}</div>
                            <div class="watchlist-card-setup">${archetype}</div>
                            <div class="watchlist-card-conviction">
                                <div class="conviction-bar">
                                    <div class="conviction-fill" style="width: ${convictionScore}%"></div>
                                </div>
                                <span>${convictionScore}% Conviction</span>
                            </div>
                            <div class="watchlist-card-zone">Zone: ${trade.entry ? trade.entry.toFixed(5) : 'N/A'}</div>
                            <div class="watchlist-card-zone">Early Score: ${(setupScore * 100).toFixed(0)}% | Grade ${setupGrade}</div>
                            <div class="radar-micro-row">
                                <span>${passedComponents}/${components.length || 8} checks</span>
                                <span class="${spreadSafe ? 'metric-profit' : 'metric-loss'}">Spread ${spreadText}</span>
                            </div>
                            <div class="setup-checklist">${componentChips || '<span>No component detail</span>'}</div>
                            <div class="watchlist-card-trigger">${trigger}</div>
                            <div class="watchlist-card-action">${actionNeeded}</div>
                        </div>
                    `;

                    const signalDetails = {
                        ...trade,
                        conviction: trade.conviction_score || 0,
                        nature: trade.type || trade.nature || trade.setup_name || 'Signal',
                        trade_style: trade.setup_name || trade.phase || 'N/A',
                        scalp_potential: { label: trade.phase || 'N/A' },
                        trend_strength: { label: trade.trend_strength || 'N/A' },
                        order_block: { description: trade.order_block?.description || trade.setup_name || 'N/A' },
                        liquidity_zone: { description: trade.liquidity_zone?.description || trade.zone || 'N/A' },
                        divergence: { label: trade.divergence?.label || trade.nature || 'N/A' },
                        confluence_score: trade.confluence_score || trade.score || 0,
                        setup_score: trade.setup_score,
                        liquidity_sweep: trade.liquidity_sweep,
                        market_structure_shift: trade.market_structure_shift,
                        higher_timeframe_bias: trade.higher_timeframe_bias,
                        session_bias: trade.session_bias,
                        displacement: trade.displacement,
                        premium_discount: trade.premium_discount,
                        spread_safety: trade.spread_safety,
                    };

                    card.addEventListener('click', () => this.showSignalDetails(signalDetails));
                    grid.appendChild(card);
                });
            } else {
                this.renderRadarSummary([]);
                emptyState.style.display = "block";
                grid.style.display = "none";
            }
        } catch (e) {
            console.error("Global Radar error:", e);
            if (emptyState) {
                emptyState.style.display = "block";
                emptyState.innerHTML = `
                    <i class="fas fa-circle-exclamation"></i>
                    <p>Global Radar is unavailable. Fallback polling will retry shortly.</p>
                `;
            }
            if (grid) grid.style.display = "none";
        }
    }

    getRadarFilteredTrades() {
        const trades = this.radarTrades || [];
        if (this.radarFilter === 'ALL') return trades;
        return trades.filter((trade) => (trade.trade_horizon?.type || 'INTRADAY') === this.radarFilter);
    }

    changeRadarPage(delta) {
        const totalPages = Math.max(1, Math.ceil(this.getRadarFilteredTrades().length / this.radarPageSize));
        this.radarPage = Math.min(totalPages, Math.max(1, this.radarPage + delta));
        this.renderGlobalRadar();
    }

    renderGlobalRadar() {
        const grid = document.getElementById("globalRadarGrid");
        const emptyState = document.getElementById("globalRadarEmpty");
        if (!grid || !emptyState) return;

        const filtered = this.getRadarFilteredTrades();
        const totalPages = Math.max(1, Math.ceil(filtered.length / this.radarPageSize));
        this.radarPage = Math.min(totalPages, Math.max(1, this.radarPage));
        const start = (this.radarPage - 1) * this.radarPageSize;
        const pageTrades = filtered.slice(start, start + this.radarPageSize);

        const pageInfo = document.getElementById('radarPageInfo');
        if (pageInfo) pageInfo.textContent = this.formatPageInfo(filtered.length, start, pageTrades.length, 'setups', this.radarPage, totalPages);
        const prev = document.getElementById('radarPrevBtn');
        const next = document.getElementById('radarNextBtn');
        if (prev) prev.disabled = this.radarPage <= 1;
        if (next) next.disabled = this.radarPage >= totalPages;

        grid.innerHTML = "";
        if (!pageTrades.length) {
            emptyState.style.display = "block";
            grid.style.display = "none";
            return;
        }

        emptyState.style.display = "none";
        grid.style.display = "grid";
        pageTrades.forEach((trade) => grid.appendChild(this.buildRadarCard(trade)));
    }

    buildRadarCard(trade) {
        const card = document.createElement("div");
        card.className = "watchlist-card";
        card.style.cursor = 'pointer';

        const convictionScore = trade.conviction_score || 0;
        const phase = trade.phase || "Monitoring";
        const actionNeeded = trade.action_needed || "Waiting";
        const trigger = trade.trigger || "Awaiting confirmation";
        const setupScore = trade.setup_score?.score || trade.confluence_score || 0;
        const setupGrade = trade.setup_score?.grade || '-';
        const archetype = trade.setup_score?.archetype || trade.setup_name || 'Setup';
        const horizon = trade.trade_horizon || {type: 'INTRADAY', hold_time: '30 min-4h', confidence: 0, reason: 'default management'};
        const components = trade.setup_score?.components || [];
        const passedComponents = components.filter((item) => item.passed).length;
        const spread = trade.spread_safety || trade.setup_score?.spread || {};
        const falseMove = trade.false_move || trade.setup_score?.false_move || {};
        const newsMove = trade.news_move || trade.setup_score?.news_move || {};
        const regime = trade.market_regime || trade.setup_score?.market_regime || {};
        const regimeText = regime.label ? `${String(regime.label).toUpperCase()} ${(Number(regime.confidence || 0) * 100).toFixed(0)}%` : 'UNKNOWN';
        const spreadSafe = spread.safe !== false;
        const spreadText = spread.spread_pips != null ? `${Number(spread.spread_pips).toFixed(2)}p` : 'n/a';
        const falseMoveLabel = falseMove.type && !['UNKNOWN', 'RANGE'].includes(falseMove.type)
            ? falseMove.type.replaceAll('_', ' ')
            : 'No trap';
        const newsLabel = newsMove.mode && newsMove.mode !== 'NORMAL'
            ? newsMove.mode.replaceAll('_', ' ')
            : 'Normal';
        const componentChips = components.slice(0, 4).map((item) => `
            <span class="setup-chip ${item.passed ? 'pass' : 'fail'}" title="${item.detail || ''}">
                ${item.passed ? '✓' : '×'} ${item.label}
            </span>
        `).join('');

        let statusColor = "#6b7280";
        if (convictionScore >= 80) statusColor = "#10b981";
        else if (convictionScore >= 60) statusColor = "#f59e0b";
        else if (convictionScore >= 40) statusColor = "#ef4444";

        card.innerHTML = `
            <div class="watchlist-card-header">
                <div class="watchlist-card-symbol">${trade.symbol}</div>
                <div class="watchlist-badge" style="background: ${statusColor}">${phase}</div>
            </div>
            <div class="watchlist-card-body">
                <div class="radar-horizon-row">
                    <span class="horizon-badge horizon-${String(horizon.type).toLowerCase()}">${horizon.type}</span>
                    <small>${horizon.hold_time || ''}</small>
                </div>
                <div class="watchlist-card-type">${trade.type || trade.nature || 'Signal'}</div>
                <div class="watchlist-card-setup">${archetype}</div>
                <div class="watchlist-card-conviction">
                    <div class="conviction-bar">
                        <div class="conviction-fill" style="width: ${convictionScore}%"></div>
                    </div>
                    <span>${convictionScore}% Conviction</span>
                </div>
                <div class="watchlist-card-zone">Zone: ${trade.entry ? trade.entry.toFixed(5) : 'N/A'}</div>
                <div class="watchlist-card-zone">Early Score: ${(setupScore * 100).toFixed(0)}% | Grade ${setupGrade}</div>
                <div class="watchlist-card-zone">Regime: <span class="status-badge ${this.regimeState(regime.label)}">${regimeText}</span></div>
                <div class="radar-micro-row">
                    <span>${passedComponents}/${components.length || 8} checks</span>
                    <span class="${spreadSafe ? 'metric-profit' : 'metric-loss'}">Spread ${spreadText}</span>
                </div>
                <div class="radar-micro-row">
                    <span class="${falseMove.safe === false ? 'metric-loss' : 'metric-neutral'}">${falseMoveLabel}</span>
                    <span class="${newsMove.safe === false ? 'metric-loss' : 'metric-profit'}">News ${newsLabel}</span>
                </div>
                <div class="setup-checklist">${componentChips || '<span>No component detail</span>'}</div>
                <div class="watchlist-card-trigger">${trigger}</div>
                <div class="watchlist-card-action">${actionNeeded}</div>
            </div>
        `;

        card.addEventListener('click', () => this.showSignalDetails({...trade, conviction: trade.conviction_score || 0}));
        return card;
    }

    renderRadarSummary(trades = []) {
        const summary = document.getElementById('radarSummary');
        if (!summary) return;
        const total = trades.length;
        const strong = trades.filter((trade) => ['A', 'B'].includes(trade.setup_score?.grade)).length;
        const spreadSafe = trades.filter((trade) => (trade.spread_safety || trade.setup_score?.spread || {}).safe !== false).length;
        const scalp = trades.filter((trade) => trade.trade_horizon?.type === 'SCALP').length;
        const swing = trades.filter((trade) => trade.trade_horizon?.type === 'SWING').length;
        const traps = trades.filter((trade) => {
            const falseMove = trade.false_move || trade.setup_score?.false_move || {};
            return falseMove.type && !['UNKNOWN', 'RANGE'].includes(falseMove.type);
        }).length;
        const newsWatch = trades.filter((trade) => {
            const newsMove = trade.news_move || trade.setup_score?.news_move || {};
            return newsMove.mode && newsMove.mode !== 'NORMAL';
        }).length;
        const regimeCounts = {};
        trades.forEach((trade) => {
            const regime = trade.market_regime || trade.setup_score?.market_regime || {};
            const label = String(regime.label || 'unknown').toUpperCase();
            regimeCounts[label] = (regimeCounts[label] || 0) + 1;
        });
        const topRegime = Object.entries(regimeCounts).sort((a, b) => b[1] - a[1])[0];
        summary.innerHTML = `
            <span>Signals: <strong>${total}</strong></span>
            <span>Regime: <strong id="radarRegimeLabel">${topRegime ? `${topRegime[0]} (${topRegime[1]})` : 'UNKNOWN'}</strong></span>
            <span>A/B setups: <strong>${strong}</strong></span>
            <span>Spread safe: <strong>${spreadSafe}</strong></span>
            <span>Scalps: <strong>${scalp}</strong></span>
            <span>Swings: <strong>${swing}</strong></span>
            <span>Traps: <strong>${traps}</strong></span>
            <span>News watch: <strong>${newsWatch}</strong></span>
        `;
    }

    async loadPendingOrders(data) {
        try {
            if (!data) {
                const response = await fetch(`${this.apiBase}/pending-orders`);
                data = await response.json();
            }
            const tbody = document.querySelector("#pendingOrdersTable tbody");
            const emptyState = document.getElementById("pendingOrdersEmpty");
            if (!tbody || !emptyState) return;
            tbody.innerHTML = "";

            const orders = (data.data && Array.isArray(data.data)) ? data.data : [];

            if (orders.length > 0) {
                emptyState.style.display = "none";
                tbody.style.display = "table-row-group";
                
                orders.forEach((order) => {
                    const row = tbody.insertRow();
                    const rr = order.tp && order.sl && order.entry 
                        ? ((Math.abs(order.tp - order.entry) / Math.abs(order.entry - order.sl)) || 0).toFixed(1)
                        : "-";
                    
                    row.innerHTML = `
                        <td><strong>${order.symbol}</strong></td>
                        <td>
                            <span class="phase-badge" style="
                                background: ${order.action === 'BUY' ? 'rgba(16, 185, 129, 0.2)' : 'rgba(239, 68, 68, 0.2)'};
                                color: ${order.action === 'BUY' ? '#10b981' : '#ef4444'};
                            ">${order.action}</span>
                        </td>
                        <td>${order.entry ? order.entry.toFixed(5) : '-'}</td>
                        <td>${order.sl ? order.sl.toFixed(5) : '-'}</td>
                        <td>${order.tp ? order.tp.toFixed(5) : '-'}</td>
                        <td>
                            <span class="score-high">${order.probability ? (order.probability * 100).toFixed(0) + '%' : 'N/A'}</span>
                        </td>
                        <td><span class="badge-pending">${order.ticket ? 'ACTIVE' : 'PENDING'}</span></td>
                    `;
                });
            } else {
                emptyState.style.display = "block";
                tbody.style.display = "none";
            }
        } catch (e) {
            console.error("Pending orders error:", e);
        }
    }

    loadWatchlist(data) {
        try {
            const tbody = document.querySelector("#watchlistTable tbody");
            const emptyState = document.getElementById("watchlistEmpty");
            tbody.innerHTML = "";

            const watchlist = (data.data && data.data.watchlist) ? data.data.watchlist : [];

            if (watchlist.length > 0) {
                emptyState.style.display = "none";
                tbody.style.display = "table-row-group";
                
                watchlist.forEach((entry) => {
                    const row = tbody.insertRow();
                    const phase = entry.phase || 1;
                    const phaseClass = entry.ready_for_execution ? 'phase-ready' : `phase-${phase}`;
                    const phaseText = entry.ready_for_execution ? '✓ READY' : `Phase ${phase}/3`;
                    
                    const progress = (phase / 3) * 100;
                    
                    row.innerHTML = `
                        <td><strong>${entry.symbol}</strong></td>
                        <td>
                            <span class="phase-badge ${phaseClass}">${phaseText}</span>
                        </td>
                        <td>
                            <i class="fas ${entry.sweep_detected ? 'fa-check' : 'fa-hourglass-half'}"
                               style="color: ${entry.sweep_detected ? '#10b981' : '#fbbf24'}"></i>
                        </td>
                        <td>
                            <i class="fas ${entry.mBOS_detected ? 'fa-check' : 'fa-hourglass-half'}"
                               style="color: ${entry.mBOS_detected ? '#10b981' : '#fbbf24'}"></i>
                        </td>
                        <td>
                            <i class="fas ${entry.ready_for_execution ? 'fa-check' : 'fa-times'}"
                               style="color: ${entry.ready_for_execution ? '#10b981' : '#cbd5e1'}"></i>
                        </td>
                        <td>
                            <div class="phase-progress">
                                <div class="phase-progress-bar" style="width: ${progress}%"></div>
                            </div>
                        </td>
                    `;
                });
            } else {
                emptyState.style.display = "block";
                tbody.style.display = "none";
            }
        } catch (e) {
            console.error("Watchlist error:", e);
        }
    }

    loadPredictedZones(data) {
        try {
            const tbody = document.querySelector("#predictedZonesTable tbody");
            const emptyState = document.getElementById("zonesEmpty");
            tbody.innerHTML = "";

            const ready = (data.data && data.data.ready_for_execution) ? data.data.ready_for_execution : [];

            if (ready.length > 0) {
                emptyState.style.display = "none";
                tbody.style.display = "table-row-group";
                
                ready.forEach((zone, idx) => {
                    const fvg = zone.extreme_fvg || {};
                    const rr = fvg.tp && fvg.sl && fvg.entry 
                        ? (Math.abs(fvg.tp - fvg.entry) / Math.abs(fvg.entry - fvg.sl)).toFixed(1)
                        : "-";
                    
                    const action = fvg.action || "UNKNOWN";
                    const actionColor = action === "BUY" ? "#10b981" : "#ef4444";
                    
                    const row = tbody.insertRow();
                    row.innerHTML = `
                        <td><strong>${zone.symbol}</strong></td>
                        <td>
                            <span style="color: ${actionColor}; font-weight: 600;">${action}</span>
                        </td>
                        <td>
                            <strong>${fvg.entry ? fvg.entry.toFixed(5) : '-'}</strong>
                            <div style="font-size: 11px; color: rgba(203, 213, 225, 0.6);">
                                ±${fvg.gap_size ? (fvg.gap_size * 0.5).toFixed(5) : '0'}
                            </div>
                        </td>
                        <td>${fvg.sl ? fvg.sl.toFixed(5) : '-'}</td>
                        <td>${fvg.tp ? fvg.tp.toFixed(5) : '-'}</td>
                        <td><span class="score-high">1:${rr}</span></td>
                        <td><span class="score-high">HIGH</span></td>
                        <td>
                            <button class="btn-small btn-place" onclick="bot.placeZoneOrder('${zone.symbol}')">
                                <i class="fas fa-play"></i> Execute
                            </button>
                        </td>
                    `;
                });
            } else {
                emptyState.style.display = "block";
                tbody.style.display = "none";
            }
        } catch (e) {
            console.error("Predicted zones error:", e);
        }
    }

    async placeZoneOrder(symbol) {
        try {
            const res = await fetch(`${this.apiBase}/pending-orders/place`, {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({symbols: [symbol]})
            });
            const data = await res.json();
            if (data.status === 'success') {
                alert(`Order placed for ${symbol}!`);
                this.loadFutureTrades();
            } else {
                alert('Error: ' + (data.message || 'unknown'));
            }
        } catch (e) {
            alert('Error: ' + e.message);
        }
    }

    async togglePause() {
        const btn = document.getElementById('pauseBtn');
        if (!btn) return;

        const isPaused = btn.textContent.includes('Resume');
        try {
            const res = await fetch(`${this.apiBase}/bot/${isPaused ? 'start' : 'stop'}`, {
                method: 'POST'
            });
            const data = await res.json();
            if (data.status === 'success') {
                btn.innerHTML = isPaused ?
                    '<i class="fas fa-pause"></i> Pause Scan' :
                    '<i class="fas fa-play"></i> Resume Scan';
                this.updateDashboard();
            }
        } catch (e) {
            console.error('Toggle pause error:', e);
        }
    }

    async approveSignal() {
        // This would need to be implemented based on current signal
        alert('Signal approval feature - to be implemented');
    }

    async rejectSignal() {
        // This would need to be implemented based on current signal
        alert('Signal rejection feature - to be implemented');
    }

}

document.addEventListener('DOMContentLoaded', () => {
    const bot = new Bot();
    window.bot = bot;
});
