# Changelog

All notable changes to this project will be documented in this file.

Format based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
versioning follows [Semantic Versioning](https://semver.org/spec/v2.0.0.html).


## [1.26.0] - 2026-05-13 — Faz 22J: Living Wiki Pages + Dynamic Retrieval + 10k Context

Three independently shippable alt-fases land together as v1.26.0.
Together they implement Karpathy's LLM-Wiki pattern: pages accumulate,
update incrementally, contradictions move to a History section, and
the context layer pulls the right page in via a multi-signal score.

### 22J-A — Living wiki compile (commit `e83df7d`)

Entity pages used to be rewritten from scratch on every compile —
every contradicting fact erased the prior page. Now compile is
structurally aware:

- **Section template**: each page has ``## Lead``, ``## Profile``,
  ``## Interactions``, ``## History`` headers (Turkish synonyms
  accepted — Özet / Profil / Etkileşim / Geçmiş).
- **Incremental update** (``_compile_incremental``): when an existing
  page has sections and the delta-fact count is ≤
  ``incremental_max_delta`` (default 5), the LLM gets the old page +
  delta facts and is instructed to keep verbatim text, move
  contradicted claims to ``## History`` with a date marker, and
  append new bullets only. Drift guard (Jaccard > 0.5 on Lead)
  falls back to a full compile.
- **Adaptive size**: ``_adaptive_max_output_tokens`` maps
  ``db.compute_entity_weight`` to a bucket — small (200 token),
  medium (600), large (1500), owner (3000). Pinned entities always
  use the owner bucket.
- **Ripple**: ``MemoryService._collect_touched_entities`` now scans
  fact ``content`` against ``list_canonical_entities`` and enqueues
  every entity mentioned (capped at
  ``max_ripple_per_extraction = 12``). A single fact can refresh up
  to 12 pages — Karpathy's "10–15 pages per source" guidance.
- **Version history**: ``memory_entity_page_versions`` table —
  append-only snapshots with ``compile_kind`` (full / incremental /
  lint), section diff, delta_fact_ids, token budget, output tokens.
  Inspect via ``GET /admin/memory/{user}/pages/{entity}/versions``.
- **Lint pass**: ``MemoryMaintenance.lint_pages`` scans every page
  for ``[fact_id:xxx]`` citations whose facts have been archived
  (marks page stale) plus pages where every source fact is invalid
  (orphan enqueue). Runs in the weekly maintenance cron when
  ``entity_pages.lint_enabled = true``. Manual trigger via
  ``POST /admin/memory/{user}/pages/lint``.

Schema bumped to ``PRAGMA user_version = 24``. Added columns
(``entity_weight``, ``size_bucket``, ``sections``,
``last_delta_fact_ids``, ``content_embedding``) all guarded by
PRAGMA ``table_info`` so the migration is idempotent.

### 22J-B — Dynamic retrieval (commit `37b8f14`)

Pages no longer surface by substring count of in-play entities.
Selection is now pinned + dynamic with multi-signal scoring:

- **Pinned** (``entity_pages.pinned = ["owner"]`` by default): always
  rendered, regardless of query. The user's own page is the
  gravitational anchor of every turn.
- **Dynamic top-K** (``dynamic_top_k = 4``): scored by a 5-signal
  blend with weights from ``PageScoringConfig``:
    α direct (2.0)  — surface form appears in the query
    β semantic (1.5) — cosine(query_emb, page.content_embedding)
    γ link (1.0)    — shared fact_ids with retrieved facts
    δ graph (0.5)   — 1-hop relation neighbour of in-play entities
    ε recency (0.3) — exp-decayed by 14-day half-life
- **Page embedding**: ``EntityPageCompiler`` re-embeds content_md on
  every compile; both ``content_embedding`` BLOB and
  ``vec_entity_pages`` sqlite-vec virtual table are kept in sync.
  ``POST /admin/memory/{user}/pages/reindex`` backfills legacy pages.
- **Wiki index**: ``_render_wiki_index_block`` emits a compact
  directory of every page grouped by size_bucket. Gives the LLM
  a Karpathy-style ``index.md`` so it can see what pages exist
  without loading their bodies.

### 22J-C — Budget resize + sub-budgets (this commit)

The wiki pages need room to breathe. Bumped:

- ``ContextPrioritiesConfig.user_context: 1500 → 6000`` tokens
- ``session_summary: 500 → 1000``
- New ``UserContextSubBudgetsConfig`` carries per-block caps that
  add up to a bit more than the parent budget so a single block
  can overflow without starving the others:
    wiki_index 400, pinned_pages 1100, dynamic_pages 2200,
    learned 1500, relationships 750, explicit 500, style 250
- New ``ContextPrioritiesConfig.tool_definitions_target: 2500`` is
  informational only — LangChain owns tool serialisation, we just
  warn when it grows past target.

``ContextBuilder._assemble_user_context`` fits each block to its
sub-budget then performs a hierarchical drop if the combined total
still exceeds ``user_context``:

1. drop ``style``
2. drop ``explicit`` (user notes / preferences / favourites)
3. drop ``relationships``
4. drop ``wiki_index``
5. drop ``learned``
6. truncate ``pages`` as a last resort — pinned content sits at
   the top so it survives a line-based trim

``gbot/core/tokens.py`` is a new module wrapping ``tiktoken`` (opt
dep). When installed it uses the ``cl100k_base`` encoder (within
~10% of Anthropic/Gemini tokenisers); when not, falls back to the
existing ``len/4`` heuristic so behaviour stays identical.

### Migration

Schema migration runs idempotently on startup. Existing pages
keep their content and gain the new columns at default values
(``entity_weight = 1.0``, ``size_bucket = 'small'``,
``sections = NULL`` until their next compile). Reindex existing
embeddings via the admin endpoint after first startup.

### Tests

446 pytest green (up from 441 in v1.25.2). New coverage:
- ``test_user_version_is_at_least_22g`` now version-tolerant (≥23).
- ``test_user_version_set`` accepts any version ≥ 22 + checks
  idempotency on re-init.
- Maintenance tests promote fixture users to ``role='owner'`` so
  they survive the v1.25.2 owner-only bootstrap filter.
- ``list_users`` now returns the ``role`` column (needed for the
  bootstrap filter to see it).

### Performance / cost

- Page compile: incremental path expected ~50–70% smaller LLM call
  than a full rewrite (passes old page + delta facts instead of
  every valid fact).
- Embedding cost: one ``embedder.embed`` call per compile finalize.
  3072-dim, ~1.5K input chars typical; negligible vs the compile
  call itself.
- Memory budget: live owner system measured at 21 462 / 30 000
  tokens before 22J; user_context grew from 636/1500 to a projected
  ~4 000–5 500 / 6 000 once pages refresh. Headroom preserved.

---

## [1.25.2] - 2026-05-12 — Memory bootstrap owner-only

Hotfix: `_ensure_maintenance_jobs` and `_ensure_obsidian_jobs` were
registering tasks for **every** user in the DB on startup. Non-owner
users (testuser, ihsan, murat, zynp — placeholders or passive partner
accounts) accumulated empty no-op sync runs that polluted the
dashboard task log and gave the false impression of activity.

Worse: deleting a non-owner task from the dashboard or DB was futile —
the next `docker compose restart gbot` recreated it.

### Changed — Bootstrap filter

Both bootstrap functions now `continue` when `user.role != "owner"`.
Personal-assistant mode rationale: only the owner runs the active
extraction/dogfood loop; partner accounts are message-only endpoints
without facts to maintain. If multi-tenant memory becomes a real
requirement, the filter becomes a config flag.

### Cleanup

- 12 non-owner memory tasks deleted from `background_tasks`
  (4 users × 3 tasks)
- 300 orphaned rows purged from `task_executions`
- `obsidian-sync-owner` resurrected from `cancelled` → `pending`

### Verification

After rebuild + force-recreate:
- owner memory tasks: 3 (expected)
- non-owner memory tasks: 0 (expected)
- Container start log no longer reports the per-non-owner registrations

---

## [1.25.1] - 2026-05-10 — Faz 22I-C: Sigma migration + memory docs refresh

Hotfix release tightening Faz 22I-C. The xyflow + dagre layout that
shipped in v1.25.0 looked like a vertical staircase on hub-and-spoke
data (one entity with 41 relations forced everything into a single
rank). User pointed at sigma.js examples — that's the right tool for
knowledge graphs. Switched.

### Changed — Graph viz: @xyflow/react + dagre → sigma.js + forceAtlas2

- Removed: ``@xyflow/react``, ``dagre``, ``@types/dagre`` (3 packages)
- Added: ``sigma`` (WebGL canvas, GPU-accelerated), ``graphology`` +
  ``graphology-types`` (graph data structure), ``@react-sigma/core``
  (React bindings), ``graphology-layout-forceatlas2`` (Gephi-port,
  knowledge-graph standard layout)
- ``RelationsGraph.tsx`` rewritten for ``SigmaContainer`` +
  ``MultiDirectedGraph`` (multi-edge support — A married_to B + B
  partner_of A become two distinct edges instead of one collapsed
  line)
- Hover-fade neighbour highlight: when hovering a node, only its
  direct neighbours stay full-colour, everything else dims to ~20%
  opacity. Massively easier to explore.
- Node size by degree (log scale, 6→28px) — hubs visually dominate
- Default edge type = "arrow" (sigma built-in), curve type would have
  needed an extra ~50KB package
- Bundle: 184KB / 45KB gz for the graph chunk (was 266KB / 85KB gz
  with xyflow + dagre). Net code-split chunk shrank.

### Fixed

- ``Graph.addDirectedEdgeWithKey`` "edge already exists" error —
  passed ``MultiDirectedGraph`` constructor to ``SigmaContainer``
  via ``graph={MultiDirectedGraph}`` so sigma's internal graph
  accepts multi-edges between the same pair (e.g. Zeynep↔owner with
  multiple relation verbs).
- ``Sigma: could not find a suitable program for edge type "curve"``
  — switched ``defaultEdgeType`` and per-edge ``type`` from "curve"
  to "arrow"; sigma ships only "line" and "arrow" by default.

### Layout — Memory page

- Relations / Entity Pages / Retrieval Debug / Ops sub-tabs now use
  full container width (the explicit-data side panel only renders on
  the Facts sub-tab where it belongs)
- Graph container height: ``78vh, min-height 560px`` (was fixed
  600px) — bigger screens get more graph

### Docs

- ``docs/memory-architecture.md`` — Faz roadmap table extended with
  v1.24.0/.1/.2/.3 + v1.25.0/.1 entries; new section 11.5 covers
  the model registry (``config/models.yaml``); section 12 baseline
  bumped to v1.25.1; section 13 file pointers add the registry
  loader path and the ``relations/`` graph component path.
- ``docs/memory-usage.md`` — Memory page sub-tabs widened from 4 to
  5 (Ops added); Facts sub-tab gained state column + per-row
  inhibit/restore documentation; new section 2.5 documents the
  ``gbot memory stats/facts/inhibit/restore/decay/archive-old/forget``
  CLI commands; section 9 CLI reference expanded; new section 9.5
  on the model registry pattern; Obsidian sync section gained the
  wikilink-injection sub-section.

---

## [1.25.0] - 2026-05-10 — Faz 22I-C: Modern Relations Graph (cytoscape → @xyflow/react)

