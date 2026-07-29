# Saarthi Runbook

## 14.6 Operational Queries

These six queries answer the majority of operational questions, all served by existing indexes.

### 1. Which tools are failing, and at what rate? (7d)
Tool executions grouped by name and status over a period. This query distinguishes between "Provider rate limited", "model chose not to call the tool", and "model hallucinated a tool name".

```sql
SELECT 
    tool_name,
    COUNT(*) as total_calls,
    SUM(CASE WHEN status != 'success' THEN 1 ELSE 0 END) as failures,
    SUM(CASE WHEN status = 'error' THEN 1 ELSE 0 END) as hard_errors,
    SUM(CASE WHEN status = 'timeout' THEN 1 ELSE 0 END) as timeouts,
    ROUND(SUM(CASE WHEN status != 'success' THEN 1 ELSE 0 END)::numeric / NULLIF(COUNT(*), 0) * 100, 2) as failure_rate_pct
FROM tool_executions
WHERE created_at > now() - INTERVAL '7 days'
GROUP BY tool_name
ORDER BY failure_rate_pct DESC;
```

### 2. What is per-agent latency at the 95th percentile?
Messages grouped by agent, over duration.

```sql
SELECT 
    a.name as agent_name,
    COUNT(m.id) as total_turns,
    PERCENTILE_CONT(0.50) WITHIN GROUP (ORDER BY m.latency_ms) as p50_latency_ms,
    PERCENTILE_CONT(0.95) WITHIN GROUP (ORDER BY m.latency_ms) as p95_latency_ms,
    PERCENTILE_CONT(0.99) WITHIN GROUP (ORDER BY m.latency_ms) as p99_latency_ms
FROM conversation_messages m
JOIN agents a ON m.agent_id = a.id
WHERE m.role = 'assistant' 
  AND m.latency_ms IS NOT NULL
  AND m.created_at > now() - INTERVAL '7 days'
GROUP BY a.name
ORDER BY p95_latency_ms DESC;
```

### 3. How is routing distributed across gates?
Messages grouped by route reason over a period.

```sql
SELECT 
    route_reason,
    COUNT(*) as total_routes,
    ROUND(COUNT(*)::numeric / NULLIF(SUM(COUNT(*)) OVER (), 0) * 100, 2) as pct_of_total
FROM conversation_messages
WHERE role = 'assistant'
  AND route_reason IS NOT NULL
  AND created_at > now() - INTERVAL '7 days'
GROUP BY route_reason
ORDER BY total_routes DESC;
```

### 4. Are any sessions stuck finalising? (> 10 min)
Sessions in the finalising state beyond a threshold.

```sql
SELECT 
    id as session_id,
    conversation_id,
    remote_session_id,
    last_activity_at,
    now() - last_activity_at as time_stuck
FROM agent_sessions
WHERE state = 'finalizing'
  AND last_activity_at < now() - INTERVAL '10 minutes'
ORDER BY last_activity_at ASC;
```

### 5. What is cost per agent per day?
Messages grouped by agent and day, over token counts and model.

```sql
SELECT 
    DATE(m.created_at) as usage_date,
    a.name as agent_name,
    m.model,
    SUM(m.prompt_tokens) as total_prompt_tokens,
    SUM(m.completion_tokens) as total_completion_tokens,
    SUM(COALESCE(m.prompt_tokens, 0) + COALESCE(m.completion_tokens, 0)) as total_tokens
FROM conversation_messages m
JOIN agents a ON m.agent_id = a.id
WHERE m.role = 'assistant'
  AND m.created_at > now() - INTERVAL '7 days'
GROUP BY DATE(m.created_at), a.name, m.model
ORDER BY usage_date DESC, total_tokens DESC;
```

### 6. What changed in configuration, and who changed it?
Audit entries filtered by action over a period.

```sql
SELECT 
    created_at,
    actor,
    action,
    entity_type,
    entity_id,
    note
FROM audit_logs
WHERE action IN ('config_sync', 'config_create', 'config_activate', 'agent_enable', 'agent_disable')
  AND created_at > now() - INTERVAL '7 days'
ORDER BY created_at DESC;
```
