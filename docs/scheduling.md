# Scheduling System — Reminders, Cron Jobs & Alerts

> Versiyon: 1.9.0 | Tarih: 2026-02-21

---

## 1. Genel Bakis

GraphBot **3 zamanlama tool'u** sunar. Hepsi ayni altyapiyi kullanir (APScheduler) ama farkli kullanim senaryolari icindir:

| Tool | Ne Zaman | Tekrar | Bildirim | Mod |
|------|----------|--------|----------|-----|
| `create_reminder` | X saniye sonra | Tek sefer | Her zaman | Static veya Agent |
| `add_cron_job` | Cron expression | Tekrarli | Her zaman | Static veya Agent |
| `create_alert` | Cron expression | Tekrarli | **Sadece kosul saglanirsa** | Sadece Agent |

---

## 2. Tool Detaylari

### 2.1 create_reminder — Tek Seferlik Hatirlatma

**Kullanim:** "X dakika/saat sonra sunu yap"

**Iki modu var:**

**Static mod** — Mesaj olduğu gibi iletilir, LLM çağrılmaz:
```
Kullanici: "2 saat sonra toplanti var diye hatırlat"
→ create_reminder(delay_seconds=7200, message="Toplantı hatırlatması!")
→ 2 saat sonra → kullaniciya "Toplantı hatırlatması!" gider
```

**Agent mod** — LightAgent calistirilir, tool kullanabilir:
```
Kullanici: "5 dk sonra Murat'a naber yaz"
→ create_reminder(
    delay_seconds=300,
    message="Send 'naber' to Murat",
    agent_prompt="Use send_message_to_user tool to send 'naber' to user Murat.",
    agent_tools=["send_message_to_user"]
  )
→ 5 dk sonra → LightAgent → send_message_to_user("Murat", "naber")
```

**Karar agaci:** `agent_prompt` varsa → Agent mod, yoksa → Static mod.

---

### 2.2 add_cron_job — Tekrarli Gorev

**Kullanim:** "Her gun/saat/dakika sunu yap"

Cron expression kullanir (APScheduler CronTrigger):
```
*/10 * * * *     → Her 10 dakikada
0 9 * * *        → Her gun saat 09:00
0 9 * * 1-5      → Hafta ici her gun 09:00
0 */2 * * *      → Her 2 saatte bir
```

**Ornekler:**

Static:
```
Kullanici: "Her sabah 9'da 'gunaydin' de"
→ add_cron_job(cron_expr="0 9 * * *", message="Günaydın!")
→ Her gun 09:00 → "Günaydın!" gider
```

Agent:
```
Kullanici: "Her 10 dk Murat'a selam yaz"
→ add_cron_job(
    cron_expr="*/10 * * * *",
    message="Send greeting to Murat",
    agent_prompt="Send 'selam' to user Murat using send_message_to_user tool.",
    agent_tools=["send_message_to_user"]
  )
→ Her 10 dk → LightAgent → Murat'a "selam"
```

---

### 2.3 create_alert — Akilli Izleme

**Kullanim:** "Sunu izle, onemli bir sey olursa bildir"

`create_alert` = `add_cron_job` + **NOTIFY/SKIP mekanizmasi**

Her tetiklemede LightAgent calisirir. Agent kontrol eder:
- Kosul saglandi → Kullaniciya bildirim gider (NOTIFY)
- Kosul saglanmadi → Sessizce gecilir ([SKIP])

```
Kullanici: "Altin fiyati 7500'u gectiyse bildir, her 30dk kontrol et"
→ create_alert(
    cron_expr="*/30 * * * *",
    check_message="web_fetch ile altin fiyatlarini kontrol et. Gram altin 7500 TL ustuyse bildir, degilse [SKIP] de."
  )
→ Her 30 dk → LightAgent:
    1. web_fetch("gold") → fiyat verisini al
    2. Fiyat < 7500 → "[SKIP]" → kullaniciya bildirim GITMEZ
    3. Fiyat >= 7500 → "Gram altin 7523 TL!" → kullaniciya bildirim GIDER
```

**ONEMLI:** `check_message` bir **görev talimatidir**, sonuc mesaji degil!
- Yanlis: `check_message="Altın 7500'ü geçti!"` (sonuc metni)
- Dogru: `check_message="Altin fiyatini kontrol et, 7500'u gectiyse bildir"` (gorev talimati)

