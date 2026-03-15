```yaml
---
name: GBot Philosophy
description: Core design philosophy and principles — personal assistant, simple but manageable, useful without complexity
type: project
---

```

GBot, özünde her zaman bir **personal assistant** olarak tasarlanmıştır. Projenin temel prensipleri şunlardır:

1. **Öncelik "Useful" Olmasıdır:** Birincil hedef her zaman fayda (utility) sağlamasıdır; ancak bu faydayı sunarken gereksiz kompleksiteden kaçınmayı ilke edinir.
2. **Basit, Yönetilebilir ve Anlaşılır:** Sistem karmaşık görevlerin altından kalkabilecek kapasitededir, ancak bu yetenek sistemin izlenebilirliğini (traceability) ve şeffaflığını asla gölgelememelidir.
3. **Kesin Modülarite ve Genişletilebilirlik:** Eklenebilir olmaktan asla ödün verilmez. Her yeni özellik, yetenek (skill) veya araç (tool) opsiyonel, izole ve sistemin geri kalanından bağımsız (decoupled) kalmalıdır.
4. **Kara Kutu Değil, Cam Kutu (White-box):** Sistemin iç işleyişi her zaman görünür, *debug* edilebilir ve öngörülebilir olmalıdır. Kapalı kapılar ardında ne olduğu belirsiz süreçler yürütülmez.
5. **Şeffaf ve Yönetilebilir Hafıza (State):** GBot öğrenir ve hatırlar, ancak bu hafıza opak bir sihir değildir. Ajanın ne öğrendiği, bilginin kaynağı ve nasıl kullanıldığı her an izlenebilir ve yönetilebilir durumdadır. Kullanıcı her an *"Ne biliyorsun ve bu kararı neden verdin?"* sorusunun cevabını veriden okuyabilmelidir.
6. **Minimalist Execution:** Ajan her şeyi tek başına yapmak veya tüm bağlamı (context) aynı anda hatırlamak zorunda değildir. Basit bir işlem için ağır bir *agent loop*'u tetiklenmez veya tüm veri modele aktarılmaz. Sadece ihtiyaç duyulan iş için gereken kadar kaynak ve bilgi kullanılır — fazlası israf, azı yetersizdir.
7. **Varsayılan Olarak Gözlemlenebilirlik (Observability by Default):** Her not, davranış ve aksiyon izlenir, kaydedilir. Bu kayıtlar gizli log dosyalarına gömülmez; *dashboard*, API veya CLI üzerinden kolayca erişilebilir formattadır. *Observability* sonradan eklenen bir lüks değil, temel bir mimari gereksinimdir.
8. **Framework'e Yaslanma, Framework'ü Kullan (Agnostik Altyapı):** Hiçbir framework'ün *opinionated* yapısına sıkı sıkıya bağlanılmaz. LLM *provider*, veritabanı veya orkestrasyon aracı değişse bile *core* sistem ayakta kalır. Sağlam *abstraction* katmanları, dış bağımlılıkları izole eder ve kontrolün her zaman projede kalmasını sağlar.

**Why:** Bu prensipler GBot'un DNA'sını oluşturur. Alınan her mimari ve tasarımsal karar bu felsefeyle doğrudan uyumlu olmak zorundadır.

**How to apply:** Yeni bir özellik eklerken şu kritik soruyu sor: *"Bu eklenti sistemi daha kullanışlı mı yapıyor, yoksa sadece daha karmaşık mı?"*

* Cevap **"İkisi de"** ise: Karmaşıklığı doğru *abstraction*'lar arkasına gizlemenin (iyi mimari) bir yolunu bul.
* Cevap **"Sadece karmaşık"** ise: O özelliği yapma.

