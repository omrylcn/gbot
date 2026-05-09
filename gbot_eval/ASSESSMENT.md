# `gbot_eval/` — Durum Değerlendirmesi (2026-05-09)

> Bu dosya `gbot_eval` LLM evaluation framework'unun **mevcut durumu, ne
> çalışıyor, ne eksik, hangi sinyaller alındı, hangi kararlar bekliyor**
> sorularına cevap verir. İş "park edildi"; LiteLLM→OpenRouter migration
> önceliklendirildi. Bu döküman, eval işine geri dönüldüğünde 5 dakikada
> bağlam kuracak şekilde yazıldı.

## 1. Tek Cümlede

**`gbot_eval/`** — `gbot-eval` CLI'i ile çalışan bağımsız bir LLM
evaluation framework. **8 hedef suite'in 3'ü tamam** (memory.* ailesi);
agent + stress suite'leri ile CLI tamamlanması ve release park edildi.

## 2. Çalışan / Çalışmayan

| Bölüm | Durum | Kanıt |
|---|---|---|
| Paket iskeleti (`gbot_eval/`) | ✅ tam | `uv sync` ile `gbot-eval` PATH'te |
| `Suite` / `CaseResult` / `SuiteResult` contract | ✅ stable | `gbot_eval/suites/base.py` |
| Pricing tablosu (17 model) | ✅ | `gbot-eval models` Rich tablo |
| `track_call()` token+latency+cost capture | ✅ | her suite kullanıyor |
| `CallResult.text` reasoning_content fallback | ✅ | Kimi K2 testinde doğrulandı |
| `judge.py` LLM-as-judge | ✅ kod hazır, henüz suite kullanmıyor | Step 5G'de aktif olacak |
| `runner.run_all()` orchestrator | ✅ | sequential, sampling-aware |
| Output: `output/runs/<ts>_<model>/{manifest,matrix,*.json}` | ✅ | 2 run mevcut |
| **Sampling** `--sample=N%` | ✅ deterministic prefix | manifest'e kaydediliyor |
| CLI: `run`, `models`, `list`, `list-runs`, `matrix`, `compare`, `clean` | ✅ minimal | `--help` her birinde |
| **Memory suites** (extraction / audn / page_compile) | ✅ üçü çalışır | 2 modelde test edildi |
| `tests/llm_eval/` silinmesi | ⏸️ park | gbot_eval olgunlaşana kadar duacak |
| **Agent suites** (delegation / tool_calling / structured / instruction) | ❌ yapılmadı | Step 5D-G |
| **Stress suite** (long_context) | ❌ yapılmadı | Step 5H |
| `models add` (pricing düzenleme) | ❌ yapılmadı | Step 5I |
| `baseline` komutu | ❌ yapılmadı | Step 5I |
| README + release notes | ❌ yapılmadı | Step 5J |
| v1.21.0 release | ⏸️ bloklu | tüm suite'ler tamamlanmadan ship etmiyoruz |

**Tamamlanma oranı (suite bazında):** 3/8 = ~37%.
**İskelet/altyapı bazında:** ~95% (sadece `models add`/`baseline` eksik).

## 3. Live Sinyaller (3 memory suite — ham veri)

### gemini-3-flash-preview (production model, baseline)
| Suite | Quality | tokens (in/out avg) | p95 ms | Cost ($) |
|---|---|---|---|---|
| memory.extraction | 0.80 | 312/100 | 2940 | 0.0012 |
| memory.audn | 0.93 | 187/45 | 1100 | 0.0008 |
| memory.page_compile | 0.00 | 240/180 | 1500 | 0.0006 |

**Bulgu:** Production model extraction + AUDN'de güçlü, ama
`[fact_id:xxx]` citation formatına hiç uymuyor (citation_recall=0).
`memory.entity_pages` zaten default-off — bu skor onu değiştirmek
için yeterli sebep değil.

### moonshotai/kimi-k2.6 (sample=20%, dogfood)
| Suite | Quality | tokens (in/out avg) | p95 ms | Cost ($) |
|---|---|---|---|---|
| memory.audn | 1.00 (3/3) | 1148/314 | 5862 | 0.0006 |
| memory.extraction | 0.25 | 1158/1990 | 38558 | 0.0012 |
| memory.page_compile | 0.00 | 318/1919 | 36789 | 0.0005 |

