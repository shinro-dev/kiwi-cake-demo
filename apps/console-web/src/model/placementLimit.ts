// Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE.
//
// THE LIMIT A PLACEMENT REFUSAL NAMES, in one sentence, from the values its record carries and from nothing else. Read by
// the failure banner and by the scene map's not-placeable block, so the two say the same thing.
//
// Three shapes reach the page, and each is read for what it was computed from:
//
//   THE PLACEMENT SOLVER'S EXPLANATION (track P, placement_envelope.explain_refusal): `explanation` is the binding
//   constraint's own sentence, with its deficit and the margin that deficit was measured against; `hint` the move of the
//   block; `hint_places_block` whether that move was checked to place it; `<constraint>_deficit_mm` every other limit
//   that also holds. The sentence is stated as recorded. Its side deficit is measured after the best bounded advance, so
//   it is never set beside `side_margin_mm`, which is the margin before any advance.
//   A CONSTRAINT WITHOUT THE SENTENCE (`constraint` with `deficit_mm`): the constraint in words and the deficit, paired
//   only with what the solver computed it from.
//   TRACK S'S SCENE-BLOCK RECORD (policy_search._block_measurements: `binding_constraint`, `side_margin_mm`,
//   `required_side_margin_mm`, `along_axis_need_mm`, `advance_budget_mm`, `deficit_mm`). Its `deficit_mm` is ALWAYS the
//   along-axis need less the advance budget, whichever limit it names as binding, so it is never read as the side limit's
//   shortfall: each limit is stated from its own values, the binding one first, and each only when it is short.
//   NO CONSTRAINT AT ALL (the controller's placement_refusal_values): the side limit and the along-axis limit are
//   derived from the recorded margins, each only when the record shows it short, each against its own requirement.

type Value = string | number | boolean | null | undefined;

function mm(value: number): string {
  return `${String(Math.round(value * 100) / 100)} mm`;
}

/** Each constraint the solver can name, in an operator's words, with its deficit. */
const CONSTRAINT_WORDS: Readonly<Record<string, (deficit: string | null, edge: string | null) => string>> = {
  side_edge: (deficit, edge) => `the side limit${edge === null ? '' : ` on the ${edge} edge`}${deficit === null ? '' : `, ${deficit} short at the best bounded advance`}`,
  along_budget: (deficit) => `the along-axis advance budget${deficit === null ? '' : `, ${deficit} past it`}`,
  far_edge: (deficit, edge) =>
    `the pad edge the reach axis runs toward${edge === null ? '' : ` (the ${edge} edge)`}${deficit === null ? '' : `, ${deficit} short`}, which every advance only shortens`,
  arm_reach: (deficit) => `the arm alone falls short with no base motion allowed${deficit === null ? '' : `, by ${deficit}`}`,
  pickup_inside_grasp_radius: (deficit) => `the block stands nearer than the pregrasp can reach${deficit === null ? '' : `, by ${deficit}`}`,
  lateral_needs_capability: (deficit) => `only a sideways base move${deficit === null ? '' : ` of ${deficit}`} would place it, which the forward-only base cannot make`,
};

/** The other limits a record names beside the binding one, in the words of the sentence "X is also short". */
const ALSO_WORDS: Readonly<Record<string, string>> = {
  side_edge: 'the side limit',
  along_budget: 'the along-axis budget',
  far_edge: 'the edge the reach axis runs toward',
  arm_reach: 'the arm reach',
  pickup_inside_grasp_radius: 'the pickup band',
  lateral_needs_capability: 'the sideways move',
};