---

## 3. Mimari — Nasil Calisiyor?

```
Kullanici mesaji
    │
    ▼
MainAgent (GraphRunner)
    │
    ├─ create_reminder() ──→ APScheduler DateTrigger
    ├─ add_cron_job()     ──→ APScheduler CronTrigger
    └─ create_alert()     ──→ APScheduler CronTrigger + NOTIFY/SKIP
                                    │
                                    ▼
                              Tetikleme zamani
                                    │
                    ┌───────────────┼───────────────┐
                    ▼               ▼               ▼
              Static mod      Agent mod       Alert (Agent)
              (mesaj gonder)  (LightAgent)   (LightAgent + SKIP)
                    │               │               │
                    ▼               ▼               ▼
              Telegram/API    Tool calistir    Kosul kontrol
                              → sonucu gonder  → SKIP veya bildir
```

### 3.1 Altyapi Bilesenleri

| Bilesen | Dosya | Gorev |
|---------|-------|-------|
| CronScheduler | `graphbot/core/cron/scheduler.py` | APScheduler yonetimi, job/reminder CRUD |
| LightAgent | `graphbot/agent/light.py` | Izole agent — kendi graph'i, kisitli tool seti |
| Background Registry | `graphbot/agent/tools/registry.py` | Agent modda kullanilabilecek tool'lar |
| Tool: cron_tool.py | `graphbot/agent/tools/cron_tool.py` | add_cron_job, list_cron_jobs, remove_cron_job, create_alert |
| Tool: reminder.py | `graphbot/agent/tools/reminder.py` | create_reminder, list_reminders, cancel_reminder |

### 3.2 Veritabani Tablolari

**cron_jobs** — Tekrarli gorevler:
```
job_id, user_id, cron_expr, message, channel, enabled,
agent_prompt, agent_tools, agent_model, notify_condition,
consecutive_failures, created_at
```

**reminders** — Tek seferlik hatirlatmalar:
```
reminder_id, user_id, channel, message, run_at, status,
agent_prompt, agent_tools, cron_expr, created_at
```

**cron_execution_log** — Her calisma kaydedilir:
```
log_id, job_id, result, status (success/error/skipped), duration_ms, executed_at
```

### 3.3 Hata Yonetimi

- **3 ardisik hata** → Job otomatik `paused` olur (`consecutive_failures >= 3`)
- Basarili calisma → `consecutive_failures` sifirlanir
- Execution log her calismada yazilir (basarili/basarisiz/skip)

---

## 4. Agent Mod — Kullanilabilir Tool'lar

Background agent (LightAgent) su tool'lara erisebilir:

| Tool | Aciklama |
|------|----------|
| `send_message_to_user` | Baska kullaniciya mesaj gonder |
| `web_search` | Web'de arama yap |
| `web_fetch` | URL veya shortcut'tan veri cek (gold, weather, vb.) |
| `save_memory` | Agent hafizasina kaydet |
| `search_memory` | Agent hafizasinda ara |

**Guvenlik:** `filesystem`, `shell`, `delegation`, `scheduling` gruplari background agent'ta **devre disi**dir.

---

## 5. Karar Agaci — Hangisini Kullanmaliyim?

```
Kullanici ne istiyor?
    │
    ├─ Tek seferlik?
    │   └─ create_reminder
    │       ├─ Basit hatirlatma → Static (agent_prompt=None)
    │       └─ Islem gerekiyor → Agent (agent_prompt + agent_tools)
    │
    └─ Tekrarli?
        ├─ Her zaman bildir → add_cron_job
        │   ├─ Basit mesaj → Static
        │   └─ Islem gerekiyor → Agent
        │
        └─ Sadece kosullu bildir → create_alert
            └─ check_message = gorev talimati
```

---

## 6. Bilinen Sinirlamalar

| Sinir | Aciklama | Cozum |
|-------|----------|-------|
| Scheduler cache | Cron silindiginde APScheduler'dan silinmiyor | Container restart veya `reload()` (TODO) |
| Timezone | Sunucu timezone'u kullanilir | config'e timezone eklenebilir |
| Min interval | APScheduler minimum ~1 saniye | Pratik limit: 1 dakika |
| Tool erişimi | Background agent tum tool'lara erişemez | Guvenlik geregi kisitli |
