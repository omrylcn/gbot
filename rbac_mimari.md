# Mimari Karar: Role-Based Access Control (RBAC)

_Tarih: 2026-02-19_
_Durum: Onaylandı_

---

## 1. Problem

Tüm kullanıcılar aynı tool seti, context ve veriye erişiyor. Owner'ın shell/filesystem erişimi ile guest'in web araması aynı seviyede. CLI sadece owner kullanmalı ama kanal bazlı ayrım yok.

**Mevcut durum:**
- `users.role` DB'de var (default: `'user'`) ama hiç kullanılmıyor
- `make_tools()` herkese aynı listeyi döndürüyor
- ContextBuilder rol farkı gözetmiyor
- JWT'de rol bilgisi yok
- Admin endpointlerde sadece `_require_owner()` var (config-based)

---

## 2. Üç Rol

| Rol | Kim | Nereden gelir |
|-----|-----|---------------|
| **owner** | Sistem sahibi, tam yetki | `config.yaml` → `assistant.owner.username` |
| **member** | Kayıtlı kullanıcı, standart yetki | Admin tarafından atanır |
| **guest** | Tanınmayan/yeni kullanıcı, sınırlı | Default — yeni user oluşturulduğunda |

---

## 3. Tool Grupları

Tool'lar tek tek değil, gruplar halinde yönetilir. Yeni tool eklendiğinde grubuna düşer, roller otomatik güncellenir.

| Grup | Tool'lar | Açıklama |
|------|----------|----------|
| **memory** | save_user_note, get_user_context, log_activity, get_recent_activities, add_favorite, get_favorites, remove_favorite | Kişisel veri yönetimi |
| **search** | search_items, get_item_detail | RAG / bilgi tabanı |
| **web** | web_search, web_fetch | Dış dünya erişimi |
| **filesystem** | read_file, write_file, edit_file, list_dir | Dosya sistemi (tehlikeli) |
| **shell** | exec_command | Komut çalıştırma (tehlikeli) |
| **scheduling** | add_cron_job, list_cron_jobs, remove_cron_job, create_alert, create_reminder, list_reminders, cancel_reminder | Zamanlama |
| **messaging** | send_message_to_user | Kullanıcılar arası mesaj |
| **delegation** | delegate | Alt görev atama |

---

## 4. Rol → Yetki Matrisi

### Tool Erişimi

| Grup | owner | member | guest |
|------|-------|--------|-------|
| memory | ✅ | ✅ | ❌ |
| search | ✅ | ✅ | ❌ |
| web | ✅ | ✅ | ✅ |
| filesystem | ✅ | ❌ | ❌ |
| shell | ✅ | ❌ | ❌ |
| scheduling | ✅ | ✅ | ❌ |
| messaging | ✅ | ✅ | ❌ |
| delegation | ✅ | ❌ | ❌ |

### Context Katmanları

| Katman | owner | member | guest |
|--------|-------|--------|-------|
| identity | ✅ | ✅ | ✅ |
| runtime (user_id, datetime) | ✅ | ✅ | ✅ |
| role description | ✅ | ✅ | ✅ |
| agent_memory | ✅ | ✅ | ❌ |
| user_context (notes, favorites) | ✅ | ✅ | ❌ |
| background_events | ✅ | ✅ | ❌ |
| session_summary | ✅ | ✅ | ❌ |
| skills | ✅ | ✅ | ❌ |

### Session & Veri

| Kural | owner | member | guest |
|-------|-------|--------|-------|
| Max session | ∞ | ∞ | 1 |
| Kendi verisini görür | ✅ | ✅ | ✅ |
| Başka user verisini görür | ✅ (admin) | ❌ | ❌ |
| CLI erişimi | ✅ | ❌ | ❌ |

---

## 5. Tanım Yeri: `roles.yaml`

Proje root'unda ayrı dosya. `config.yaml` zaten kalabalık — roller bağımsız bir concern.

Deploy ortamına göre farklı `roles.yaml` kullanılabilir. `roles.yaml` yoksa → fallback: tüm kullanıcılar owner yetkisinde (geriye uyumluluk).

---

## 6. Teknik Mimari

### Filtreleme Noktaları (3 katman)

```
User mesaj gönderir
    │
    ▼
[Runner] → DB'den user.role al → permissions.get_allowed_tools(role)
    │       → allowed_tools set'ini state'e koy
    │
    ▼
[load_context node] → permissions.get_context_layers(role)
    │                  → sadece izinli katmanları build et
    │
    ▼
[reason node] → tool_defs'i allowed_tools ile filtrele
    │            → LLM sadece izinli tool'ları görür
    │
    ▼
[execute_tools node] → çağrılan tool allowed_tools'da mı? (double-check)
    │                   → LLM hallucination guard
    │
    ▼
Response → kullanıcıya
```

### Neden Graph Recompile Yok?