**Bulgu:** Kimi K2.6 reasoning model — `content` boş, `reasoning_content`
dolu. İlk run'da fallback yoktu, sıfır skor aldı; bu **bug bizdeydi,
LiteLLM/SDK'da değil**. Fix sonrası audn mükemmel (3/3). Extraction
yine düşük çünkü Kimi prompt schema'sını farklı yorumluyor (fact'ler
döndürüyor ama bizim ground-truth keyword'lerine uymuyor — gerçek
model davranış farkı). Page compile yine 0 — Kimi de citation
format'ına uymuyor.

**Dolaylı önemli sinyal:** Kimi K2.6 latency Gemini-3'ten 5-13× yavaş
(p95 ~36s vs 2.9s). Reasoning model olduğu için doğal, ama production
hot-path'inde (her conversation'da extraction) kabul edilemez.

## 4. Tasarım Kararları + Niye

| Karar | Niye | Alternatif |
|---|---|---|
| `gbot_eval/` ayrı paket, `gbot-eval` ayrı komut | Eval ≠ test; test pytest, eval kalite ölçümü. Kullanıcı kararı. | `tests/llm_eval/` pytest suite (önceki implementasyon) |
| Pricing static tablo | OpenRouter SDK inline cost dönmüyor; OpenRouter `usage:{include:true}` SDK'dan pas etmiyor; generations API ek round trip. Static tablo basit + "yeterince doğru". | Live API çekme (`gbot-eval models --refresh`), Faz 22F+'da. |
| LLM-as-judge default `claude-haiku-4.5` | Test edilen modelden farklı, sabit, tarafsızlık | `gpt-4o-mini` daha ucuz, ama hatalı bias riski |
| Sampling = deterministic prefix slice | Run-to-run kararlılık (random olsa karşılaştırılamaz) | Random with fixed seed; aynı sonuç ama daha karmaşık |
| Output: `gbot_eval/output/runs/<ts>_<model>/` (gitignored) | Kullanıcı kararı. Repo içi → cross-version kolay; gitignored → noise yok. | `~/.gbot-eval/runs/` (multi-repo paylaşım) |
| max_tokens artırıldı (200→2000, 1000→4000, 400→2000) | Reasoning model'lar reasoning'e harcıyor, asıl cevap için yer kalmıyor | Reasoning'i kapatma flag — modelden modele farklı, riskli |

## 5. Bilinen Borç / Açık Sorular

1. **Reasoning kapatma:** OpenRouter `reasoning: {effort: 'none'}`
   parametresi var ama SDK'mız `thinking=False` ile bunu pas etmiyor.
   Kimi/DeepSeek-R1 testlerinde ya max_tokens'i şişirip reasoning'e
   katlanıyoruz ya da reasoning_content'i fallback olarak okuyoruz.
   **Karar bekliyor:** production memory'de reasoning model kullanılırsa
   max_tokens default'unu artırmak mı, yoksa SDK'ya `--no-thinking`
   pas etmek mi?

2. **Page compile baseline = 0**: hem Gemini-3 hem Kimi `[fact_id:xxx]`
   formatına uymuyor. Citation'ı zorunlu kılan prompt değişikliği veya
   farklı bir output schema (sadece fact_id list ekleme) denenmeli.
   `entity_pages.enabled` zaten default-off; bu suite production
   davranışını ölçmüyor şu an.

3. **Suite contract genişletme:** Şimdiki `Suite.run(model, sample_pct)`
   yeterli, ama agent suite'ler için `tools=...`, `system_prompt=...`,
   gibi ek parametreler gerekebilir. Şimdilik suite kendi içinde
   resolve ediyor; sorun olunca contract'a eklenir.

4. **OpenRouter SDK's event-loop binding** pytest'te sorun çıkarmıştı
   (function-scoped autouse fixture ile aşıldı). CLI'de tek event
   loop var (asyncio.run), şimdiye kadar problem yok. Ama uzun
   `gbot-eval run` sırasında httpx client beklenmedik yerde kapanırsa
   tek tek case fail olabilir; izlemeli.