export function placementLimit(values: readonly { name: string; value: Value }[]): string | null {
  const read = (name: string): Value => values.find((item) => item.name === name)?.value ?? null;
  const num = (name: string): number | null => {
    const value = read(name);
    return typeof value === 'number' && Number.isFinite(value) ? value : null;
  };
  const text = (name: string): string | null => {
    const value = read(name);
    return typeof value === 'string' && value.trim() !== '' ? value : null;
  };

  const explanation = text('explanation');
  const parts: string[] = [];

  // TRACK S'S SCENE RECORD: each limit from its own numbers, never its along-axis deficit set beside the side limit.
  if (explanation === null && text('constraint') === null && (num('along_axis_need_mm') !== null || num('advance_budget_mm') !== null)) {
    const clauses: { name: string; words: string }[] = [];
    const side = num('side_margin_mm');
    const required = num('required_side_margin_mm');
    if (side !== null && required !== null && side < required) {
      clauses.push({ name: 'side_edge', words: `the side limit: a side margin of ${mm(side)} against the ${mm(required)} required before any advance, ${mm(required - side)} short` });
    }
    const need = num('along_axis_need_mm');
    const budget = num('advance_budget_mm');
    if (need !== null && budget !== null && need > budget) {
      const recorded = num('deficit_mm');
      const past = recorded !== null && recorded > 0 ? recorded : need - budget;
      clauses.push({ name: 'along_budget', words: `the along-axis advance: ${mm(need)} needed against the ${mm(budget)} budget, ${mm(past)} past it` });
    }
    if (clauses.length === 0) return null;
    const binding = text('binding_constraint');
    clauses.sort((a, b) => Number(b.name === binding) - Number(a.name === binding));
    const said = clauses.map((clause, index) => (index === 0 ? clause.words : `and ${clause.words}`)).join('; ');
    const hint = text('hint');
    return hint === null ? said : `${said}; ${read('hint_places_block') === false ? `for this limit only: ${hint}` : hint}`;
  }

  const constraint = text('constraint');
  if (explanation !== null) {
    parts.push(explanation);
  } else if (constraint !== null) {
    const deficit = num('deficit_mm');
    const deficitWords = deficit === null || deficit === 0 ? null : mm(deficit);
    const say = CONSTRAINT_WORDS[constraint];
    if (constraint === 'along_budget' && num('axis_advance_needed_unbounded_mm') !== null) {
      // The deficit is the need less the budget: stated with the need it was computed from.
      const need = num('axis_advance_needed_unbounded_mm') as number;
      const budget = num('bounded_base_advance_limit_mm');
      parts.push(`the along-axis advance budget: ${mm(need)} of base advance needed${deficitWords === null ? '' : `, ${deficitWords} past the ${budget === null ? 'budget' : `${mm(budget)} budget`}`}`);
    } else {
      parts.push(say === undefined ? `the limit the record names (${constraint})${deficitWords === null ? '' : `, ${deficitWords} short`}` : say(deficitWords, text('binding_edge')));
    }
  } else {
    // NO CONSTRAINT NAMED: each limit the recorded margins show short, against its own requirement.
    const side = num('side_margin_mm');
    const required = num('required_side_margin_mm');
    if (side !== null && required !== null && side < required) {
      parts.push(`the side limit: a side margin of ${mm(side)} against the ${mm(required)} required before any advance, ${mm(required - side)} short`);
    }
    const need = num('axis_advance_needed_unbounded_mm');
    const limit = num('bounded_base_advance_limit_mm');
    if (need !== null && limit !== null && need > limit) {
      parts.push(`${parts.length > 0 ? 'and ' : ''}the along-axis advance: ${mm(need)} needed against the ${mm(limit)} limit, ${mm(need - limit)} past it`);
    }
    if (parts.length === 0) return null;
  }

  // Every other limit the record says also holds, by its own deficit.
  if (constraint !== null) {
    for (const [other, words] of Object.entries(ALSO_WORDS)) {
      if (other === constraint) continue;
      const deficit = num(`${other}_deficit_mm`);
      if (deficit !== null && deficit !== 0) parts.push(`${words} is also short, by ${mm(deficit)}`);
    }
  }

  const hint = text('hint');
  if (hint !== null) parts.push(read('hint_places_block') === false ? `for this limit only: ${hint}` : hint);
  return parts.join('; ');
}
