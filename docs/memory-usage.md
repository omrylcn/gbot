# Memory Usage Guide

GBot memory layer'ının pratik kullanım rehberi — günlük operasyon,
config opt-in flag'leri, dashboard üzerinden inceleme, admin API,
hata giderme.

> Mimari ve tasarım kararları için → `docs/memory-architecture.md`.

---

## 1. Günlük akış (otomatik)

Sen mesaj atarken arka planda olan biten:

```
Telegram / API / WS mesajı
      ↓
GraphRunner.process()
      ↓
respond node — kullanıcıya cevap yollanır (sen beklemedin)
      ↓
_maybe_extract_facts (her N=5 user mesajda):
  • LLM extraction → typed facts (semantic/episodic/preference/
    procedural/style) + relations
  • Embedding (3072-dim cosine)
  • AUDN: existing fact ile karşılaştır → ADD / UPDATE / DELETE / NOOP
  • Entity touched ise → 60s sonra entity_page recompile
      ↓
Sonraki turn'de ContextBuilder retrieval:
  • USER STYLE block (her zaman var, query-bağımsız)
  • LEARNED FACTS (semantic search → rerank)
  • RELATIONSHIPS (canonical entity → ilişkiler)
  • ENTITY PAGES (compiled markdown summaries)
```

Her gece 04:00 (default) → `apply_decay`:
- ACTIVE → WEAK (fade_days geçince, untouched ise)
- WEAK → ARCHIVED (importance < 0.1)
- INHIBITED → ACTIVE (hold süresi dolmuşsa)

Hiç müdahaleye gerek yok — bot çalışıyorsa hafıza da çalışıyor.

---

## 2. Dashboard — hafızayı inceleme

`http://localhost:8000` → **Memory** tab → kullanıcı seç (örn `owner`).

### 4 alt-tab

| Tab | Ne gösteriyor |
|---|---|
| **Facts** | Tüm fact'ler. Filter: `all / semantic / episodic / preference / procedural / style`. Her satırda content, importance, confidence, access_count, valid range, fact_type badge. |
| **Relations** | Üst sağda **Table / Graph** toggle. Table: kanonik source → relation → target liste. Graph (Faz 22G): cytoscape force-directed view, drag/zoom, click-to-inspect side panel. |
| **Entity Pages** | LLM-derlenmiş kompakt sayfalar (markdown). Her page için Recompile / Forget butonu. |
| **Retrieval Debug** | Bir sorgu yaz, `/admin/memory/{user}/retrieval-debug` çağırılır. Distance histogramı + `above_gate` flag. Distance threshold tuning için. |

### Memory page'in soltarafı

Hangi tab'da olursan ol, sol tarafta `notes / preferences / favorites`
(unified `user_notes` tablosu) ve `processing_log` (son 10 extraction
özet) görünür.

---

## 3. Admin API — manuel müdahale

Her endpoint owner-only auth gerektirir. Token: `gbot login`'den.

### Lifecycle yönetimi (Faz 22G)

```bash
# State'e göre fact'ler
curl -H "Authorization: Bearer $TOKEN" \
  "http://localhost:8000/admin/memory/owner/facts?state=active&limit=20"

# Belirli state filtreleri
.../facts?state=weak       # solgun (decay sonrası)
.../facts?state=inhibited  # askıda
.../facts?state=archived   # arşivlenmiş (audit-only)

# Belirli fact_type
.../facts?fact_type=style  # iletişim stili fact'leri

# Bir fact'i 7 gün askıya al
curl -X POST -H "Authorization: Bearer $TOKEN" \
  ".../facts/abc12345/inhibit?hold_days=7"

# Geri al (auto-restore beklemeden)
curl -X POST ".../facts/abc12345/restore"
```

### Maintenance + sync manuel tetik

```bash
# Decay + stale page recompile + orphan cleanup
curl -X POST ".../maintenance/run"

# Obsidian export (Faz 22G — opt-in olmalı)
curl -X POST ".../obsidian-sync/run"

# Belirli entity'nin page'ini yeniden derle
curl -X POST ".../pages/recompile?entity=Murat"
```

### Forget (cascade — facts + relations + page)

```bash
# Soft delete (audit-safe — valid_until set)
curl -X DELETE ".../entity/Murat"

# Eşdeğeri agent tool: forget_entity("Murat")
```

### Retrieval debug

```bash
curl -G ".../retrieval-debug" --data-urlencode "query=Murat ne yapıyor"
# → her candidate için distance + final_score + above_gate
```

---

## 4. Opt-in feature'ları açma

`config/config.yaml`:

