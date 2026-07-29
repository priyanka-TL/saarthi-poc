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
        <svg xmlns="http://www.w3.org/2000/svg" width="28" height="28" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" class="lucide lucide-bot">
            <path d="M12 8V4H8"/>
            <rect width="16" height="12" x="4" y="8" rx="2"/>
            <path d="M2 14h2"/>
            <path d="M20 14h2"/>
            <path d="M15 13v2"/>
            <path d="M9 13v2"/>
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

    if (mobileMenuBtn) mobileMenuBtn.addEventListener('click', toggleSidebar);
    if (mobileSidebarClose) mobileSidebarClose.addEventListener('click', toggleSidebar);
    if (sidebarOverlay) sidebarOverlay.addEventListener('click', toggleSidebar);

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

            resetConversation();
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
            resetConversation();
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
                    resetConversation();
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

                li.addEventListener('click', () => {
                    clearActiveItems();
                    li.classList.add('active');
                    loadConversationHistory(conv.id);

                    if (window.innerWidth <= 768) {
                        sidebar.classList.remove('active');
                        sidebarOverlay.classList.remove('active');
                    }
                });

                recentConversationsList.appendChild(li);
            });
        } catch (error) {
            console.error('Failed to load recent conversations:', error);
        }
    }

    loadRecentConversations();

    // A page reload with an active conversation still in sessionStorage should
    // restore its visible history, not silently fall back to the static
    // greeting -- this is what main.js:95-97's own comment already claimed.
    if (conversationId) {
        loadConversationHistory(conversationId);
    }

    // -----------------------------------------------------------------------
    // Message rendering
    // -----------------------------------------------------------------------
    function scrollToBottom() {
        chatMessages.scrollTop = chatMessages.scrollHeight;
    }

    function addMessage(content, type, agentName = null, messageId = null, timestamp = new Date()) {
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

            const textDiv = document.createElement('div');
            textDiv.className = 'message-text';

            if (type === 'agent') {
                textDiv.innerHTML = DOMPurify.sanitize(marked.parse(content));
            } else {
                textDiv.textContent = content;
            }
            contentDiv.appendChild(textDiv);

            const meta = document.createElement('div');
            meta.className = 'message-meta';
            meta.textContent = `${formatTime(timestamp)} · ${agentName || 'Home'}`;
            contentDiv.appendChild(meta);

            body.appendChild(contentDiv);
            messageDiv.appendChild(body);
        } else if (type === 'user') {
            const body = document.createElement('div');
            body.className = 'message-body';

            const contentDiv = document.createElement('div');
            contentDiv.className = 'message-content';

            const textDiv = document.createElement('div');
            textDiv.className = 'message-text';
            textDiv.textContent = content;
            contentDiv.appendChild(textDiv);

            const meta = document.createElement('div');
            meta.className = 'message-meta';
            meta.textContent = formatTime(timestamp);
            contentDiv.appendChild(meta);

            body.appendChild(contentDiv);
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
    function renderOptions(options, messageDiv, { readOnly = false, selectedId = null } = {}) {
        if (!options || options.length === 0) return;

        const group = document.createElement('div');
        group.className = 'message-options';

        options.forEach(opt => {
            const btn = document.createElement('button');
            btn.className = 'option-btn';
            btn.dataset.id = opt.id;
            btn.dataset.value = opt.value;
            btn.textContent = opt.label;

            if (readOnly) {
                // Historical replay: already answered (or the conversation moved
                // on) -- show the group as decided, never live. No listener at
                // all, so a replayed option group can't re-fire sendMessage.
                btn.disabled = true;
                btn.classList.add('option-btn--used');
                if (opt.id === selectedId) btn.classList.add('option-btn--selected');
            } else {
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
            }

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
    // Resume a conversation -- replays its full history into the chat pane.
    // Used both for a sidebar recent-conversation click and for restoring
    // sessionStorage.saarthi_cid on page reload (main.js:95-97's own comment
    // already claimed reload-restore worked; this is what makes that true).
    // -----------------------------------------------------------------------
    async function loadConversationHistory(id) {
        try {
            const response = await fetch(`/api/conversations/${id}/messages`);
            if (!response.ok) return;
            const data = await response.json();

            chatMessages.innerHTML = '';

            let lastAgentName = null;
            data.messages.forEach(m => {
                const type = m.role === 'assistant' ? 'agent' : 'user';
                if (m.role === 'assistant') lastAgentName = m.agent_name;

                const messageDiv = addMessage(m.content, type, m.agent_name, m.id, new Date(m.created_at));

                if (m.role === 'assistant' && m.options && m.options.length) {
                    renderOptions(m.options, messageDiv, { readOnly: true, selectedId: m.selected_option_id });
                }
            });

            // Deliberately does NOT set currentAgentKey -- the backend's own
            // session-pin state already drives routing for the next real
            // message correctly; duplicating that decision here would just be
            // a second, driftable copy of server-side truth.
            conversationId = id;
            sessionStorage.setItem('saarthi_cid', id);

            if (lastAgentName) {
                setContextBanner(lastAgentName, 'Resumed conversation');
            }

            // Replay the session state the transcript cannot carry. The
            // completion notice is generated, not stored, so without this a
            // reload silently dropped the report link on a finished story.
            // Passing lastAgentName explicitly rather than leaning on the
            // module-level `lastAgent`, which is still null on a fresh load.
            _handleSession(data.session, lastAgentName);

            scrollToBottom();
        } catch (error) {
            console.error('Failed to load conversation history:', error);
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

    // addMessage() renders 'system' messages with textContent, NOT innerHTML —
    // that is the XSS guard for server-supplied strings and must stay. Passing
    // it an <a> tag therefore printed the raw markup into the bubble instead of
    // a link. Build the anchor as a DOM node instead, so the escaping rule is
    // respected rather than worked around.
    //
    // Shape follows the WhatsApp client (storyPostSessionService.js:313-334):
    // a confirmation line, then the download offered as its own distinct
    // action — not a hyperlink buried mid-sentence.
    function _appendReportAction(messageEl, url) {
        const textDiv = messageEl.querySelector('.message-text');
        if (!textDiv) return;

        // The server already allowlists this URL (MitraRestClient._validate_url),
        // but this is the one place it becomes a clickable href, so re-check the
        // scheme here rather than trusting the response shape.
        const safeUrl = DOMPurify.sanitize(url || '');
        if (!/^https:\/\//i.test(safeUrl)) return;

        const link = document.createElement('a');
        link.className = 'report-link';
        link.href = safeUrl;
        link.target = '_blank';
        link.rel = 'noopener';
        link.textContent = '⬇  Download PDF report';
        textDiv.appendChild(link);
    }

    function _renderCompletedUI(session, agentName = null) {
        // Remove the "writing…" notice if still present
        const notice = document.getElementById('session-finalizing-notice');
        if (notice) notice.remove();

        // lastAgent, so the completion bubble is attributed to the interview
        // agent like every bubble above it. Passing nothing made addMessage
        // fall back to 'Home', which reads as a different speaker.
        const attribution = agentName || lastAgent;
        if (session.report_url) {
            const msg = addMessage('✅ Your story is ready.', 'system', attribution);
            _appendReportAction(msg, session.report_url);
        } else {
            // Report still generating — show a "checking…" message and poll
            const pollMsg = addMessage(
                'Your story is ready. Checking for the PDF report…', 'system', attribution,
            );
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
                // .message-text, not .message-content — the latter also holds
                // the timestamp/agent line, which writing to it wipes out.
                const body = placeholderEl.querySelector('.message-text');
                if (body) body.textContent = 'PDF report is still being generated. Please check back later.';
                return;
            }

            try {
                const r = await fetch(`/api/sessions/${sessionId}/report`);
                if (r.status === 200) {
                    const data = await r.json();
                    _clearSessionPoll();
                    const body = placeholderEl.querySelector('.message-text');
                    if (body) {
                        body.textContent = '✅ Your story is ready.';
                        _appendReportAction(placeholderEl, data.report_url);
                    }
                }
                // 202 → keep polling
            } catch (_) { /* network hiccup — try again next tick */ }
        }, 3000);
    }

    function _handleSession(session, agentName = null) {
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
                        _renderCompletedUI(updated, agentName);
                    } else if (updated.state === 'failed' || updated.state === 'abandoned') {
                        _clearSessionPoll();
                        const notice = document.getElementById('session-finalizing-notice');
                        if (notice) notice.remove();
                        addMessage('Story capture could not be completed. Please try again.', 'system');
                    }
                } catch (_) { }
            }, 2000);
        } else if (session.state === 'completed') {
            _renderCompletedUI(session, agentName);
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

        // RE-SENDING IS ONLY SAFE WITHOUT A REMOTE SESSION.
        //
        // An LLM agent holds no server-side conversation state, so re-posting
        // the same text is harmless. A remote_flow interview is the opposite:
        // Mitra may already have recorded the answer and moved to the next
        // question, so a re-send lands against the WRONG question and destroys
        // the real answer (§1.6). That is what happened live -- Mitra had
        // replied in 8.4s and only Saarthi stopped listening.
        //
        // With a session, ask the server what actually happened instead.
        if (_lastSessionId) {
            retryBtn.textContent = 'Check for reply';
            retryBtn.addEventListener('click', () => {
                _resumeSession(_lastSessionId, retryBtn, wrapper, retryText);
            });
        } else {
            retryBtn.textContent = 'Retry';
            retryBtn.addEventListener('click', () => {
                wrapper.remove();
                sendMessage(retryText);
            });
        }
        body.appendChild(retryBtn);

        wrapper.appendChild(body);
        chatMessages.appendChild(wrapper);
        scrollToBottom();
    }

    // Recover a turn the server stopped listening for. Read-only against
    // Mitra: it never re-submits the user's answer. Only the explicit
    // can_resend outcome -- Mitra has no record of the message -- allows that.
    async function _resumeSession(sessionId, btn, wrapper, retryText, attempt = 0) {
        const MAX_ATTEMPTS = 10;
        btn.disabled = true;
        btn.textContent = 'Checking…';

        try {
            const res = await fetch(`/api/sessions/${sessionId}/resume`, { method: 'POST' });
            const data = await res.json();

            if (res.status === 200 && data.outcome === 'answered') {
                wrapper.remove();
                addMessage(data.response, 'agent', lastAgent);
                if (data.session) _handleSession(data.session, lastAgent);
                return;
            }

            if (res.status === 202 && attempt < MAX_ATTEMPTS) {
                // Mitra is still generating -- keep waiting, never re-send.
                setTimeout(
                    () => _resumeSession(sessionId, btn, wrapper, retryText, attempt + 1),
                    (data.retry_after || 3) * 1000,
                );
                return;
            }

            if (data.can_resend) {
                // Mitra has no record of the message, so re-sending is safe.
                wrapper.remove();
                sendMessage(retryText);
                return;
            }

            btn.disabled = false;
            btn.textContent = 'Check again';
        } catch (error) {
            console.error('Failed to resume session:', error);
            btn.disabled = false;
            btn.textContent = 'Check again';
        }
    }

    // -----------------------------------------------------------------------
    // Core send function — shared by form submit, option click, and autostart
    // -----------------------------------------------------------------------
    let lastAgent = null;
    // Track last sent text for the Retry button (UPSTREAM_TIMEOUT is safe to retry
    // because the session persists in awaiting_user — §10.3 change 6).
    let _lastSentText = '';
    // Id of the remote_flow session in play, if any. Its PRESENCE is what makes
    // a blind re-send unsafe -- see _renderErrorWithRetry.
    let _lastSessionId = null;

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
                    // Remembered so a later timeout knows a remote interview is
                    // in play and must never be recovered by re-sending.
                    _lastSessionId = data.session.id;
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

        // Adopt the conversation the server just created. Clearing the id and
        // letting the next turn resolve "most recent active" was only safe
        // while reset archived the old conversation; it no longer does (that
        // archiving is what hid finished chats from the sidebar), so the new
        // id has to be explicit or the next message reopens the old thread.
        let newConversationId = null;
        try {
            const res = await fetch('/api/reset', { method: 'POST' });
            if (res.ok) {
                newConversationId = (await res.json()).conversation_id || null;
            }
        } catch (error) {
            console.error('Failed to reset conversation:', error);
        }

        conversationId = newConversationId;
        if (newConversationId) {
            sessionStorage.setItem('saarthi_cid', newConversationId);
        } else {
            sessionStorage.removeItem('saarthi_cid');
        }

        // The chat just moved to a new conversation, so the previous one is now
        // history -- refresh the sidebar instead of waiting for a page reload.
        loadRecentConversations();

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
    // Form submit & Textarea auto-resize
    // -----------------------------------------------------------------------
    userInput.addEventListener('input', function() {
        this.style.height = 'auto';
        this.style.height = (this.scrollHeight) + 'px';
    });

    userInput.addEventListener('keydown', function(e) {
        if (e.key === 'Enter' && !e.shiftKey) {
            e.preventDefault();
            chatForm.dispatchEvent(new Event('submit', { cancelable: true, bubbles: true }));
        }
    });

    chatForm.addEventListener('submit', async (e) => {
        e.preventDefault();
        const message = userInput.value.trim();
        if (!message) return;
        addMessage(message, 'user');
        sendMessage(message);
        
        userInput.style.height = 'auto'; // Reset height after send
    });
});
