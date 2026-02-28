# Session Summarization & Fact Extraction Policy

> Versiyon: 1.7.0 | Tarih: 2026-02-19

---

## 1. Genel Bakis

GraphBot, uzun konusmalarda bilgi kaybini onlemek icin **iki katmanli hafiza mimarisi** kullanir:

| Katman | Zamanlama | Mekanizma | Hedef |
|--------|-----------|-----------|-------|
| **Proaktif** | Session boyunca | Agent tool cagrilari | Yapisal veri → DB tablolari |
| **Reaktif** | Session sonunda | LLM otomatik cikarma | Kayip bilgiyi kurtarma |

Bu iki katman birbirini tamamlar: proaktif katman agent'in bilinçli kaydettigi verileri yakalar, reaktif katman ise konusmada gecen ama tool ile kaydedilmemis bilgileri kurtarir.

---

## 2. Ne Zaman Tetiklenir?

Session summarization, **token limiti asildigi zaman** otomatik tetiklenir:

```
GraphRunner.process()
  → token_count >= config.assistant.session_token_limit (default: 30,000)
  → _rotate_session(user_id, session_id)
```

**Tetikleme sureci:**
1. Her LLM yaniti sonrasi `token_count` guncellenir
2. Limit asilidiginda `_rotate_session()` cagirilir
3. Mevcut session kapatilir, yeni session otomatik olusturulur
4. Kullanici icin kesintisiz gecis saglanir

---

## 3. Veri Akisi

### 3.1 Session Sırasında (Proaktif)

Agent, konusma sirasinda su tool'lari kullanarak yapisal veri kaydeder:

| Tool | Hedef Tablo | Ornek |
|------|-------------|-------|
| `save_user_note` | `user_notes` | "Kullanici Django projesi uzerinde calisiyor" |
| `set_user_preference` | `preferences` | language=Turkce, theme=dark |
| `get_user_preferences` | `preferences` (okuma) | Mevcut tercihleri gorme |
| `remove_user_preference` | `preferences` | Tercih silme |
| `log_activity` | `activity_logs` | "RAG araması: Python tutorials" |
| `add_favorite` | `favorites` | Favori oge ekleme |

### 3.2 Session Sonunda (Reaktif)

`_rotate_session()` su adimlari izler:

```
_rotate_session(user_id, session_id)
│
├─ 1. get_recent_messages(session_id, limit=50)
│     Son 50 mesaji DB'den al
│
├─ 2. _prepare_summary_messages(db_messages)
│     Tool mesajlarini ve bos icerikleri filtrele
│     Sadece user/assistant mesajlarini birak
│
├─ 3. asummarize(llm_messages) → hybrid summary
│     Model: gpt-4o-mini (ucuz, hizli)
│     Format: Narratif paragraf + yapisal maddeler
│     Max tokens: 500
│
├─ 4. aextract_facts(llm_messages) → JSON
│     Model: gpt-4o-mini
│     Structured output (response_format: json_object)
│     Max tokens: 300
│
├─ 5. _save_extracted_facts(user_id, facts)
│     ├─ preferences → db.update_preferences() (JSON merge)
│     └─ notes → db.add_note(source="extraction")
│
└─ 6. db.end_session(summary=hybrid_text, close_reason="token_limit")
```

---

## 4. Summary Formati (Hybrid)

Summary iki bolumden olusur:

### 4.1 Narratif Paragraf (2-4 cumle)

Konusmanin ana akisini, onemli kararlari ve baglami yakalar. Ornek:

> Kullanici Django projesinde authentication sistemi uzerinde calisiyordu.
> JWT token yapisi ve session yonetimi hakkinda sorular sordu.
> OAuth2 yerine basit JWT yaklasimini tercih ettigi belirlendi.

### 4.2 Yapisal Maddeler

```
- TOPICS: Django authentication, JWT token, session yonetimi
- DECISIONS: OAuth2 yerine JWT tercih edildi, bcrypt hashing
- PENDING: Token yenileme mekanizmasi henuz uygulanmadi
- USER_INFO: Yazilim muhendisi, Django projesi uzerinde calisiyor
```

**Neden hybrid format:**
- Narratif kisim → ContextBuilder'da okunabilir baglam saglar (insan ve LLM icin)
- Yapisal maddeler → hizli referans, fact extraction'a ek girdi olabilir
- 500 token butceye sigar (300 kelime ≈ 400-450 token)
- Icerik olmayan bolumler atlanir (yer tasarrufu)

---

## 5. Fact Extraction

### 5.1 Nedir?

Konusmadan yapisal veriler (tercihler, kisisel bilgiler) cikarilir ve DB tablolarina kaydedilir. Bu, tool'larin yakalayamadigi bilgileri kurtarma mekanizmasidir.

