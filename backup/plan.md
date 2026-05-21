## Plan: Build Robust Internal Research Product

Transform backend + basic HTML dashboard into a full-featured team tool with persistent chat, async processing, drive integration, and conversation history. Focus on single-team use (no auth), stateful operations, and reliable data persistence.

### Architecture Overview

**Three Core Additions:**
1. **Data Layer:** Persistent chat/session storage + markdown summaries
2. **Frontend:** Replace HTML with React app (better state management)
3. **Backend:** Add session handlers, async job orchestration, chat persistence layer

### Steps

1. **Create Chat & Session Storage Schema**
   - Add TypedDict/Pydantic models for `ChatSession` (id, project, messages[], created_at, updated_at, daily_summary)
   - Store in JSON files under `/data/sessions/{project_name}/` or upgrade to SQLite for better querying
   - Endpoint: `GET /sessions/{project_name}` + `POST /sessions/{project_name}/messages`

2. **Add Drive Folder Configuration Page**
   - New page in frontend: "Drive Sync Config"
   - Store drive folder mapping in `/data/state/drive_config.json` with schema: `{project_name: {folder_id, credentials_path, token_path, last_sync}}`
   - Backend endpoint: `POST /config/drive` to save, `GET /config/drive` to retrieve
   - Use scheduled job to poll new files (or manual trigger button)

3. **Implement History Tab**
   - Add "History" page listing all projects with past conversations
   - Endpoint: `GET /projects/{project_name}/history` returns list of chat sessions
   - Click session → loads full conversation + ability to continue chatting
   - Auto-load latest chat when viewing a project

4. **Build Daily Summary System**
   - New function: `summarize_daily_chats(project_name, date)` 
   - Runs at end of day (or manually triggered)
   - Endpoint: `POST /sessions/{project_name}/daily-summary` → generates `.md` file in `/data/summaries/{project_name}/{date}.md`
   - On next chat, prepend summary to system prompt for context
   - Include chat count, key questions, decisions made, next steps

5. **Add Async Job Orchestration**
   - Extend `pipeline_v2.py` to support progress updates during agent runs
   - Add WebSocket or polling endpoint: `GET /jobs/{job_id}/progress` for real-time updates
   - Store job state: `queued → running → complete/failed` with progress % for each agent
   - Ensure multiple async jobs write to separate project folders without conflicts

6. **Upgrade Frontend to React + State Management**
   - Replace static HTML with React app (Vite + React)
   - Use Context API or Zustand for single global session store (no auth needed)
   - Manage: `currentProject`, `chatHistory`, `jobs`, `driveConfig`
   - Persist to localStorage + sync from backend
   - Pages: Dashboard, Drive Config, History, Copilot Chat, Pipeline Monitor

### Further Considerations

1. **LLM Memory Problem (API Statelessness)**
   - Current design: Each API call = new context window
   - Solution: **You CANNOT fix this at LLM level** (APIs are stateless), BUT you CAN:
     - **Option A:** Save full chat + previous day summary to system prompt before each call (current approach, will work but grows tokens over time)
     - **Option B:** Use a real vector DB (Supabase pgvector) to retrieve relevant past conversations + inject as context (best long-term)
     - **Option C:** Stream to Gemini's stored conversation feature (if using native Gemini app instead of API)
   - **Recommendation:** Start with Option A (easy), migrate to Option B when budget allows

2. **Chat Persistence Strategy**
   - Store messages in `/data/sessions/{project_name}/{session_id}.json` as newline-delimited JSON (append-only for safety)
   - Also store in-memory in frontend for quick access
   - On page reload: fetch latest session from backend
   - Sync every message to backend (no risk of loss)

3. **Async Handling for Concurrent Requests**
   - Each job gets unique `job_id` + separate project folder with UUID suffix
   - Prevent file conflicts: pipeline writes to `database_mock/{project_name}_{job_id}/` during run
   - After completion, merge/upsert into main `database_mock/{project_name}/` (keep latest version or version control)
   - Frontend tracks multiple jobs independently

### Implementation Order

**Phase 1 (Week 1):** Storage layer + chat persistence
- JSON schema for sessions + summaries
- Backend endpoints for chat CRUD
- Verify no data loss on concurrent writes

**Phase 2 (Week 2):** History tab + drive config page
- Backend endpoints for history retrieval + drive config
- Frontend pages (minimal React conversion first)
- Test conversation continuity

**Phase 3 (Week 3):** Daily summaries + async orchestration
- Summary generation logic
- Job progress tracking
- Frontend job monitor updates

**Phase 4 (Week 4):** Full React app + polish
- Complete frontend migration
- Global state management
- Error handling + user feedback