5. **`tests/llm_eval/` hâlâ tree'de:** Plan baştan "silinecek" diyordu
   ama gbot_eval olgunlaşmadan silmek riskli (regression suite eski
   davranışı dokümanlıyor). gbot_eval Step 5J'ye geldiğinde silinecek.

6. **`gbot/__version__.py` 1.21.0'a çekildi** ama henüz tag/release yok.
   Bu state'te public push yapılırsa yarım versiyon görünür. Park
   edilince hatırlatma: release ya 1.21.0 olarak full eval ile
   tamamlanmalı, ya da geriye 1.20.1 + bump tasarısı düşünülmeli.

## 6. Bir Sonraki Adımlar (gündeme geri döndüğünde)

Sıralı liste:

1. **LiteLLM → OpenRouter migration** (yeni gündem) — `gbot/core/providers/litellm.py`
   facade'ından LiteLLM kodu çıkar, sadece OpenRouter SDK kalsın. Bu
   migration eval framework'una iki şekilde dokunur:
   - `judge.py` ve tüm `track_call()` çağrıları LiteLLM yerine
     OpenRouter SDK'sından geçecek (zaten geçiyor — `setup_provider`
     openrouter prefix'inde direkt SDK kullanıyor).
   - LiteLLMLLM fallback path silinince `gbot_eval.config.init_provider`
     basitleşir.

2. **Step 5D — agent.delegation suite** — `DelegationPlanner.plan()`
   doğrudan invoke. 15 case (immediate / delayed / multi-step / ambiguous
   / no-op). Beklenen quality signal Gemini-3 için ~0.85.

3. **Step 5E — agent.tool_calling suite** — `_build_tool_definitions`
   ile schema, `llm_provider.achat(tools=...)`. 20 case. False-positive
   tool çağrısı + hallucinated args ölçer.

4. **Step 5F — agent.structured** — JSON schema adherence. 10 case.

5. **Step 5G — agent.instruction** — 10 regex + 5 LLM-as-judge. judge
   modülü burada aktive olur.

6. **Step 5H — stress.long_context** — 30-turn stub'lı dialog × 3
   pozisyon. Ham model context-recall ölçer.

7. **Step 5I — CLI tamamla** — `models add`, `baseline set/diff`,
   `run-baseline-diff` komutu. Pricing.py persistent rewrite.

8. **Step 5J — README + dogfood + release** — gbot_eval/README.md,
   2-3 modelle baseline matrix yayınla, CHANGELOG, tag, public push.

## 7. Komut Hızlı Başvurusu

```bash
# Suite'leri listele
gbot-eval list

# Hızlı smoke (production model, %20)
gbot-eval run --suite=memory --sample=20

# Tek suite, belirli model
gbot-eval run --suite=memory.audn --model=openrouter/anthropic/claude-haiku-4.5

# Run'ları gör
gbot-eval list-runs

# Son run'ın matrix'i
gbot-eval matrix

# A/B karşılaştırma
gbot-eval compare 2026-05-09T01-01-26_... 2026-05-09T01-10-37_...

# Disk temizle
gbot-eval clean --keep=10

# Pricing tablosu
gbot-eval models
```

## 8. Sınırlar / Bu Suite Ne Ölçmüyor

- **End-to-end pipeline davranışı** — GraphRunner, ContextBuilder,
  memory injection, tool execution. Bunlar `tests/test_*.py` regression'da.
- **Cost gerçeği** — pricing static tablo; %1-5 sapma olabilir.
  Production cost izleme için OpenRouter dashboard daha sahih.
- **Multi-turn coherence** — long_context suite henüz yok; suite gelse
  bile stub mod (kullanıcı kararı), production davranışı değil ham
  model yeteneği ölçecek.
- **Provider routing dalgalanması** — OpenRouter farklı backend'lere
  route ederse latency/quality varyasyonu var; manifest.json'a
  `provider_used` kaydetme henüz yapılmadı.
- **Dil duyarlılığı** — fixture'lar Türkçe; İngilizce-only model'larda
  düşük skor "model kötü" değil "Türkçe zayıf" sinyali olabilir.
  Gerekirse `fixtures/_en/` paralel set eklenebilir.