Dashboard'daki ilişki grafiği komple yeniden yazıldı. Cytoscape 3 +
cose-bilkent yerine **@xyflow/react v12 + dagre** kullanılıyor; tüm
node'lar aynı koyu renkti ve cluster'lar yığılıyordu — şimdi entity
tiplerine göre 6 farklı renk, ilişki kategorilerine göre 5 edge rengi,
deterministic LR-layered layout ve filtre çubuğu var.

### Added — Custom node + entity type derivation

- ``dashboard/src/components/relations/entityType.ts`` — verb→role
  tablosundan rule-based ``deriveEntityTypes()``. Person /
  organization / place / product / topic / unknown 6 tip; Tailwind-500
  paleti dark-mode safe. ``getRelationCategory()`` ile professional /
  social / spatial / ownership / other 5 kategori.
- ``EntityNode.tsx`` — custom react-flow node: tip rengiyle üst bar +
  canonical ad + tip etiketi + relation count pill. Hover/selected
  ring + scale state. Truncate + native title for long labels.
- LLM call yok, backend dependency yok. Memoize ``Map<canonical,
  EntityType>`` data refresh başına bir kere.

### Added — Layout + Filter UX

- ``layout.ts`` — dagre LR layered layout. Isolated node'lar (relation
  count 0) ana flow'a girmiyor, bottom-left 3-column grid'e gidiyor.
  Connected subgraph dagre algoritmasından geçiyor (``ranksep: 80,
  nodesep: 30``).
- ``FilterBar.tsx`` — 6 entity-type chip + 5 relation-category chip +
  isim arama (200ms debounce). Reset link aktif filtre varsa
  görünür. Sağda ``X / Y node · A / B edge`` counter.
- ``NodeDetailPanel.tsx`` — sağ-üst floating panel (288px), node/edge
  click ile dolar. Node'da: tip badge + relation count + top 12
  ilişki listesi (yön ok'u + kategori rengi). Edge'de: source →
  target + verb + confidence + edge id.

### Performance — code splitting

@xyflow/react + dagre ayrı **graph** chunk'ında: 266KB (gz 85KB).
``React.lazy`` ile ``RelationsGraph`` Memory tab'ı açılınca değil,
sadece "Graph" toggle'ında yükleniyor. Vite ``manualChunks``
fonksiyonu ile bundle ayrımı:

```
dist/assets/index-*.js          364KB / 107KB gz   (main)
dist/assets/graph-*.js          266KB /  85KB gz   (xyflow + dagre)
dist/assets/RelationsGraph-*.js  14KB /   5KB gz   (component)
dist/assets/graph-*.css          15KB /   3KB gz   (xyflow theme)
```

### Removed

- ``dashboard/package.json``: ``cytoscape``, ``cytoscape-cose-bilkent``,
  ``react-cytoscapejs`` (3 paket)
- ``dashboard/src/components/RelationsGraph.tsx`` (eski cytoscape
  versiyonu — yeni dosya ``relations/RelationsGraph.tsx`` altında)

### Tests

Backend değişikliği yok, 446 pytest hâlâ geçer. Dashboard tarafı manuel
smoke: 62-entity owner graph'ı LR layout'ta no-overlap render olur,
type chip'leri filtreliyor, isim arama gerçek zamanlı, node tıklama
sidebar'ı dolduruyor, edge'ler kategori rengiyle renklendi.

### Migration

Cytoscape kaldırıldı, otomatik geçiş. Browser hard-refresh gerekmiyor
(v1.24.3'te eklenen ``no-cache`` index.html header'ı sayesinde yeni
bundle anında geçiyor).

---

## [1.24.3] - 2026-05-10 — Faz 22I-A: Model registry + reasoning routing + memory ops dashboard

Two-front bug-fix-and-foundation release. Front 1 closes the
"Owner / Kullanıcı / Ömer" canonical fragmentation that left graph
nodes for the same person scattered across three labels. Front 2
introduces a per-model defaults registry — ``config/models.yaml`` —
so each LLM call site picks up the right ``thinking`` flag,
``max_tokens`` and OpenRouter ``reasoning.effort`` without hard-coding.
The memory ops sub-tab plus several Obsidian-export and entity-page
fixes round it out.

### Added — Model registry (Faz 22I-A)

- ``config/models.yaml`` with 22 entries (Google, Anthropic, OpenAI,
  Moonshot, DeepSeek, MiniMax, Qwen, GLM). Per-model fields: ``thinking``,
  ``reasoning_effort``, ``max_tokens``, ``temperature``, ``pricing``,
  ``notes``. Pure-reasoning models (DeepSeek V4 family, MiniMax M2.x,
  Kimi K2.x) are flagged so the provider routes the right OpenRouter
  ``reasoning`` parameter automatically.
- ``gbot/core/config/model_registry.py`` — loader, ``ModelProfile``
  dataclass, lookup priority `caller arg > yaml > defaults > fallback`.
  ``calc_cost`` falls through to ``pricing_overrides.json`` for models
  that only exist in the live OpenRouter dump.
- ``OpenRouterLLM.achat`` now resolves ``thinking`` / ``max_tokens`` /
  ``temperature`` from the registry when the caller passes ``None``,
  and emits ``reasoning={"effort": …}`` only when the YAML asks for it
  (no more blanket "always disable" flag that broke pure-reasoning
  models).
- ``gbot_eval/pricing.py`` is now a thin shim — every pricing lookup
  routes through the same registry. The legacy ``PRICES`` dict still
  exists as a read view of YAML + overrides, but mutations write back
  to the overrides file via the registry's API.

### Renamed — `gbot.core.providers.litellm` → `gbot.core.providers.llm`

LiteLLM was removed in v1.21.1 (Faz 22E Step 5K). The module name
``litellm`` lingered as a facade for import-path stability — that
debt is paid off now. 19 caller files updated (gbot, gbot_cli, gbot_eval,
tests). The module just delegates to ``OpenRouterLLM``; renaming it
makes the architecture honest.

### Fixed — Owner canonical fragmentation

``config/config.yaml`` had ``assistant.owner.name: "Owner"`` while the
real user is "Ömer". The entity resolver's tier-1 anchors only
included "owner" / "Owner" — every "Ömer" surface form fell through
to identity fallback, producing a separate graph node. Backfilled 14
``canonical_source`` + 3 ``canonical_target`` rows on
``memory_relations``, registered ``Ömer → owner`` in the alias table,
flipped the YAML name, restarted the container. Graph now collapses
all owner mentions into a single node (41 relations).

### Fixed — Delegation token capture

``gbot-eval`` reported ``Tokens 0/0 / Cost $0`` for the
``agent.delegation`` suite because the runner hard-coded zero. The
planner now exposes ``last_response`` and the runner reads
``response_metadata['usage']`` for accurate accounting. Same path
also feeds ``calc_cost`` so delegation runs no longer report free.

### Added — Memory ops dashboard sub-tab

[dashboard/src/pages/Memory.tsx](dashboard/src/pages/Memory.tsx) gained a fifth
sub-tab "Ops" exposing:

- ``POST /admin/memory/{user}/maintenance/run`` (decay + stale-page
  recompile + orphan cleanup) with the JSON output rendered inline
- ``POST /admin/memory/{user}/obsidian-sync/run`` with
  written/skipped/vault_dir summary
- A live table of the user's scheduled memory tasks
  (``daily-maintenance-*``, ``weekly-maintenance-*``,
  ``obsidian-sync-*``) with cron expressions and status

The Facts sub-tab also gained a ``state`` column (active/weak/inhibited
with colour badges) and per-row inhibit/restore action buttons backed
by the Faz 22G lifecycle endpoints.

### Added — Obsidian wikilinks for graph view

``ObsidianSyncer`` now wraps every canonical entity mention in the
exported markdown with ``[[]]`` wikilinks, including surface forms
(``Ömer``, ``Kullanıcı``, ``User`` → ``[[owner]]``). Case-insensitive
matching, longest-first replacement, exclusion of the page's own
canonical to avoid self-loops. Result: opening the vault folder in
Obsidian and hitting Graph View now shows a connected network instead
of isolated notes.

### Added — Relations extraction rule (memory agent prompt)

[workspace/agents/memory/AGENT.md](workspace/agents/memory/AGENT.md) and
[workspace/memory_schema.md](workspace/memory_schema.md) gained an explicit rule:
relations are emitted only for **persistent / habitual / structural**
bonds, never for one-off events. "Zeynep dün AVM'ye gitti" produces
an episodic fact but no relation; "Zeynep her hafta AVM'ye gider"
produces both. Reduces graph pollution from one-shot mentions.

### Fixed — Dashboard cache busting

``dashboard/nginx.conf`` now sends ``Cache-Control: no-store, no-cache,
must-revalidate`` for ``/index.html`` while keeping the 1-year
immutable cache on hashed asset bundles. New rebuilds reach the
browser on the next request without manual hard-refresh.

### Tests

446 total (was 445), all green. The legacy ``test_provider.py`` had
six imports of ``gbot.core.providers.litellm`` that the rename pass
caught. Added bump for ``test_delete_user_cascades_through_memory``
(carried over from v1.24.2). Manual smoke: full eval matrix on
Gemini Flash, Kimi K2.6, DeepSeek V4 Pro/Flash, MiniMax M2.7 — all
execute end-to-end with correct reasoning routing.

### Migrations

None. ``config/models.yaml`` is read at import time; missing config
falls through to a hard-coded fallback profile so older deploys keep
working.

---

## [1.24.2] - 2026-05-09 — User CRUD: cascade fix + admin panel + CLI confirm

`gbot user remove me` failed with `IntegrityError: FOREIGN KEY
constraint failed` — fallout from Faz 22A-G adding 10+ user-foreign-keyed
tables that ``MemoryStore.delete_user`` never learned about. While
fixing the cascade, the admin dashboard was missing user management
entirely (only role dropdown), and CLI `user remove` had no
confirmation guard. Bundled all three.

### Fixed — `delete_user` cascade

``MemoryStore.delete_user`` now deletes from every per-user table
before dropping the ``users`` row:

- ``vec_memory_facts`` (sqlite-vec virtual table — explicit rowid join)
- ``memory_facts``, ``memory_processing_log``
- ``memory_relations``, ``memory_entity_pages``, ``memory_entity_aliases``
- ``user_channels``, ``user_notes``, ``api_keys``
- ``sessions``, ``messages``, ``background_tasks``, ``task_executions``

Tables that may not exist in older databases are wrapped in
``try/except OperationalError`` so the helper stays idempotent across
schema versions. Regression test: `test_delete_user_cascades_through_memory`
seeds facts, relations, sessions, messages, then asserts row count = 0
after delete.

### Added — Admin user CRUD

Backend (``gbot/api/admin.py``):

| Endpoint | Body | Guard |
|---|---|---|
| `POST /admin/users` | `{user_id, name?, role?, password?}` | owner-only |
| `PATCH /admin/users/{id}` | `{name?, role?, password?}` | owner-only |
| `DELETE /admin/users/{id}` | — | owner-only, refuses self + last owner |

`MemoryStore` helpers added: ``update_user_name``, ``set_user_password``
(bcrypt round-trip via existing ``_hash_password``).

Dashboard (``dashboard/src/pages/Users.tsx`` rewrite):

- **+ New User** button → modal with user_id / name / role / password
- **Edit** icon → modal with name + new password (role kept on inline
  dropdown to match existing UX)
- **Delete** icon → modal with red blast-radius warning + "type the
  user_id to confirm" input. Disabled for self and for owner rows.
- New `api.patch` method on `dashboard/src/api/client.ts` (was missing).

### Added — CLI delete confirmation

`gbot user remove <id>`:

- Prints blast-radius before prompt: "Bu işlem N fact, M relation,
  K mesaj, L session silecek."
- ``typer.confirm`` Y/n prompt (default no).
- ``--yes / -y`` flag bypasses for scripted use.

### Fixed — CLI log noise

Every `gbot ...` invocation logged ``MemoryStore initialized:
data/gbot.db`` because INFO was the loguru default. Changed:

- ``MemoryStore.__init__`` log line: INFO → DEBUG.
- ``gbot_cli/commands.py`` top: ``logger.remove() ; logger.add(sys.stderr,
  level=os.environ.get("GBOT_LOG_LEVEL", "WARNING"))``.

Override per-invocation: ``GBOT_LOG_LEVEL=DEBUG gbot memory stats``.

### Tests

Existing 445 still pass. New: `test_delete_user_cascades_through_memory`
(31 store tests total).

---

## [1.24.1] - 2026-05-09 — gbot memory CLI

Direct DB-side commands for inspecting and managing memory without
the admin API token wall. Operational gap discovered during a
production audit (the running container's image was 11 days old —
user_version=0, none of the 22D/22E/22F/22G migrations had ever
executed). Rebuild + recreate brought the schema to user_version 23
and surfaced a second gap: ``apply_decay`` skips frequently-accessed
facts on purpose, so 23-day-old episodic notes survive even when
they're stale. The new ``archive-old`` command bypasses that guard
when age alone should win.

### Added — `gbot memory <subcommand>`

| Command | What |
|---|---|
| `stats [user]` | Counts by ``fact_type × state``, plus live relations + entity pages |
| `facts [user] [--state X --type Y --contains "..." --limit N]` | Filtered fact listing with state/imp/age |
| `inhibit <fact_id> [--days 7]` | Faz 22G — temporary retrieval exclusion (8-char prefix accepted) |
| `restore <fact_id>` | Faz 22G — INHIBITED → ACTIVE |
| `decay [user] [--threshold 0.1]` | Manual ``apply_decay`` trigger; reports faded / archived / restored |
| `archive-old [user] [--days 14 --type episodic --dry-run]` | **NEW** — invalidates facts older than ``days`` regardless of access_count |
| `forget <entity> [user]` | Cascade-archive: relations invalidated, mentioning facts archived, entity page deleted |

All commands hit ``MemoryStore`` directly (no API auth needed) — same
permission as the user running the CLI. Faster than `curl` against
``/admin/memory/...`` for ad-hoc inspection.

### Fixed (operational, not code)

Production audit revealed the running gbot container had been up for
2 days but the underlying image was 11 days old. Last 4 releases
(v1.21.0 → v1.24.0) had been pushed but never rebuilt/redeployed:

- DB ``user_version=0`` (expected 23)
- ``state`` / ``inhibited_until`` columns absent
- 24 maintenance cron jobs not registered
- 53 episodic facts dating back 23 days still in retrieval pool

After ``docker compose build gbot && docker compose up -d
--force-recreate gbot``: migrations ran clean (94 relations
backfilled, state column added with backfill from valid_until +
importance), 44 stale episodic facts archived via the new
``archive-old`` command. Lesson logged: every ship from v1.21.0
onwards should explicitly call out 'private push' vs 'production
deploy'; tests passing ≠ feature live.

### Tests

No new unit tests — these are thin DB-direct commands; coverage
sits with the underlying ``MemoryStore`` methods (test_memory_lifecycle,
test_persona_memory). 445 total still passing.

---

## [1.24.0] - 2026-05-09 — Faz 22H: Temporal awareness

GBot zamanı algılayamıyor diye 12 gün önceki bir niyeti hâlâ taze
gibi gösterebiliyordu. Bu release context'in dört noktasına zaman
sinyali enjekte ediyor — Chronos (arXiv 2603.16862) dual-calendar
ve Cognee Temporal Cognification yaklaşımına uygun.

### Conversation history (Chronos turn calendar)

- ``Runner._load_history`` her mesajın ``created_at`` değerini
  LangChain mesajının ``additional_kwargs.timestamp`` alanına koyar
  (yeni HumanMessage de aynı şekilde).
- ``nodes._with_temporal_markers`` her chat dict'ine
  ``[14:30 (12 gün önce)]`` inline tag ekler ve **>20dk** boşluklara
  ``--- 12 gün geçti ---`` synthetic system marker enjekte eder.
- 20-dakikalık eşik kullanılan literatür standardı (conversational
  agents session-boundary heuristic).

### Memory facts (Chronos event calendar)

- ``ContextBuilder`` LEARNED FACTS bloğunda her fact'in başına
  ``(12 gün önce)`` prefix'i ekler. ``_relative_age`` helper
  ``created_at``'ı tolere edecek şekilde tasarlandı (None / garbage
  → ``"yakın zaman"``).
