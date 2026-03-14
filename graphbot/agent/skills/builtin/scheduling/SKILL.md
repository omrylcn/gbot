---
name: scheduling
description: Zamanlama araçları karar ağacı — hatırlatıcı, cron job ve alert oluşturma rehberi
always: false
metadata:
  requires: {}
---

# Zamanlama Araçları — Karar Ağacı

Kullanıcı zamanlama ile ilgili istekte bulunursa, aşağıdaki karar ağacını takip et:

## Tek seferlik mi, tekrarlı mı?

**Tek seferlik** → `create_reminder`
- "2 saat sonra toplantıyı hatırlat" → `create_reminder(delay_seconds=7200, message="Toplantı!")`
- "5 dk sonra Murat'a mesaj at" → `create_reminder(delay_seconds=300, message="Send message to Murat", agent_prompt="...", agent_tools=["send_message_to_user"])`

**Tekrarlı, her zaman bildir** → `add_cron_job`
- "Her sabah 9'da günaydın de" → `add_cron_job(cron_expr="0 9 * * *", message="Günaydın!")`
- "Her 10 dk Murat'a selam yaz" → `add_cron_job(cron_expr="*/10 * * * *", message="Send greeting", agent_prompt="...", agent_tools=["send_message_to_user"])`

**Tekrarlı, sadece koşul sağlanırsa bildir** → `create_alert`
- "Altın 7500'ü geçerse bildir, her 30 dk kontrol et" → `create_alert(cron_expr="*/30 * * * *", check_message="web_fetch('gold') ile altın fiyatını kontrol et. Gram altın 7500 TL üstüyse fiyatı bildir, değilse [SKIP] de.", agent_tools=["web_fetch"])`

## create_alert Kullanım Kuralları

**ÖNEMLİ:** `check_message` bir GÖREV TALİMATIDIR, bildirim metni DEĞİLDİR.

- YANLIŞ: `check_message="Altın fiyatı 7500'ü geçti!"` ← Bu bir sonuç, görev değil
- DOĞRU: `check_message="web_fetch('gold') ile altın fiyatını kontrol et. 7500 TL üstüyse bildir, değilse [SKIP]."` ← Bu bir görev talimatı

`check_message` içinde şu bilgiler olmalı:
1. Hangi tool ile ne kontrol edilecek (örneğin web_fetch('gold'))
2. Koşul nedir (örneğin 7500 TL üstü)
3. Koşul sağlanmazsa [SKIP] dönülmesi gerektiği

`agent_tools` parametresini mutlaka belirt — agent'ın koşulu kontrol etmesi için hangi araçlara ihtiyacı olduğunu.

## Agent Mod vs Static Mod

- `agent_prompt` parametresi **varsa** → Agent mod (LightAgent çalışır, tool kullanır)
- `agent_prompt` parametresi **yoksa** → Static mod (mesaj olduğu gibi iletilir)

Agent mod gereken durumlar: mesaj gönderme, web'den veri çekme, bilgi arama
Static mod yeterli durumlar: basit hatırlatma, sabit metin bildirimi

## Model Seçim Kuralları

- Basit bildirim/mesaj → ucuz model (haiku/flash sınıfı)
- Analiz/akıl yürütme → standart model
- Ana agent her zaman hangi modelin kullanılacağına karar verir
