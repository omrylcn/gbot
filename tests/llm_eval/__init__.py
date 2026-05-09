"""LLM evaluation suite (Faz 22E Step 5).

Memory pipeline'ında 3 LLM call var:
1. Fact extraction (MemoryService)
2. AUDN decision (MemoryService._audn_decide)
3. Entity page compilation (EntityPageCompiler)

Bu paket her biri için bir eval suite içerir. Default'ta config'deki
``memory.model`` ile çalışır; ``--model=<id>`` argümanı ile farklı
bir model denenebilir (yeni model çıkınca veya A/B karşılaştırması
için).

API key (OPENROUTER_API_KEY) yoksa testler skip edilir.
"""