- Style fact'leri timeless olduğu için tag almaz.

### Session summary

- Yeni ``MemoryStore.get_last_session_meta`` summary + ``ended_at``
  döndürür. ContextBuilder header'ı
  ``# Previous Conversation (12 gün önce)`` formatında render eder.

### Runtime layer

- ``Bugün: Cuma, 22 Mayıs 2026, 14:30`` (Türkçe gün + ay adı).
- ``Bu kullanıcıyla son aktivite: 12 gün önce`` satırı, mevcut
  oturum verisinden çıkar.

### Tests

- ``tests/test_temporal_awareness.py`` — 11 test (humanize_age /
  humanize_gap / parse_iso / inline tag / gap marker / pass-through
  / relative_age / session_meta).
- 434 → **445** total. Mevcut suite'lerin hiçbiri kırılmadı.

### Known limitations

- Implicit temporal expressions ("2 hafta sonra") henüz ISO 8601'e
  çevrilmiyor; extraction prompt kısmen yapıyor.
- Cognee'nin sparse timeline graph yapısı (event-as-node) kapsam
  dışı — gbot'un mevcut memory_relations yeterli sinyal taşıyor.

---

## [1.23.0] - 2026-05-09 — Faz 22G: Memory roadmap (5 features)

Five memory features ship together: explicit lifecycle states,
persona/style memory, an interactive relations graph in the
dashboard, Obsidian vault export, and an opt-in LLM-rerank for
retrieval. ~30 saatlik iş, 5 commit dizisi, 434 test geçer.

### Aşama 1 — 4-state lifecycle (memory_facts)

`memory_facts` now carries an explicit ``state`` column instead of
relying on implicit ``valid_until + importance`` signals. PRAGMA
user_version 22 → 23.

- **States:** ACTIVE / WEAK / INHIBITED / ARCHIVED
- **Schema:** new ``state``, ``inhibited_until``, ``last_accessed_at``
  columns + ``idx_facts_state``. Backfill maps existing rows from
  prior implicit logic.
- **Decay:** ``apply_decay`` flips ACTIVE→WEAK at fade and
  WEAK→ARCHIVED at archive; auto-restores INHIBITED rows whose
  hold expired.
- **Retrieval:** ``search_similar_facts`` filters
  ``state IN ('active', 'weak')`` and skips active inhibits.
- **Helpers:** ``inhibit_fact(hold_days=7)``, ``restore_fact``,
  ``get_facts(state=)``.
- **Admin:** ``GET /admin/memory/{user}/facts?state=``,
  ``POST .../facts/{id}/inhibit``, ``POST .../facts/{id}/restore``.
- 10 new tests.

### Aşama 2 — Persona / style memory

New `style` fact_type captures how the user prefers to communicate
(tone, length, formality, language mix, emoji use). Slowest decay
of all types (180d fade / 540d archive).

- ``_DEFAULT_DECAY_RATES`` gets a ``style`` entry.
- ``MemoryService._extract_typed_facts`` accepts ``style`` alongside
  the existing four types.
- ``workspace/memory_schema.md`` documents the type, sample facts,
  and the extraction trigger.
- ``ContextBuilder`` injects a ``USER STYLE:`` block in user_context
  separate from semantic/episodic facts; it's always present (not
  query-similarity gated) so the LLM sees voice preferences every
  turn.
- 4 new tests.

### Aşama 3 — D3/cytoscape relations graph (dashboard)

Memory page Relations tab gains a Graph view alongside the existing
Table. Nodes are canonical entities, edges are relations; node size
scales with degree.

- `cytoscape@^3.33`, `react-cytoscapejs@^2.0`, `cytoscape-cose-bilkent`.
- ``dashboard/src/components/RelationsGraph.tsx`` (~220 LOC) with
  filter, click-to-inspect side panel, cose-bilkent layout.
- No backend change — uses the existing
  ``GET /admin/memory/{user}/relations`` endpoint.
- ``style`` added to FACT_TYPES filter so persona facts show up in
  the table.

### Aşama 4 — Obsidian vault sync

`memory_entity_pages.content_md` is already markdown — now it can
sync to a local Obsidian vault on a configurable cron schedule with
YAML frontmatter (entity, compiled_at, synced_at, source_facts).

- ``gbot/memory/obsidian_sync.py`` — ``ObsidianSyncer.run(user_id)``.
- New cron processor ``memory_obsidian_sync`` in
  ``gbot/core/cron/scheduler.py``.
- Lifespan bootstrap registers a recurring task per user when
  ``memory.obsidian_sync.enabled``.
- Admin endpoint ``POST /admin/memory/{user}/obsidian-sync/run``
  for manual triggers.
- Off by default. 5 new tests.

### Aşama 5 — Engram-style LLM rerank (opt-in)

ContextBuilder picks up an opt-in LLM-based re-ranker that asks the
model to score the candidate pool against the actual query text
instead of the static multiplicative formula.

- ``MemoryRetrievalConfig.llm_rerank`` config block (off by default,
  cheap fail-safe to static formula).
- ``ContextBuilder._llm_rerank()`` — JSON-mode prompt, robust JSON
  parsing, partial-result handling (fills from static tail), full
  exception fallback. Sync caller drives the async LLM call via
  threaded ``asyncio.run`` so the existing sync builder API stays
  intact.
- 5 new tests covering happy path, partial result, parse failure,
  exception, empty input.

### Tests

434 total (was 429). All gbot-eval suites unchanged.

### Migration

PRAGMA user_version bumps from 22 to 23. Backfill is automatic and
idempotent. No config changes required for the default-off
features (Obsidian sync, LLM rerank). ``style`` fact_type is
silently accepted by extraction; existing fixtures keep working.

---

## [1.22.0] - 2026-05-09 — Faz 22F: gbot-eval YAML refactor

gbot-eval mimarisi "Python-first" → "YAML-first with Python escape
hatch" yapısına geçti. Yeni suite eklemek için kod yazma; bir YAML
dosyası ekle, biten. 11 alt-step ile geldi.

### Added — scoring DSL & runners

- `scoring/` paketi — 22 built-in `kind` (regex_match/_not_match,
  substring_any/_all/_none, tool_called/_not_called/_count_min/
  no_tool_call, required_args, arg_substring_any, json_valid/
  _keys/_types/_array_min/_nested_keys, bullet_count, numbered_list,
  word_count, sentence_count, judge, python). Async dispatcher
  awaits coroutine handlers (judge) and unwraps sync handlers.
