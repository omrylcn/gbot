# Memory Architecture

GBot'un hafıza katmanı — şu an nasıl çalışıyor, planda ne var, araştırmaya
göre ne eklenebilir. Faz 22A→22G (v1.16.0–v1.23.0) ile inşa edilen bu
katman, A-Mem akademik temeli ve Engram open-source çizgisiyle %95
hizalı; Karpathy LLM-Wiki paterninden entity-page kavramını alıyor.

> **Pratik kullanım için** → `docs/memory-usage.md`. Bu dosya iç
> mimari ve tasarım kararları odaklı; günlük operasyon ve config
> opt-in flag'leri ayrı belgede.

---

## 1. Tasarım felsefesi

İki paradigmayı birleştiriyoruz:

| Paradigma | Kaynak | GBot karşılığı |
|---|---|---|
| **Yapılandırılmış bilgi grafiği** | A-Mem, Engram, Mem0 | `memory_facts` (typed facts) + `memory_relations` (entities + relations) |
| **LLM-derlenmiş özet sayfaları** | Karpathy LLM-Wiki, memU | `memory_entity_pages` (per-entity markdown summaries) |

Tek dosyada SQLite (WAL) + sqlite-vec virtual table. Hiçbir external
dependency yok (Postgres, Redis, Qdrant, Neo4j hiçbiri yok). 8 tablo,
~300 KB index dahil tipik kullanıcı için.

Üç temel ilke:

1. **SQLite single source of truth** — fact, relation, page, embedding
   hepsi aynı dosyada, aynı transaction'da.
2. **Embedding bulur, LLM karar verir** — vector similarity sadece
   "benzerleri getir" işine yarar; ADD/UPDATE/DELETE/NOOP kararı LLM'de.
3. **Audit-safe** — invalidate edilen fact `valid_until` set'lenir, satır
   silinmez (supersede zinciri korunur). GDPR hard-delete ayrı yol.

---

## 2. Tablolar (8)

```
┌─────────────────────────────────────────────────────────────┐
│ memory_facts                                                │
│   fact_id, user_id, content, fact_type, category,           │
│   confidence, importance, access_count,                     │
│   valid_from, valid_until, superseded_by,                   │
│   keywords, source, source_session, source_channel          │
│   state ∈ {active, weak, inhibited, archived}  ◄ Faz 22G   │
│   inhibited_until, last_accessed_at             ◄ Faz 22G   │
│   created_at, updated_at                                    │
└─────────────────────────────────────────────────────────────┘
        │
        ├─ rowid = vec_memory_facts.rowid (1:1)
        │
┌──────────────────────────────────┐
│ vec_memory_facts (vec0 virtual)  │
│   rowid, embedding[3072]         │
│   distance_metric=cosine         │
└──────────────────────────────────┘

┌─────────────────────────────────────────────────────────────┐
│ memory_relations                                            │
│   relation_id, user_id,                                     │
│   source_entity, relation, target_entity,                   │
│   canonical_source, canonical_target,                       │
│   confidence, valid_from, valid_until,                      │
│   source_fact, created_at                                   │
│                                                             │
│   UNIQUE INDEX (user_id, source, relation, target)          │
│   WHERE valid_until IS NULL  -- partial: live rows only     │
└─────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────┐
│ memory_entity_aliases                                       │
│   user_id, surface_form, canonical_form, source             │
│   PK (user_id, surface_form)                                │
└─────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────┐
│ memory_entity_pages                                         │
│   page_id, user_id, entity_canonical,                       │
│   content_md (LLM-compiled markdown),                       │
│   source_fact_ids JSON, source_relation_ids JSON,           │
│   fact_count, relation_count,                               │
│   version, stale, last_compiled_at, last_accessed_at,       │
│   access_count                                              │
│                                                             │
│   UNIQUE (user_id, entity_canonical)                        │
└─────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────┐
│ memory_processing_log                                       │
│   user_id, session_id, trigger,                             │
│   facts_extracted, facts_added, facts_updated,              │
│   facts_invalidated, duration_ms, processed_at              │
└─────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────┐
│ user_notes (unified — Faz 22A: notes + prefs + favs → 1)    │
│   id, user_id, note_type, content, key, metadata, created_at│
│   note_type ∈ {note, preference, favorite}                  │
└─────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────┐
│ agent_memory                                                │
│   key, value, updated_at  -- bot-wide long-term k/v          │
└─────────────────────────────────────────────────────────────┘
```

**Toplam: 8 tablo + 1 virtual.** Hepsi aynı `gbot.db` dosyasında.
**PRAGMA user_version: 23** (Faz 22G ile bumped).

`memory_facts.fact_type ∈ {semantic, episodic, preference, procedural,
style}` — A-Mem'in bilişsel taksonomisi + Faz 22G'de eklenen `style`
(LinkedIn Cognitive Memory paterni).
`memory_facts.category` 11 sabit değer (location, work, tech, personal,
preference, interest, habit, finance, health, relationship, style) →
`workspace/memory_schema.md`'de tanımlı (Karpathy schema-as-document
paterni).

