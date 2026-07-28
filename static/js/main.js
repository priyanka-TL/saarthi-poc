document.addEventListener('DOMContentLoaded', () => {
    const chatForm = document.getElementById('chat-form');
    const userInput = document.getElementById('user-input');
    const chatMessages = document.getElementById('chat-messages');
    const typingIndicator = document.getElementById('typing-indicator');
    const agentList = document.getElementById('agent-list');
    const themeToggle = document.getElementById('theme-toggle');
    const mobileMenuBtn = document.getElementById('mobile-menu-btn');
    const mobileSidebarClose = document.getElementById('mobile-sidebar-close');
    const sidebar = document.getElementById('sidebar');
    const sidebarOverlay = document.getElementById('sidebar-overlay');
    const newChatBtn = document.getElementById('new-chat-btn');
    const flowBar = document.getElementById('flow-bar');
    const flowTitleEl = document.getElementById('flow-title');
    const flowStopLabel = document.getElementById('flow-stop-label');
    const flowSteps = document.getElementById('flow-steps');
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

    // Load saved theme (light is the default Saarthi look)
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

    // Mobile Sidebar Toggle
    function toggleSidebar() {
        sidebar.classList.toggle('active');
        sidebarOverlay.classList.toggle('active');
    }

    if (mobileMenuBtn) {
        mobileMenuBtn.addEventListener('click', toggleSidebar);
    }

    if (mobileSidebarClose) {
        mobileSidebarClose.addEventListener('click', toggleSidebar);
    }

    if (sidebarOverlay) {
        sidebarOverlay.addEventListener('click', toggleSidebar);
    }

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

    let currentSelectedAgent = 'Saarthi'; // Default to orchestrator

    function clearActiveItems() {
        document.querySelectorAll('.agent-item, .capability-card, .highlight-card').forEach(el => el.classList.remove('active'));
    }

    document.querySelectorAll('.capability-card, .highlight-card').forEach(card => {
        card.addEventListener('click', () => {
            clearActiveItems();
            card.classList.add('active');

            const titleEl = card.querySelector('.capability-title') || card.querySelector('.highlight-title');
            if (titleEl) {
                const name = titleEl.textContent;
                currentSelectedAgent = name;
                
                const banner = document.getElementById('active-context-banner');
                const contextNameEl = document.getElementById('context-name');
                const subContextNameEl = document.getElementById('sub-context-name');
                if (banner && contextNameEl && subContextNameEl) {
                    contextNameEl.textContent = name + " Context";
                    subContextNameEl.textContent = name === 'Listening at Scale' ? 'Story capture' : name;
                    banner.classList.remove('hidden');
                }
            }

            if (window.innerWidth <= 768) {
                sidebar.classList.remove('active');
                sidebarOverlay.classList.remove('active');
            }
        });
    });

    async function loadAgents() {
        try {
            const response = await fetch('/api/agents');
            const agents = await response.json();

            agentList.innerHTML = '';

            agents.forEach(agent => {
                const li = document.createElement('li');
                li.className = `agent-item ${agent.name === currentSelectedAgent ? 'active' : ''}`;
                li.innerHTML = `
                    <div class="agent-item-title">${AGENT_ICON_SVG}<span class="agent-name">${agent.name}</span></div>
                    <div class="agent-desc">${agent.description}</div>
                `;

                li.addEventListener('click', () => {
                    clearActiveItems();
                    li.classList.add('active');
                    currentSelectedAgent = agent.name;
                    
                    const banner = document.getElementById('active-context-banner');
                    const contextNameEl = document.getElementById('context-name');
                    const subContextNameEl = document.getElementById('sub-context-name');
                    if (banner && contextNameEl && subContextNameEl) {
                        contextNameEl.textContent = agent.name + " Context";
                        subContextNameEl.textContent = "Agent interaction";
                        banner.classList.remove('hidden');
                    }

                    // Close sidebar on mobile after selecting an agent
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

    // Load agents on startup
    loadAgents();

    function scrollToBottom() {
        chatMessages.scrollTop = chatMessages.scrollHeight;
    }

    function addMessage(content, type, agentName = null) {
        const messageDiv = document.createElement('div');
        messageDiv.className = `message ${type}`;

        if (type === 'agent' || type === 'system') {
            const avatar = document.createElement('div');
            avatar.className = 'message-avatar';
            avatar.innerHTML = BOT_AVATAR_SVG;
            messageDiv.appendChild(avatar);

            const body = document.createElement('div');
            body.className = 'message-body';

            const contentDiv = document.createElement('div');
            contentDiv.className = 'message-content';
            contentDiv.innerHTML = type === 'agent' ? marked.parse(content) : content;
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
    }

    let lastAgent = null;

    function renderFlow(flow) {
        if (!flow || !flow.stops || flow.stops.length === 0) {
            flowTitleEl.textContent = 'Home';
            flowSteps.classList.add('hidden');
            flowSteps.innerHTML = '';
            return;
        }

        flowTitleEl.textContent = flow.title;
        flowSteps.classList.remove('hidden');

        flowSteps.innerHTML = '';
        flow.stops.forEach((agentName, index) => {
            if (index > 0) {
                const chevron = document.createElement('span');
                chevron.className = 'flow-step-connector';
                chevron.textContent = '›';
                flowSteps.appendChild(chevron);
            }

            const step = document.createElement('div');
            step.className = `flow-step ${index === flow.current_index ? 'active' : 'done'}`;
            step.innerHTML = `
                <span class="flow-step-dot"></span>
                <span class="flow-step-text">
                    <span class="flow-step-name">${agentName}</span>
                    <span class="flow-step-sublabel">${index === flow.current_index ? 'In progress' : 'Handled'}</span>
                </span>`;
            flowSteps.appendChild(step);
        });
    }

    async function resetConversation() {
        try {
            await fetch('/api/reset', { method: 'POST' });
        } catch (error) {
            console.error('Failed to reset conversation:', error);
        }

        chatMessages.innerHTML = '';
        addMessage("Namaste. How can I help you today?", 'system');
        renderFlow(null);
        lastAgent = null;

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

    chatForm.addEventListener('submit', async (e) => {
        e.preventDefault();

        const message = userInput.value.trim();
        if (!message) return;

        // Add user message to chat
        addMessage(message, 'user');
        userInput.value = '';
        userInput.disabled = true;

        // Show typing indicator
        typingIndicator.classList.remove('hidden');
        scrollToBottom();

        try {
            const response = await fetch('/api/chat', {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json'
                },
                body: JSON.stringify({
                    message,
                    agent_name: currentSelectedAgent
                })
            });

            const data = await response.json();

            // Hide typing indicator
            typingIndicator.classList.add('hidden');

            if (data.status === 'success') {
                if (lastAgent !== data.agent_name) {
                    addMessage(`Switched context to ${data.agent_name}`, 'context-switch');
                    lastAgent = data.agent_name;
                }
                // Add agent response
                addMessage(data.response, 'agent', data.agent_name);
                renderFlow(data.flow);
            } else {
                addMessage(data.error || 'An error occurred.', 'system');
            }
        } catch (error) {
            typingIndicator.classList.add('hidden');
            addMessage('Network error. Please try again.', 'system');
        } finally {
            userInput.disabled = false;
            userInput.focus();
        }
    });
});