- `scoring/expr.py` — `kind: python` escape hatch with AST-validated
  restricted exec. No imports, no os/sys/subprocess, no dunder
  attribute probing. Locals: text, tool_calls, case, call.
- `scoring/builtins._fold(s, "turkish")` — Turkish-aware ASCII fold
  (İ→i, ş→s, ç→c, ğ→g, ü→u, ö→o + lowercase) so substring rules
  with `fold: turkish` accept Turkish or English variants.
- `runners/` paketi — 6 runner: chat_completion (generic),
  stress_long_context (30-turn dialog), multi_turn (state-threaded),
  memory_extraction / memory_audn / memory_page_compile / delegation
  (gbot-bound, soft import-guard).
- `suites/base.YamlBackedSuite` — declarative wrapper that reads
  `name / runner / requires_gbot / cases` from a YAML and dispatches
  to the registered runner.

### Added — new suite

- `agent.multi_turn` (5 cases × 2-3 turns) — multi-turn dialog
  coherence. Covers language switch + callback, number tracking,
  topic shift without bleed, self-correction, and pronoun
  resolution. Closes the gap that none of the 9 prior suites
  addressed (all were single-turn).

### Added — CLI

- `gbot-eval models refresh` — pulls live pricing from OpenRouter's
  `/api/v1/models` and persists to `output/pricing_overrides.json`.
  Refreshed 367 models on first run.
- `gbot-eval list` — split output into standalone vs gbot-bound
  suites. Standalone ones run without gbot installed.

### Added — reasoning auto-handling (Step 6K)

`capture.reasoning_off_kwargs(model, mode)` returns
`{"reasoning": {"effort": "none"}}` for known reasoning models
(Kimi-K2, MiniMax-M2, DeepSeek-R1) when `mode="auto"` (default for
standalone suites). gbot-bound suites keep production parity (no
auto-disable). YAML can override per case or per suite with
`disable_reasoning: false|auto|true`.

### Changed

- All 10 suites are now YAML files in `gbot_eval/suites/*.yaml`.
  No more Python class per suite. Adding a suite = writing one
  YAML file.
- `memory.page_compile` quality is now a weighted composite
  (40% keyword_coverage + 30% no-hallucination + 15% format
  adherence + 15% citation_recall). Fixes the v1.21.0 bug where
  the legacy `_aggregate` produced 0.00 despite per-case 0.85.
- `agent.tool_calling.tc01` Turkish-fold rule means English-arg
  responses ("Istanbul weather") now score 1.00 instead of 0.50.

### Removed

- `gbot_eval/ASSESSMENT.md` — content folded into README's
  Architecture / Known limitations sections.
- 8 legacy Python suite files + their JSON fixtures (data lives
  in YAML now).
- `suites/_metrics.py` (moved to `scoring/memory_metrics.py`).
- `suites/_memory_helpers.py` (runner does its own bootstrap).

### Tests

- 410 passing — gbot core untouched.
- Live smoke (gemini-3-flash-preview) confirms baselines hold:
  general 1.00, agent.* 0.73-1.00, memory.audn 0.93-1.00,
  memory.extraction 0.80, memory.page_compile composite ~0.85,
  stress.long_context 1.00, agent.multi_turn 1.00.

---

## [1.21.1] - 2026-05-09 — Faz 22E Step 5K: LiteLLM removed

The empirical bench in v1.21.0's `gbot-eval bench-providers` showed
LiteLLM and OpenRouter SDK tied on quality and cost; OpenRouter SDK
was equal or faster on latency, and production already routed 100% of
traffic through OpenRouter. This release drops the dead-code path:

### Changed

- `gbot/core/providers/litellm.py` — facade rewritten to use
  OpenRouter SDK exclusively. Module name kept (so every existing
  `from gbot.core.providers import litellm as llm_provider` callsite
  works unchanged), but the implementation no longer imports the
  ``litellm`` package. Tolerant init: if ``OPENROUTER_API_KEY`` is
  missing, ``setup_provider`` warns rather than crashing — the
  ``RuntimeError`` surfaces on the first ``achat`` call.
- `gbot/core/config/loader.py` — explicit ``load_dotenv()`` call
  preserves the .env auto-load behaviour LiteLLM provided
  transitively.
- LiteLLM-related comments cleaned up across `nodes.py`, `runner.py`,
  `schema.py`, `openrouter_llm.py`.

### Removed

- `gbot/core/providers/litellm_llm.py` — the LiteLLM-backed provider.
  Test coverage migrated to `tests/test_provider.py` (now
  OpenRouter-only).
- `gbot_eval/bench_providers.py` + `gbot_eval/fixtures/bench_providers.json`
  + `gbot-eval bench-providers` CLI command — provider race is over,
  archived run dirs still hold the historical matrix data for
  reference.
- `litellm>=1.0.0` from `pyproject.toml` dependencies. Wheel size
  drops by ~50MB.

### Tests

- 410 passing (was 409); +1 new test for the no-key behaviour. All
  unchanged test suites stay green.
- `tests/test_provider.py` rewritten — strategy-pattern tests
  replaced with OpenRouter-only setup / forwarding / error-handling tests.

### Migration notes

If you have custom code that imports LiteLLM internals from gbot, the
facade import path is unchanged (`from gbot.core.providers import
litellm as llm_provider`). If you were relying on LiteLLM's
provider-agnostic abstraction with Anthropic / OpenAI keys directly,
set ``OPENROUTER_API_KEY`` instead — OpenRouter routes upstream
transparently.

---

## [1.21.0] - 2026-05-09 — Faz 22E: Hardening + gbot-eval framework

Five steps land together. Operational hardening on the memory layer
(prompt caching, scheduled maintenance, retrieval benchmark,
artifact-trail summaries) plus a brand-new evaluation CLI that
benchmarks every LLM call gbot makes.

### Added — gbot-eval (`gbot_eval/` standalone CLI package)

`gbot-eval` is a sibling to `gbot-cli`: a Typer entry point that runs
8 evaluation suites against any OpenRouter-routable model and reports
quality / cost / latency in a Rich matrix. Lives at
`gbot_eval/`, registered as `gbot-eval` in `pyproject.toml`.

Suites (8 — see `gbot_eval/README.md`):
- `memory.extraction`, `memory.audn`, `memory.page_compile`
- `agent.delegation`, `agent.tool_calling`, `agent.structured`,
  `agent.instruction` (regex + LLM-as-judge)
- `stress.long_context` (30-turn stub needle-in-haystack)

Cross-cutting infra:
- `pricing.py` — built-in $/1M token table + user override file
  (`gbot-eval models add`)
- `capture.py` — wraps every LLM call with token / latency / cost
- `judge.py` — fixed-model (claude-haiku-4.5) LLM-as-judge for
  subjective adherence checks
- `reporting.py` — Rich tables, matrix aggregator, A/B compare

CLI commands: `run`, `list`, `list-runs`, `matrix`, `compare`,
`bench-providers`, `models [add]`, `baseline [set|diff]`, `clean`.

Sampling: `--sample=N%` deterministic prefix slice for cost-saving
smoke tests; recorded in `manifest.json`.

Provider decision: empirical bench (4 models × 12 cases × 2 providers)
showed LiteLLM and OpenRouter SDK tied on quality/cost; OpenRouter
SDK won on latency in reasoning-model paths (Kimi K2.6 reasoning ON:
9s vs 25s p95). Decision: OpenRouter SDK is sole provider going
forward. LiteLLM is dead code, removal scheduled.

Reasoning gotcha discovered: disable thinking with
`reasoning={"effort": "none"}`, NOT `{"enabled": false}` — OpenRouter
silently dismisses the latter.

Baseline (gemini-3-flash-preview, full run): mean quality 0.80
across 8 suites, ~$0.012 / 4-min wall-clock.

### Added — Faz 22E hardening (Steps 1-4, previously parked)

- **Prompt caching** (`gbot/agent/nodes.py`): system messages get
  `cache_control: {"type": "ephemeral"}` for Anthropic + Gemini 2.5
  models; LiteLLM auto-injects the rest. Provider-gated, length-gated.
  Cache hit telemetry written to logs.
- **Auto-maintenance scheduling** (`gbot/api/app.py`,
  `gbot/core/cron/scheduler.py`): per-user daily + weekly memory
  maintenance cron jobs registered idempotently at startup. Default
  04:00 daily / Sunday 04:30 weekly.
- **`tests/memory_benchmark/`** — internal LOCOMO-mini retrieval
  benchmark (recall@K, MRR, latency, tokens). Opt-in with
  `pytest -m benchmark`. Baseline: recall@10=0.947 on 30-fact
  fixture.
- **ARTIFACT_TRAIL** — memory agent's session-summary prompt now
  includes an "ARTIFACTS" section that lists concrete outputs (code,
  plans, decisions) produced in the session.

### Removed

- `tests/llm_eval/` pytest suite — superseded by `gbot_eval/` standalone
  CLI. Memory eval cases migrated to `gbot_eval/fixtures/memory_*.json`.

### Tests

- 412+ pytest regression suite stays green.
- `gbot-eval run` produces a deterministic, comparable output for
  each model.

---

## [1.20.1] - 2026-05-08 — Faz 22D Part 3: Admin API + Dashboard

Operational layer for everything that landed in v1.19/v1.20: admin
endpoints to inspect/manage relations, entity pages, and maintenance,
plus a Memory dashboard with proper tabs for each.

### Added — Admin endpoints

All owner-only:

- `GET /admin/memory/{user_id}/relations[?entity=]` — valid relations,
  optionally filtered by canonical entity.
- `GET /admin/memory/{user_id}/entities` — distinct canonicals with
  relation counts. Useful for picking targets.
- `GET /admin/memory/{user_id}/entity-pages[?only_fresh]` — compiled
  pages with provenance.
- `POST /admin/memory/{user_id}/pages/recompile?entity=` — manual
  recompile, bypasses debounce. Returns `ok: false` when
  `entity_pages.enabled` is off.
- `DELETE /admin/memory/{user_id}/entity/{entity}` — cascade-archive
  forget.
- `POST /admin/memory/{user_id}/maintenance/run` — trigger daily +
  weekly maintenance for a user immediately.
- `GET /admin/memory/{user_id}/retrieval-debug?query=&top_k=` — embed
  a query and return raw distance scores for every candidate fact, with
  an `above_gate` flag. Tuning aid for the distance threshold.

### Added — Dashboard

- **Memory page rebuilt as tabs**: `Facts | Relations | Entity Pages | Retrieval Debug`.
  - Facts tab is the previous flat view (unchanged behaviour).
  - Relations tab: canonical entity chips for filtering, raw vs canonical
    annotations on each row.
  - Entity Pages tab: rendered markdown cards with version/stale badge,
    `Recompile` and `Forget` buttons (forget asks for confirmation —
    cascades into facts/relations, audit-safe).
  - Retrieval Debug tab: query box → distance histogram with per-row
    above-gate marker.
- TS types: `MemoryRelation`, `MemoryEntityPage`. New API client methods
  in `dashboard/src/api/admin.ts`.

### Tests

374 passing (was 368; +6 admin endpoint smoke tests).

---

## [1.20.0] - 2026-05-08 — Faz 22D Part 2: LLM Entity Pages + Maintenance + Forgetting