Graph tüm tool'larla bir kez compile edilir. Per-request filtreleme `reason()` ve `execute_tools()` node'larında yapılır:
- **Performans:** Graph her request'te yeniden oluşturulmaz
- **Basitlik:** Tek graph instance, tool'lar state'e göre filtrelenir
- **Güvenlik:** İki katmanlı kontrol (LLM görmez + execution engellenir)

---

## 7. Değişecek Dosyalar

| Dosya | Değişiklik |
|-------|-----------|
| `roles.yaml` | **YENİ** — rol tanımları (~60 satır) |
| `graphbot/agent/permissions.py` | **YENİ** — YAML loader + tool/context check (~80 satır) |
| `graphbot/agent/state.py` | `role`, `allowed_tools` alanları |
| `graphbot/agent/runner.py` | Rol lookup, allowed_tools hesaplama, guest session |
| `graphbot/agent/nodes.py` | reason() filtreleme, execute_tools() guard |
| `graphbot/agent/context.py` | context_layers parametresi, katman filtreleme |
| `graphbot/memory/store.py` | `set_user_role()` metodu, migration |
| `graphbot/api/admin.py` | PUT /admin/users/{id}/role endpoint |

---

## 8. Geriye Uyumluluk

- `roles.yaml` yoksa → tüm kullanıcılara owner yetkisi (mevcut davranış korunur)
- Mevcut `role='user'` → migration'da `'member'`'a çevrilecek
- Owner kullanıcı config'den belirleniyor → startup'ta DB'de `role='owner'` yapılacak
- `allowed_tools=None` → hiç filtre yok (eski davranış)

---

## 9. Gelecek İyileştirmeler (Şimdi Yapılmayacak)

### 9.1 Linux-tarzı Grup Sistemi

Sabit 3 rol yerine esnek, kullanıcı tanımlı gruplar. Bir user birden fazla grupta olabilir, yetkiler union (birleşim) ile hesaplanır.

**Motivasyon:** Aile senaryosu (çocuk/yetişkin), takım senaryosu (departmanlar), proje bazlı erişim.

```yaml
# roles.yaml → groups.yaml evrimi
groups:
  admin:
    tool_groups: [memory, search, web, filesystem, shell, scheduling, messaging, delegation]
    context_layers: [identity, runtime, role, agent_memory, user_context, events, session_summary, skills]
    max_sessions: 0

  family_adult:
    tool_groups: [memory, web, scheduling, messaging]
    context_layers: [identity, runtime, role, agent_memory, user_context, events, session_summary, skills]
    max_sessions: 0

  family_child:
    tool_groups: [web]
    context_layers: [identity, runtime, role]
    max_sessions: 1
    content_filter: safe

  work_team:
    tool_groups: [memory, search, web, scheduling]
    context_layers: [identity, runtime, role, agent_memory, user_context, skills]
    max_sessions: 3

# Kullanıcı → grup ataması
user_groups:
  murat: [family_adult, work_team]   # iki grubun birleşimi
  elif: [family_child]               # sadece web, güvenli içerik
```

**Teknik etki:**
- DB: `user_groups` tablosu (user_id, group_name)
- `permissions.py`: `set.union(*[get_tools(g) for g in user_groups])` — en kısıtlayıcı değil, en geniş yetki
- `roles.yaml` → `groups.yaml` geçişi, geriye uyumlu (eski format desteklenir)

### 9.2 İçerik Filtreleme (Content Filter)

Grup bazlı LLM davranış kısıtlaması. System prompt'a enjekte edilir.

| Filtre | Açıklama |
|--------|----------|
| `safe` | Şiddet, yetişkin içerik, zararlı bilgi üretmez |
| `educational` | Sadece eğitim amaçlı yanıtlar, ödev yapma yerine açıklama |
| `business` | İş odaklı, kişisel sohbet sınırlı |

**Teknik etki:** `context.py`'da `content_filter` katmanı → system prompt'a ek talimat.

### 9.3 Zaman & Kullanım Kısıtlamaları

| Kısıtlama | Örnek | Açıklama |
|-----------|-------|----------|
| `allowed_hours` | `"09:00-21:00"` | Çocuk gece kullanamaz |
| `daily_limit` | `50` | Günlük mesaj sınırı |
| `rate_limit` | `10/min` | Dakikada max istek |

**Teknik etki:** `runner.py`'da process() başında kontrol, `429 Too Many Requests` veya bilgilendirme mesajı.

### 9.4 Per-User Override

Grup yetkilerinin üstüne kullanıcı bazlı ince ayar:

```yaml
user_overrides:
  murat:
    add_tools: [exec_command]      # grubunda yok ama bu user'a özel ekle
    remove_tools: [web_fetch]      # grubunda var ama bu user'dan çıkar
```

### 9.5 Diğer

- JWT'ye rol/grup bilgisi ekleme (her istekte DB'ye gitmemek için)
- API key scope (key bazında yetki sınırlama)
- Audit log per role (kim ne tool çağırdı, denied log'ları)