### 5.2 Cikti Formati

```json
{
  "preferences": [
    {"key": "language", "value": "Turkce"},
    {"key": "theme", "value": "dark"}
  ],
  "notes": [
    "Yazilim muhendisi, Django ile calisiyor",
    "Projesinde JWT authentication kullaniyor"
  ]
}
```

### 5.3 Kayit Yeri

| Veri Tipi | Hedef Tablo | Kayit Yontemi | Kaynak Etiketi |
|-----------|-------------|---------------|----------------|
| Preferences | `preferences` | JSON merge (`update_preferences`) | — |
| Notes | `user_notes` | Satir ekleme (`add_note`) | `source="extraction"` |

**`source="extraction"`** etiketi, tool ile kaydedilen notlardan (`source="conversation"`) ayirt etmek icindir.

### 5.4 Neden Ayri Fonksiyon?

`asummarize` ve `aextract_facts` birlestirilmedi cunku:
- Tek sorumluluk: summary = metin, extraction = JSON
- JSON parse hatasi summary'yi etkilemez
- Biri basarisiz olursa digeri calisir
- Her ikisi de gpt-4o-mini kullanir — toplam maliyet ~$0.002/rotation

---

## 6. Yeni Session'da Kullanim

Kaydedilen veriler yeni session basladiginda ContextBuilder tarafindan enjekte edilir:

```
ContextBuilder.build()
│
├─ Layer 4: agent_memory (kalici notlar)
│
├─ Layer 5: user_context
│     ├─ user_notes (tool + extraction kaynakli)
│     ├─ activity_logs
│     ├─ favorites
│     └─ preferences (tool + extraction kaynakli)  ← Yeni veriler burada
│
└─ Layer 7: session_summary (hybrid text)
      └─ get_last_session_summary() → sessions.summary
```

**Token butceleri:**

| Layer | Butce (token) | Butce (~karakter) |
|-------|---------------|-------------------|
| identity | 500 | ~2000 |
| agent_memory | 500 | ~2000 |
| user_context | 1500 | ~6000 |
| session_summary | 500 | ~2000 |
| skills | 1000 | ~4000 |

---

## 7. Hata Yonetimi

| Senaryo | Davranis |
|---------|----------|
| `asummarize` basarisiz | Fallback: "Session closed due to token limit (summary unavailable)." |
| `aextract_facts` basarisiz | Warning log, session yine kapatilir, fact'ler kaybolur |
| `aextract_facts` gecersiz JSON | Fonksiyon internal try/except, bos dict doner |
| Bos konusma (sadece tool mesajlari) | `asummarize`/`aextract_facts` cagrilmaz, fallback summary |
| Iki istek ayni anda token limit | `end_session` idempotent (UPDATE WHERE), ikisi de calisir |
| Kapatilmis session_id gonderildi | Yeni session olusturulur (bug fix) |

**Temel prensip:** Session her kosulda kapatilir. Summary ve fact extraction "best-effort" — basarisizlik session kapatmayi engellemez.

---

## 8. Maliyet Analizi

Her session rotation icin:

| Islem | Model | Input | Output | Maliyet |
|-------|-------|-------|--------|---------|
| Summary | gpt-4o-mini | ~2000 token | ~500 token | ~$0.001 |
| Fact extraction | gpt-4o-mini | ~2000 token | ~300 token | ~$0.001 |
| **Toplam** | | | | **~$0.002** |

Bu maliyet, 30k token'lik bir session icin ihmal edilebilir duzeydedir.

---

## 9. Ilgili Dosyalar

| Dosya | Rol |
|-------|-----|
| `graphbot/core/providers/litellm.py` | `asummarize()`, `aextract_facts()` |
| `graphbot/agent/runner.py` | `_rotate_session()`, `_save_extracted_facts()`, `_prepare_summary_messages()` |
| `graphbot/agent/tools/memory_tools.py` | Preference tool'lari (set/get/remove) |
| `graphbot/agent/context.py` | ContextBuilder — summary ve user_context enjeksiyonu |
| `graphbot/memory/store.py` | DB CRUD — `update_preferences()`, `add_note()`, `remove_preference()` |
| `roles.yaml` | Tool grubu: memory (preference tool'lari dahil) |

---

## 10. Gelecek Iyilestirmeler

- **Proaktif memory**: Konusmadan otomatik insight cikarma (session sonunu beklemeden)
- **Memory explorer**: Kaydedilen bilgileri goruntuleme/filtreleme/silme (Faz 25)
- **Ozet zincirleme**: Birden fazla onceki session'in ozetlerini birlestirme
- **Adaptive token limit**: Konusma karmasikligina gore dinamik limit
