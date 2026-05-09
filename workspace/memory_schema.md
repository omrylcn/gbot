# Memory Schema — Extraction Contract

This file defines the extraction contract that the memory agent uses when
turning a conversation into typed facts and entity relations. It is a
**public, editable schema** — change it here and the LLM extractor picks
up the change on next run, no code edit needed.

> Karpathy LLM-Wiki pattern: the schema is the canonical source. Code
> consumes it; humans edit it.

---

## Output format

The extractor returns a single JSON object:

```json
{
  "facts": [
    {
      "content": "...",
      "type": "semantic | episodic | preference | procedural | style",
      "confidence": 0.0-1.0,
      "category": "<one of the categories below>",
      "keywords": ["...", "..."]
    }
  ],
  "relations": [
    {"source": "<entity>", "relation": "<relation>", "target": "<entity>"}
  ]
}
```

If nothing is worth extracting, return `{"facts": [], "relations": []}`.

---

## Fact types

| Type | What it captures | Decay rate |
|------|------------------|------------|
| `semantic` | Enduring truths — name, location, work, skills, identity | Slow |
| `episodic` | Time-bound events — "had a meeting yesterday" | Fast |
| `preference` | Likes / dislikes / settings — "prefers dark theme", "vegetarian" | Medium |
| `procedural` | Behavioral patterns — "checks stock market every morning" | Slow |
| `style` *(Faz 22G)* | Communication style — tone, length, formality, language mix, emoji use | Slowest |

### Style fact extraction (Faz 22G)

Capture **how** the user prefers to communicate, not what they're
saying. Examples:

- "Kullanıcı kısa, doğrudan cevaplar tercih ediyor"
- "Kullanıcı küfür / argo içeren samimi bir dil kullanıyor"
- "Kullanıcı emoji kullanmıyor"
- "Kullanıcı teknik terimleri Türkçe karşılığıyla kullanıyor"
- "Kullanıcı sohbette sen-zamiri yerine siz tercih ediyor"

Extract a `style` fact only when:

- The user explicitly states a preference ("kısa yaz", "Türkçe konuşalım"), **or**
- A pattern is clearly consistent across the conversation (no need to
  count messages — the model judges the strength of the signal).

Use `category: "style"` (added in Faz 22G — see the table below).
Tone facts age very slowly; the decay table reflects that.

---

## Categories (mandatory — pick exactly one per fact)

| Category | Use for |
|----------|---------|
| `location` | Where they live, moves, travel |
| `work` | Job, company, position, industry |
| `tech` | Programming languages, tools, technologies |
| `personal` | Marital status, family, physical traits |
| `preference` | Food, drink, style, theme preferences |
| `interest` | Hobbies, sports, entertainment |
| `habit` | Daily routines, recurring behaviours |
| `finance` | Investments, budget, financial events |
| `health` | Health, diet, nutrition |
| `relationship` | Interpersonal — friends, colleagues, family |
| `style` *(Faz 22G)* | Communication style — tone, length, formality, language mix, emoji use |

> "uncategorized" is **not allowed**. Every fact must commit to a category.
> If no category fits, the fact probably shouldn't be extracted.

---

## Relations vocabulary

Use one of these standard relations whenever possible. Custom relations
are accepted but discouraged — they fragment the graph.

| Relation | Direction | Example |
|----------|-----------|---------|
| `works_at` | person → org | Ömer → works_at → HangiKredi |
| `works_with` | person → person | Ömer → works_with → Murat |
| `lives_in` | person → place | Ömer → lives_in → İstanbul |
| `owns` | person → thing/animal | Ömer → owns → Pamuk |
| `married_to` | person → person | Ömer → married_to → Ayşe |
| `partner_of` | person → person | Ömer → partner_of → Zeynep |
| `knows` | person → person | Ömer → knows → Zeynep |
| `uses` | person → tool | Ömer → uses → Python |
| `studies` | person → subject | Ömer → studies → Django |
| `visits` | person → place | Zeynep → visits → Akasya AVM |

Relations are stored bidirectionally where it makes semantic sense
(`married_to`), but the source/target ordering should reflect what was
**explicitly stated** in the conversation.

---

## Confidence

| Score | When to use |
|-------|-------------|
| `1.0` | Explicitly stated by the user |
| `0.7-0.9` | Strongly implied — inference from clear context |
| `0.5-0.7` | Inferred — could plausibly be wrong |
| `< 0.5` | Don't extract; the bar is too low |

Default to `1.0` for direct statements. Use lower scores sparingly and
only when the inference is justifiable.

---

## Keywords

2–5 short search terms per fact. Used for keyword fallback when semantic
search misses. Pick terms a human would naturally search for, not
auto-extracted noun phrases.

---

## Language

Preserve the user's language for proper nouns, preferences, and
quotations. Conjugate fact content in the user's primary conversation
language. (For Turkish users, that's Turkish.)

---

## What NOT to extract

- Greetings, fillers, small talk
- The assistant's own messages or reasoning
- Tool call mechanics ("the agent called search_memory")
- Speculation framed as facts ("maybe Ömer likes coffee")
- Information already known to be invalidated within the same conversation
  (the user said X, then immediately corrected themselves to Y — extract Y only)

---

## AUDN — update decisions

When asked to compare a new fact against existing ones, return:

```json
{"action": "add | update | delete | noop", "target_fact_id": "...", "reason": "..."}
```

| Action | When |
|--------|------|
| `add` | Genuinely new — no overlap with existing |
| `update` | Replaces an existing fact (Istanbul → Ankara, employer change) |
| `delete` | Negation without replacement ("I quit tracking stocks") — invalidate the old, do NOT add a negative fact |
| `noop` | Already known — duplicate or subset |

`target_fact_id` is required for `update` and `delete`, must be `null`
for `add` and `noop`.

---

This schema is editable. Updating it:

1. Edit `workspace/memory_schema.md`.
2. Restart the bot (or wait for the next agent profile reload — Faz 22E).
3. Next extraction call honors the new contract.
