/**
 * The category registry: one palette, defined once, shared by all three views.
 *
 * `CATEGORY_ORDER` is the canonical order — it runs from the most deliberate use
 * of attention to the least, and then to the categories that are deliberately
 * neutral. It is also the palette's fixed slot order, which is what the
 * colourblind-separation validation was run against, so reordering this array
 * invalidates that result. Colour follows the category, never its rank in a
 * particular day, so filtering a view never repaints the survivors.
 */

export interface CategoryMeta {
  /** Category key exactly as the server sends it. */
  key: string;
  /** The CSS custom property holding this category's colour, both modes. */
  varName: string;
  /** Short gloss used in the legend and the uncertain queue's answer buttons. */
  gloss: string;
}

const META: readonly CategoryMeta[] = [
  { key: "deep work", varName: "--cat-deep-work", gloss: "sustained, single-task attention" },
  { key: "research", varName: "--cat-research", gloss: "reading to answer a question" },
  { key: "learning", varName: "--cat-learning", gloss: "studying something new" },
  { key: "devotion", varName: "--cat-devotion", gloss: "prayer, scripture, reflection" },
  { key: "communication", varName: "--cat-communication", gloss: "mail, chat, calls" },
  { key: "shallow work", varName: "--cat-shallow-work", gloss: "real work, low concentration" },
  { key: "entertainment", varName: "--cat-entertainment", gloss: "chosen leisure" },
  { key: "doomscrolling", varName: "--cat-doomscrolling", gloss: "feed consumption without an end state" },
  { key: "admin", varName: "--cat-admin", gloss: "overhead and errands" },
  { key: "idle", varName: "--cat-idle", gloss: "machine awake, nobody there" },
  { key: "uncertain", varName: "--cat-uncertain", gloss: "not guessed — awaiting your answer" },
];

const BY_KEY = new Map(META.map((m) => [m.key, m]));

/** Canonical display order. Unknown categories sort after all known ones. */
export const CATEGORY_ORDER: readonly string[] = META.map((m) => m.key);

/** Reserved label from `agent/groundtruth/types.py` — stored rather than guessed. */
export const UNCERTAIN = "uncertain";

/**
 * The CSS colour for a category. A category the server invents beyond the seed
 * taxonomy resolves to `--cat-other` rather than to a generated hue: the palette
 * is a fixed set of validated slots, so there is no ninth colour to hand out.
 */
export function categoryColor(key: string): string {
  const meta = BY_KEY.get(key);
  return `var(${meta ? meta.varName : "--cat-other"})`;
}

export function categoryGloss(key: string): string | undefined {
  return BY_KEY.get(key)?.gloss;
}

export function isKnownCategory(key: string): boolean {
  return BY_KEY.has(key);
}

/** Sort comparator placing categories in canonical order, unknowns last (alphabetically). */
export function byCanonicalOrder(a: string, b: string): number {
  const ia = CATEGORY_ORDER.indexOf(a);
  const ib = CATEGORY_ORDER.indexOf(b);
  if (ia === -1 && ib === -1) return a.localeCompare(b);
  if (ia === -1) return 1;
  if (ib === -1) return -1;
  return ia - ib;
}