```yaml
memory:
  enabled: true                      # ana switch (default true)

  # Faz 22D — entity pages (LLM-derlenmiş kompakt sayfalar)
  entity_pages:
    enabled: true                    # default false
    model: openrouter/google/gemini-3-flash-preview
    debounce_seconds: 60
    min_facts_for_page: 3
    min_relations_for_page: 2

  # Faz 22E — auto-maintenance (default açık)
  maintenance:
    enabled: true
    daily_cron: "0 4 * * *"
    weekly_cron: "30 4 * * 0"

  # Faz 22G Aşama 4 — Obsidian sync (default kapalı)
  obsidian_sync:
    enabled: true
    vault_path: "~/Obsidian/GBot Memory"
    sync_cron: "0 * * * *"           # her saat
    include_archived: false
    include_stale: false             # stale page'leri export etme

  # Faz 22G Aşama 5 — LLM rerank (production hot path, dikkat)
  retrieval:
    max_distance: 0.45               # cosine ceiling
    top_k_candidates: 20
    top_k_final: 10
    llm_rerank:
      enabled: true                  # +1 LLM call/turn
      model: null                    # null → memory.model
      candidates_pool: 30            # statik formula yerine 30 candidate
      max_output_tokens: 200
      temperature: 0.0
```

Restart sonrası:
- `entity_pages.enabled=true` → mevcut entity'ler için
  `apply_decay` veya `pages/recompile` ile sayfa derlenir.
- `obsidian_sync.enabled=true` → lifespan'de `obsidian-sync-{user}`
  cron job'ları otomatik kayıt olur.
- `llm_rerank.enabled=true` → her turn'de 1 ekstra LLM call.

### Hangisini ne zaman aç

| Feature | Default | Ne zaman aç |
|---|---|---|
| `memory.enabled` | true | Her zaman açık |
| `entity_pages.enabled` | false | 2-3 ay aktif kullanım sonrası, hafıza yeterince zenginleşince |
| `obsidian_sync.enabled` | false | Obsidian zaten kullanıyorsan + entity_pages açıksa |
| `llm_rerank.enabled` | false | gbot-eval'da ölçülmüş quality artışı varsa, cost/latency OK ise |

---

## 5. Persona / style memory (Faz 22G Aşama 2)

### Otomatik yakalanma

Konuşmadan ekstrakt ediliyor (`MemoryService.extract_and_save`).
Memory agent prompt'u (`workspace/memory_schema.md` STYLE bölümü)
şu pattern'leri yakalar:

- Açık tercih: "kısa yaz", "Türkçe konuşalım", "emoji kullanma"
- Tutarlı pattern: birkaç turn'de aynı stil sinyali

Örnekler:
- "Kullanıcı kısa, doğrudan cevaplar tercih ediyor"
- "Kullanıcı küfür içeren samimi bir dil kullanıyor"
- "Kullanıcı emoji kullanmıyor"
- "Kullanıcı sen-zamiri yerine siz tercih ediyor"

### Manuel ekleme

```bash
curl -X POST ".../notes" -H "Content-Type: application/json" -d '{
  "content": "Kullanıcı her cevaba örnek istiyor",
  "note_type": "style"
}'
```

Veya doğrudan agent'a söyle: "Kısa cevap ver" → MemoryService bu
sinyali bir sonraki extraction'da yakalar.

### Decay

Style fact'ler en yavaş eskiyen tipte:
- Fade: 180 gün (semantic 90, preference 120)
- Archive: 540 gün

Mantık: tonun kayması zaman alır. "Kısa yaz" diyen biri 6 ay sonra
hâlâ kısa yazıyordur muhtemelen.

### ContextBuilder'da nasıl görünür

Her turn'de sistem promptunda **USER STYLE** alt-section olarak
(query-bağımsız, hep var):

```
USER STYLE:
- Kullanıcı kısa, doğrudan cevaplar tercih ediyor
- Türkçe iletişim
- Emoji kullanmıyor

LEARNED FACTS:
- ...
```

LLM bunlara göre cevabı şekillendirir.

---

## 6. Obsidian sync (Faz 22G Aşama 4)

### Aktivasyon

```yaml
memory:
  obsidian_sync:
    enabled: true
    vault_path: "~/Obsidian/GBot Memory"
    sync_cron: "0 * * * *"
```

Restart → her kullanıcı için `obsidian-sync-{user}` recurring task
otomatik kayıt olur.

### Output

```
~/Obsidian/GBot Memory/
└── gbot/
    └── owner/
        ├── Murat.md
        ├── Aye.md         # özel karakterler ASCII'ye fold edilir
        └── HangiKredi.md
```

Her dosya:

