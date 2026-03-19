# GBot Philosophy

## Kimlik

GBot gerçek bir kişisel asitandır. Kullanıcısını tanır — mesajlarını, isteklerini, aksiyonlarını düzenli olarak kayıt altına alır ve analiz eder. Farklı kanallardan (Telegram, WhatsApp, API, mobil) gelen etkileşimleri ayrı ayrı tutar ama birlikte ve sürekli analiz eder. Zaman içinde kullanıcısını daha iyi tanır, tercihlerini öğrenir, yakın çevresini bilir. Bu GBot'un var oluş sebebidir.

Her şeyi yapmak zorunda değildir, her şey için bir plugin olmak zorunda değildir. Ama kullanıcısını iyi tanımak zorundadır — context, kullanıcı özelinde son derece kişisel olmalıdır.

---

## Prensipler

1. **Useful Olmak Önceliktir:** Birincil hedef her zaman fayda sağlamaktır; ancak bu faydayı sunarken gereksiz kompleksiteden kaçınmayı ilke edinir.

2. **Basit, Yönetilebilir ve Anlaşılır:** Sistem karmaşık görevlerin altından kalkabilecek kapasitededir, ancak bu yetenek sistemin izlenebilirliğini ve şeffaflığını asla gölgelememelidir.

3. **Her Şey Görünür, Her Şey İzlenebilir:** LLM call, tool call, hafıza değişikliği, karar, log — her şey kayıt altındadır. Bu kayıtlar gizli log dosyalarına gömülmez; dashboard, API veya CLI üzerinden kolayca erişilebilir formattadır. Observability sonradan eklenen bir lüks değil, temel bir mimari gereksinimdir.

4. **Kontrol Edilebilir Bağlam:** GBot öğrenir ve hatırlar, ancak bu hafıza opak bir sihir değildir. Ne öğrendiği, bilginin kaynağı ve nasıl kullanıldığı her an izlenebilir ve yönetilebilir durumdadır. Bugün öğrendiği bilgi 6 ay sonra hâlâ değerlidir. Kullanıcı her an *"Ne biliyorsun ve bu kararı neden verdin?"* sorusunun cevabını veriden okuyabilmelidir.

5. **Tek Kimlik, Ayrı Bağlam:** Kullanıcı kanaldan bağımsız tanınır — hafıza, tercih, notlar ortak veritabanındadır. Ancak her kanalın konuşma bağlamı birbirinden ayrıdır; bir kanalda konuşulan diğerine otomatik taşınmaz. Ortaklık kimlikte, ayrışma bağlamdadır.

6. **Minimalist Execution:** Ajan her şeyi tek başına yapmak veya tüm bağlamı aynı anda hatırlamak zorunda değildir. Basit bir işlem için ağır bir agent loop tetiklenmez veya tüm veri modele aktarılmaz. Sadece ihtiyaç duyulan iş için gereken kadar kaynak ve bilgi kullanılır — fazlası israf, azı yetersizdir.

7. **Modüler ve Agnostik Altyapı:** Her yeni özellik, yetenek veya araç opsiyonel, izole ve sistemin geri kalanından bağımsız kalmalıdır. Hiçbir framework'ün yapısına sıkı sıkıya bağlanılmaz. LLM provider, veritabanı veya orkestrasyon aracı değişse bile core sistem ayakta kalır.

---

**Kritik Soru:** Yeni bir özellik eklerken: *"Bu sistemi daha kullanışlı mı yapıyor, yoksa sadece daha karmaşık mı?"*

* **İkisi de** → Karmaşıklığı doğru abstraction'lar arkasına gizle.
* **Sadece karmaşık** → Yapma.
