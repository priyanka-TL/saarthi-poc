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

    if (themeToggle) {
        themeToggle.addEventListener('click', () => {
            if (root.getAttribute('data-theme') === 'dark') {
                root.removeAttribute('data-theme');
                localStorage.setItem('theme', 'light');
            } else {
                root.setAttribute('data-theme', 'dark');
                localStorage.setItem('theme', 'dark');
            }
        });
    }

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

    // -----------------------------------------------------------------------
    // Per-conversation mutable state.
    //
    // Declared HERE, not next to sendMessage() 700 lines down, because the
    // page-load restore below calls loadConversationHistory() synchronously and
    // that function now resets these. A `let` further down the same scope would
    // put them in the temporal dead zone at that point -- a ReferenceError that
    // only fires on the reload path, i.e. exactly the path least likely to be
    // exercised by hand.
    // -----------------------------------------------------------------------
    let lastAgent = null;
    // Last sent text, for the Retry button (UPSTREAM_TIMEOUT is safe to retry
    // because the session persists in awaiting_user — §10.3 change 6).
    let _lastSentText = '';
    // Id of the remote_flow session in play, if any. Its PRESENCE is what makes
    // a blind re-send unsafe -- see _renderErrorWithRetry. It belongs to ONE
    // conversation and must be cleared by every path that switches conversation.
    let _lastSessionId = null;
    // Key of the agent that owns the session in play, so completion copy is
    // chosen from the agent key rather than its display name (which has already
    // been renamed once).
    let _lastSessionAgentKey = null;
    // A turn is in flight. Guards EVERY entry point into sendMessage -- typed
    // submit, option click, autostart -- plus the handlers that switch or reset
    // the conversation underneath one. userInput.disabled only ever covered the
    // typed path.
    let _busy = false;

    function setContextBanner(label, subLabel, stops = null) {
        const banner = document.getElementById('active-context-banner');
        const bannerTop = document.getElementById('context-banner-top');
        if (!banner || !bannerTop) return;

        banner.classList.remove('hidden');

        if (stops && stops.length > 0) {
            let html = '';
            stops.forEach((stop, index) => {
                const isActive = index === stops.length - 1;
                html += `<span class="breadcrumb-item ${isActive ? 'active' : ''}">${stop}</span>`;
                if (!isActive) {
                    html += `<span class="breadcrumb-separator">
                               <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                                 <polyline points="9 18 15 12 9 6"></polyline>
                               </svg>
                             </span>`;
                }
            });
            bannerTop.innerHTML = html;
        } else {
            const displayLabel = label === 'Home' ? label : label + ' Context';
            const displaySubLabel = subLabel || label;
            bannerTop.innerHTML = `
                <span class="context-pill primary">Context: <span id="context-name">${displayLabel}</span></span>
                <span class="context-pill secondary">Sub-context: <span id="sub-context-name">${displaySubLabel}</span></span>
            `;
        }
    }

    function clearActiveItems() {
        document.querySelectorAll('.agent-item, .capability-card, .highlight-card').forEach(el =>
            el.classList.remove('active')
        );
    }

    // -----------------------------------------------------------------------
    // §10.3 change 4: wire the dead "Record Stories" button.
    //   The old card handler read .capability-title text ("Listening at Scale")
    //   and set currentSelectedAgent = "Listening at Scale". That matched no
    //   agent → /api/chat returned 404.
    //
    //   Fix: any element with data-agent-key gets a dedicated listener that
    //   stopPropagation()s so the generic card handler below never fires.
    //   Routing comes from data-agent-key, not from display text.
    // -----------------------------------------------------------------------
    document.querySelectorAll('[data-agent-key]').forEach(el => {
        el.addEventListener('click', async (e) => {
            e.stopPropagation();   // prevent generic card handler from winning

            // RE-ENTRANCY GUARD. This handler is async and nothing stopped it
            // running twice: a double-click fired two resetConversation()s, so
            // two conversations were created and the first handler's autostart
            // was posted into the conversation the second reset had already
            // replaced. `_busy` also covers the window while the autostart turn
            // itself is in flight.
            if (_busy || el.dataset.pending === '1') return;
            el.dataset.pending = '1';

            try {
                // AWAIT. resetConversation() only assigns the new conversationId
                // AFTER its `await fetch('/api/reset')` resolves. Called without
                // await, this handler ran straight past it and sendMessage() below
                // posted the autostart message with the PREVIOUS conversation id --
                // so opening this panel wrote its first turn into whatever
                // conversation was already open, including a completed story.
                // Awaiting also orders the reset's own `currentAgentKey = null`
                // BEFORE the assignment below, instead of clobbering it after.
                await resetConversation();
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
                // Flagged as autostart so the server neither titles the
                // conversation from it nor stores it as the user's own words --
                // it is a UI affordance, not something the user said.
                if (el.dataset.autostart) {
                    await sendMessage(el.dataset.autostart, null, { autostart: true });
                }
            } finally {
                delete el.dataset.pending;
            }
        });
    });

    // Generic card handler for cards WITHOUT a dedicated data-agent-key listener.
    // §10.3: this handler must NEVER drive routing — that's the bug we fixed above.
    document.querySelectorAll('.capability-card:not([data-agent-key]), .highlight-card').forEach(card => {
        card.addEventListener('click', async () => {
            if (_busy) return;
            // Awaited for the same reason as above: reset clears the message
            // pane and the agent key only after its fetch resolves, so an
            // un-awaited call lands those side effects on whatever the user
            // does next.
            await resetConversation();
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
    // Agents already surfaced through the capability-card buttons in the
    // "Listening at Scale" group, plus agents that should never appear as
    // standalone sidebar entries (the synthetic Saarthi router and the hidden
    // fallback General Support Agent).  New direct messages always go to the
    // orchestrator (currentAgentKey === null), so Saarthi needs no list entry.
    const SIDEBAR_HIDDEN_KEYS = new Set([
        'record_stories',       // shown under Listening at Scale card
        'capture_discussion',   // shown under Listening at Scale card
        'general_support',      // fallback-only; not user-selectable
    ]);

    async function loadAgents() {
        try {
            const response = await fetch('/api/agents');
            const agents = await response.json();

            agentList.innerHTML = '';

            agents.forEach(agent => {
                // Skip the synthetic Saarthi router entry (no key) and any
                // agent already represented by a capability-card button.
                if (!agent.key || SIDEBAR_HIDDEN_KEYS.has(agent.key)) return;

                const li = document.createElement('li');
                li.className = 'agent-item';
                // §10.3 change 2: store agent.key on the element, not agent.name
                li.dataset.key = agent.key;
                li.innerHTML = `
                    <div class="agent-item-title">${AGENT_ICON_SVG}<span class="agent-name">${agent.name}</span></div>
                    <div class="agent-desc">${agent.description}</div>
                `;

                li.addEventListener('click', async () => {
                    if (_busy) return;
                    // Awaited: without it, resetConversation()'s late
                    // `currentAgentKey = null` overwrote the assignment below,
                    // so the next message routed as "route me" instead of the
                    // agent the user had just picked.
                    await resetConversation();
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
            // 20 is the server's own cap (chat_routes.list_conversations). At
            // the previous 5, all but the newest handful of a user's
            // conversations were simply unreachable from the UI -- there is no
            // other way in. Paging past 20 needs a "Load more" affordance and
            // the keyset cursor ConversationRepository already supports.
            const response = await fetch('/api/conversations?limit=20');
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
                    // Switching conversations mid-turn would leave the in-flight
                    // reply to render into the wrong transcript.
                    if (_busy) return;
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

            // The list is rebuilt from scratch on every load, so the highlight
            // has to be reapplied. Previously only a click ever set it, which
            // meant a restored-on-reload conversation was open in the pane with
            // nothing in the sidebar showing which one it was.
            _highlightActiveConversation();
        } catch (error) {
            console.error('Failed to load recent conversations:', error);
        }
    }

    // Marks the sidebar row for `conversationId`, if it is currently listed.
    // Safe to call before the list has loaded -- it simply finds nothing, and
    // loadRecentConversations calls it again once the rows exist.
    function _highlightActiveConversation() {
        if (!recentConversationsList) return;
        recentConversationsList.querySelectorAll('.agent-item').forEach(el => {
            el.classList.toggle('active', !!conversationId && el.dataset.id === conversationId);
        });
    }

    // Drop a conversation id that the server no longer recognises, and put the
    // pane back to the Home greeting. Leaving a dead id in sessionStorage meant
    // the next message carried it to /api/chat, which created a brand-new
    // conversation using that caller-supplied primary key.
    function _forgetConversation() {
        conversationId = null;
        sessionStorage.removeItem('saarthi_cid');
        chatMessages.innerHTML = '';
        addMessage('Namaste. How can I help you today?', 'system');
        setContextBanner('Home', '–');
        clearActiveItems();
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
    // Retire EVERY choice group in the pane. Called before each send, because
    // renderOptions only ever disabled the group whose button was clicked: a
    // question answered by typing instead left its buttons live indefinitely,
    // and clicking one of them later submitted that stale answer against
    // whatever step the interview had since moved to -- the same §1.6 answer
    // destruction the option group's own click handler guards against.
    function _disableAllOptions() {
        document.querySelectorAll('#chat-messages .option-btn').forEach(b => {
            b.disabled = true;
            b.classList.add('option-btn--used');
        });
    }

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
                    // A turn already in flight: do nothing at all, and in
                    // particular do NOT echo the label, or the transcript would
                    // show an answer that was never sent.
                    if (_busy) return;
                    // Disable every group immediately — before the POST — so a
                    // slow network can't allow a double-click to go through.
                    // sendMessage does this too; doing it here as well closes
                    // the window between the click and sendMessage's own guard.
                    _disableAllOptions();

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
        // Switching conversations is exactly the same state boundary that
        // resetConversation() guards, and everything it clears has to be
        // cleared here too. Left behind, `_lastSessionId` pointed at the
        // PREVIOUS conversation's interview, so a timeout in this one offered
        // "Check for reply" and POST /api/sessions/{other_id}/resume happily
        // wrote a recovered turn into the conversation the user had left.
        _clearSessionPoll();
        _lastSessionId = null;
        _lastSessionAgentKey = null;
        lastAgent = null;
        // Resuming means "let the server route me". The old comment below said
        // this already, but leaving a stale value in place meant a resumed
        // Record Stories conversation could be posted to with
        // agent_key=capture_discussion -- see SessionService.open_for.
        currentAgentKey = null;

        try {
            const response = await fetch(`/api/conversations/${id}/messages`);
            if (!response.ok) {
                // A 404 means the stored id is dead (different login, wiped
                // database, someone else's conversation). Returning silently
                // left it in sessionStorage, so the next message re-sent it and
                // the server materialised a NEW conversation under that
                // caller-supplied id. Forget it and fall back to Home.
                if (response.status === 404) _forgetConversation();
                return;
            }
            const data = await response.json();

            chatMessages.innerHTML = '';

            let lastAgentName = null;
            // Last rendered element per session, so a session's completion
            // notice can be anchored to ITS point in the timeline rather than
            // appended after everything -- otherwise, in a conversation that
            // later moved to another agent, a finished story's report link
            // would appear below that other agent's messages.
            const lastElementBySession = new Map();
            const agentNameBySession = new Map();

            data.messages.forEach(m => {
                const type = m.role === 'assistant' ? 'agent' : 'user';
                if (m.role === 'assistant') lastAgentName = m.agent_name;

                const messageDiv = addMessage(m.content, type, m.agent_name, m.id, new Date(m.created_at));

                if (m.agent_session_id) {
                    lastElementBySession.set(m.agent_session_id, messageDiv);
                    if (m.agent_name) agentNameBySession.set(m.agent_session_id, m.agent_name);
                }

                if (m.role === 'assistant' && m.options && m.options.length) {
                    renderOptions(m.options, messageDiv, { readOnly: true, selectedId: m.selected_option_id });
                }
            });

            // currentAgentKey stays null (cleared above) -- the backend's own
            // session-pin state already drives routing for the next real
            // message correctly; duplicating that decision here would just be
            // a second, driftable copy of server-side truth.
            conversationId = id;
            sessionStorage.setItem('saarthi_cid', id);
            _highlightActiveConversation();

            // Restore the speaker the transcript ended on. Without this the
            // next reply always looked like a switch, so every resumed
            // conversation opened with a spurious "Switched context to Capture
            // Discussions" pill.
            lastAgent = lastAgentName;

            if (lastAgentName) {
                setContextBanner(lastAgentName, 'Resumed conversation');
            } else {
                setContextBanner('Home', '–');
            }

            // Replay the session state the transcript cannot carry. The
            // completion notice is generated, not stored, so without this a
            // reload silently dropped the report link on a finished story.
            // Agent names come from the messages rather than the module-level
            // `lastAgent`, which is still null on a fresh load.
            const sessions = data.sessions || [];

            // EVERY finished session gets its completion notice back, not just
            // the most recent one. A conversation that moved on to another
            // agent used to hide the completed story's Download PDF button.
            //
            // And NOT only the ones that already have a report_url. Finalising
            // deliberately completes with report_url = null when Mitra's PDF
            // generation lags -- OrchestrationService._finalize says so in as
            // many words -- so requiring one here meant that refreshing in the
            // gap between "interview finished" and "PDF ready" showed no
            // completion notice, no download, no polling and no error. The user
            // was told nothing at all, while GET /api/sessions/{id}/report
            // would have served the report moments later.
            // _renderCompletedUI already handles the null case by polling.
            sessions.forEach(s => {
                if (s.state === 'completed') {
                    _renderCompletedUI(
                        s,
                        agentNameBySession.get(s.id) || lastAgentName,
                        lastElementBySession.get(s.id) || null,
                    );
                }
            });

            // Only the session still in flight can be finalizing, and only one
            // can be: polling is driven off the newest.
            const latest = sessions[sessions.length - 1];
            if (latest && latest.state === 'finalizing') {
                _lastSessionId = latest.id;
                _lastSessionAgentKey = latest.agent_key || null;
                _handleSession(latest, agentNameBySession.get(latest.id) || lastAgentName);
            } else if (latest && latest.state !== 'completed') {
                // An interview mid-flight: remember it so a timeout recovers
                // via /resume instead of re-sending (§1.6).
                _lastSessionId = latest.id;
                _lastSessionAgentKey = latest.agent_key || null;
            }

            scrollToBottom();
        } catch (error) {
            console.error('Failed to load conversation history:', error);
        }
    }

    // -----------------------------------------------------------------------
    // §10.3 change 5: session state UI — finalizing / completed / report
    // -----------------------------------------------------------------------
    // A SET, not a single handle. _handleSession and _pollReport both used to
    // assign to one shared variable, so whichever started last was the only one
    // that could ever be cleared -- and a conversation with more than one
    // session still awaiting its PDF leaked an interval per session, each
    // writing into a DOM node that had already been discarded.
    const _pollSessionTimers = new Set();

    function _trackPoll(timerId) {
        _pollSessionTimers.add(timerId);
        return timerId;
    }

    function _clearSessionPoll() {
        _pollSessionTimers.forEach(clearInterval);
        _pollSessionTimers.clear();
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

    function _renderCompletedUI(session, agentName = null, anchorEl = null) {
        // Remove the "writing…" notice if still present
        const notice = document.getElementById('session-finalizing-notice');
        if (notice) notice.remove();

        // lastAgent, so the completion bubble is attributed to the interview
        // agent like every bubble above it. Passing nothing made addMessage
        // fall back to 'Home', which reads as a different speaker.
        const attribution = agentName || lastAgent;
        let readyText = '✅ Your story is ready.';
        let checkingText = 'Your story is ready. Checking for the PDF report…';

        // agent_key, NOT the display name. This branched on the literal string
        // 'Capture Discussions', and this agent has already been renamed once
        // ("Capture Discussion" -> "Capture Discussions"); the next rename would
        // have silently reverted discussions to "Your story is ready." with no
        // test to catch it. Both payloads that carry a session carry agent_key.
        const agentKey = session.agent_key || _lastSessionAgentKey;
        if (agentKey === 'capture_discussion') {
            readyText = '✅ Your discussion report is ready.';
            checkingText = 'Your discussion report is ready. Checking for the PDF report…';
        }

        if (session.report_url) {
            const msg = addMessage(readyText, 'system', attribution);
            // anchorEl places the notice where the session actually ended,
            // instead of at the bottom of a conversation that has since moved
            // on to another agent. addMessage() appends, so this relocates it.
            if (anchorEl) anchorEl.after(msg);
            _appendReportAction(msg, session.report_url);
        } else {
            // Report still generating — show a "checking…" message and poll.
            // anchorEl applies here too: on a history replay this branch is now
            // reachable for a session that finished earlier in a conversation
            // which has since moved on, and an unanchored notice would land
            // under a different agent's messages.
            const pollMsg = addMessage(checkingText, 'system', attribution);
            if (anchorEl) anchorEl.after(pollMsg);
            _pollReport(session.id, pollMsg, readyText);
        }
    }

    function _pollReport(sessionId, placeholderEl, readyText = '✅ Your story is ready.') {
        let attempts = 0;
        const MAX_ATTEMPTS = 30; // 30 × 3 s = 90 s max poll

        // Stops ONLY this poll. A conversation can hold several completed
        // sessions still waiting on their PDF, and _clearSessionPoll() would
        // cancel every one of them the moment the first report landed.
        const stop = () => {
            clearInterval(timer);
            _pollSessionTimers.delete(timer);
        };

        const check = async () => {
            attempts++;
            if (attempts > MAX_ATTEMPTS) {
                stop();
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
                    stop();
                    const body = placeholderEl.querySelector('.message-text');
                    if (body) {
                        body.textContent = readyText;
                        _appendReportAction(placeholderEl, data.report_url);
                    }
                }
                // 202 → keep polling
            } catch (_) { /* network hiccup — try again next tick */ }
        };

        const timer = _trackPoll(setInterval(check, 3000));
        // Check IMMEDIATELY as well. On a history replay the report is usually
        // long since generated and only `report_url` on the session row is
        // stale, so waiting a full tick to say so is three seconds of "checking
        // for the PDF report…" for a file that is already there.
        check();
    }

    function _handleSession(session, agentName = null) {
        if (!session) return;

        if (session.state === 'finalizing') {
            _renderFinalizingUI();
            const stop = () => {
                clearInterval(timer);
                _pollSessionTimers.delete(timer);
            };
            // Poll GET /api/sessions/{id} every 2 s until completed
            const timer = _trackPoll(setInterval(async () => {
                try {
                    const r = await fetch(`/api/sessions/${session.id}`);
                    if (!r.ok) return;
                    const updated = await r.json();
                    if (updated.state === 'completed') {
                        stop();
                        _renderCompletedUI(updated, agentName);
                    } else if (updated.state === 'failed' || updated.state === 'abandoned') {
                        stop();
                        const notice = document.getElementById('session-finalizing-notice');
                        if (notice) notice.remove();
                        addMessage(
                            (session.agent_key || _lastSessionAgentKey) === 'capture_discussion'
                                ? 'Capturing this discussion could not be completed. Please try again.'
                                : 'Story capture could not be completed. Please try again.',
                            'system',
                        );
                    }
                } catch (_) { }
            }, 2000));
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
    async function sendMessage(text, optionId = null, { autostart = false } = {}) {
        if (!text || !text.trim()) return;
        // One turn at a time. Two user messages in a row silently collapse in
        // Mitra's DB (§1.6), so a double-submit destroys an answer -- and a
        // first turn sent twice creates a second, orphaned Mitra session.
        if (_busy) return;
        _busy = true;

        _lastSentText = text;

        // Every option group in the pane, not just the one that was clicked.
        // A question answered by TYPING left its buttons live forever, so
        // scrolling up and clicking one later posted that answer against
        // whatever step the interview had since reached.
        _disableAllOptions();

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
            // The opening message a capability button sends on the user's
            // behalf. Flagged so the server neither titles the conversation
            // from it ("I want to capture a discussion" was the title of EVERY
            // discussion in the sidebar) nor treats it as the user's own text.
            if (autostart) body.autostart = true;
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
                if (data.agent_key) {
                    currentAgentKey = data.agent_key;
                }

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
                    _lastSessionAgentKey = data.session.agent_key || null;
                    _handleSession(data.session);
                }
                // Sidebar titles/timestamps change with every turn, and the
                // first turn of a brand-new conversation adds a row.
                loadRecentConversations();

                // Update Context Banner with breadcrumbs
                const stops = data.flow ? data.flow.stops : null;
                setContextBanner(data.agent_name, data.agent_name, stops);
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
            _busy = false;
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

        setContextBanner('Home', '–');
        clearActiveItems();
        // Belongs to the conversation we just left. Kept, a timeout in the NEW
        // conversation would POST /api/sessions/{old_id}/resume and recover a
        // turn from the previous interview.
        _lastSessionId = null;
        _lastSessionAgentKey = null;

        if (window.innerWidth <= 768) {
            sidebar.classList.remove('active');
            sidebarOverlay.classList.remove('active');
        }
    }

    if (newChatBtn) {
        newChatBtn.addEventListener('click', () => {
            // Resetting under an in-flight turn would strand its reply: the
            // response handler would render it into the fresh conversation and
            // persist its session id as the new one's.
            if (_busy) return;
            resetConversation();
        });
    }

    // Stamp the initial greeting's timestamp on load
    const initialTimeEl = chatMessages.querySelector('.message-time');
    if (initialTimeEl) {
        initialTimeEl.textContent = formatTime(new Date());
    }

    // -----------------------------------------------------------------------
    // Form submit & Textarea auto-resize
    // -----------------------------------------------------------------------
    userInput.addEventListener('input', function () {
        this.style.height = 'auto';
        this.style.height = (this.scrollHeight) + 'px';
    });

    userInput.addEventListener('keydown', function (e) {
        if (e.key === 'Enter' && !e.shiftKey) {
            e.preventDefault();
            chatForm.dispatchEvent(new Event('submit', { cancelable: true, bubbles: true }));
        }
    });

    chatForm.addEventListener('submit', async (e) => {
        e.preventDefault();
        const message = userInput.value.trim();
        if (!message) return;
        // Echo only if the send is actually going to happen -- otherwise a
        // submit during an in-flight turn left an unanswered user bubble in the
        // transcript that was never sent anywhere.
        if (_busy) return;
        addMessage(message, 'user');
        sendMessage(message);

        userInput.style.height = 'auto'; // Reset height after send
    });
});
