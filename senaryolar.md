# Delegation Refactor — Test Senaryolari

## Mimari Ozet

```
Kullanici mesaji
  → Ana Agent (GraphRunner)
    → delegate tool
      → DelegationPlanner.plan(task) — tek LLM call
        → {execution, processor, tools, prompt, ...}

Execution (NE ZAMAN):
  immediate → SubagentWorker.spawn()
  delayed   → CronScheduler.add_reminder()
  recurring → CronScheduler.add_job()
  monitor   → CronScheduler.add_job(notify_skip)

Processor (NASIL):
  static   → duz text, scheduler gonderir
  function → direkt tool.ainvoke(), LLM yok, scheduler gonderMEZ
  agent    → LightAgent calisir, agent KENDISI gonderir (send_message_to_user)
```

---

## Senaryo 1: Basit Hatirlatma (delayed/static)

**Mesaj:** "2 saat sonra toplantim var hatırlat"
**Beklenen:** execution=delayed, processor=static, delay_seconds=7200

**Akis:**
1. Ana agent → delegate(task="2 saat sonra toplantim var hatırlat")
2. Planner → delayed/static, message="Toplanti hatirlatma!"
3. Scheduler.add_reminder(delay=7200, processor="static")
4. 2 saat sonra → scheduler._send_to_channel(text) → Telegram'a mesaj

**Kontrol:**
- [ ] Planner dogru karar verdi mi (delayed/static)
- [ ] Reminder DB'ye kaydedildi mi
- [ ] Suresi gelince mesaj geldi mi
- [ ] Tek mesaj geldi mi (duplike yok)

---

## Senaryo 2: Gecikmeli Mesaj Gonderme (delayed/function)

**Mesaj:** "5 dakika sonra Murat'a naber yaz"
**Beklenen:** execution=delayed, processor=function, tool_name=send_message_to_user

**Akis:**
1. Ana agent → delegate(task="5 dk sonra Murat'a naber yaz")
2. Planner → delayed/function, tool_name="send_message_to_user", tool_args={target_user:"Murat", message:"naber"}
3. Scheduler.add_reminder(delay=300, processor="function", plan_json=...)
4. 5 dk sonra → scheduler direkt tool.ainvoke({target_user:"Murat", message:"naber"})
5. LLM yok, agent yok — ucuz ve guvenilir

**Kontrol:**
- [ ] Planner function secti mi
- [ ] tool_name ve tool_args dogru mu
- [ ] Suresi gelince Murat'a mesaj gitti mi
- [ ] Owner'a ekstra mesaj gitmedi mi (function = no delivery)

---

## Senaryo 3: Gecikmeli Arastirma (delayed/agent)

**Mesaj:** "2 dakika sonra hava durumunu soyle"
**Beklenen:** execution=delayed, processor=agent, tools=[web_search, send_message_to_user]

**Akis:**
1. Ana agent → delegate(task="2 dk sonra hava durumunu soyle")
2. Planner → delayed/agent, tools=["web_search","send_message_to_user"], prompt="Hava durumunu kontrol et ve kullaniciya gonder."
3. Scheduler.add_reminder(delay=120, processor="agent", plan_json=...)
4. 2 dk sonra → LightAgent calisir:
   - web_search("Istanbul hava durumu") → sonuc alir
   - send_message_to_user(target_user="owner", message="22°C, parcali bulutlu") → mesaj gonderir
5. Scheduler teslim etMEZ (return text, False) — agent kendi gonderdi

**Kontrol:**
- [ ] Planner agent secti mi
- [ ] tools listesinde send_message_to_user var mi
- [ ] LightAgent web_search cagirdi mi
- [ ] LightAgent send_message_to_user cagirdi mi
- [ ] TEK mesaj geldi mi (agent gonderdi, scheduler gondermedi)

---

## Senaryo 4: Karmasik Gecikmeli (delayed/agent + farkli kanal + farkli kisi)

**Mesaj:** "2 dakika sonra hava durumunu Murat'a WhatsApp uzerinden gonder ve 'arkadasin olarak seni dusunmeliyim' mesajini ekle"
**Beklenen:** execution=delayed, processor=agent, tools=[web_search, send_message_to_user]

**Akis:**
1. Ana agent → delegate(task="...", channel="whatsapp")
2. Planner → delayed/agent, prompt icinde Murat + WhatsApp + ozel mesaj bilgisi var
3. Scheduler.add_reminder(delay=120, processor="agent")
4. 2 dk sonra → LightAgent calisir:
   - web_search("Istanbul hava durumu")
   - send_message_to_user(target_user="Murat", message="Arkadasin olarak seni dusunmeliyim! Hava 22°C...", channel="whatsapp")
5. Mesaj Murat'a WhatsApp'tan gider

**Kontrol:**
- [ ] Planner prompt'a hedef kisi (Murat) ve kanal (WhatsApp) bilgisini yazdi mi
- [ ] LightAgent send_message_to_user'i Murat'a yonlendirdi mi
- [ ] WhatsApp uzerinden gitti mi
- [ ] Owner'a ekstra mesaj gitmedi mi

---

## Senaryo 5: Anlik Arastirma (immediate/agent)

**Mesaj:** "Bitcoin fiyatini arastir"
**Beklenen:** execution=immediate, processor=agent

**Akis:**
1. Ana agent → delegate(task="Bitcoin fiyatini arastir")
2. Planner → immediate/agent, tools=["web_search","web_fetch","send_message_to_user"]
3. SubagentWorker.spawn() → LightAgent hemen calisir
4. LightAgent web_search yapar, sonucu send_message_to_user ile gonderir
5. Worker da DB'ye kaydeder + system_event olusturur