`memory_facts.state` (Faz 22G) explicit lifecycle:
- **active** — taze, retrieval'da tam ağırlık
- **weak** — fade sonrası, retrieval'a giriyor ama düşük öncelik
- **inhibited** — geçici dışlama (`inhibited_until` lapse'ına kadar);
  `apply_decay` otomatik geri alır
- **archived** — `valid_until` set, retrieval dışı (audit-only)

---

## 3. Yazma akışı (write path) — A'dan Z'ye

```
1. Kullanıcı mesaj atar (Telegram / WhatsApp / API / WS)
       │
       ▼
2. Runner.process(user_id, message, session_id)
       │ (LangGraph agent loop: load_context → reason ⇄ execute_tools → respond)
       ▼
3. respond node — yanıt kullanıcıya döner. Kullanıcı bekletilmedi.
       │
       ▼
4. _maybe_extract_facts():
       Session içindeki user mesaj sayısını saymak için sessions tablosuna bak.
       n % extraction_every_n == 0 ise (default n=5):
       │
       ▼
5. asyncio.create_task(self._extract_facts_bg(user_id, last_10_msgs, session_id))
       │ FIRE-AND-FORGET — kullanıcı response'unu beklemedi.
       ▼
6. _create_memory_service():
       - EntityResolver(db, owner_username, owner_display_name)
       - EntityPageCompiler(db, config.memory, resolver)
       - MemoryService(db, model, config, embedder, resolver, entity_compiler)
       │
       ▼
7. MemoryService.extract_and_save(user_id, messages, session_id, "hot_path")
       │
       ▼
8. _extract_typed_facts(messages):
       LLM call (config.memory.model | assistant.model — default Gemini 3 Flash)
       Response_format=json_object, prompt'u workspace/agents/memory/AGENT.md
       (memory_schema.md'yi reference ediyor).
       Returns: (raw_facts: list, raw_relations: list)
       │
       ▼
9. RELATIONS LOOP (raw_relations için her bir):
       resolver.canonicalize(user_id, surface)  ← 3-tier:
           1. Owner self-references (Ömer/Kullanıcı/User → owner.username)
           2. memory_entity_aliases tablosu
           3. Identity fallback
       db.add_relation(canonical_source, canonical_target, ...)
       ON CONFLICT (user_id, source, relation, target) WHERE valid_until IS NULL
           DO UPDATE SET confidence, source_fact, canonical_*
       │ (raw_entity, surface_form korunur — audit için)
       ▼
10. FACTS LOOP (raw_facts için her bir):
       │
       │ a) Embed:
       │    embedder.embed(fact.content)  ← OpenRouter API call
       │    (google/gemini-embedding-001, 3072-dim, ~100ms)
       │
       │ b) Search similar:
       │    db.search_similar_facts(user_id, embedding, top_k=5)
       │    (sqlite-vec CTE + cosine distance)
       │
       │ c) AUDN decision:
       │    Eğer similar yoksa → ADD (atla d'ye)
       │    LLM call (config.memory.update.model — gpt-4o-mini)
       │    Returns: {"action": "add|update|delete|noop", "target_fact_id"}
       │
       │ d) Action execution:
       │    ADD     → db.add_fact() + vec_memory_facts INSERT (atomik)
       │    UPDATE  → invalidate_fact(old, superseded_by=new) + add_fact(new)
       │    DELETE  → invalidate_fact(old, superseded_by=None)
       │              (negatif fact ekleme — "X bıraktım" → eski sil, yeni yok)
       │    NOOP    → atla
       ▼
11. Compute touched_entities (canonical names from raw_relations)
       │
       ▼
12. log_memory_processing(facts_extracted, facts_added, facts_updated,
                          facts_invalidated, duration_ms)
       │
       ▼
13. ENTITY PAGE ENQUEUE (config.memory.entity_pages.enabled ise):
       For each canonical in touched_entities:
           compiler.enqueue(user_id, canonical):
               db.mark_entity_pages_stale(canonical)  ← anında stale=1
               60s debounce — task = asyncio.create_task(_wait_then_compile)
               Yeni enqueue gelirse önceki task cancel edilir (coalescing).
       │
       │ ... 60 saniye sessizlik sonra ...
       ▼
14. EntityPageCompiler._compile(user_id, canonical):
       │ a) gather context:
       │    - surface_forms = resolver.expand(canonical) → tüm bilinen yazımlar
       │    - facts = valid facts whose content mentions any surface form
       │      (substring match, importance × access ile sıralı)
       │    - relations = db.get_relations(canonical=canonical)
       │
       │ b) eligibility:
       │    facts < min_facts_for_page (default 3) AND
       │    relations < min_relations_for_page (default 2) → SKIP
       │    son compile <5dk önce + stale=0 → SKIP (idempotent)
       │
       │ c) LLM call:
       │    config.memory.entity_pages.model (default gpt-4o-mini)
       │    Prompt: 1 paragraf (≤80 kelime) + 3-7 fact bullet'ı
       │            (her bullet sonu [fact_id:xxx] citation)
       │    Çelişki: en güncel valid fact'i kullan
       │
       │ d) persist:
       │    db.upsert_entity_page(content_md, source_fact_ids JSON,
       │                          source_relation_ids JSON, ...)
       │    version + 1, stale = 0
       ▼
15. process_temporal_notes(user_id):
       For each user_note (note_type='note'):
           AUDN pipeline aynı şekilde — embed → search → LLM karar
           Sonra: db.delete_user_note() — temporal buffer temizlenir.
       (Bu adım her hot-path'te çalışır; user_notes geçici yer.)
```

**Tetikleyiciler:**

| Tetikleyici | Sıklık | Konum |
|---|---|---|
| Hot-path | Her N user mesajda (default 5) | `runner.py:_maybe_extract_facts` |
| Session close | Token limit aşıldığında | `runner.py:_rotate_session` |
| Manuel | Admin endpoint | `POST /admin/memory/{user_id}/maintenance/run` |

---

## 4. Okuma akışı (read path) — context build

Her kullanıcı mesajında çalışır. Sync (~100-200ms toplam: 1 embed call
+ SQL).

```
1. Kullanıcı mesaj → ContextBuilder.build_layers(user_id, last_message)
       │
       ▼
2. Layer'lar sırayla inşa edilir (her birinin token budget'ı var):
       │
       ├─ identity      (priorities.identity = 500 token)
       ├─ runtime       (~50 token: user_id, datetime, model)
       ├─ role          (RBAC role — owner/member/guest description)
       ├─ agent_memory  (~500 token: bot-wide long-term k/v)
       │
       ├─ user_context  (priorities.user_context = 1500 token):
       │     │
       │     ▼
       │  2.1 EXPLICIT — db.get_user_context(user_id):
       │      - notes (user_notes WHERE note_type='note', limit=20)
       │      - preferences (note_type='preference')
       │      - favorites (note_type='favorite')
       │      Render: "USER NOTES: ...\nPREFERENCES: ...\nFAVORITES: ..."
       │
       │  2.2 STYLE — Faz 22G, kullanıcının iletişim tonu:
       │      style_facts = db.get_facts(fact_type='style', limit=8)
       │          (query-similarity gating UYGULANMAZ — her turn'de göster)
       │      Render: "USER STYLE:\n- Kısa cevap tercih ediyor\n- ..."
       │      LLM her cevabı bu stil sinyallerine göre şekillendirir.
       │
       │  2.3 LEARNED — 2-stage retrieval:
       │      embedder.embed(last_message)  ← 1 API call
       │      candidates = db.search_similar_facts(
       │          top_k=20, max_distance=0.45)
       │          ← SQL CTE: vec_memory_facts MATCH + JOIN memory_facts
       │             WHERE valid_until IS NULL
       │               AND state IN ('active', 'weak')             ◄ Faz 22G
       │               AND (inhibited_until IS NULL OR expired)    ◄ Faz 22G
       │               AND distance ≤ 0.45
       │      facts = _rerank_facts(candidates, top_k=10):
       │          for each fact:
       │              recency = max(0.1, 1.0 - days_old/365)
       │              access = min(1.0, access_count/10)
       │              strength = recency × (0.3 + 0.7×access) × confidence
       │              similarity = 1.0 - distance
       │              final_score = similarity × strength
       │          sort desc, take top_k
       │
       │      OPSİYONEL (memory.retrieval.llm_rerank.enabled — Faz 22G):
       │          candidates_pool=30 (statik formula yerine 20)
       │          LLM call (memory.model — gemini-3-flash):
       │              "Bu sorgu için en alakalı {top_k} fact'i sırala"
       │              JSON: {"ranked": [<index>, ...]}
       │          On error → static formula'ya graceful fallback.
       │          Cost: ~$0.0002/query, latency +0.5-1.5s.
       │
       │      Render: "LEARNED FACTS:\n- ..."
       │      db.batch_increment_access(fact_ids)  ← feedback loop
       │
       │  2.4 BACKLINKS (config.memory.relations.enabled ise):
       │      _detect_canonical_entities(facts):
       │          - Tek SQL: kullanıcı için tüm canonical entity set'i çek
       │          - Her fact.content içinde substring match
       │          - Mention sayısına göre top-N (default N=3)
       │      _render_relationships_block(entities):
       │          For each entity:
       │              db.get_relations(canonical=entity, limit=8)
       │              Render: "**Entity**:\n  - src → relation → tgt"
       │      Block: "RELATIONSHIPS:\n..."
       │
       │  2.5 ENTITY PAGES (config.memory.entity_pages.enabled ise):
       │      _render_entity_pages_block(entities):
       │          For top max_pages_in_context (default 3) entity:
       │              page = db.get_entity_page(canonical)  ← access++
       │              Render: "## Entity\n{content_md}"
       │              Stale page → "## Entity *(stale — recompile pending)*"
       │      Block: "ENTITY PAGES:\n..."
       │
       │  Combined: explicit + style + learned + relationships + entity_pages
       │  (USER STYLE en üstte — voice, sonra raw fact'ler, en altta
       │  derlenmiş; LLM "voice → raw → derived" okur, stale page fresh
       │  fact'i ezme riski azalır)
       │
       ├─ session_summary (önceki session özeti — anchored summary pattern)
       ├─ skills (always-on skill content — workspace/skills/*/SKILL.md)
       └─ skills_index (load_skill için katalog)
       │
       ▼
3. ContextService — token budget enforcement, layer truncation cascade.
       Cap aşılırsa: relationships → facts → pages sırasıyla trim.
       │
       ▼
4. Final system prompt → LLM (graph node "reason")
```

---

## 5. Bakım (maintenance) — `MemoryMaintenance`

`MemoryConsolidator` (eski adıyla `consolidation.py`) silindi (dead code).
Yerine `gbot/memory/maintenance.py`.

```
MemoryMaintenance.run_daily(user_id):
    1. Type-aware decay with explicit state transitions (db.apply_decay):
         For each fact_type in {episodic, procedural, semantic,
                                preference, style}:        ◄ Faz 22G
             Stage 1 fade — older than fade_days, access_count=0,
                            state='active'
                 → importance × fade_factor
                 → state = 'weak'                          ◄ Faz 22G
             Stage 2 fade — older than archive_days, still untouched
                 → importance × archive_factor
         Archive — importance < threshold (default 0.1)
                 → valid_until = now()
                 → state = 'archived'                      ◄ Faz 22G
         Auto-restore — INHIBITED rows whose hold expired   ◄ Faz 22G
                 → state = 'active', inhibited_until = NULL

         Defaults:
             episodic    14d × 0.7  / 60d × 0.4
             procedural  60d × 0.85 / 180d × 0.6
             semantic    90d × 0.85 / 365d × 0.6
             preference  120d × 0.9 / 365d × 0.7
             style       180d × 0.92 / 540d × 0.75   ◄ Faz 22G (slowest)

    2. Stale page recompile catch-up:
         For each page WHERE stale=1:
             compiler.compile_now(canonical)  ← bypass debounce
         (Debouncer çoğunu yakalar; bu sadece process restart kaçıranlar için.)

    3. Orphan page cleanup:
         For each page:
             source_fact_ids içindeki tüm fact'ler invalid mı?
             Evetse → db.delete_entity_page() (sayfa türetilmiş, kaynaksız kalmasın)

MemoryMaintenance.run_weekly(user_id):
    Relations dedup catch-up — UNIQUE constraint öncesi sızanlar için
    (normalde olmaz; insurance).

MemoryMaintenance.run_now(user_id):
    Daily + weekly birden — admin endpoint için manuel tetik.
```

**Otomatik schedule (Faz 22E Step 2):** her kullanıcı için
APScheduler ile `daily-maintenance-{user}` (default `0 4 * * *`) ve
`weekly-maintenance-{user}` (default `30 4 * * 0`) cron job'ları
startup'ta `_ensure_maintenance_jobs` ile idempotent olarak kayıt
edilir. Manuel tetik hâlâ
`POST /admin/memory/{user_id}/maintenance/run` üzerinden mümkün.

**Lifecycle helpers (Faz 22G admin endpoints):**

- `POST /admin/memory/{user}/facts/{id}/inhibit?hold_days=7` —
  geçici dışlama; varsayılan 7 gün sonra `apply_decay` otomatik
  geri alır.
- `POST /admin/memory/{user}/facts/{id}/restore` — INHIBITED → ACTIVE
- `GET /admin/memory/{user}/facts?state=weak|inhibited|archived` —
  state'e göre filtrelenmiş liste.

---

## 6. Forget cascade

`forget_entity(user_id, canonical)` — bir entity'nin GBot'taki tüm
izlerini arşivler:

```
1. Relations:
     UPDATE memory_relations
     SET valid_until = now()
     WHERE canonical_source/target = canonical
        OR raw source/target = canonical (eski kayıtlar için)

2. Facts:
     surface_forms = {canonical} ∪ aliases
     for each valid fact:
         if any(form in fact.content for form in surface_forms):
             invalidate_fact(fact_id)
             ← bu hook entity_pages'e stale=1 yapar (başka entity'ler için)

3. Entity page:
     delete_entity_page(canonical)  ← türetilmiş view, hard-delete

Audit-safe: Facts ve relations valid_until set, satır silinmez. Page
türetilmiş olduğu için silinir (fact'lerden tekrar üretilebilir).
```

Erişim:
- Agent tool: `forget_entity(entity_name)` — agent çağırır
- Admin API: `DELETE /admin/memory/{user_id}/entity/{entity}`
- Dashboard: Entity Pages tab → "Forget" button (confirm prompt)

---

## 7. RBAC ve multi-tenant

Tüm tablolarda `user_id` foreign key. Tüm sorgular `WHERE user_id = ?`
ile filtreli. Test izolasyonu `tests/test_e2e.py`
`test_e2e_memory_facts_user_scoped` ile garanti.

`workspace/agents/memory/AGENT.md` — tek prompt, tüm kullanıcılar aynı
extraction kontratını paylaşır. Per-user kişisel prompt'lama yok (Faz
22E'de düşünülür).

---

## 8. Geliştirme aşamaları (faz roadmap)

### Tamamlanmış

| Faz | Versiyon | Ne geldi |
|---|---|---|
| 22A — Foundation | v1.16.0 | memory_facts, vec_memory_facts, memory_processing_log, hot-path extraction her 5 mesajda, AGENT.md memory profile, user_notes unified (3→1 tablo) |
| 22B — Semantic retrieval + AUDN | v1.17.0 | sqlite-vec wiring, AUDN (ADD/UPDATE/NOOP), temporal user_notes pattern, query-aware retrieval |
| 22C — Decay + Relations + Memory Tools | v1.18.0 | apply_decay (basit), memory_relations tablosu (extraction'da çıkarılır ama context'e inject edilmez), 11 memory tool (search_memory, forget_fact, what_do_you_know dahil), 2-stage retrieval + re-rank, access tracking, AUDN'a DELETE eklendi |
| 22D Part 1 — Backlinks revival | v1.19.0 | Partial UNIQUE on relations, dedup migration (155→94), canonical_source/target columns, EntityResolver (3-tier), distance gate (max_distance=0.45), backlinks injection in ContextBuilder (RELATIONSHIPS block), opportunistic backfill at startup |
| 22D Part 2 — Entity Pages + Maintenance + Forget | v1.20.0 | memory_entity_pages tablosu, EntityPageCompiler (debounced 60s), Karpathy LLM-Wiki entity-page injection, type-aware decay rates, MemoryMaintenance (consolidation.py dead code'u silindi), forget_entity cascade, memory_schema.md (public extraction contract) |
| 22D Part 3 — Admin + Dashboard | v1.20.1 | 7 admin endpoint (relations/entities/pages/recompile/maintenance/retrieval-debug/forget), Memory.tsx 4 tab (Facts/Relations/Pages/Debug), retrieval debug ile distance histogram |
| 22E — Hardening + gbot-eval (Step 5) | v1.21.0 | Anthropic/Gemini prompt caching (cache_control breakpoint), otomatik daily/weekly maintenance cron schedule, LOCOMO-mini retrieval benchmark (`tests/memory_benchmark/`), session summary'lerine ARTIFACTS section, gbot-eval LLM evaluation framework (3 memory + 4 agent + 1 stress suite) |
| 22E Step 5K — LiteLLM removal | v1.21.1 | OpenRouter SDK provider'a kesin geçiş; LiteLLM dead code silindi, `litellm` dep çıkarıldı (~50MB) |
| 22F — gbot-eval YAML refactor | v1.22.0 | Suite definitions YAML'a taşındı (Python yerine), 22 scoring kind'lık DSL + restricted Python escape hatch, multi_turn coherence suite, OpenRouter `/api/v1/models` live pricing refresh, soft-dep import-guards, reasoning model auto-handling |
| 22G — Memory roadmap (5 features) | v1.23.0 | 4-state lifecycle (ACTIVE/WEAK/INHIBITED/ARCHIVED) on memory_facts + auto state transitions in apply_decay + inhibit/restore admin endpoints, persona/style memory (`fact_type='style'` slow decay + USER STYLE block), D3/cytoscape relations graph viz in dashboard, Obsidian vault sync (entity pages → markdown export, opt-in cron), Engram-style LLM rerank (opt-in retrieval-time deep scoring with static fallback) |

**Şu an: 434 unit test geçiyor. Memory layer canlı dogfood'da; PRAGMA
user_version=23.**

### Bekleyen / sonraki adımlar

Faz 22A→22G ile memory roadmap'in TBD listesi tamamlandı. İleri adımlar:

1. **GDPR hard-delete + audit log** — `forget_entity(hard=True)` ile
   gerçek satır silimi + `memory_audit_log` separate file. Personal
   asistan için non-blocker, multi-tenant SaaS için zorunlu.
2. **GDPR data export endpoint** — `GET /admin/memory/{user}/export`
   ZIP olarak (Art. 20 portability).
3. **Manual alias editing UI** — dashboard'da
   `memory_entity_aliases` düzenleme. Yanlış canonical eşleşmelerini
   düzeltmek için.
4. **Dashboard user-facing vs internal split** — `min_confidence`
   slider, "Senin hakkında ne biliyoruz" sade view.

---

## 9. Yeni eklenebilecekler — araştırma temelli öneriler

2026 Mayıs araştırma turu (A-Mem, Engram, Letta benchmark, Mem0 State of
2026, Factory.ai anchored summary, GDPR heydata, SpeakBetter memory
notes) çıktısı. Önceliğe göre.

### 9.1 🟢 P0 — Prompt caching (yüksek ROI, az iş) ✅ v1.21.0

**Sorun:** Sistem prompt'u (identity + skills + role + agent_memory)
yaklaşık 3-5K token. Her turn'de yeniden gönderiliyor. Kullanıcı 20
mesaj atınca 60-100K token boşa.

**Çözüm:** Anthropic ve Gemini'nin `cache_control` parametresi (OpenRouter
proxy'lerinden geçiyor). Cached read maliyeti %10, cached write %25;
hit oranına göre %50-90 tasarruf.

**Yer:** Static layers (identity, skills, role, agent_memory) tek bir
"cache breakpoint" altına alınır; dynamic layer'lar (user_context —
çünkü retrieval her turn değişir, session_summary) cache-dışı.

**Efor:** 3-5 saat. `gbot/core/providers/litellm.py` veya
`gbot/agent/nodes.py:reason` içinde mesaj listesine cache_control
eklenir. Provider-specific (Anthropic vs Gemini farklı syntax).

**Beklenen kazanım:** Token cost'ta %50-80, latency'de %20-30 düşüş.
Ölçülebilir, A/B test edilebilir.

**Engellem:** OpenRouter'ın bunu nasıl proxy ettiği belgesi gerekli;
provider'a doğrudan da gidilebilir.

### 9.2 🟢 P0 — GDPR hard-delete + ayrı audit log (compliance riski)

**Sorun:** `forget_entity` şu an `valid_until` set ediyor — satır DB'de
kalıyor. heydata 2026 raporu: "**hard deletion with immutable audit logs
(separate from memory store)**". GDPR Art. 17 ile uyumsuzluk riski; AB
pazarı için non-negotiable.

**Çözüm:**

1. Yeni tablo: `memory_audit_log(user_id, entity, action, deleted_at,
   summary)`. Memory store'dan **ayrı** dosya da olabilir
   (`gbot_audit.db`).
2. `forget_entity(hard=True)` parametresi:
   - hard=False (default): mevcut davranış — `valid_until` set
   - hard=True: `DELETE FROM memory_facts WHERE ...`, `DELETE FROM
     memory_relations WHERE ...`, `DELETE FROM memory_entity_pages WHERE
     ...`, sonra `INSERT INTO memory_audit_log` ile minimum trace
3. Default-soft (mevcut audit chain), opt-in-hard (GDPR request için).
4. CLI: `gbot memory forget <user> <entity> --hard`
5. Admin endpoint param: `DELETE /admin/memory/{user}/entity/{ent}?hard=true`

**Efor:** 3-4 saat. Schema migration + 1 yeni tablo + flag pipe-through.

### 9.3 🟢 P0 — GDPR data export endpoint (Art. 20 portability)

**Sorun:** Kullanıcının "tüm hafızamı indir" hakkı yok şu an.

**Çözüm:** `GET /admin/memory/{user_id}/export` → ZIP içerik:

```
export-2026-05-08-owner.zip
├── facts.json        (tüm memory_facts, valid + invalid)
├── relations.json    (tüm memory_relations)
├── pages.json        (tüm memory_entity_pages)
├── notes.json        (user_notes — note + preference + favorite)
├── aliases.json      (memory_entity_aliases)
├── processing_log.json
└── README.txt        (export tarihi, schema versiyon, format açıklaması)
```

**Efor:** 1-2 saat. FastAPI streaming response, `zipfile` modülüyle
in-memory ZIP. Admin auth zaten var.

### 9.4 🟡 P1 — Summary `ARTIFACT_TRAIL` (Factory.ai pattern) ✅ v1.21.0

**Sorundu:** Session summary "KONULAR / KARARLAR / BEKLEYEN /
KULLANICI_BİLGİSİ" yapısındaydı. Factory.ai paterni "Session Intent +
**Artifact Trail** + Breadcrumbs" diyor. Artifact = bu session'da
üretilen somut çıktı (kod parçası, plan, karar listesi, mesaj
şablonu). Bizde yoktu.

**Çözüm (uygulandı):** `workspace/agents/memory/AGENT.md` Görev 1
prompt'una ARTIFACTS satırı eklendi:
> "ARTIFACTS: Bu session'da somut olarak üretilen çıktılar — kod
> parçaları, planlar/taslaklar, alınan kararların listesi, oluşturulan
> dökümanlar. SADECE üretildiyse yaz. 'X hakkında konuştuk' değil;
> konuşmadan çıkan tutulur bir şey olmalı. Üretim yoksa bu satırı
> tamamen atla."

**Kazanım:** Session continuity. Kullanıcı 3 gün sonra geldiğinde "geçen
sefer hangi planı yapmıştık?" demeden agent kendi getirir.

### 9.5 🟡 P1 — Entity pages A/B benchmark

**Sorun:** Letta benchmark'i ("naive file beats clever graph") önemli
bir uyarı: **agent capability matters more than retrieval mechanism**.
Entity pages'in gerçek katkısını ölçmedik. Açık-kapalı dogfood
karşılaştırması yapılmadı.

**Çözüm:** İç test seti (50 query) hazırlanır:

1. `entity_pages.enabled=true` ile 50 query → response, token, latency
2. `entity_pages.enabled=false` ile aynı 50 query
3. Response quality LLM-judge ile karşılaştır

**Çıktı:** "v1.20.0 entity pages → response quality +X%, token cost
+Y%". Veriyle "default-on yapalım mı?" sorusu cevaplanır.

**Efor:** 4-6 saat. Internal benchmark suite (LOCOMO mini).

### 9.6 🟡 P1 — Read-time relevance scoring (Engram pattern) ✅ v1.23.0 (opt-in)

**Sorun:** Engram %80 LOCOMO ulaşıyor; Mem0 %67. Engram'ın insight'ı:
**"read-time optimization over write-time"**. Bizde write-time'da fact
saklanır, read-time'da retrieval + statik formül rerank. Engram
read-time'da daha derin scoring yapıyor.

**Çözüm:** Top-3 candidate üzerinde mini-LLM call: "kullanıcının
sorduğu X için bu fact relevant mi? 0/1 cevap". Selektif, sadece üst 3.

**Tradeoff:** Her query için 1 ekstra LLM call (gpt-4o-mini ~$0.0001).
Personal asistan için kabul edilebilir, ama `memory.retrieval.deep_score:
true` flag ile opt-in olur.

**Efor:** 5-8 saat. Yeni `_deep_score_facts` method,
`MemoryRetrievalConfig.deep_score: bool = False`, prompt design,
benchmark.

**Kazanım:** Belirsiz. Sadece A/B benchmark edip "var mı yok mu" görmek
için yapılır.

### 9.7 🟡 P1 — Dashboard user-facing vs internal split

**Sorun:** Memory page şu an her şeyi gösteriyor — düşük confidence
fact'ler, invalidated supersede chain, internal extraction noise.
SpeakBetter raporu Section 21: memory'nin **yarısı kullanıcıya görünmez,
yarısı user-facing**. Aynı veri, iki sunum.

**Çözüm:** Memory page'e `min_confidence` slider (default 0.7), default
"valid only" toggle (off = gelişmiş mod). Settings sayfasına ayrı
"Senin hakkında ne biliyoruz" view — sadece doğrulanmış subset, "Bunu
sil" butonlarıyla.

**Efor:** 4-6 saat dashboard + 2 yeni endpoint.

### 9.8 🟢 P2 — Internal memory benchmark suite ✅ v1.21.0

**Sorun:** Versiyondan versiyona regresyon yok diye bilmiyoruz.
"v1.20.0 v1.19.0'dan iyi mi?" niceliksel cevabımız yok.

**Çözüm:** `tests/memory_benchmark/` klasörü:

- `fixture.json` — 100 manuel-küratörlü facts (Türkçe + İngilizce karışım)
- `queries.json` — 50 query, her birinin "expected facts" listesi var
- `runner.py` — fixture yükle → her query için retrieval → recall@K hesapla
- CI'da `pytest -m benchmark` → versiyondan versiyona karşılaştırılır

**Efor:** 6-10 saat (en zoru fixture küratörü).

### 9.9 🔵 P3 — 4-state lifecycle (ACTIVE / WEAK / INHIBITED / ARCHIVED) ✅ v1.23.0

**Sorun:** Şu an binary. Decay importance düşürdükçe sadece score
düşüyor; "yarı-unutulmuş" state yok.

**Çözüm:** `memory_facts.lifecycle_state` kolonu (ENUM):
- `active` (importance ≥ 0.5) — search'te ilk sırada
- `weak` (0.2 ≤ importance < 0.5) — search'te çıkar, context priority düşük
- `inhibited` (0.1 ≤ importance < 0.2) — search dışı ama valid
- `archived` (importance < 0.1) — `valid_until` set, search yok

**Efor:** 3-4 saat. Mevcut decay mekanizmasının üstüne state machine.

**Kazanım:** Görsel/inspectable lifecycle. Aslında mevcut `valid_until +
importance` zaten benzer iş yapıyor; bu sadece kategorik isim koymak.
Pratik fark az.

### 9.10 🔵 P3 — LECTOR-style cluster retrieval

**Sorun:** Tek fact retrieve edildiğinde, semantik komşu fact'ler
gelmiyor. "Ali kahve içiyor" retrieve olunca "Ali çay sevmiyor" yanına
gelmeli.

**Çözüm:** Aslında bizim **relations injection bunu zaten yapıyor** —
"Ali" entity'si tespit edilince ilgili relations geliyor. LECTOR
academic insight sadece bu paterne **isim** veriyor.

**Aksiyon:** Yeni iş yok — bu eklenmiş gibi davranabiliriz; CHANGELOG'da
"LECTOR-style semantic neighbor injection via memory_relations" diye
belgele.

### 9.11 🔵 P3 — Persona / style memory (declared + behavioural) ✅ v1.23.0

**Sorun:** Şu an "kullanıcı resmi mi konuşuyor, samimi mi" gibi style
sinyalleri saklanmıyor. Memory'de "Ömer İstanbul'da yaşıyor" var ama
"Ömer kısa cevap istiyor" yok.

**Çözüm:** Yeni `fact_type: 'persona'` kategorisi, extraction prompt'una
ek section:

```
PERSONA (varsa):
- communication_style: formal | casual | technical
- response_length_preference: short | medium | long
- emotional_tone: neutral | warm | direct
```

LinkedIn Cognitive Memory Agent paterninden geliyor. Behavioural sinyal
(turn uzunluğu, soru tipleri) + declared sinyal (kullanıcının söylediği).

**Efor:** 4-6 saat. Schema yok (fact_type enum'una ekleme), prompt
genişletme, retrieval'da persona facts'leri her zaman context'e koy
(5-10 token/each).

---

## 10. Tartışmalı / verilmiş kararlar

Plan'da zaten reddedildi ama belirtmek lazım — ileride biri "bunu yapsak
mı?" diye gelirse:

| Reddedilen | Sebep |
|---|---|
| Neo4j / graph database | SQLite + memory_relations tablosu kişisel asistan ölçeği için yeterli; Engram da aynı tercih |
| Periyodik consolidation cycle (timer-based) | AUDN ingest'te zaten conflict çözüyor; 2. pass duplicate iş |
| Category summary (memU paterni) | Retrieval zaten filtreliyor; summary'ler bilgi kaybediyor + güncel tutma maliyeti |
| Letta-style "agent self-edits memory" | Lock-in (agent runtime), bizim stateless executor felsefesiyle uyumsuz |
| Per-user fine-tuning | OpenRouter stateless, fine-tune yapmıyoruz; privacy + cost gerekçesi |
| Voice / audio memory | Transcript yeterli; raw audio bloat |
| Sentiment / emotion tracking | Creep faktörü + güvenilmez sinyal |

---

## 11. Akademik / endüstri referansları

GBot'un memory layer kararları şu kaynaklardan beslenir:

- **A-Mem (arXiv 2502.12110)** — formal 5-op cycle, write-time
  extraction, recency × access decay. Bizim mimari %95 hizalı.
- **Engram (engram.fyi)** — açık-kaynak, SQLite + sqlite-vec + bi-temporal.
  Bizim klonumuz; LOCOMO 80%.
- **Karpathy LLM-Wiki gist** — entity pages + schema-as-document.
- **Letta benchmark** — agent capability > retrieval mechanism. Entity
  pages'i critical olarak benchmark'lamamız gerek.
- **Factory.ai anchored summary** — bizim session-end summary'miz tam bu
  pattern; drift düşük.
- **Mem0 / Zep** — relation/graph yaklaşımının üst sınırı; bizim
  entity_pages onların graph mode'una alternatif.
- **GDPR heydata 2026** — soft delete riski + audit log split.

---

## 12. Kalite ölçümü — `gbot-eval` (Faz 22E + 22F)

Memory layer'ın LLM-yoğun adımları (extraction, AUDN, entity-page
compile) artık `gbot-eval` framework'unda regression-tested.

| Suite | Ne ölçer |
|---|---|
| `memory.extraction` | Fact recall + relation recall + category accuracy |
| `memory.audn` | ADD/UPDATE/DELETE/NOOP exact-match accuracy |
| `memory.page_compile` | Composite: 40% keyword + 30% no-hallu + 15% format + 15% citation |

```bash
# Production model baseline
gbot-eval run --suite=memory --model=openrouter/google/gemini-3-flash-preview

# Hızlı %20 sample
gbot-eval run --suite=memory --sample=20

# Yeni model dene
gbot-eval run --suite=memory --model=openrouter/moonshotai/kimi-k2.6
gbot-eval matrix
gbot-eval baseline diff
```

Ek olarak `tests/memory_benchmark/` LOCOMO-mini retrieval benchmark
(recall@K, MRR) `pytest -m benchmark` ile koşar.

**Baseline (gemini-3-flash, v1.23.0):**
- memory.extraction quality 0.80
- memory.audn quality 0.93
- memory.page_compile composite ~0.85

Detay: `gbot_eval/README.md`.

---

## 13. İlerlemeyi nereden takip etmek

| Konu | Yer |
|---|---|
| Faz progress tracking | `notes/todo.md` |
| Mimari kararlar (tarihsel) | `notes/mimari_kararlar.md` |
| Bug günlüğü (root causes + fixes) | `notes/journal.md` |
| Manuel test rehberi | `notes/test.md` |
| Bu dosya (memory iç mimari) | `docs/memory-architecture.md` |
| Memory pratik kullanım rehberi | `docs/memory-usage.md` |
| Kod | `gbot/memory/` |
| Schema kontratı | `workspace/memory_schema.md` |
| Memory agent prompt | `workspace/agents/memory/AGENT.md` |
| LLM kalite ölçümü | `gbot_eval/README.md` |