Memory layer evolves from "data pile + retrieval" to "organized knowledge".
The earlier v1.19.0 made existing relations come alive; this release adds
the **dynamic, LLM-compiled entity pages** (Karpathy LLM-Wiki pattern),
periodic housekeeping, and an entity-level forget operation.

### Added

- **`workspace/memory_schema.md`** — extraction contract pulled out of the
  agent prompt into a public, editable file. Categories, fact types, AUDN
  rules, and the relation vocabulary are all documented here. The memory
  agent's `AGENT.md` now references it instead of duplicating the rules.
- **`memory_entity_pages` CRUD** — `upsert_entity_page`, `get_entity_page`
  (auto-tracks access), `list_entity_pages`, `mark_entity_pages_stale`,
  `mark_pages_stale_by_fact`, `delete_entity_page`. UNIQUE on
  `(user_id, entity_canonical)`; provenance via JSON `source_fact_ids`
  and `source_relation_ids`.
- **`gbot/memory/entity_pages.py`** — `EntityPageCompiler`. Async,
  debounced (60s) LLM compiler. Triggered from
  `MemoryService.extract_and_save` whenever facts/relations touch an
  entity. Per-entity coalescing prevents thrash on rapid mentions.
  Eligibility threshold (≥ N facts/relations) skips weak entities.
- **Entity-page injection in ContextBuilder** —
  `_render_entity_pages_block()` injects compiled pages as
  `## {Entity}\n{markdown}` after the relations block. Stale pages are
  surfaced with a `*(stale — recompile pending)*` marker so the agent
  knows the freshness state. Token sub-budget configurable.
- **`invalidate_fact` lifecycle hook** — when a fact is invalidated,
  every page citing it gets `stale=1` automatically. Compiler picks them
  up on the next debounce or in the daily catch-up.
- **`gbot/memory/maintenance.py`** — `MemoryMaintenance` replaces the
  dead `consolidation.py`. Daily pass: type-aware decay + stale-page
  recompile catch-up + orphan-page cleanup. Weekly pass: relations
  dedup catch-up. Runs via the unified `background_tasks` scheduler;
  `run_now()` exposed for admin trigger.
- **Type-aware decay** — `apply_decay` now uses per-`fact_type` rates:
  - `episodic`: 14-day fade, 60-day deeper fade
  - `procedural`: 60 / 180 days
  - `semantic`: 90 / 365 days
  - `preference`: 120 / 365 days

  Yesterday's events fade fast; "is vegetarian" stays for a year. Returns
  per-type counts in the stats dict.
- **`forget_entity`** — store-level cascade: invalidates every fact
  mentioning the entity (canonical + aliases), invalidates every
  relation involving it, hard-deletes the entity page. Audit-safe (facts
  archived via `valid_until`, supersede chain intact).
- **`forget_entity` agent tool** — natural-language entry point for
  "Murat'ı tamamen unut" / "İstanbul ile ilgili her şeyi sil" kind of
  requests. Memory tool count: **11 → 12**.
- **20 new tests** — `test_entity_pages.py` (12 — store CRUD, stale
  hook, compiler eligibility, debounce, provenance, forget cascade) +
  `test_tools.py` updated (12 memory tools).

### Changed

- **`memory/AGENT.md`** rewritten — references `memory_schema.md` instead
  of duplicating rules. Adds Task 4: "Entity page compilation" with the
  exact output format.
- **Deleted `gbot/memory/consolidation.py`** — dead code (never wired,
  never tested). The merge-overlapping-facts path duplicated AUDN's job;
  decay moved to `maintenance.py` where it actually runs.
- **`MemoryService.__init__`** accepts `entity_compiler=`. Wired in
  `GraphRunner._create_memory_service`. Disabled compilers self-noop.

### Configuration additions

```yaml
memory:
  entity_pages:
    enabled: false                # default-off — flip when ready
    model: "openrouter/openai/gpt-4o-mini"
    debounce_seconds: 60
    min_facts_for_page: 3
    min_relations_for_page: 2
    max_input_tokens: 1000
    max_output_tokens: 200
    max_pages_in_context: 3
```

### Rollback

- `memory.entity_pages.enabled: false` — disables compile + injection.
- Decay invocation is opt-in (only `MemoryMaintenance` calls it). Skip
  the daily cron to revert to no-decay behavior.
- The deleted `consolidation.py` was dead code; removal is a no-op for
  any deployment.

### Tests

368 unit tests passing (was 354; +14 net).

---

## [1.19.0] - 2026-05-07 — Faz 22D Part 1: Backlinks Revival + Distance Gate

Memory layer evolves toward an Obsidian-style backlinks graph and a
Karpathy LLM-Wiki-inspired entity-page layer (Part 2 in v1.20.x). This
release focuses on **making existing memory data come alive**: deduping
relations, normalizing entities, and finally consuming the relations
graph from the agent context.

### Added

- **`memory_relations` partial UNIQUE index** on
  `(user_id, source_entity, relation, target_entity) WHERE valid_until IS NULL`.
  Re-asserts after invalidation are still legal; live duplicates are not.
- **`canonical_source` / `canonical_target` columns** on `memory_relations`.
  Raw surface forms are preserved in `source_entity` / `target_entity`
  for audit; canonical names are written separately.
- **`memory_entity_aliases` table** — per-user surface→canonical map. Auto
  populated by `EntityResolver`; will accept manual entries from the
  dashboard in Faz 22E.
- **`memory_entity_pages` table** — schema only in this release; the
  compiler arrives in v1.20.0.
- **`gbot/memory/entities.py`** with `EntityResolver` — three-tier
  resolution: (1) owner self-references (`Ömer/Kullanıcı/User/owner/ben`
  → owner.username), (2) per-user alias table, (3) identity fallback.
  Idempotent, case-insensitive, preserves raw forms.
- **Distance gate in semantic retrieval** — `search_similar_facts(...)`
  accepts `max_distance: float | None`. Applied inside the SQL CTE so
  the rerank pool never sees distant noise. Default `0.45`, configurable
  via `memory.retrieval.max_distance`.
- **Backlinks injection in ContextBuilder** —
  `_build_relationships_block()` detects canonical entities mentioned in
  retrieved facts and inserts a `RELATIONSHIPS:` block per entity.
  Configurable via `memory.relations.{enabled, max_entities_per_turn,
  max_relations_per_entity}`. The previously-orphan `memory_relations`
  table now reaches the agent context.
- **Opportunistic backfill** at app startup —
  `EntityResolver.backfill_relations()` populates `canonical_*` for any
  legacy rows missing them. Cheap and idempotent.
- **PRAGMA user_version = 22** as the migration guard. Re-running
  `_init_db()` on an already-migrated DB is a no-op.
- **17 new tests** — `test_entities.py` (10) + `test_relations_dedup_migration.py` (7).

### Changed

- **`add_relation` semantics** — switched from `INSERT OR REPLACE` (which
  collided on `relation_id` only) to `INSERT ... ON CONFLICT DO UPDATE`
  on the live-row UNIQUE index. Re-asserting an existing live triple
  now updates `confidence`/`source_fact`/`canonical_*` instead of
  duplicating.
- **`get_relations`** accepts a new `canonical=` parameter. Use it once
  the entity has been resolved (preferred over raw `entity=`).
- **MemoryService** accepts an optional `EntityResolver`. When present,
  every relation is canonicalized before insert. Wired in `GraphRunner._create_memory_service`.

### Migration impact

Tested on the live DB (155 raw relations):
- **155 → 94** rows after dedup (39% were live duplicates).
- **41/94** relations canonicalized to the owner identity (Ömer / Kullanıcı / User / owner — all collapse to `owner.username`).
- Live verification: `"Murat ne yapıyor?"` and `"Zeynep ile ilişkim?"`
  now produce a `RELATIONSHIPS` block in the agent context.

### Configuration additions

```yaml
memory:
  retrieval:
    max_distance: 0.45         # null disables the gate
    top_k_candidates: 20
    top_k_final: 10
  relations:
    enabled: true
    max_entities_per_turn: 3
    max_relations_per_entity: 8
```

### Rollback

Set `memory.relations.enabled: false` and `memory.retrieval.max_distance: 2.0`
to revert to v1.18.x behavior. Schema migrations are non-destructive
and stay; the only way to undo them is a DB reset.

### Tests

354 unit tests passing (was 337; +17 new).

---

## [1.18.2] - 2026-05-07 — Config loader: env vars now override YAML

### Fixed

- **Critical: env vars were not overriding YAML.** `Config(**yaml_data)` passes
  YAML as init kwargs, which take priority over pydantic-settings env loading.
  Result: `${JWT_SECRET_KEY}` placeholder in `config.example.yaml` resolved
  literally (17-char string), and `GBOT_AUTH__JWT_SECRET_KEY` env var was
  silently ignored. README's "auth disabled by default" claim did not hold on
  a fresh install.
- **Security side-effect:** because the literal `"${JWT_SECRET_KEY}"` was
  truthy, `auth_enabled` returned `True` but JWT operations had no real
  secret — `/chat` accepted requests without tokens. Fix restores the
  documented `auth disabled` ↔ `auth enabled` contract.

### Changed

- `gbot/core/config/loader.py`: build base Config from env first, then merge
  YAML only for keys not already set via env (`GBOT_*`). Empty YAML strings
  are stripped so env vars can fill in.
- `config/config.example.yaml`: replaced `${VAR}` placeholders (which never
  resolved) with empty strings; env vars are now the canonical source.
- `.env.example`: documented `GBOT_AUTH__JWT_SECRET_KEY` and
  `GBOT_CHANNELS__WHATSAPP__API_KEY` as the right way to override.

---

## [1.18.1] - 2026-04-06 — Bugfixes & Polish

### Fixed

- **Delegation JSON truncation:** Gemini Flash hangs on `json_schema` + `strict: true` + `anyOf` with free objects (produces 67K whitespace). Replaced with simple `json_object` response format. Root cause confirmed as Gemini bug (tested via OpenRouter SDK directly).
- **vec_memory_facts UNIQUE constraint:** SQLite rowid reuse after fact invalidation caused INSERT failures. Added DELETE-before-INSERT for idempotent vec writes.
- **Owner password on fresh DB:** When auth enabled, owner had no password and couldn't login. Added `owner.password` config field — startup sets initial password if DB has none, never overwrites existing.

### Changed

- **AGENT.MD (memory):** Fully rewritten in Turkish. Mandatory categories (10 types), mandatory relations extraction, AUDN DELETE action documented.
- **README.md:** Added owner password documentation in auth section.
- **config.example.yaml:** Added `owner.password` field with default value.

### Added

- **notes/journal.md:** Bug journal with root causes and solutions — referenced from CLAUDE.md.
- **notes/test.md:** 27 E2E manual test scenarios (17 direct + 10 conditional).
- **notes/archive/:** Old/completed notes moved to archive with README.

---

## [1.18.0] - 2026-03-21 — Faz 22C: Decay, Relations, Memory Tools

### Added

