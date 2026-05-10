/**
 * Entity-type derivation for the relations graph.
 *
 * The backend (`memory_relations` table) stores raw verbs but not the
 * semantic role of each endpoint. We infer the type client-side from the
 * relation vocabulary — every standard verb in `workspace/memory_schema.md`
 * implies what its source/target should be (e.g. `works_at` →
 * person → organization). For each entity we score the implied roles
 * across all touching relations and pick the winner.
 *
 * No LLM call. No backend dependency. Deterministic + cheap.
 */

import type { MemoryRelation } from "@/api/admin";

export type EntityType =
  | "person"
  | "organization"
  | "place"
  | "product"
  | "topic"
  | "unknown";

export type RelationCategory =
  | "professional"
  | "social"
  | "spatial"
  | "ownership"
  | "other";

/** Tailwind-500 hex codes — readable on dark slate, dark-mode safe. */
export const ENTITY_COLOR: Record<EntityType, string> = {
  person: "#3b82f6", // blue-500
  organization: "#8b5cf6", // violet-500
  place: "#10b981", // emerald-500
  product: "#f59e0b", // amber-500
  topic: "#ec4899", // pink-500
  unknown: "#64748b", // slate-500
};

export const RELATION_COLOR: Record<RelationCategory, string> = {
  professional: "#8b5cf6",
  social: "#ec4899",
  spatial: "#10b981",
  ownership: "#f59e0b",
  other: "#64748b",
};

export const ENTITY_LABEL: Record<EntityType, string> = {
  person: "Kişi",
  organization: "Kurum",
  place: "Yer",
  product: "Ürün",
  topic: "Konu",
  unknown: "Diğer",
};

export const RELATION_LABEL: Record<RelationCategory, string> = {
  professional: "İş",
  social: "Sosyal",
  spatial: "Mekan",
  ownership: "Sahiplik",
  other: "Diğer",
};

/** Verb → (source role, target role). Verbs absent from this table
 *  contribute nothing to the score; their endpoints fall back to
 *  `unknown` unless other relations rescue them. */
const VERB_ROLES: Record<string, [EntityType, EntityType]> = {
  works_at: ["person", "organization"],
  works_with: ["person", "person"],
  lives_in: ["person", "place"],
  visits: ["person", "place"],
  owns: ["person", "product"],
  uses: ["person", "product"],
  studies: ["person", "topic"],
  married_to: ["person", "person"],
  partner_of: ["person", "person"],
  knows: ["person", "person"],
  // common informal extensions seen in memory extraction:
  shares_with: ["person", "person"],
  located_in: ["place", "place"],
  manages: ["person", "organization"],
  attended: ["person", "organization"],
};

/** Verb → category; everything outside this map → "other". */
const VERB_CATEGORY: Record<string, RelationCategory> = {
  works_at: "professional",
  works_with: "professional",
  studies: "professional",
  manages: "professional",
  attended: "professional",
  married_to: "social",
  partner_of: "social",
  knows: "social",
  shares_with: "social",
  lives_in: "spatial",
  visits: "spatial",
  located_in: "spatial",
  owns: "ownership",
  uses: "ownership",
};

/** Tie-break order — when two roles score equal, prefer "more salient"
 *  semantic categories. People dominate chat memory; products/topics are
 *  rarer so default to them last. */
const PRIORITY: EntityType[] = [
  "person",
  "organization",
  "place",
  "product",
  "topic",
  "unknown",
];

/** Build a `Map<canonical, EntityType>` from one pass over relations.
 *  Memoize at the call site (e.g. via `useMemo` keyed on relations array). */
export function deriveEntityTypes(
  relations: readonly MemoryRelation[],
): Map<string, EntityType> {
  const scores = new Map<string, Map<EntityType, number>>();

  const bump = (entity: string, type: EntityType) => {
    if (!entity || type === "unknown") return;
    let inner = scores.get(entity);
    if (!inner) {
      inner = new Map();
      scores.set(entity, inner);
    }
    inner.set(type, (inner.get(type) ?? 0) + 1);
  };

  for (const r of relations) {
    const src = r.canonical_source || r.source_entity;
    const tgt = r.canonical_target || r.target_entity;
    const roles = VERB_ROLES[r.relation];
    if (roles) {
      bump(src, roles[0]);
      bump(tgt, roles[1]);
    } else {
      // Ensure unknown verbs still register the entities so they can be
      // rescued by other relations or fall back cleanly.
      if (!scores.has(src)) scores.set(src, new Map());
      if (!scores.has(tgt)) scores.set(tgt, new Map());
    }
  }

  const out = new Map<string, EntityType>();
  for (const [entity, inner] of scores) {
    if (inner.size === 0) {
      out.set(entity, "unknown");
      continue;
    }
    let best: EntityType = "unknown";
    let bestScore = -1;
    for (const t of PRIORITY) {
      const score = inner.get(t) ?? 0;
      if (score > bestScore) {
        bestScore = score;
        best = t;
      }
    }
    out.set(entity, best);
  }
  return out;
}

/** Map a relation verb to a category for edge styling. */
export function getRelationCategory(verb: string): RelationCategory {
  return VERB_CATEGORY[verb] ?? "other";
}

/** All entity types in display order — used by the FilterBar chips. */
export const ALL_ENTITY_TYPES: EntityType[] = [
  "person",
  "organization",
  "place",
  "product",
  "topic",
  "unknown",
];

/** All relation categories in display order. */
export const ALL_RELATION_CATEGORIES: RelationCategory[] = [
  "professional",
  "social",
  "spatial",
  "ownership",
  "other",
];