```markdown
---
tags: [gbot-memory, owner]
entity: Murat
compiled_at: 2026-05-09T18:30:00
synced_at: 2026-05-09T19:00:00
source_facts: ["abc123", "def456"]
---

## Murat
Murat, kullanıcının HangiKredi'deki backend developer iş arkadaşı.
Ankara'da yaşıyor, 5 yıllık deneyime sahip.

- Aynı takımda çalışıyor [fact_id:abc123]
- Hafta sonları yürüyüş yapıyor [fact_id:def456]
```

### Manuel tetik

```bash
curl -X POST ".../obsidian-sync/run"
# → {"written": 12, "skipped": 3, "deleted": 0,
#    "enabled": true, "vault_dir": "/home/.../gbot/owner"}
```

### Önkoşul

`entity_pages.enabled=true` olmalı. Yoksa derlenmiş sayfa olmaz,
sync edilecek bir şey yok (output: `written=0`).

### Idempotent

İçerik aynı kalmışsa dosya yeniden yazılmaz (skipped++). Sadece
frontmatter `synced_at` değiştiği için pratikte her run'da bir
satır farkı oluşur — Obsidian git plugin kullanıyorsan minimal
diff.

---

## 7. LLM rerank (Faz 22G Aşama 5)

### Aktivasyon

```yaml
memory:
  retrieval:
    llm_rerank:
      enabled: true
      candidates_pool: 30
```

Hot path'te her sorgu için +1 LLM call. Latency +0.5-1.5s,
cost ~$0.0002/query.

### Akış farkı

```
KAPALI (default — statik):
  Sorgu → embed → top 30 candidate
       → multiplicative formula:
         similarity × recency × access × confidence
       → top 10

AÇIK (LLM rerank):
  Sorgu → embed → top 30 candidate
       → LLM'e "bu sorguya göre sırala" sor
       → JSON: {"ranked": [3, 1, 14, 7, ...]}
       → top 10 (kalan boşluğu statik formula ile doldur)

Hata olursa → otomatik statik formula'ya düşer (graceful fallback)
```

### Karar verme — gbot-eval ile A/B

```bash
# 1. Önce statik baseline al
gbot-eval run --suite=memory --model=openrouter/google/gemini-3-flash-preview
gbot-eval baseline set --run=<latest>

# 2. llm_rerank.enabled=true yap, restart
# 3. Yeniden çalıştır
gbot-eval run --suite=memory --model=openrouter/google/gemini-3-flash-preview
gbot-eval baseline diff
```

Quality artışı latency/cost'a değiyor mu? Şu an default `false`
çünkü herkes için kazançlı değil.

---

## 8. Kalite ölçümü — gbot-eval

Memory pipeline'ında 3 LLM call var; her biri için ayrı suite:

```bash
gbot-eval run --suite=memory               # 3 suite (extraction/audn/page_compile)
gbot-eval run --suite=memory.extraction    # tek suite

# Hızlı smoke (her fixture'ın ilk %20'si)
gbot-eval run --suite=memory --sample=20

# Yeni model dene
gbot-eval run --suite=memory \
  --model=openrouter/moonshotai/kimi-k2.6

# Sonucu tablola
gbot-eval matrix
gbot-eval list-runs
gbot-eval compare <ts_a> <ts_b>
```

Detay: `gbot_eval/README.md`.

Production retrieval (vector search + rerank) için ayrıca
`tests/memory_benchmark/` LOCOMO-mini benchmark suite var:

```bash
uv run pytest -m benchmark
```

Recall@K, MRR, latency, tokens çıktısı.

---

## 9. CLI hızlı referans

```bash
# Server'ı başlat
gbot run

# Kullanıcılar
gbot user list

# Cron job'ları (maintenance + obsidian-sync görmeli)
gbot cron list

# Belirli bir kullanıcı için memory dump
gbot memory dump owner          # opsiyonel — komut yoksa admin API kullan

# Eval CLI
gbot-eval list                  # 10 suite
gbot-eval models                # pricing tablosu
gbot-eval models refresh        # OpenRouter'dan canlı çek
```

---

## 10. Senaryo bazlı tarifler

### "Bu fact yanlış görünüyor ama silmek istemiyorum"

```bash
# 1. Dashboard'da fact_id'yi bul (Memory > Facts)
# 2. Inhibit (default 7 gün)
curl -X POST ".../facts/abc12345/inhibit?hold_days=7"
# 3. Eğer yanlış olduğu sabitlerse → cascading invalidate
curl -X DELETE ".../entity/X"
# 4. Eğer haklıymış meğer → restore
curl -X POST ".../facts/abc12345/restore"
```

### "Hafıza dolup retrieval kötüleşti"

Decay otomatik 04:00'da çalışıyor ama manuel tetiklemek istersen:

```bash
curl -X POST ".../maintenance/run"
# → faded, archived, restored counts
```