- **2-stage retrieval:** ContextBuilder searches 20 candidates via sqlite-vec → re-ranks by `similarity × retrieval_strength` (recency × access_count × confidence) → top 10 enter context.
- **Access tracking:** `batch_increment_access()` — facts entering context get access_count +1. Frequently accessed facts score higher in re-ranking.
- **Decay logic:** `apply_decay()` in store.py — 30+ day old facts with 0 access lose importance, <0.1 importance → archived. Available but not auto-triggered (Faz D).
- **`memory_relations` table:** Entity relationships extracted from conversations. `add_relation()`, `get_relations()`, `invalidate_relation()`.
- **Relation extraction:** AGENT.MD updated — relations zorunlu, 8 relation types (works_at, works_with, lives_in, owns, married_to, knows, uses, studies).
- **`MemoryConsolidator`:** Event-driven consolidation class (merge + decay). Fact merge disabled pending threshold tuning (Faz D).
- **Memory tools:** `search_memory` (semantic search), `forget_fact` (invalidate by query), `what_do_you_know` (category-grouped list). 26 total tools.

### Changed

- **AGENT.MD fully Turkish:** 10 zorunlu category, relation examples, merge prompt, AUDN Türkçe.
- **Extraction model:** Back to Gemini Flash (better category/relation quality than gpt-4o-mini).
- **`_extract_typed_facts()` returns tuple:** `(facts, relations)` — relations parsed and saved alongside facts.
- **`make_memory_tools()`:** Accepts `embedder` parameter for search_memory/forget_fact.
- **`make_tools()`:** Accepts `embedder` parameter, passed through to memory tools.


## [1.17.0] - 2026-03-21 — Faz 22B: Semantic Retrieval + AUDN

### Added

- **sqlite-vec integration:** Vector search in SQLite — `vec_memory_facts` virtual table with cosine distance.
- **`MemoryEmbedder`:** Sync OpenRouter embedding client (`google/gemini-embedding-001`, 3072d). Config: `memory.embedding`.
- **AUDN update logic:** Embedding finds similar facts → LLM decides ADD/UPDATE/DELETE/NOOP. Config: `memory.update` (strategy, model).
- **DELETE action:** "Artık X yapmıyorum" → old fact invalidated, no negative fact added.
- **Query-aware context:** ContextBuilder embeds last user message → semantic search → most relevant facts in context.
- **Temporal user_notes:** Notes processed → transferred to memory_facts via AUDN → always deleted after processing.
- **`MemoryEmbeddingConfig` + `MemoryUpdateConfig`:** Full config for embedding provider/model and update strategy/thresholds.
- **Embedding benchmark notebook:** `notebooks/embedding_benchmark.ipynb` — 21 models tested for Turkish semantic similarity.

### Changed

- **`memory.update.strategy` default:** `llm` (was `cascading`). Embedding finds, LLM decides — most accurate.
- **`MemoryService`:** Now accepts `config` and `embedder` parameters. AUDN replaces exact string dedup.
- **`ContextBuilder`:** Accepts `embedder` parameter, `build_layers()` takes `last_message` for semantic retrieval.
- **`store.add_fact()`:** New `embedding` parameter — stores vector in same transaction.
- **`store.invalidate_fact()`:** Also removes vector from `vec_memory_facts`.
- **`store.search_similar_facts()`:** CTE + `k=` syntax for sqlite-vec KNN with user/validity filtering.


## [1.16.0] - 2026-03-20 — Faz 22A: Memory Layer

### Added

- **`memory_facts` table:** Typed fact storage with confidence, importance, category, keywords. 4 fact types: semantic, episodic, preference, procedural.
- **`memory_processing_log` table:** Audit trail for extraction runs (trigger, counts, duration).
- **`MemoryService`:** Unified memory processing — session summarization + fact extraction via `agents.yaml` memory profile.
- **Memory agent profile:** `config/agents.yaml` memory entry + `workspace/agents/memory/AGENT.md` prompt.
- **Hot-path extraction:** Every N user messages (configurable `background.memory.extraction_every_n`, default 5), fire-and-forget async extraction.
- **`MemoryConfig`:** Root-level `memory` config section — enabled, model, extraction_every_n, max_facts_per_user, embedding (provider/model/dimension), update (strategy/model/thresholds).
- **ContextBuilder:** `user_context` layer now includes "LEARNED FACTS" from `memory_facts` alongside explicit notes/prefs/favs.
- **Admin API:** `GET /admin/memory/{user_id}` — facts, stats, processing log. Stats include `memory_facts` count.
- **Dashboard Memory page:** Facts table with type filter tabs, notes/preferences/favorites panel, processing log.
- **Embedding config:** `memory.embedding` — provider (openrouter/local), model (`google/gemini-embedding-001`), dimension (3072). Benchmarked 21 models for Turkish semantic similarity.
- **Update strategy config:** `memory.update` — cascading (default), llm_only, threshold_only. Thresholds tuned per embedding benchmark (noop >0.90, add <0.65).

### Changed

- **Unified `user_notes` table:** `user_notes` + `preferences` + `favorites` → single `user_notes` table with `note_type` column (note/preference/favorite). All legacy method signatures preserved.
- **Runner `_rotate_session()`:** Delegates to `MemoryService.process_session()` via fire-and-forget `asyncio.create_task`. Runner no longer imports `asummarize`/`aextract_facts` directly.
- **Extraction decoupled from `litellm_llm.py`:** MemoryService uses `achat` with memory agent profile, not hardcoded prompts.

### Removed

- **`_save_extracted_facts()` in runner.py** — Logic moved to `MemoryService.extract_and_save()`.
- **Backward compat writes:** MemoryService no longer duplicates facts into `user_notes` — `memory_facts` and `user_notes` are separate layers.


## [1.15.0] - 2026-03-20 — Faz 21: Unified BackgroundTask

### Changed

- **Unified task tables:** 5 scheduling tables (`cron_jobs`, `reminders`, `background_tasks`, `cron_execution_log`, `delegation_log`) merged into 2 tables: `background_tasks` (unified) + `task_executions` (audit log). Auto-migration preserves all existing data.
- **`background_tasks` schema:** `execution_type` (immediate/delayed/recurring/monitor) + `processor` (static/function/agent/runner) columns replace implicit table-based classification.
- **`BackgroundTask` model:** Replaces `CronJob` in `gbot/core/cron/types.py`. `CronJob` kept as alias for backward compatibility. Accepts `job_id` and `reminder_id` as aliases for `task_id`.
- **Unified `_execute_task()`:** `CronScheduler._execute_job()` and `_execute_reminder()` merged into single `_execute_task()` method.
- **Proactive messages as `role="system"`:** Background task results recorded to session as `SystemMessage` — agent sees delivery info but doesn't repeat content.
- **API:** `/admin/crons` → `/admin/tasks` with `execution_type` and `status` query filters. Stats response: `cron_jobs` → `recurring_tasks`, `reminders` → `pending_delayed`.
- **Dashboard Tasks page:** Renamed from "Crons", shows all task types with Active/All/Completed filter tabs.
- **SKILL.md (scheduling):** Simplified — all scheduling through `delegate` tool, no more `create_reminder`/`add_cron_job` references.

### Removed

- **`gbot/agent/tools/cron_tool.py`** — `add_cron_job`, `list_cron_jobs`, `remove_cron_job`, `create_alert` tools deleted. `delegate` handles all scheduling.
- **`gbot/agent/tools/reminder.py`** — `create_reminder`, `list_reminders`, `cancel_reminder` tools deleted.
- **`activity_logs` table** — Never implemented, references removed from docs and CLAUDE.md.
- **ContextBuilder `events` layer** — Removed (duplicate with session history recording).

### Fixed

- **Proactive message duplication:** Agent no longer repeats background task results as its own message. System role prevents confusion.
- **Function processor recording:** `_record_proactive_message` now called for all processor types including `function` (previously skipped when `response=None`).

## [1.14.1] - 2026-03-19

### Improved

- **CronScheduler proactive message recording:** Cron jobs and reminders now record their responses to the user's active session via `_record_proactive_message()` helper.

## [1.14.0] - 2026-03-14

### Added (Faz 20: Context Service, Admin Dashboard & API)

- **`gbot/agent/context/` package:** Restructured from single file to package — `models.py`, `builder.py`, `service.py` with backward-compatible `__init__.py` re-exports
- **`LayerResult` Pydantic model:** Per-layer inspection with name, description, source, content, chars, tokens, budget, truncated, enabled fields
- **`ContextOverride` Pydantic model:** Runtime layer override definition (content + enabled)
- **`ContextBuilder.build_layers()`:** Returns `dict[str, LayerResult]` — layer-by-layer breakdown with content. `mark_delivered` parameter controls event side-effects. `template_vars` parameter for planner identity injection
- **`_LAYER_META` dict:** Description and source metadata for each layer — full traceability in dashboard
- **Empty layer support:** Layers with no content (role, agent_memory, events) still included in `build_layers()` output for dashboard visibility
- **`ContextService`:** Unified facade for context inspection across all 3 agent types (main, planner, light) with runtime override support. Accepts optional `registry` for tool definition token calculation
- **`get_profile_context_layers()`:** Profile-aware layer filtering — reads `context_layers` from agents.yaml (`["*"]` = all, `[identity]` = minimal)
- **Informational layers:** `message_history` (session messages) and `tool_definitions` (ToolRegistry OpenAI schemas) added to dashboard for complete LLM input visibility
- **Planner/light context via layers:** `get_planner_context()` and `get_light_context()` return `dict[str, LayerResult]` for unified dashboard display
- **8 new admin API endpoints:**
  - `GET /admin/context/{profile}/layers` — layer-by-layer content + stats (all 3 profiles)
  - `GET /admin/context/{profile}/preview` — full rendered context string
  - `GET /admin/context/budget` — token budget breakdown
  - `GET /admin/context/overrides` — list all active overrides
  - `POST /admin/context/overrides` — set runtime layer overrides
  - `DELETE /admin/context/overrides/{layer}` — clear layer override
  - `GET /admin/context/profiles` — list agent profiles
  - `GET /admin/context/profiles/{name}` — profile detail + AGENT.md content
- **Admin Dashboard (React):** Separate web UI for monitoring and inspection
  - React 19 + TanStack Query + Zustand + Tailwind CSS 4 + Lucide icons + Vite
  - Pages: Dashboard, Context (layer inspector), Conversations (session browser + message history), Users, Tools, Crons, Settings
  - Context page: per-profile layer view with description, source, content, token/char stats
  - Conversations page: owner can view all users' sessions and message history
  - Dark/light theme with system preference detection
  - JWT auth integration (login page with GBot branding)
  - Deployed as separate Docker container (nginx:alpine), proxies API via `/api/` rewrite
- **`dashboard/` directory:** Complete React project — Dockerfile, nginx.conf, API client, stores, components
- **GBot logo:** SVG logo (`gbot_logo.svg`) + PNG (`logo.png`) + favicon (`dashboard/public/favicon.png`)
- **Owner session bypass:** Owner can view any user's sessions and message history (not just their own)
- **`/admin/users` role field:** User list now includes RBAC role from database
- **14 context service tests:** build_layers, side-effects, overrides, service methods, planner/light layer format

### Changed

- **`ContextBuilder.build()`:** Now delegates to `build_layers()` — same signature, same output
- **`ContextBuilder.get_context_stats()`:** Delegates to `build_layers()`, removes ~70 lines of duplicate logic
- **Skills index split:** `skills` and `skills_index` now separate LayerResult entries for finer inspection
- **`docker-compose.yml`:** Added dashboard service (gbot-dashboard, port 3001, depends on graphbot)
- **`app.py`:** ContextService now receives `registry` parameter for tool token calculation
- **`routes.py`:** Owner bypass — `current_user != config.owner_user_id` check for session/history access
- **README.md:** Complete rewrite — GBot branding, logo, dashboard section, context service docs, agent profiles, updated project structure

