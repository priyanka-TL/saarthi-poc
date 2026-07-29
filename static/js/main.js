document.addEventListener('DOMContentLoaded', () => {
    const chatForm = document.getElementById('chat-form');
    const userInput = document.getElementById('user-input');
    const chatMessages = document.getElementById('chat-messages');
    const typingIndicator = document.getElementById('typing-indicator');
    const agentList = document.getElementById('agent-list');
    const recentConversationsList = document.getElementById('recent-conversations-list');
    const themeToggle = document.getElementById('theme-toggle');
    const mobileMenuBtn = document.getElementById('mobile-menu-btn');
    const mobileSidebarClose = document.getElementById('mobile-sidebar-close');
    const sidebar = document.getElementById('sidebar');
    const sidebarOverlay = document.getElementById('sidebar-overlay');
    const newChatBtn = document.getElementById('new-chat-btn');
    const root = document.documentElement;

    const BOT_AVATAR_SVG = `
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"
            stroke-linecap="round" stroke-linejoin="round" width="18" height="18">
            <rect x="3" y="10" width="18" height="10" rx="3"></rect>
            <circle cx="8.5" cy="15" r="1.2" fill="currentColor" stroke="none"></circle>
            <circle cx="15.5" cy="15" r="1.2" fill="currentColor" stroke="none"></circle>
            <line x1="12" y1="10" x2="12" y2="6"></line>
            <circle cx="12" cy="4.5" r="1.5"></circle>
        </svg>`;

    const AGENT_ICON_SVG = `
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"
            stroke-linecap="round" stroke-linejoin="round" width="16" height="16">
            <path d="M3 12a9 9 0 1 0 2.6-6.36"></path>
            <polyline points="3 4 3 12 11 12"></polyline>
        </svg>`;

    function formatTime(date) {
        return date.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', hour12: false });
    }

    function formatRelativeTime(date) {
        const seconds = Math.floor((Date.now() - date.getTime()) / 1000);
        if (seconds < 60) return 'Just now';
        const minutes = Math.floor(seconds / 60);
        if (minutes < 60) return `${minutes} minute${minutes === 1 ? '' : 's'} ago`;
        const hours = Math.floor(minutes / 60);
        if (hours < 24) return `${hours} hour${hours === 1 ? '' : 's'} ago`;
        const days = Math.floor(hours / 24);
        if (days < 7) return `${days} day${days === 1 ? '' : 's'} ago`;
        return date.toLocaleDateString();
    }

    // -----------------------------------------------------------------------
    // Theme
    // -----------------------------------------------------------------------
    if (localStorage.getItem('theme') === 'dark') {
        root.setAttribute('data-theme', 'dark');
    }

    themeToggle.addEventListener('click', () => {
        if (root.getAttribute('data-theme') === 'dark') {
            root.removeAttribute('data-theme');
            localStorage.setItem('theme', 'light');
        } else {
            root.setAttribute('data-theme', 'dark');
            localStorage.setItem('theme', 'dark');
        }
    });

    // -----------------------------------------------------------------------
    // Mobile Sidebar Toggle
    // -----------------------------------------------------------------------
    function toggleSidebar() {
        sidebar.classList.toggle('active');
        sidebarOverlay.classList.toggle('active');
    }

    if (mobileMenuBtn)        mobileMenuBtn.addEventListener('click', toggleSidebar);
    if (mobileSidebarClose)   mobileSidebarClose.addEventListener('click', toggleSidebar);
    if (sidebarOverlay)       sidebarOverlay.addEventListener('click', toggleSidebar);

    const advancedToggle = document.getElementById('advanced-toggle');
    const advancedContent = document.getElementById('advanced-content');

    if (advancedToggle && advancedContent) {
        advancedToggle.addEventListener('click', () => {
            const isExpanded = advancedToggle.getAttribute('aria-expanded') === 'true';
            advancedToggle.setAttribute('aria-expanded', !isExpanded);
            if (isExpanded) {
                advancedContent.classList.add('collapsed');
            } else {
                advancedContent.classList.remove('collapsed');
            }
        });
    }

    // -----------------------------------------------------------------------
    // §10.3 change 1: conversation identity
    //   conversationId lives in sessionStorage so a page reload on the same
    //   tab restores the interview; opening a new tab starts fresh.
    // -----------------------------------------------------------------------
    let conversationId = sessionStorage.getItem('saarthi_cid') || null;

    // §10.3 change 2: agent identity by key, not display name.
    //   null means "route me" — the server selects the best agent.
    //   'Saarthi' magic string removed; routing never derived from display text.
    let currentAgentKey = null;

    function setContextBanner(label, subLabel) {
        const banner = document.getElementById('active-context-banner');
        const contextNameEl = document.getElementById('context-name');
        const subContextNameEl = document.getElementById('sub-context-name');
        if (banner && contextNameEl && subContextNameEl) {
            contextNameEl.textContent = label + ' Context';
            subContextNameEl.textContent = subLabel || label;
            banner.classList.remove('hidden');
        }
    }

    function clearActiveItems() {
        document.querySelectorAll('.agent-item, .capability-card, .highlight-card').forEach(el =>
            el.classList.remove('active')
        );
    }

    // -----------------------------------------------------------------------
    // §10.3 change 4: wire the dead "Capture Stories" button.
    //   The old card handler read .capability-title text ("Listening at Scale")
    //   and set currentSelectedAgent = "Listening at Scale". That matched no
    //   agent → /api/chat returned 404.
    //
    //   Fix: any element with data-agent-key gets a dedicated listener that
    //   stopPropagation()s so the generic card handler below never fires.
    //   Routing comes from data-agent-key, not from display text.
    // -----------------------------------------------------------------------
    document.querySelectorAll('[data-agent-key]').forEach(el => {
        el.addEventListener('click', (e) => {
            e.stopPropagation();   // prevent generic card handler from winning

            clearActiveItems();
            const card = el.closest('.capability-card, .highlight-card');
            if (card) card.classList.add('active');

            currentAgentKey = el.dataset.agentKey;
            setContextBanner(el.dataset.agentLabel || el.dataset.agentKey, 'Story capture');

            if (window.innerWidth <= 768) {
                sidebar.classList.remove('active');
                sidebarOverlay.classList.remove('active');
            }

            // data-autostart: send the opening message automatically so the
            // user doesn't have to type anything to start the interview.
            if (el.dataset.autostart) {
                sendMessage(el.dataset.autostart);
            }
        });
    });

    // Generic card handler for cards WITHOUT a dedicated data-agent-key listener.
    // §10.3: this handler must NEVER drive routing — that's the bug we fixed above.
    document.querySelectorAll('.capability-card:not([data-agent-key]), .highlight-card').forEach(card => {
        card.addEventListener('click', () => {
            clearActiveItems();
            card.classList.add('active');

            const titleEl = card.querySelector('.capability-title') || card.querySelector('.highlight-title');
            if (titleEl) {
                // Display label only — do NOT assign to currentAgentKey.
                setContextBanner(titleEl.textContent, titleEl.textContent);
            }

            if (window.innerWidth <= 768) {
                sidebar.classList.remove('active');
                sidebarOverlay.classList.remove('active');
            }
        });
    });

    // -----------------------------------------------------------------------
    // Agent list (sidebar)
    // -----------------------------------------------------------------------
    async function loadAgents() {
        try {
            const response = await fetch('/api/agents');
            const agents = await response.json();

            agentList.innerHTML = '';

            agents.forEach(agent => {
                const li = document.createElement('li');
                li.className = 'agent-item';
                // §10.3 change 2: store agent.key on the element, not agent.name
                li.dataset.key = agent.key;
                li.innerHTML = `
                    <div class="agent-item-title">${AGENT_ICON_SVG}<span class="agent-name">${agent.name}</span></div>
                    <div class="agent-desc">${agent.description}</div>
                `;

                li.addEventListener('click', () => {
                    clearActiveItems();
                    li.classList.add('active');
                    // §10.3: route by key, keep sending agent_name for backward compat
                    currentAgentKey = agent.key;

                    setContextBanner(agent.name, 'Agent interaction');

                    if (window.innerWidth <= 768) {
                        sidebar.classList.remove('active');
                        sidebarOverlay.classList.remove('active');
                    }
                });

                agentList.appendChild(li);
            });
        } catch (error) {
            console.error('Failed to load agents:', error);
        }
    }

    loadAgents();

    // -----------------------------------------------------------------------
    // Recent conversations (sidebar, before Advanced)
    // -----------------------------------------------------------------------
    async function loadRecentConversations() {
        if (!recentConversationsList) return;
        try {
            const response = await fetch('/api/conversations?limit=5');
            const data = await response.json();

            recentConversationsList.innerHTML = '';

            (data.conversations || []).forEach(conv => {
                const li = document.createElement('li');
                li.className = 'agent-item';
                li.dataset.id = conv.id;

                const titleRow = document.createElement('div');
                titleRow.className = 'agent-item-title';
                titleRow.innerHTML = AGENT_ICON_SVG;
                const nameSpan = document.createElement('span');
                nameSpan.className = 'agent-name';
                // conv.title is user-supplied text (the conversation's first
                // message, truncated) -- textContent, never innerHTML, unlike
                // loadAgents()'s admin-authored agent.name/description.
                nameSpan.textContent = conv.title;
                titleRow.appendChild(nameSpan);

                const descDiv = document.createElement('div');
                descDiv.className = 'agent-desc';
                descDiv.textContent = conv.last_message_at
                    ? `Last active ${formatRelativeTime(new Date(conv.last_message_at))}`
                    : 'No messages yet';

                li.appendChild(titleRow);
                li.appendChild(descDiv);
                recentConversationsList.appendChild(li);
            });
        } catch (error) {
            console.error('Failed to load recent conversations:', error);
        }
    }

    loadRecentConversations();

    // -----------------------------------------------------------------------
    // Message rendering
    // -----------------------------------------------------------------------
    function scrollToBottom() {
        chatMessages.scrollTop = chatMessages.scrollHeight;
    }

    function addMessage(content, type, agentName = null, messageId = null) {
        const messageDiv = document.createElement('div');
        messageDiv.className = `message ${type}`;
        if (messageId) messageDiv.dataset.messageId = messageId;

        if (type === 'agent' || type === 'system') {
            const avatar = document.createElement('div');
            avatar.className = 'message-avatar';
            avatar.innerHTML = BOT_AVATAR_SVG;
            messageDiv.appendChild(avatar);

            const body = document.createElement('div');
            body.className = 'message-body';

            const contentDiv = document.createElement('div');
            contentDiv.className = 'message-content';

            if (type === 'agent') {
                // §10.3 change 7: SANITISE.
                //   contentDiv.innerHTML = marked.parse(content) is an XSS path:
                //   Mitra bot content is externally controlled and marked does NOT
                //   escape HTML by default. DOMPurify strips any injected scripts.
                //   Verified against design doc §13.2 risk #20.
                contentDiv.innerHTML = DOMPurify.sanitize(marked.parse(content));
            } else {
                contentDiv.textContent = content;
            }
            body.appendChild(contentDiv);

            const meta = document.createElement('div');
            meta.className = 'message-meta';
            meta.textContent = `${formatTime(new Date())} · ${agentName || 'Home'}`;
            body.appendChild(meta);

            messageDiv.appendChild(body);
        } else if (type === 'user') {
            const body = document.createElement('div');
            body.className = 'message-body';

            const contentDiv = document.createElement('div');
            contentDiv.className = 'message-content';
            contentDiv.textContent = content;
            body.appendChild(contentDiv);

            const meta = document.createElement('div');
            meta.className = 'message-meta';
            meta.textContent = formatTime(new Date());
            body.appendChild(meta);

            messageDiv.appendChild(body);
        } else {
            // context-switch pill — no avatar/timestamp
            const contentDiv = document.createElement('div');
            contentDiv.className = 'message-content';
            contentDiv.textContent = content;
            messageDiv.appendChild(contentDiv);
        }

        chatMessages.appendChild(messageDiv);
        scrollToBottom();
        return messageDiv;
    }

    // -----------------------------------------------------------------------
    // §10.3 change 3: renderOptions — choice buttons under a bot message.
    //   Purely additive: options is [] for all existing LLM agents.
    //
    //   On click:
    //     1. Echo the label as a user message (user sees what they picked).
    //     2. POST {message: value, option_id: id, conversation_id} — value is
    //        what the interview bot expects; label is display only.
    //     3. DISABLE THE WHOLE GROUP — §1.6: two user messages in a row silently
    //        collapse in Mitra's DB. A double-submit destroys an answer.
    // -----------------------------------------------------------------------
    function renderOptions(options, messageDiv) {
        if (!options || options.length === 0) return;

        const group = document.createElement('div');
        group.className = 'message-options';

        options.forEach(opt => {
            const btn = document.createElement('button');
            btn.className = 'option-btn';
            btn.dataset.id = opt.id;
            btn.dataset.value = opt.value;
            btn.textContent = opt.label;

            btn.addEventListener('click', () => {
                // Disable the whole group immediately — before the POST —
                // so a slow network can't allow a double-click to go through.
                group.querySelectorAll('.option-btn').forEach(b => {
                    b.disabled = true;
                    b.classList.add('option-btn--used');
                });

                addMessage(opt.label, 'user');
                sendMessage(opt.value, opt.id);
            });

            group.appendChild(btn);
        });

        const body = messageDiv.querySelector('.message-body');
        if (body) {
            body.appendChild(group);
        } else {
            messageDiv.appendChild(group);
        }
    }

    // -----------------------------------------------------------------------
    // §10.3 change 5: session state UI — finalizing / completed / report
    // -----------------------------------------------------------------------
    let _pollSessionTimer = null;

    function _clearSessionPoll() {
        if (_pollSessionTimer !== null) {
            clearInterval(_pollSessionTimer);
            _pollSessionTimer = null;
        }
    }

    function _renderFinalizingUI() {
        const notice = document.createElement('div');
        notice.id = 'session-finalizing-notice';
        notice.className = 'message system session-notice';
        notice.innerHTML = `
            <div class="message-avatar">${BOT_AVATAR_SVG}</div>
            <div class="message-body">
                <div class="message-content">
                    <span class="session-spinner"></span>
                    Writing your story&hellip; This may take a moment.
                </div>
            </div>`;
        chatMessages.appendChild(notice);
        scrollToBottom();
        return notice;
    }

    function _renderCompletedUI(session) {
        // Remove the "writing…" notice if still present
        const notice = document.getElementById('session-finalizing-notice');
        if (notice) notice.remove();

        if (session.report_url) {
            addMessage(
                `Your story has been written. <a href="${DOMPurify.sanitize(session.report_url)}" ` +
                `target="_blank" rel="noopener" class="report-link">Download PDF report</a>`,
                'system'
            );
        } else {
            // Report still generating — show a "checking…" message and poll
            const pollMsg = addMessage('Your story is ready. Checking for the PDF report…', 'system');
            _pollReport(session.id, pollMsg);
        }
    }

    function _pollReport(sessionId, placeholderEl) {
        let attempts = 0;
        const MAX_ATTEMPTS = 30; // 30 × 3 s = 90 s max poll

        _pollSessionTimer = setInterval(async () => {
            attempts++;
            if (attempts > MAX_ATTEMPTS) {
                _clearSessionPoll();
                const body = placeholderEl.querySelector('.message-content');
                if (body) body.textContent = 'PDF report is still being generated. Please check back later.';
                return;
            }

            try {
                const r = await fetch(`/api/sessions/${sessionId}/report`);
                if (r.status === 200) {
                    const data = await r.json();
                    _clearSessionPoll();
                    const body = placeholderEl.querySelector('.message-content');
                    if (body) {
                        body.innerHTML = `Your story is ready. <a href="${DOMPurify.sanitize(data.report_url)}" ` +
                            `target="_blank" rel="noopener" class="report-link">Download PDF report</a>`;
                    }
                }
                // 202 → keep polling
            } catch (_) { /* network hiccup — try again next tick */ }
        }, 3000);
    }

    function _handleSession(session) {
        if (!session) return;

        if (session.state === 'finalizing') {
            _renderFinalizingUI();
            // Poll GET /api/sessions/{id} every 2 s until completed
            _pollSessionTimer = setInterval(async () => {
                try {
                    const r = await fetch(`/api/sessions/${session.id}`);
                    if (!r.ok) return;
                    const updated = await r.json();
                    if (updated.state === 'completed') {
                        _clearSessionPoll();
                        _renderCompletedUI(updated);
                    } else if (updated.state === 'failed' || updated.state === 'abandoned') {
                        _clearSessionPoll();
                        const notice = document.getElementById('session-finalizing-notice');
                        if (notice) notice.remove();
                        addMessage('Story capture could not be completed. Please try again.', 'system');
                    }
                } catch (_) {}
            }, 2000);
        } else if (session.state === 'completed') {
            _renderCompletedUI(session);
        }
    }

    // -----------------------------------------------------------------------
    // §10.3 change 6: error handling — UPSTREAM_TIMEOUT shows a Retry button
    // -----------------------------------------------------------------------
    function _renderErrorWithRetry(errorMsg, retryText) {
        const wrapper = document.createElement('div');
        wrapper.className = 'message system';

        const avatar = document.createElement('div');
        avatar.className = 'message-avatar';
        avatar.innerHTML = BOT_AVATAR_SVG;
        wrapper.appendChild(avatar);

        const body = document.createElement('div');
        body.className = 'message-body';

        const content = document.createElement('div');
        content.className = 'message-content error-content';
        content.textContent = errorMsg;
        body.appendChild(content);

        const retryBtn = document.createElement('button');
        retryBtn.className = 'retry-btn';
        retryBtn.textContent = 'Retry';
        retryBtn.addEventListener('click', () => {
            wrapper.remove();
            sendMessage(retryText);
        });
        body.appendChild(retryBtn);

        wrapper.appendChild(body);
        chatMessages.appendChild(wrapper);
        scrollToBottom();
    }

    // -----------------------------------------------------------------------
    // Core send function — shared by form submit, option click, and autostart
    // -----------------------------------------------------------------------
    let lastAgent = null;
    // Track last sent text for the Retry button (UPSTREAM_TIMEOUT is safe to retry
    // because the session persists in awaiting_user — §10.3 change 6).
    let _lastSentText = '';

    async function sendMessage(text, optionId = null) {
        if (!text || !text.trim()) return;

        _lastSentText = text;

        userInput.value = '';
        userInput.disabled = true;
        typingIndicator.classList.remove('hidden');
        scrollToBottom();

        try {
            const body = {
                message: text,
                // §10.3 change 2: send agent_key; also send agent_name for one release
                // so a stale cached bundle still works (§11.1 note).
                agent_key: currentAgentKey,
                agent_name: currentAgentKey,   // backward-compat, remove in next release
            };
            if (optionId) body.option_id = optionId;
            // §10.3 change 1: attach conversation_id if we have one
            if (conversationId) body.conversation_id = conversationId;

            const response = await fetch('/api/chat', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(body),
            });

            const data = await response.json();
            typingIndicator.classList.add('hidden');

            // §10.3 change 1: persist conversation_id from every response
            if (data.conversation_id) {
                conversationId = data.conversation_id;
                sessionStorage.setItem('saarthi_cid', conversationId);
            }

            if (data.status === 'success') {
                if (lastAgent !== data.agent_name) {
                    addMessage(`Switched context to ${data.agent_name}`, 'context-switch');
                    lastAgent = data.agent_name;
                }

                const msgEl = addMessage(data.response, 'agent', data.agent_name);

                // §10.3 change 3: render option buttons when present
                if (data.options && data.options.length > 0) {
                    renderOptions(data.options, msgEl);
                }

                // §10.3 change 5: react to session state
                if (data.session) {
                    _handleSession(data.session);
                }
            } else {
                // §10.3 change 6: show retry on timeout; plain error otherwise
                if (data.error_code === 'UPSTREAM_TIMEOUT') {
                    _renderErrorWithRetry(
                        'The request timed out. Your session is still active.',
                        text
                    );
                } else {
                    addMessage(data.error || 'An error occurred.', 'system');
                }
            }
        } catch (error) {
            typingIndicator.classList.add('hidden');
            addMessage('Network error. Please try again.', 'system');
        } finally {
            userInput.disabled = false;
            userInput.focus();
        }
    }

    // -----------------------------------------------------------------------
    // Reset
    // -----------------------------------------------------------------------
    async function resetConversation() {
        _clearSessionPoll();

        try {
            await fetch('/api/reset', { method: 'POST' });
        } catch (error) {
            console.error('Failed to reset conversation:', error);
        }

        // §10.3 change 1: clear conversation identity on reset
        conversationId = null;
        sessionStorage.removeItem('saarthi_cid');

        chatMessages.innerHTML = '';
        addMessage('Namaste. How can I help you today?', 'system');
        lastAgent = null;
        currentAgentKey = null;

        if (window.innerWidth <= 768) {
            sidebar.classList.remove('active');
            sidebarOverlay.classList.remove('active');
        }
    }

    if (newChatBtn) {
        newChatBtn.addEventListener('click', resetConversation);
    }

    // Stamp the initial greeting's timestamp on load
    const initialTimeEl = chatMessages.querySelector('.message-time');
    if (initialTimeEl) {
        initialTimeEl.textContent = formatTime(new Date());
    }

    // -----------------------------------------------------------------------
    // Form submit
    // -----------------------------------------------------------------------
    chatForm.addEventListener('submit', async (e) => {
        e.preventDefault();
        const message = userInput.value.trim();
        if (!message) return;
        addMessage(message, 'user');
        sendMessage(message);
    });
});