WEAK / ARCHIVED dağılımına `Memory > Facts > state filter` ile bak.
Çok fazla fact varsa `top_k_candidates` (default 20) artırılabilir
ama cost'u dengeliyor `top_k_final=10`.

### "Asistan benim stilimde konuşmuyor"

1. Birkaç turn'de stilini açıkça söyle: "kısa yaz", "Türkçe", "emoji
   kullanma" → otomatik yakalanır.
2. `Memory > Facts > style` filter ile yakalanan stil fact'lerini gör.
3. Manuel ekleme:
   ```bash
   curl -X POST ".../notes" -d '{
     "content": "Kullanıcı her cevaba örnek istiyor",
     "note_type": "style"
   }'
   ```

### "Kişi/işyeri ilişkilerini görmek istiyorum"

Memory > Relations > **Graph** toggle. Cytoscape force-directed
view, node = canonical entity, edge = relation. Filter input ile
belirli entity'leri izole et.

### "Hafızamı Obsidian'a yedeklemek istiyorum"

`config.yaml` → `obsidian_sync.enabled=true`, restart. Saatte bir
otomatik sync. Manuel: `POST .../obsidian-sync/run`.

### "Yeni model çıktı, retrieval iyi mi?"

```bash
gbot-eval run --suite=memory \
  --model=openrouter/moonshotai/kimi-k2.6
gbot-eval matrix
gbot-eval baseline diff      # mevcut baseline'la karşılaştır
```

3 metric (extraction, AUDN, page_compile) bir bakışta görünür.

### "Reasoning model (Kimi/DeepSeek) ile retrieval daha iyi olur mu?"

`llm_rerank.enabled=true` + `model: openrouter/moonshotai/kimi-k2.6`
→ gbot-eval ile A/B karşılaştır. Reasoning model'lar `effort: none`
ile çağırılır (otomatik — `is_reasoning_model` detect ediyor).

---

## 11. Hata giderme

### "Hafızam dolu ama retrieval bir şey getirmiyor"

- `Memory > Retrieval Debug` ile sorguyu test et. Tüm distance'lar
  > 0.45 mi? `max_distance` çok sıkı, gevşet.
- Fact'lerin `state`'ine bak — hepsi ARCHIVED'a düşmüş olabilir.
  `apply_decay`'in `archive_threshold`'u (default 0.1) yükseltirsen
  daha az archive olur.

### "Aynı fact iki kere ekleniyor"

AUDN'un `update.model`'i (default `memory.model`) zayıf olabilir.
gbot-eval `memory.audn` skoruna bak; <0.7 ise farklı model dene.

### "Entity pages derlenmiyor"

- `entity_pages.enabled=true` mı? `gbot cron list` ile kontrol et.
- `min_facts_for_page` threshold (default 3) altındaysan eligibility
  yok. Daha fazla mesaj at o entity hakkında.
- Hızlı tetik: `POST .../pages/recompile?entity=X`

### "Obsidian'a hiçbir şey yazılmıyor"

- `obsidian_sync.enabled=true` mı?
- `entity_pages.enabled=true` mı? (önkoşul)
- `vault_path` yazılabilir mi? `chmod` kontrol et.
- Stale page'ler skip ediliyor — `include_stale=true` yap test için.

### "LLM rerank açık ama hâlâ statik formula gibi davranıyor"

- Provider error → graceful fallback devrede. `loguru` log'una bak:
  `llm_rerank failed, falling back to static`.
- `candidates_pool` (default 30) `top_k_candidates` (20) aşıyor mu?

### "Memory layer test'leri kırıldı"

```bash
uv run pytest tests/test_memory_lifecycle.py tests/test_persona_memory.py \
  tests/test_obsidian_sync.py tests/test_llm_rerank.py -v
```

Her biri Faz 22G feature'ları için targeted; broken olan failure mesajı
hangi feature olduğunu söylüyor.

---

## 12. İlgili dosyalar

| Konu | Yer |
|---|---|
| Bu rehber (pratik kullanım) | `docs/memory-usage.md` |
| İç mimari + tasarım kararları | `docs/memory-architecture.md` |
| Memory kodu | `gbot/memory/` |
| Schema kontratı (extraction prompt için) | `workspace/memory_schema.md` |
| Memory agent system prompt | `workspace/agents/memory/AGENT.md` |
| Config schema | `gbot/core/config/schema.py` |
| Admin endpoint'ler | `gbot/api/admin.py` |
| Dashboard memory page | `dashboard/src/pages/Memory.tsx` |
| Relations graph component | `dashboard/src/components/RelationsGraph.tsx` |
| Obsidian syncer | `gbot/memory/obsidian_sync.py` |
| LLM eval framework | `gbot_eval/` |