**Kontrol:**
- [ ] Planner immediate secti mi
- [ ] Worker spawn etti mi
- [ ] LightAgent calisip sonuc dondu mu
- [ ] Sonuc kullaniciya ulasti mi

---

## Senaryo 6: Tekrarli Arastirma (recurring/agent)

**Mesaj:** "Her sabah 9'da hava durumunu bildir"
**Beklenen:** execution=recurring, processor=agent, cron_expr="0 9 * * *"

**Akis:**
1. Ana agent → delegate(task="Her sabah 9'da hava durumunu bildir")
2. Planner → recurring/agent, cron_expr="0 9 * * *", tools=["web_search","send_message_to_user"]
3. Scheduler.add_job(cron_expr, processor="agent", plan_json=...)
4. Her sabah 9'da → LightAgent calisir, hava durumunu arastirir, send_message_to_user ile gonderir

**Kontrol:**
- [ ] Planner recurring secti mi
- [ ] cron_expr dogru mu ("0 9 * * *")
- [ ] APScheduler'a job kaydedildi mi
- [ ] Ilk tetiklemede agent calisip mesaj gonderiyor mu
- [ ] Tekrarli calisiyor mu

---

## Senaryo 7: Tekrarli Mesaj (recurring/function)

**Mesaj:** "Her 10 dakikada Zeynep'e selam yaz"
**Beklenen:** execution=recurring, processor=function, cron_expr="*/10 * * * *"

**Akis:**
1. Ana agent → delegate(task="Her 10 dk'da Zeynep'e selam yaz")
2. Planner → recurring/function, cron_expr="*/10 * * * *", tool_name="send_message_to_user", tool_args={target_user:"Zeynep", message:"selam"}
3. Scheduler.add_job(cron_expr, processor="function", plan_json=...)
4. Her 10 dk'da → scheduler direkt tool.ainvoke() — LLM yok

**Kontrol:**
- [ ] Planner function secti mi
- [ ] tool_name ve tool_args dogru mu
- [ ] Her 10 dk'da Zeynep'e mesaj gidiyor mu
- [ ] LLM cagrisi yok (ucuz)

---

## Senaryo 8: Monitor/Alert (monitor/agent)

**Mesaj:** "Altin 3000 TL'yi gecerse haber ver"
**Beklenen:** execution=monitor, processor=agent, cron_expr="*/30 * * * *"

**Akis:**
1. Ana agent → delegate(task="Altin 3000 gecerse haber ver")
2. Planner → monitor/agent, cron_expr="*/30 * * * *", tools=["web_fetch","send_message_to_user"], prompt="Altin fiyatini kontrol et. 3000 ustundeyse bildir. Degilse [SKIP]."
3. Scheduler.add_job(cron_expr, processor="agent", notify_condition="notify_skip")
4. Her 30 dk'da → LightAgent calisir:
   - Altin < 3000 → "[SKIP]" → scheduler atlar, mesaj gitmez
   - Altin > 3000 → agent send_message_to_user ile bildirir

**Kontrol:**
- [ ] Planner monitor secti mi
- [ ] notify_condition="notify_skip" ayarlandi mi
- [ ] Kosul saglanmadiginda [SKIP] donuyor mu
- [ ] Kosul saglandiginda mesaj geliyor mu

---

## Senaryo 9: Dogrudan Mesaj (delegation'a gitmez)

**Mesaj:** "Murat'a selam yaz" (delay yok, schedule yok)
**Beklenen:** Ana agent dogrudan send_message_to_user cagirir, delegate'e gitmez

**Akis:**
1. Ana agent → send_message_to_user(target_user="Murat", message="selam")
2. Direkt gider, delegation/planner/scheduler kullanilmaz

**Kontrol:**
- [ ] delegate cagirilmadi
- [ ] send_message_to_user dogrudan cagirildi
- [ ] Mesaj Murat'a ulasti

---

## Senaryo 10: Dogrudan Soru (delegation'a gitmez)

**Mesaj:** "Hava durumu ne?" (delay yok, background gerektirmiyor)
**Beklenen:** Ana agent dogrudan web_fetch/web_search yapar, delegate'e gitmez

**Akis:**
1. Ana agent → web_search("hava durumu") veya web_fetch(...)
2. Sonucu dogrudan Telegram'a yazar

**Kontrol:**
- [ ] delegate cagirilmadi
- [ ] Ana agent dogrudan arac kullanip cevap verdi

---

## Processor Ozet Tablosu

| Processor | LLM? | Kim gonderir? | Ornek |
|-----------|------|---------------|-------|
| static | Hayir | Scheduler (_send_to_channel) | "toplanti hatırlat" |
| function | Hayir | Kimse (action kendisi yeterli) | "Murat'a naber yaz" |
| agent | Evet | LightAgent (send_message_to_user) | "hava durumunu bildir" |

## Onemli Kurallar

1. **Agent processor = LightAgent tam sorumluluk.** Agent mesajı kime göndereceğine kendisi karar verir (planner prompt'ta yazılı). Scheduler agent için teslim etMEZ.
2. **Function processor = LLM yok.** Planner tool_name ve tool_args belirler, scheduler direkt çağırır. Ucuz ve güvenilir.
3. **Static processor = en basit.** Düz text, scheduler gönderir.
4. **Duplike mesaj olmamalı.** Her processor tipi için tek bir teslim kanalı var.
5. **json_schema structured output** planner'ın her zaman valid JSON dönmesini garanti eder.
