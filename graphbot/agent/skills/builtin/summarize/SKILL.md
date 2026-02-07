---
name: summarize
description: Summarize or extract text from URLs, files, and YouTube videos
always: false
metadata:
  requires:
    bins: [summarize]
---

# Summarize

Fast CLI to summarize URLs, local files, and YouTube links.

## When to use

Use this skill when the user asks:
- "summarize this URL/article"
- "what's this link/video about?"
- "transcribe this YouTube/video"

## Quick start

```bash
summarize "https://example.com"
summarize "/path/to/file.pdf"
summarize "https://youtu.be/dQw4w9WgXcQ" --youtube auto
```

## YouTube: summary vs transcript

Best-effort transcript (URLs only):
```bash
summarize "https://youtu.be/..." --youtube auto --extract-only
```

## Useful flags

- `--length short|medium|long|xl` — control output length
- `--extract-only` — extract text without summarizing (URLs only)
- `--json` — machine-readable output