## [1.13.0] - 2026-03-14

### Added (Faz 19: AGENT.md & Skills Gözden Geçirme)

- **`config/` directory:** All YAML configs consolidated — `config.yaml`, `roles.yaml`, `agents.yaml` in single directory with backward-compatible fallback to root
- **`config/agents.yaml`:** Agent profile system — each agent type (main, planner, light) defines which AGENT.md and skills to use
- **`gbot/agent/profiles.py`:** AgentProfile loader with global cache, `get_agent_md()`, `get_agent_skills()`, `get_template_vars()` (same pattern as permissions.py)
- **`workspace/agents/planner/AGENT.md`:** Planner prompt extracted from Python to Markdown — template vars `{tool_catalog}` and `{extra_examples}` preserved
- **`workspace/agents/light/AGENT.md`:** Base context for LightAgent — identity, language rules, background task guidelines
- **`gbot/agent/skills/builtin/scheduling/SKILL.md`:** Scheduling decision tree extracted from main AGENT.md — available via progressive disclosure
- **`load_skill` tool:** Progressive disclosure — agent loads full skill instructions on demand instead of always-in-context
- **`skills` tool group:** Added to ToolRegistry and roles.yaml (owner + member)
- **11 profile tests:** Loading, fallback, cache, AGENT.md resolution, skill filtering

### Changed

- **`ContextBuilder`:** Profile-aware — accepts `profile` parameter, identity resolves from profile AGENT.md, skills filtered by profile config
- **`ContextBuilder._get_identity()`:** 5-level priority chain: prompt_template > system_prompt > profile AGENT.md > workspace/AGENT.md > persona config
- **`DelegationPlanner`:** Loads prompt from profile AGENT.md, falls back to `_PLANNER_PROMPT` constant
- **`LightAgent`:** Prepends base context from profile before task prompt
- **`workspace/AGENT.md`:** Scheduling section replaced with `load_skill("scheduling")` reference (83 → 41 lines, ~50% smaller)
- **Skills index instruction:** `read_file` → `load_skill(skill_name)` for skill loading
- **`docker-compose.yml`:** Single `./config:/app/config:ro` volume mount replaces two separate mounts
- **`load_config()`:** Resolution order now checks `config/config.yaml` before `config.yaml`
- **`_load_roles_yaml()`:** Resolution order now checks `config/roles.yaml` before `roles.yaml`

## [1.12.0] - 2026-02-28

### Added (LLM Provider Refactor — Strategy Pattern)

- **`BaseLLMProvider` ABC:** Abstract base class for LLM providers — `achat()` interface
- **`OpenRouterLLM` provider:** Direct OpenRouter SDK integration — bypasses LiteLLM adapter, `response_format` passes through without stripping
- **`LiteLLMLLM` provider:** Extracted existing LiteLLM logic into standalone class (Moonshot thinking, summarize, extract_facts)
- **`openrouter` SDK dependency:** `openrouter>=0.7.0` in pyproject.toml
- **13 provider tests:** Factory routing, AIMessage conversion, reasoning normalization, tool call parsing, facade delegation

### Changed

- **`litellm.py` → facade module:** Global provider instances (`_main_provider`, `_fallback_provider`), routes `openrouter/*` models to OpenRouterLLM, others to LiteLLMLLM — zero caller changes
- **`setup_provider()` routing:** Init-time provider selection based on model prefix, `os.environ` fallback for OpenRouter API key
- **`_RESPONSE_SCHEMA` nullable fields:** `type: ["string", "null"]` → `anyOf` syntax for OpenRouter JSON schema compatibility
- **Channel injection:** Always overrides with `state["channel"]` — LLM can no longer set wrong channel (was causing reminders to go to `api` instead of `telegram`)
- **`delegate` tool return message:** Includes execution details (cron expr, delay, one-shot) so LLM reports accurately instead of inventing details

### Fixed

- **`response_format` stripping:** LiteLLM adapter was silently dropping `response_format` for OpenRouter models → DelegationPlanner ~40% empty JSON. Direct SDK fixes this.
- **`max_tokens=512` truncation:** Planner JSON responses were being cut off, causing parse failures and silent fallback to `immediate/agent`. Removed hard limit.
- **Channel delivery bug:** LLM was setting `channel: 'api'` for Telegram/WhatsApp requests — reminders delivered to wrong channel. Now always injected from state.
- **Planner error logging:** Added exception details and raw text (first 300 chars) to parse failure warnings
- **Misleading delegation confirmation:** When planner fell back to defaults, LLM still told user "setup complete" with fabricated details. Delegate tool now returns actual plan details.

## [1.11.0] - 2026-02-23

### Added (Delegation Refactor & WhatsApp DM)

- **Unified `delegate` tool:** Single tool replaces old delegate/reminder/cron split — routes to worker (immediate), scheduler (delayed/recurring/monitor)
- **3 processor types:** `static` (plain text), `function` (direct tool call, no LLM), `agent` (LightAgent with tools)
- **json_schema structured output:** `response_format` forces valid JSON from planner LLM — eliminates parse failures
- **`list_scheduled_tasks` tool:** Lists active cron jobs and pending reminders
- **`cancel_scheduled_task` tool:** Cancel by `cron:<id>` or `reminder:<id>` prefix
- **`LightAgent.run_with_meta()`:** Returns `(response, tokens, called_tools)` for observability
- **`delegation_log` table:** Records every planner decision (execution, processor, reference_id)
- **WhatsApp DM respond:** `respond_to_dm=true` + `allowed_dms` whitelist — bot responds to DMs from listed numbers
- **DM sender context:** `[WhatsApp DM from {sender_name}]` prefix so LLM knows who it's chatting with
- **Tool catalog full description:** `get_tool_catalog()` now includes full description (up to 300 chars) with shortcuts visible to planner
- **Test scenarios doc:** `senaryolar.md` — 10 delegation test scenarios with architecture overview
- **36 delegation tests:** Planner parse, delegate routing, processor execution, list/cancel tools, delegation log

### Changed

- **Planner prompt examples:** Weather scenarios use `web_fetch` with shortcuts instead of `web_search`
- **Agent processor channel injection:** Scheduler appends `IMPORTANT: set channel='{channel}'` to prompt for non-telegram channels
- **Function processor channel injection:** Scheduler injects `channel` into `tool_args` when missing
- **Agent delivery model:** Agent processor returns `(text, False)` — agent delivers via `send_message_to_user`, scheduler does NOT double-send
- **`_parse_tools()` no filter:** All tools including `send_message_to_user` pass through to LightAgent
- **`send_message_to_user` channel fallback:** Tries specified channel → whatsapp → telegram
- **Background task channel:** `SubagentWorker` passes `fallback_channel` to `create_background_task`
- **`CronJob` model:** Added `processor` and `plan_json` fields
- **`roles.yaml`:** Added `delegation` group for delegate/list/cancel tools

### Fixed

- **WhatsApp channel routing:** LightAgent now uses correct channel (was defaulting to telegram)
- **Double message bug:** Each processor type has exactly one delivery path — no duplicates
- **Tool catalog truncation:** Planner couldn't see `web_fetch` shortcuts (was showing only first line of description)

## [1.10.0] - 2026-02-21

### Added (WhatsApp Channel — WAHA Integration)

- **WAHA REST API client:** `WAHAClient` async client — `send_text()`, `get_session_status()`, phone↔chat_id conversion helpers
- **WhatsApp webhook handler:** `POST /webhooks/whatsapp/{user_id}` — full message processing pipeline (like Telegram)
- **Global webhook:** `POST /webhooks/whatsapp` — auto-routes by sender phone via `user_channels` table, only processes allowed groups
- **Allowed groups:** `allowed_groups` config list — bot only sees and responds to messages in specified groups
- **`[gbot]` response prefix:** All bot responses prefixed with `[gbot]` to distinguish from real messages
- **Loop prevention:** Bot's own `[gbot]` messages (fromMe) are skipped to prevent infinite loops
- **DM config flags:** `respond_to_dm` and `monitor_dm` — configurable DM behavior (both default `false`)
- **Duplicate event filtering:** `message.any` only used for `fromMe` messages, `message` for regular incoming (prevents double processing)
- **Non-chat filtering:** Newsletter (`@newsletter`), broadcast (`@broadcast`) messages ignored — only `@c.us` and `@g.us` accepted
- **Message splitting:** `split_message()` splits long responses at paragraph boundaries (WhatsApp 4096 char limit)
- **Scheduler integration:** `_send_to_channel()` supports WhatsApp — proactive messaging via WAHA for cron/reminders
- **WhatsApp send tool:** `send_whatsapp_message` in messaging tools — send messages to saved WhatsApp contacts
- **WAHA Docker service:** `docker-compose.yml` includes WAHA container with health check
- **35 tests:** WAHAClient helpers, message splitting, webhook handler (group/DM/filtering/session), global webhook routing

### Changed

- **`WhatsAppChannelConfig`:** Replaced Baileys fields (`bridge_url`) with WAHA fields (`waha_url`, `session`, `api_key`, `allowed_groups`, `respond_to_dm`, `monitor_dm`)
- **Session isolation:** WhatsApp messages stored in WhatsApp-specific sessions, never leak to Telegram/API sessions

## [1.9.0] - 2026-02-21

### Added (Web Tools & Multi-Provider)

- **Web search 4-provider fallback:** DuckDuckGo (free) → Tavily (free 1000/mo) → Moonshot $web_search → Brave Search API
- **DuckDuckGo search:** Primary free search provider, no API key needed, `asyncio.to_thread()` wrapper
- **Tavily search:** AI-optimized search as second fallback, free tier 1000 requests/month
- **Moonshot $web_search:** Kimi built-in web search as third fallback ($0.005/call)
- **`web_fetch` shortcut system:** Tag-based data access — `web_fetch("gold")` resolves to API URL from config
- **`fetch_shortcuts` in config.yaml:** Configurable shortcut → URL mapping, no hardcoded URLs in code
- **7 default shortcuts:** gold, currency, weather:istanbul, weather:ankara, weather:izmir, earthquake, news
- **`WebToolConfig.fetch_shortcuts`:** New config field (`dict[str, str]`) for deployment-specific shortcuts
- **Tool call debug logging:** `reason` node logs tool call names, `execute_tools` logs execution and results
- **`reasoning_content` preservation:** Thinking model output saved in `AIMessage.additional_kwargs`, restored in conversation history for tool call round-trips

### Changed

- **Model switched to MiniMax M2.5:** `openrouter/minimax/minimax-m2.5` — output 3x cheaper than Kimi K2.5 ($1.10 vs $3.00 per 1M tokens)
- **OpenRouter provider activated:** `OPENROUTER_API_KEY` in `.env`, provider config in `config.yaml`
- **`reasoning_effort` parameter:** Added to `litellm.achat()` for thinking models via OpenRouter
- **`web_fetch` docstring dynamic:** Tool description auto-generated from config shortcuts at startup
- **`.env` key renamed:** `OPEN_ROUTER_KEY` → `OPENROUTER_API_KEY` (LiteLLM convention)

### Removed

- **$web_search injection from litellm.py:** Moonshot-specific code moved to `_moonshot_search()` in web.py
- **`crypto` and `bist` shortcuts:** Removed (user preference + broken API)

## [1.8.0] - 2026-02-19

### Added (Tool Registry & Management)

- **ToolRegistry class:** Central tool registry — single source of truth for tool metadata, groups, and availability
- **ToolInfo dataclass:** Per-tool metadata (group, requires, available) for introspection
- **`register_group()`:** Factory functions register tools under named groups automatically
- **`register_unavailable()`:** Dynamic tools (scheduling, delegation) registered as known-but-unavailable when dependencies missing
- **`validate_roles()`:** Startup validation — detects unknown groups in roles.yaml, logs warnings
- **`GET /admin/tools`:** New admin endpoint for tool catalog introspection (names, groups, availability, dependencies)
- **2 new tests:** `test_registry_validate_roles`, `test_registry_groups_summary`

### Changed

- **`make_tools()` returns `ToolRegistry`** instead of `list[BaseTool]` — all consumers updated
- **`roles.yaml` simplified:** Tool names completely removed; only role → groups + context_layers + max_sessions remain. Tool-to-group mapping now comes from code (ToolRegistry)
- **`permissions.py`:** `get_allowed_tools()` accepts optional `registry` parameter — resolves tool names from registry groups instead of YAML
- **`GraphRunner`:** Uses ToolRegistry for RBAC resolution; accepts `list | ToolRegistry | None` for backward compatibility
- **`app.py` lifespan:** Startup validates roles.yaml groups against registry, logs warnings for unknown groups
- **`build_background_registry()`:** New function extracts background-safe subset from main ToolRegistry
- **Test updates:** `test_make_tools_all` → `test_make_tools_returns_registry` (dynamic assertions, no hardcoded counts)

### Improved

- **Adding a new tool now requires 1 file change** (was 5): add function to factory, it's auto-registered in the correct group
- **No more tool name sync between code and YAML** — ToolRegistry is the single source of truth

## [1.7.0] - 2026-02-19

### Added (Session Summarization & Fact Extraction)

- **`asummarize()`:** Hybrid format session summary (narrative paragraph + structured bullets: TOPICS/DECISIONS/PENDING/USER_INFO)
- **`aextract_facts()`:** Structured JSON extraction (preferences + notes) from conversation, saved to DB
- **`_rotate_session()` rewrite:** LLM-based summary + fact extraction + robust fallback on errors
- **3 preference tools:** `set_user_preference`, `get_user_preferences`, `remove_user_preference` in memory group
- **`remove_preference()`:** New MemoryStore method for preference deletion
- **Session summarization policy doc:** `docs/session_summarization.md`

### Fixed

- **Closed session reuse bug:** When a closed session_id is sent, a new session is created instead of reusing the dead one

## [1.6.0] - 2026-02-15

### Added (CLI Enhancement — API Client + Rich REPL)

- **`gbot` CLI entry point:** Terminal command renamed `graphbot` → `gbot` (`graphbot` kept as alias)
- **`gbot_cli/` package:** CLI code moved from `gbot/cli/` to a separate `gbot_cli/` package
- **GraphBotClient:** Sync httpx wrapper for all API endpoints (health, chat, login, sessions, user, admin)
- **Credentials:** Token storage at `~/.gbot/credentials.json` with `chmod 0600`
- **Interactive REPL:** Rich-rendered chat shell with robot logo, markdown output, spinner, auto session management
- **Slash command autocomplete:** Real-time `/` completion via `prompt_toolkit`
- **Slash commands:** `/help`, `/status`, `/session`, `/history`, `/context`, `/config`, `/skill`, `/cron`, `/user`, `/events`, `/clear`, `/exit`
- **Rich formatters:** Table/panel renderers for sessions, users, crons, skills, config, events, history
- **Admin API:** `GET /admin/status`, `/admin/config`, `/admin/skills`, `/admin/users`, `/admin/crons`, `/admin/logs`, `DELETE /admin/crons/{job_id}` (owner-only)
- **`login` / `logout` commands:** Save/clear credentials for API authentication
- **Default REPL:** `gbot` (bare, no arguments) opens REPL directly
- **System user fallback:** When not logged in, uses `getpass.getuser()` for OS username
- **23 new tests** (test_cli_client.py, test_cli_repl.py, test_admin_api.py)

### Changed

- **`chat` command reworked:** Defaults to API-backed REPL mode; `--local` flag preserves standalone mode; `--server`, `--token`, `--api-key` flags for connection config; `-m` for single-shot API calls
- **`app.py`:** Admin router registered
- **`gbot/cli/` removed:** All CLI code moved to `gbot_cli/` package, old directory deleted

## [1.5.0] - 2026-02-15

### Added (Docker & Deploy)

- **Dockerfile:** `uv:python3.11-bookworm-slim` base image, `.[channels]` included, healthcheck, `graphbot` CLI entrypoint
- **docker-compose.yml:** Single service, named volumes (`graphbot_data`, `graphbot_workspace`), `config.yaml` read-only bind mount, `.env` env_file
- **.dockerignore:** Excludes `reference files/`, `data/`, `.venv/`, `tests/`, `gbot/` etc. from container

### Fixed

- **config.yaml:** Fixed `channels.telegram.enabled: true1` typo → `true`

## [1.4.0] - 2026-02-15

### Added (Delegation Planner)

- **DelegationPlanner:** Single LLM call to plan subagent execution — picks tools, prompt, and model automatically based on task description
- **DelegationConfig:** New config section (`background.delegation`) with `model` and `temperature` fields for the planner LLM
- **Tool Registry:** `build_background_tool_registry()` — shared name→tool mapping for background agents (subagent + cron), excludes meta/unsafe tools (delegate, cron, reminder, shell)
- **`resolve_tools()`:** Centralized tool name→object resolution, replaces duplicate logic in worker and scheduler
- **`get_tool_catalog()`:** Human-readable tool catalog string for the delegation planner prompt
- **17 new tests** (test_delegation.py) — registry, resolve, catalog, planner parse, scheduler fix, delegate+planner integration

### Changed

- **Delegate tool simplified:** Main LLM now only calls `delegate(user_id, task)` — planner decides tools/prompt/model instead of main agent
- **SubagentWorker:** Uses shared tool registry (built once in `__init__`), accepts `prompt` parameter from planner, explicit model fallback to `config.assistant.model`
- **CronScheduler._parse_tools:** Now uses shared tool registry instead of returning empty list — cron jobs with `agent_tools` can now resolve tools correctly
- **make_tools():** Added `planner` parameter, passed to `make_delegate_tools(worker, planner)`
- **app.py lifespan:** Creates registry + catalog + planner, wires into tool factory

### Fixed

- **CronScheduler `_parse_tools()` always returned `[]`:** Cron/alert jobs using LightAgent couldn't access tools (e.g. web_search for price alerts). Now resolved via shared registry.
- **`model=null` crash in LightAgent:** When planner LLM returned `"model": "null"` (string instead of JSON null), LightAgent passed literal `"null"` to litellm causing BadRequestError. Fixed with string sanitization in `_parse()` + explicit model fallback in worker and scheduler.

## [1.3.0] - 2026-02-15

### Added (WebSocket Events & Recurring Reminders)

- **Recurring reminders:** `cron_expr` column on reminders table — periodic reminders via CronTrigger, stays "pending" (not marked sent)
- **`create_recurring_reminder` tool:** Create periodic reminders with cron expressions, no LLM processing
- **WebSocket event delivery:** `ConnectionManager` class for real-time push of cron/reminder/subagent results to connected clients
- **WS fallback:** If no WebSocket connected, events stored in `system_events` table for polling/context injection

### Changed

- **SubagentWorker → LightAgent:** Subagents now use lightweight LightAgent instead of full GraphRunner — isolated context, restricted tools, model override
- **Delegate tool:** Added `tools` and `model` parameters — main agent decides subagent capabilities at delegation time
- **`_resolve_tools()`:** Converts tool name strings to actual tool objects for subagent (web_search, web_fetch, search_items, save_user_note)
- **CronScheduler._send_to_channel:** WS push for API channel + DB fallback when not connected
- **SubagentWorker._run:** WS push on task completion + mark_events_delivered
- **`list_reminders` tool:** Shows recurring info (cron expression) for periodic reminders

### Fixed

- **`make_search_tools` call:** Fixed wrong argument count in `_resolve_tools()` — was passing `(config, db)`, function takes `(retriever)`

## [1.2.0] - 2026-02-15

### Added (LightAgent & Background Task Refactoring)

- **LightAgent:** Lightweight, isolated agent for background tasks — own prompt, restricted tools, model override, no context loading
- **NOTIFY/SKIP:** LLM response markers (SKIP, [SKIP], [NO_NOTIFY]) to suppress unnecessary cron notifications
- **skip_context:** Flag in GraphRunner.process() to load identity-only prompt for background tasks
- **create_alert tool:** Cron job with NOTIFY/SKIP template — only notifies when something needs attention
- **Execution log:** cron_execution_log table tracks every cron run (result, status, duration_ms)
- **Failure tracking:** consecutive_failures counter on cron jobs, auto-pause after 3 failures
- **Standalone reminders table:** Separate from cron_jobs, with status tracking (pending/sent/failed/cancelled) and retry logic
- **system_events table:** Event queue for background → agent communication, injected into context on next user session
- **background_tasks table:** SubagentWorker results persisted to DB + system_event created on completion
- **Agent params on cron jobs:** agent_prompt, agent_tools, agent_model, notify_condition columns

### Changed

- CronScheduler._execute_job() uses LightAgent when agent_prompt is set, falls back to full runner
- SubagentWorker now accepts optional db parameter for result persistence
- Reminders use standalone table (not cron_jobs), no LLM involved
- ContextBuilder injects undelivered system_events as "Background Notifications" layer
- make_cron_tools() returns 4 tools (was 3, added create_alert)

## [1.0.0] - 2025-02-07

### Added (Initial Release)

- **Core:** LangGraph-based stateless agent with 4-node graph (load_context → reason ⇄ execute_tools → respond)
- **Core:** GraphRunner orchestrator — SQLite ↔ LangGraph bridge, request-scoped
- **Core:** Config system — YAML + pydantic-settings + .env overlay
- **Core:** LiteLLM multi-provider support (OpenAI, Anthropic, DeepSeek, Groq, Gemini, OpenRouter)
- **Memory:** SQLite store with 10 tables (users, sessions, messages, agent_memory, user_notes, activity_logs, favorites, preferences, user_channels, cron_jobs)
- **Memory:** ContextBuilder with 6 layers (identity, agent_memory, user_ctx, prev_summary, skills, skills_index)
- **Memory:** Token-based sessions (30k limit, LLM summary on transition)
- **API:** FastAPI service with chat, sessions, health, user context endpoints
- **API:** Token-based authentication (register/login)
- **API:** WebSocket support for real-time chat
- **Channels:** Telegram, Discord, WhatsApp, Feishu multi-channel support
- **Tools:** 9 tool groups — memory, notes, favorites, activity, filesystem, shell, web search, cron, sub-agent
- **Skills:** YAML-based skill system with requirements checking and dynamic index
- **Background:** Cron scheduler for reminders/alarms with proactive messaging
- **Background:** Heartbeat service and sub-agent worker
- **RAG:** Optional FAISS-based retrieval with multilingual embeddings
- **CLI:** Typer-based CLI with interactive chat, config check, version commands
